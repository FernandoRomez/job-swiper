#!/usr/bin/env python3
"""studio.py — servidor de Job Studio (puerto 8790). Solo stdlib + playwright.
    python3 studio/studio.py
"""
import json
import mimetypes
import os
import queue
import re
import subprocess
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

import db   # noqa: E402
import personal   # noqa: E402
import ai   # noqa: E402

STATIC = os.path.join(HERE, 'static')
DOCS = os.path.join(HERE, 'docs')
RESUMES = os.path.join(ROOT, 'resumes')
COLA = os.path.join(ROOT, 'cola')
PORT = int(os.environ.get('STUDIO_PORT', 8790))
ID_RE = r'([A-Za-z0-9_\-]+)'

os.makedirs(DOCS, exist_ok=True)
db.init()


# --------------------------------------------------------------------------- #
#  Módulos extra (plugins en studio/extras/*.py con handle())                 #
# --------------------------------------------------------------------------- #
def load_extras():
    import importlib.util
    mods = []
    d = os.path.join(HERE, 'extras')
    for f in sorted(os.listdir(d)) if os.path.isdir(d) else []:
        if not f.endswith('.py') or f in ('digest.py', '__init__.py'):
            continue
        try:
            spec = importlib.util.spec_from_file_location(f'studio_extra_{f[:-3]}', os.path.join(d, f))
            m = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(m)
            if callable(getattr(m, 'handle', None)):
                mods.append(m)
                print(f'[extras] cargado {f}')
        except Exception as e:
            print(f'[extras] error importando {f}: {e}')
    return mods


EXTRAS = load_extras()

# --------------------------------------------------------------------------- #
#  Puntaje por palabras clave del perfil                                      #
# --------------------------------------------------------------------------- #
KEYWORDS = {  # palabra -> peso
    'plc': 3, 'robot': 3, 'robót': 3, 'automatiz': 3, 'automation': 3, 'controls': 3, 'control': 1,
    'embedded': 3, 'embebid': 3, 'python': 2, 'c++': 2, 'visión': 2, 'vision': 2, 'mecatr': 3,
    'mechatron': 3, 'siemens': 2, 'tia portal': 2, 'hmi': 2, 'scada': 1, 'yaskawa': 2, 'fanuc': 1,
    'cnc': 1, 'opencv': 2, 'raspberry': 1, 'arduino': 1, 'esp32': 1, 'ros': 1, 'drone': 1, 'dron': 1,
    'commissioning': 2, 'puesta en marcha': 2, 'solidworks': 1, 'fusion 360': 1, 'matlab': 1,
    'firmware': 2, 'microcontrol': 2, 'sensor': 1, 'linux': 1, 'entry': 1, 'junior': 1, 'trainee': 1,
}


def score(job):
    t = (job.get('title') or '').lower()
    d = (job.get('description') or '').lower()
    s = 0
    for k, w in KEYWORDS.items():
        if k in t:
            s += w * 3          # el título pesa más
        elif k in d:
            s += w
    if re.search(r'\bsenior\b|\bsr\.?\b|\blead\b|\bprincipal\b|\bmanager\b', t):
        s -= 6
    return max(0, min(100, round(s * 100 / 30)))


def pais(location):
    l = (location or '').lower()
    if 'méxico' in l or 'mexico' in l:
        return 'mx'
    if 'canad' in l:
        return 'ca'
    if 'remot' in l or 'latin' in l or 'latam' in l or 'anywhere' in l or 'worldwide' in l:
        return 'remoto'
    if 'united states' in l or 'estados unidos' in l or re.search(r',\s*[A-Z]{2}\b', location or ''):
        return 'us'
    return 'otro'


# --------------------------------------------------------------------------- #
#  Datos                                                                      #
# --------------------------------------------------------------------------- #
def all_jobs():
    with db._conn() as c:
        jobs = [db._row_to_job(r) for r in c.execute('SELECT * FROM jobs ORDER BY created_at DESC')]
        apps = {r['job_id']: r['status'] for r in c.execute('SELECT job_id,status FROM applications')}
    return jobs, apps


def doc_path(jid):
    return os.path.join(DOCS, f'{jid}.json')


def load_doc(jid):
    for p in (doc_path(jid), os.path.join(COLA, f'{jid}.json'), os.path.join(COLA, 'hechas', f'{jid}.json')):
        if os.path.exists(p):
            try:
                return json.load(open(p, encoding='utf-8'))
            except Exception:
                pass
    return None


