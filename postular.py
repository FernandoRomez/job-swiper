#!/usr/bin/env python3
"""
postular.py — flujo corto de JobSwiper: buscar → CV a la medida → formulario lleno → TÚ envías.

    python3 postular.py buscar [mx|us|remoto|todo] [--n 6]   # trae ofertas nuevas de LinkedIn
    python3 postular.py lista                                # ofertas nuevas sin postular
    python3 postular.py abrir [li_123 li_456 ...]            # prepara lo que haya en cola/

Cómo se usa en la práctica (con Claude Code):
  1. Pídele a Claude: «busca trabajos y prepárame los mejores».
  2. Claude corre `buscar`, lee las ofertas, elige las que encajan y escribe
     un archivo cola/<id>.json por oferta con el CV, la carta y respuestas a la medida.
  3. `abrir` genera el PDF, abre cada solicitud en su propia pestaña, sube el CV,
     rellena los campos y se DETIENE antes de "Enviar". Tú revisas y pulsas Enviar.

Nunca envía nada por sí solo. Cierra Chrome cuando termines y el script sale.
"""
import html
import json
import os
import re
import sys
import time

import app
import db

HERE = os.path.dirname(os.path.abspath(__file__))
COLA = os.path.join(HERE, 'cola')
HECHAS = os.path.join(COLA, 'hechas')
OUT = os.path.join(HERE, 'resumes')

from personal import NOMBRE, EMAIL, TEL_MX, TEL_US, LINKEDIN, WEB, CIUDAD, EDU, SLUG  # noqa: E402

# --------------------------------------------------------------------------- #
#  Búsquedas: dónde es más probable que te contraten RÁPIDO y bien pagado       #
# --------------------------------------------------------------------------- #
ENTRY = {'f_E': '1,2,3', 'f_TPR': 'r1209600'}          # prácticas/entrada/asociado, últimas 2 semanas
BUSQUEDAS = {
    # México — nearshoring: la vía más rápida (sin visa), mucha demanda de automatización
    'mx': [
        ('Ingeniero de automatización', 'Querétaro, México'),
        ('Ingeniero de automatización', 'Nuevo León, México'),
        ('Ingeniero de automatización', 'Coahuila, México'),
        ('Ingeniero de automatización', 'Guanajuato, México'),
        ('Ingeniero de automatización', 'Puebla, México'),
        ('Controls Engineer', 'México'),
        ('Ingeniero de puesta en marcha', 'México'),
        ('Field Service Engineer', 'México'),
        ('Ingeniero de robótica', 'México'),
        ('Ingeniero de proyectos automatización', 'México'),
    ],
    # EE. UU. — visa TN (solo puestos con título "Engineer"; requiere tu título universitario)
    'us': [
        ('Controls Engineer', 'Texas, United States'),
        ('Automation Engineer', 'Texas, United States'),
        ('Commissioning Engineer', 'United States'),
        ('Robotics Engineer', 'Michigan, United States'),
        ('Controls Engineer', 'Arizona, United States'),
    ],
    # Remoto desde México — pago en USD, más competido
    'remoto': [
        ('Embedded Software Engineer', 'Latin America'),
        ('Robotics Software Engineer', 'Latin America'),
        ('PLC Programmer', 'Latin America'),
    ],
}
REMOTE = {'f_WT': '2'}


def _pais(location):
    l = (location or '').lower()
    if 'méxico' in l or 'mexico' in l:
        return 'mx'
    if 'canad' in l:
        return 'ca'
    if 'estados unidos' in l or 'united states' in l or re.search(r',\s*[A-Z]{2}\b', location or ''):
        return 'us'
    if 'latin' in l or 'remot' in l or 'latam' in l:
        return 'remoto'
    return 'us' if any(s in l for s in ('carolina', 'texas', 'virginia', 'michigan', 'ohio',
                                         'washington', 'kentucky', 'arizona', 'california')) else 'otro'


# --------------------------------------------------------------------------- #
#  CV en HTML (mismo estilo que tu CV original) → PDF                           #
# --------------------------------------------------------------------------- #
T = {
    'es': {'perfil': 'Perfil profesional', 'comp': 'Competencias técnicas',
           'exp': 'Experiencia profesional', 'proy': 'Proyectos', 'edu': 'Educación',
           'cert': 'Certificaciones', 'idiomas': 'Idiomas'},
    'en': {'perfil': 'Professional Summary', 'comp': 'Technical Skills',
           'exp': 'Professional Experience', 'proy': 'Projects', 'edu': 'Education',
           'cert': 'Certifications', 'idiomas': 'Languages'},
}