def save_doc(jid, doc):
    doc['job_id'] = jid
    with open(doc_path(jid), 'w', encoding='utf-8') as f:
        json.dump(doc, f, ensure_ascii=False, indent=1)


# --------------------------------------------------------------------------- #
#  PDF: hilo dedicado con chromium headless (playwright sync no es thread-safe) #
# --------------------------------------------------------------------------- #
class PdfWorker(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self.q = queue.Queue()

    def run(self):
        from playwright.sync_api import sync_playwright
        pw = browser = None
        while True:
            html, out, res = self.q.get()
            try:
                if browser is None:
                    pw = sync_playwright().start()
                    browser = pw.chromium.launch(headless=True)
                page = browser.new_page()
                page.set_content(html, wait_until='networkidle')
                page.pdf(path=out, format='A4', print_background=True, prefer_css_page_size=True)
                page.close()
                res.put(None)
            except Exception as e:
                res.put(e)

    def render(self, html, out, timeout=90):
        res = queue.Queue()
        self.q.put((html, out, res))
        err = res.get(timeout=timeout)
        if err:
            raise err


PDF = PdfWorker()
PDF.start()

# --------------------------------------------------------------------------- #
#  Scrapers                                                                   #
# --------------------------------------------------------------------------- #
SCRAPE = {'running': False, 'log': [], 'counts': {}, 'started_at': None, 'finished_at': None}
_scrape_lock = threading.Lock()


def _by_source():
    with db._conn() as c:
        return {r[0] or '?': r[1] for r in c.execute('SELECT source, COUNT(*) FROM jobs GROUP BY source')}


def run_scrape(sources, query, location):
    log = SCRAPE['log']
    before = _by_source()
    try:
        if not os.path.exists(os.path.join(HERE, 'scrapers', 'run.py')):
            log.append('ERROR: studio/scrapers/run.py no existe todavía.')
            return
        cmd = [sys.executable, '-u', '-m', 'studio.scrapers.run', '--sources', ','.join(sources),
               '--query', query or '', '--location', location or '']
        log.append('$ ' + ' '.join(cmd))
        p = subprocess.Popen(cmd, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        for line in p.stdout:
            log.append(line.rstrip())
            del log[:-500]
        p.wait()
        log.append(f'Terminado (código {p.returncode}).')
    except Exception as e:
        log.append(f'ERROR: {e}')
    finally:
        after = _by_source()
        SCRAPE['counts'] = {k: v - before.get(k, 0) for k, v in after.items() if v - before.get(k, 0)}
        SCRAPE['running'] = False
        SCRAPE['finished_at'] = time.time()


# --------------------------------------------------------------------------- #
#  HTTP                                                                       #
# --------------------------------------------------------------------------- #
class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send_json(self, obj, code=200):
        b = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def send_file(self, path):
        if not os.path.isfile(path):
            return self.send_json({'error': 'no encontrado'}, 404)
        b = open(path, 'rb').read()
        self.send_response(200)
        self.send_header('Content-Type', mimetypes.guess_type(path)[0] or 'application/octet-stream')
        self.send_header('Content-Length', str(len(b)))
        self.send_header('Cache-Control', 'no-cache')
        self.end_headers()
        self.wfile.write(b)

    def body(self):
        n = int(self.headers.get('Content-Length') or 0)
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n).decode('utf-8'))
        except Exception:
            return {}

    def do_GET(self):
        self.route('GET')

    def do_POST(self):
        self.route('POST')

    def do_PUT(self):
        self.route('PUT')

    def do_DELETE(self):
        self.route('DELETE')

    def route(self, method):
        u = urllib.parse.urlparse(self.path)
        path, qs = u.path, {k: v[0] for k, v in urllib.parse.parse_qs(u.query).items()}
        try:
            if path.startswith('/api/x/'):
                body = self.body() if method in ('POST', 'PUT', 'DELETE') else {}
                for mod in EXTRAS:
                    r = mod.handle(method, path, qs, body)
                    if r is not None:
                        return self.send_json(r[1], r[0])
                return self.send_json({'error': 'módulo no encontrado'}, 404)
            for m, pat, fn in ROUTES:
                if m == method:
                    mt = re.fullmatch(pat, path)
                    if mt:
                        return fn(self, qs, *mt.groups())
            if method == 'GET':
                return self.static(path)
            self.send_json({'error': 'ruta desconocida'}, 404)
        except Exception as e:
            import traceback
            traceback.print_exc()
            self.send_json({'error': str(e)}, 500)

    def static(self, path):
        if path.startswith('/files/'):
            base, rel = RESUMES, path[len('/files/'):]
        else:
            base, rel = STATIC, (path.lstrip('/') or 'index.html')
        full = os.path.realpath(os.path.join(base, urllib.parse.unquote(rel)))
        if not full.startswith(os.path.realpath(base)):
            return self.send_json({'error': 'prohibido'}, 403)
        self.send_file(full)

    # ---- endpoints ----
    def api_jobs(self, qs):
        jobs, apps = all_jobs()
        q = (qs.get('q') or '').lower()
        out = []
        for j in jobs:
            if qs.get('source') and (j.get('source') or '').lower() != qs['source'].lower():
                continue
            if qs.get('status') and j.get('status') != qs['status']:
                continue
            if qs.get('country') and pais(j.get('location')) != qs['country']:
                continue
            if q and q not in ' '.join(str(j.get(k) or '') for k in ('title', 'company', 'location', 'description')).lower():
                continue
            out.append({k: j.get(k) for k in ('id', 'title', 'company', 'location', 'source', 'link',
                                               'easy_apply', 'created_at', 'status')}
                       | {'app_status': apps.get(j['id']), 'score': score(j), 'country': pais(j.get('location')),
                          'has_doc': os.path.exists(doc_path(j['id']))})
        self.send_json(out)

    def job_or_404(self, jid):
        j = db.get_job(jid)
        if not j:
            self.send_json({'error': 'empleo no encontrado'}, 404)
        return j

    def api_job(self, qs, jid):
        j = self.job_or_404(jid)
        if j:
            self.send_json(j | {'app': db.get_application(jid), 'doc': load_doc(jid), 'score': score(j),
                                'country': pais(j.get('location'))})

    def api_status(self, qs, jid):
        st = self.body().get('status')
        if st not in ('new', 'liked', 'passed'):
            return self.send_json({'error': 'status inválido'}, 400)
        db.set_status(jid, st)
        self.send_json({'ok': True})

    def api_profile(self, qs):
        lang = qs.get('lang')
        self.send_json(ai.CONTACT | {'edu': ai.EDU, 'base': ai.build_base_doc(lang)})

    def api_cv(self, qs, jid):
        j = self.job_or_404(jid)
        if not j:
            return
        prev = load_doc(jid) or {}
        doc = ai.tailor_cv(j, ai.build_base_doc(ai.detect_lang(j)))
        for k in ('carta', 'plantilla', 'respuestas', 'salario'):   # no pisa lo que ya tenía el usuario
            if prev.get(k) and not doc.get(k):
                doc[k] = prev[k]
        save_doc(jid, doc)
        self.send_json(doc)

    def api_put_doc(self, qs, jid):
        doc = self.body()
        if not isinstance(doc, dict):
            return self.send_json({'error': 'DOC inválido'}, 400)
        save_doc(jid, doc)
        self.send_json({'ok': True})

    def api_cover(self, qs, jid):
        j = self.job_or_404(jid)
        if not j:
            return
        doc = load_doc(jid) or ai.tailor_cv(j)
        doc['carta'] = ai.cover_letter(j, doc)
        save_doc(jid, doc)
        self.send_json({'carta': doc['carta']})

    def api_pdf(self, qs, jid):
        b = self.body()
        kind = b.get('kind') if b.get('kind') in ('cv', 'cover') else 'cv'
        html = b.get('html') or ''
        if not html.strip():
            return self.send_json({'error': 'falta html'}, 400)
        os.makedirs(os.path.join(RESUMES, jid), exist_ok=True)
        rel = f'{jid}/{"CV" if kind == "cv" else "Carta"}_{personal.SLUG}.pdf'
        out = os.path.join(RESUMES, rel)
        PDF.render(html, out)
        try:   # registra la ruta en applications sin cambiar un status ya existente
            a = db.get_application(jid) or {}
            db.save_application(jid, a.get('status') or 'prepared',
                                **({'resume_pdf': out} if kind == 'cv' else {'cover_pdf': out}))
        except Exception:
            pass
        self.send_json({'path': out, 'url': '/files/' + urllib.parse.quote(rel)})

    def api_apply(self, qs, jid):
        j = self.job_or_404(jid)
        if not j:
            return
        if not jid.startswith('li_'):
            return self.send_json({'open_url': j.get('link')})
        doc = load_doc(jid)
        if not doc:
            doc = ai.tailor_cv(j)
            doc['carta'] = ai.cover_letter(j, doc)
            save_doc(jid, doc)
        clean = {k: v for k, v in doc.items() if not k.startswith('_')}
        os.makedirs(COLA, exist_ok=True)
        with open(os.path.join(COLA, f'{jid}.json'), 'w', encoding='utf-8') as f:
            json.dump(clean, f, ensure_ascii=False, indent=1)
        logf = open(os.path.join(DOCS, f'{jid}.apply.log'), 'w')
        subprocess.Popen(['python3', 'postular.py', 'abrir', jid], cwd=ROOT, stdout=logf,
                         stderr=subprocess.STDOUT, start_new_session=True)
        self.send_json({'ok': True, 'launched': True, 'cola': f'cola/{jid}.json',
                        'log': f'studio/docs/{jid}.apply.log'})

    def api_scrape(self, qs):
        b = self.body()
        with _scrape_lock:
            if SCRAPE['running']:
                return self.send_json({'error': 'ya hay un scrape en curso'}, 409)
            SCRAPE.update(running=True, log=[], counts={}, started_at=time.time(), finished_at=None)
        sources = b.get('sources') or []
        if isinstance(sources, str):
            sources = [s.strip() for s in sources.split(',') if s.strip()]
        threading.Thread(target=run_scrape, args=(sources, b.get('query', ''), b.get('location', '')),
                         daemon=True).start()
        self.send_json({'ok': True})

    def api_scrape_status(self, qs):
        self.send_json(SCRAPE)

    def api_stats(self, qs):
        with db._conn() as c:
            by_status = {r[0]: r[1] for r in c.execute('SELECT status, COUNT(*) FROM jobs GROUP BY status')}
            applied = c.execute("SELECT COUNT(*) FROM applications WHERE status='applied'").fetchone()[0]
            by_app = {r[0]: r[1] for r in c.execute('SELECT status, COUNT(*) FROM applications GROUP BY status')}
        self.send_json({'total': sum(by_status.values()), 'by_source': _by_source(), 'by_status': by_status,
                        'applied': applied, 'by_app_status': by_app})

    def api_templates(self, qs):
        d = os.path.join(STATIC, 'templates')
        names = sorted(f[:-4] for f in os.listdir(d) if f.endswith('.css')) if os.path.isdir(d) else []
        self.send_json([{'id': n, 'css': f'templates/{n}.css'} for n in names])

    def api_modules(self, qs):
        d = os.path.join(STATIC, 'modules')
        fs = sorted(f for f in os.listdir(d) if f.endswith(('.js', '.css'))) if os.path.isdir(d) else []
        self.send_json({'js': [f'modules/{f}' for f in fs if f.endswith('.js')],
                        'css': [f'modules/{f}' for f in fs if f.endswith('.css')]})


ROUTES = [
    ('GET', r'/api/modules', H.api_modules),
    ('GET', r'/api/jobs', H.api_jobs),
    ('GET', rf'/api/jobs/{ID_RE}', H.api_job),
    ('POST', rf'/api/jobs/{ID_RE}/status', H.api_status),
    ('GET', r'/api/profile', H.api_profile),
    ('POST', rf'/api/jobs/{ID_RE}/cv', H.api_cv),
    ('PUT', rf'/api/jobs/{ID_RE}/doc', H.api_put_doc),
    ('POST', rf'/api/jobs/{ID_RE}/doc', H.api_put_doc),
    ('POST', rf'/api/jobs/{ID_RE}/cover', H.api_cover),
    ('POST', rf'/api/jobs/{ID_RE}/pdf', H.api_pdf),
    ('POST', rf'/api/jobs/{ID_RE}/apply', H.api_apply),
    ('POST', r'/api/scrape', H.api_scrape),
    ('GET', r'/api/scrape/status', H.api_scrape_status),
    ('GET', r'/api/stats', H.api_stats),
    ('GET', r'/api/templates', H.api_templates),
]


if __name__ == '__main__':
    print(f'Job Studio en http://localhost:{PORT}')
    ThreadingHTTPServer(('127.0.0.1', PORT), H).serve_forever()