CSS = """
@page { size: Letter; margin: 0.5in; }
* { box-sizing: border-box; }
body { font-family: Helvetica, Arial, sans-serif; color: #111; font-size: 10.4pt; line-height: 1.32; margin: 0; }
h1 { font-size: 21pt; margin: 0 0 2px; }
.tag { font-weight: bold; font-size: 10.6pt; margin-bottom: 3px; }
.contact { font-size: 9.4pt; color: #333; }
h2 { font-size: 10.6pt; text-transform: uppercase; letter-spacing: .4px; border-bottom: 1.4px solid #111;
     padding-bottom: 2px; margin: 11px 0 5px; }
ul { margin: 2px 0 4px; padding-left: 16px; } li { margin: 1px 0; }
.row { display: flex; justify-content: space-between; font-weight: bold; margin-top: 4px; }
.sub { font-style: italic; }
p { margin: 0; }
"""


def _e(s):
    return html.escape(str(s or ''))


def cv_html(d, pais):
    lang = d.get('idioma', 'en')
    t, edu = T[lang], EDU[lang]
    tel = TEL_MX if pais == 'mx' else TEL_US if pais in ('us', 'ca') else f'{TEL_MX} · {TEL_US}'
    ubic = d.get('ubicacion') or (CIUDAD['es'] + ' · Disponible para reubicarme' if lang == 'es'
                                  else CIUDAD['en'] + ' · Open to relocate')
    parts = [f'<h1>{_e(NOMBRE)}</h1>', f'<div class="tag">{_e(d["titular"])}</div>',
             f'<div class="contact">{_e(EMAIL)} · {_e(tel)} · {_e(LINKEDIN)} · {_e(WEB)}<br>{_e(ubic)}</div>',
             f'<h2>{t["perfil"]}</h2><p>{_e(d["perfil"])}</p>',
             f'<h2>{t["comp"]}</h2><ul>']
    parts += [f'<li><b>{_e(k)}:</b> {_e(v)}</li>' for k, v in d['competencias']]
    parts.append(f'</ul><h2>{t["exp"]}</h2>')
    for x in d['experiencia']:
        parts.append(f'<div class="row"><span>{_e(x["puesto"])}</span><span>{_e(x["fechas"])}</span></div>'
                     f'<div class="sub">{_e(x["empresa"])}</div><ul>'
                     + ''.join(f'<li>{_e(b)}</li>' for b in x['bullets']) + '</ul>')
    parts.append(f'<h2>{t["proy"]}</h2>')
    for p in d['proyectos']:
        parts.append(f'<div class="row"><span>{_e(p["titulo"])}</span></div><ul>'
                     + ''.join(f'<li>{_e(b)}</li>' for b in p['bullets']) + '</ul>')
    parts.append(f'<h2>{t["edu"]}</h2><div class="row"><span>{_e(edu["titulo"])}</span>'
                 f'<span>{_e(edu["fechas"])}</span></div><div class="sub">{_e(edu["escuela"])}</div>'
                 f'<p style="margin-top:4px"><b>{t["cert"]}:</b> {_e("; ".join(edu["certs"]))}</p>'
                 f'<p style="margin-top:3px"><b>{t["idiomas"]}:</b> {_e(edu["idiomas"])}</p>')
    return (f'<!doctype html><html lang="{lang}"><head><meta charset="utf-8"><style>{CSS}</style></head>'
            f'<body>{"".join(parts)}</body></html>')


def carta_html(texto, lang):
    paras = ''.join(f'<p style="margin:0 0 10px">{_e(p)}</p>' for p in texto.split('\n\n') if p.strip())
    fecha = time.strftime('%d/%m/%Y')
    return (f'<!doctype html><html lang="{lang}"><head><meta charset="utf-8"><style>{CSS}</style></head><body>'
            f'<h1>{_e(NOMBRE)}</h1><div class="contact">{_e(EMAIL)} · {_e(LINKEDIN)} · {_e(WEB)}</div>'
            f'<p style="margin:18px 0 14px">{fecha}</p>{paras}</body></html>')


# --------------------------------------------------------------------------- #
#  Respuestas del formulario (reglas; sin IA externa, sin esperar a nadie)      #
# --------------------------------------------------------------------------- #
CTX = {}   # contexto del empleo que se está preparando (país + respuestas a la medida)

# preguntas cuya respuesta depende del país: nunca se toman de la memoria global
_DEPENDE_PAIS = ('authoriz', 'autoriz', 'sponsor', 'patrocin', 'visa', 'citizen', 'ciudadan',
                 'legally', 'legalmente', 'permiso de trabajo', 'work permit', 'eligible to work',
                 'salary', 'salario', 'sueldo', 'compensation', 'pretensi', 'phone', 'teléfono',
                 'telefono', 'celular', 'mobile')


def _pick(q, *wants):
    """Para select/radio devuelve la primera opción que contenga alguna palabra deseada."""
    opts = [o for o in (q.get('options') or []) if o and not o.lower().startswith(('select', 'selecciona'))]
    if q.get('type') in ('select', 'radio'):
        for w in wants:
            for o in opts:
                if w.lower() in o.lower():
                    return o
        return None
    return wants[0] if wants else None


def _regla(q, pais):
    l = (q.get('label') or '').lower()
    has = lambda *ws: any(w in l for w in ws)
    yes, no = ('Yes', 'Sí', 'Si'), ('No',)
    num = q.get('type') == 'number'

    for k, v in (CTX.get('respuestas') or {}).items():
        if k.lower() in l:
            return _pick(q, v) if q.get('type') in ('select', 'radio') else v

    if has('sponsor', 'patrocin'):
        fuera = has('u.s', 'united states', 'estados unidos', 'usa', 'canad')
        return _pick(q, *(no if pais == 'mx' and not fuera else yes))
    if has('authoriz', 'autoriz', 'legally', 'legalmente', 'eligible to work', 'permiso de trabajo',
           'work permit', 'derecho a trabajar', 'right to work', 'permiso para trabajar'):
        if has('u.s', 'united states', 'estados unidos', ' us ', 'usa', 'canad'):
            return _pick(q, *no)
        if pais == 'mx' or 'mexic' in l or 'méxico' in l:
            return _pick(q, *yes)
        return _pick(q, *no)
    if has('citizen', 'ciudadan', 'nacionalidad', 'nationality'):
        if 'mexic' in l or 'méxico' in l:
            return _pick(q, *yes)
        if has('u.s', 'united states', 'us citizen', 'estados unidos', 'canad'):
            return _pick(q, *no)
        return _pick(q, 'Mexic') if q.get('type') in ('select', 'radio') else 'Mexican'
    if has('visa', 'pasaporte', 'passport', 'licencia de conducir', "driver's license", 'driver license'):
        return None   # depende de tus documentos: lo contestas tú
    if has('master', 'maestr', 'phd', 'doctor', 'mba'):
        return _pick(q, *no)
    if has('bachelor', 'licenciatura', 'degree', 'título', 'titulo', 'grado'):
        return _pick(q, *yes)
    if has('relocat', 'reubic', 'commut', 'traslad', 'mudarte', 'desplaz', 'on-site', 'onsite',
           'presencial', 'hybrid', 'híbrid', 'travel', 'viaj', 'shift', 'turno', 'weekend',
           'fines de semana', 'overtime', 'horas extra', 'background check', 'antecedentes',
           'drug', 'antidoping', 'examen médico'):
        return _pick(q, *yes)
    if has('gender', 'género', 'genero', 'race', 'ethnic', 'raza', 'veteran', 'disabilit',
           'discapacidad', 'sexual orientation', 'pronoun'):
        return _pick(q, 'decline', 'prefer not', 'prefiero no', "don't wish", 'not wish',
                     'I am not a protected', 'No, I')
    if has('english', 'inglés', 'ingles'):
        if q.get('type') in ('select', 'radio'):
            return _pick(q, 'Professional', 'Profesional', 'Conversational', 'Upper', 'Intermedi',
                         'Advanced', 'Avanzado', 'B2', 'Yes', 'Sí')
        return '0' if num else 'B2 – professional working proficiency'
    if has('spanish', 'español', 'espanol'):
        return _pick(q, 'Native', 'Nativo', 'Bilingual', 'Bilingüe', 'Fluent', 'Yes', 'Sí')
    if has('salary', 'salario', 'sueldo', 'compensation', 'pretensi', 'expectativa econ'):
        v = CTX.get('salario') or {'mx': '30000', 'us': '70000', 'ca': '70000'}.get(pais, '2500')
        return re.sub(r'\D', '', v) if num else v
    if has('notice', 'start date', 'available to start', 'disponibilidad', 'incorpor', 'cuándo podrías'):
        return '0' if num else ('Inmediata' if CTX.get('idioma') == 'es' else 'Immediately')
    if has('year', 'años', 'anos'):
        strong = ('python', 'c++', 'swift', 'cad', 'solidworks', 'fusion')
        n = '2' if has(*strong) else '1'
        if q.get('type') in ('select', 'radio'):
            return _pick(q, n, '1', 'Less', 'Menos')
        return n
    if has('linkedin'):
        return 'https://www.' + LINKEDIN
    if has('website', 'portfolio', 'portafolio', 'sitio web', 'github'):
        return 'https://' + WEB
    if has('phone', 'teléfono', 'telefono', 'celular', 'mobile'):
        return TEL_MX if pais == 'mx' else TEL_US
    if has('city', 'ciudad', 'location', 'ubicación', 'ubicacion', 'where do you live', 'dónde vives'):
        return CIUDAD['en']
    if has('how did you hear', 'cómo te enteraste', 'como te enteraste', 'how did you find', 'source'):
        return _pick(q, 'LinkedIn')
    if has('plc', 'siemens', 'tia portal', 'hmi', 'robot', 'python', 'c++', 'opencv', 'vision',
           'solidworks', 'autocad', 'cad', 'arduino', 'raspberry', 'embedded', 'embebid', 'automat',
           'controls', 'control', 'cnc', 'matlab', 'linux', 'git') and q.get('type') in ('select', 'radio',
                                                                                        'checkbox'):
        return 'true' if q.get('type') == 'checkbox' else _pick(q, *yes)
    return None


def responder(questions, job, profile, base_cv):
    """Sustituye a la IA de app.py: reglas + respuestas a la medida. Lo que no sabe lo deja
    vacío para que tú lo completes en la ventana."""
    pais = CTX.get('pais', 'otro')
    out = []
    for q in questions:
        a = _regla(q, pais)
        if a not in (None, ''):
            out.append({'key': q['key'], 'answer': a, 'confidence': 'high', 'sensitive': False})
    return {'answers': out}


app.answer_application_questions = responder


# --------------------------------------------------------------------------- #
#  Comandos                                                                    #
# --------------------------------------------------------------------------- #
def cmd_buscar(args):
    grupos = [a for a in args if a in BUSQUEDAS or a == 'todo'] or ['mx']
    if 'todo' in grupos:
        grupos = list(BUSQUEDAS)
    n = int(args[args.index('--n') + 1]) if '--n' in args else 6
    queries = []
    for g in grupos:
        for kw, loc in BUSQUEDAS[g]:
            queries.append({'keywords': kw, 'location': loc,
                            'extra': {**ENTRY, **(REMOTE if g == 'remoto' else {})}})
    print(f'🔎 {len(queries)} búsquedas en LinkedIn ({", ".join(grupos)}), hasta {n} ofertas c/u...')
    app.WORKER.call('ensure_browser', timeout=60)
    if not app.WORKER.call('is_logged_in', timeout=20):
        app.WORKER.call('open_login', timeout=60)
        print('⚠️  Inicia sesión en LinkedIn en la ventana de Chrome y vuelve a correr este comando.')
        return
    jobs = app.WORKER.call('search_batch', timeout=60 * 60, queries=queries, per_query_limit=n)
    print(f'✅ {len(jobs)} ofertas guardadas. Usa `python3 postular.py lista` para verlas.')


def _ya_postulado():
    with db._conn() as c:
        return {r[0] for r in c.execute('SELECT job_id FROM applications')}


def cmd_lista(args):
    hechas = _ya_postulado()
    with db._conn() as c:
        rows = c.execute("SELECT id,title,company,location,query FROM jobs WHERE status!='passed' "
                         "ORDER BY created_at DESC").fetchall()
    rows = [r for r in rows if r[0] not in hechas]
    for r in rows:
        print(f'{r[0]:<16} {r[1][:55]:<55} | {r[2][:28]:<28} | {r[3]}')
    print(f'\n{len(rows)} ofertas sin postular.')


def _mantener_abierto():
    print('\n👀 Revisa cada pestaña y pulsa «Enviar solicitud» en las que te convenzan.'
          '\n   Cierra Chrome cuando termines (o Ctrl+C aquí).')
    try:
        while True:
            time.sleep(10)
            try:
                st = app.WORKER.call('status', timeout=120) or {}
            except RuntimeError:
                continue   # navegador ocupado: seguimos esperando
            if not st.get('launched'):
                break
    except KeyboardInterrupt:
        pass
    print('👋 Listo.')


def cmd_abrir(args):
    os.makedirs(HECHAS, exist_ok=True)
    archivos = sorted(f for f in os.listdir(COLA) if f.endswith('.json'))
    if args:
        archivos = [a + '.json' for a in args if a + '.json' in archivos]   # respeta el orden dado
    if not archivos:
        print('La cola está vacía (cola/*.json). Pídele a Claude que prepare ofertas.')
        return
    app.WORKER.call('ensure_browser', timeout=60)
    if not app.WORKER.call('is_logged_in', timeout=20):
        app.WORKER.call('open_login', timeout=60)
        print('⚠️  Inicia sesión en LinkedIn en la ventana de Chrome y vuelve a correr este comando.')
        return
    memoria = {k: v for k, v in db.get_answer_memory().items()
               if not any(w in k for w in _DEPENDE_PAIS)}
    resumen = []
    try:
        for f in archivos:
            try:
                linea, ok = _preparar(f, memoria)
            except Exception as e:
                linea, ok = f'🔴 {f[:-5]} — {e}', False
                _esperar_worker()
            if ok:   # las que fallan se quedan en cola/ para reintentar
                os.replace(os.path.join(COLA, f), os.path.join(HECHAS, f))
            resumen.append(linea)
            print('  ', linea)
        print('\n' + '\n'.join(resumen))
        print(f'\nPDFs en: {OUT}/<id>/')
    finally:
        _mantener_abierto()


def _esperar_worker():
    """Si una operación se colgó, espera a que el navegador termine antes de seguir."""
    try:
        app.WORKER.call('status', timeout=900)
    except Exception:
        pass


def _preparar(f, memoria):
    d = json.load(open(os.path.join(COLA, f), encoding='utf-8'))
    jid = d['job_id']
    job = db.get_job(jid)
    if not job:
        return f'✗ {jid}: no está en la base de datos', False
    pais = d.get('pais') or _pais(job.get('location'))
    CTX.clear(); CTX.update({'pais': pais, 'respuestas': d.get('respuestas') or {},
                             'salario': d.get('salario'), 'idioma': d.get('idioma')})
    print(f'→ {job["title"]} @ {job["company"]} ({job["location"]})')
    carpeta = os.path.join(OUT, jid)
    os.makedirs(carpeta, exist_ok=True)
    cv_pdf = os.path.join(carpeta, f'CV_{SLUG}.pdf')
    app.WORKER.call('render_pdf', timeout=120, html_doc=cv_html(d, pais), out_path=cv_pdf)
    cover_pdf = None
    if d.get('carta'):
        cover_pdf = os.path.join(carpeta, f'Carta_{SLUG}.pdf' if d.get('idioma') == 'es'
                                 else f'Cover_Letter_{SLUG}.pdf')
        app.WORKER.call('render_pdf', timeout=120, html_doc=carta_html(d['carta'], d.get('idioma')),
                        out_path=cover_pdf)
    db.save_application(jid, 'prepared', resume_pdf=cv_pdf, cover_pdf=cover_pdf,
                        cover_text=d.get('carta'))
    try:
        res = app.WORKER.call('apply_job', timeout=900, jid=app._numeric_job_id(job),
                              resume_pdf=cv_pdf, cover_pdf=cover_pdf, cover_text=d.get('carta'),
                              profile=app.CONFIG.get('profile', {}), base_cv='',
                              answer_memory=memoria, is_mock=False, allow_submit=False,
                              own_tab=True, job_meta={'title': job['title'], 'company': job['company'],
                                                      'location': job['location']})
    except Exception as e:
        res = {'status': 'error', 'error': str(e)}
        _esperar_worker()
    estado = 'needs_review' if res.get('kept_open') else res.get('status', 'error')
    db.save_application(jid, estado, detail={k: res.get(k) for k in ('answers', 'log', 'error',
                                                                      'review_reason')})
    icono = '🟢' if res.get('kept_open') else '🔴'
    linea = (f'{icono} {job["title"]} @ {job["company"]} — '
             f'{res.get("review_reason") or res.get("error") or res.get("status")}')
    return linea, bool(res.get('kept_open')) or res.get('status') == 'skipped'
def main():
    if len(sys.argv) < 2 or sys.argv[1] not in ('buscar', 'lista', 'abrir'):
        print(__doc__); return
    {'buscar': cmd_buscar, 'lista': cmd_lista, 'abrir': cmd_abrir}[sys.argv[1]](sys.argv[2:])


if __name__ == '__main__':
    main()
