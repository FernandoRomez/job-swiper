"""ai.py — CV a la medida y carta de presentación para Job Studio.
Usa Gemini si ../config.json tiene gemini_key; si falla, heurística local. Nunca inventa experiencia.
"""
import copy
import glob
import json
import os
import re
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)                      # job-swiper/
COLA = os.path.join(ROOT, 'cola')
CONFIG_PATH = os.path.join(ROOT, 'config.json')


import personal  # noqa: E402  (datos en perfil.json, fuera de git)
CONTACT = personal.contacto()
EDU = personal.EDU


def load_config():
    try:
        return json.load(open(CONFIG_PATH, encoding='utf-8'))
    except Exception:
        return {}


# --------------------------------------------------------------------------- #
#  Perfil maestro                                                             #
# --------------------------------------------------------------------------- #
def _docs():
    out = []
    for f in glob.glob(os.path.join(COLA, 'hechas', '*.json')) + glob.glob(os.path.join(COLA, '*.json')):
        try:
            d = json.load(open(f, encoding='utf-8'))
            if isinstance(d, dict) and d.get('experiencia'):
                out.append(d)
        except Exception:
            pass
    return out


def _peso(d):
    # "completitud": nº de bullets + competencias + proyectos
    return (sum(len(e.get('bullets', [])) for e in d.get('experiencia', []))
            + sum(len(p.get('bullets', [])) for p in d.get('proyectos', []))
            + len(d.get('competencias', [])) + len(json.dumps(d)) / 2000)


def build_base_doc(lang=None):
    """DOC base veraz: el ejemplo más completo (del idioma pedido si existe) + educación/certs/idiomas."""
    docs = _docs()
    if lang:
        same = [d for d in docs if d.get('idioma') == lang]
        docs = same or docs
    base = copy.deepcopy(max(docs, key=_peso)) if docs else {
        'idioma': lang or 'es', 'titular': 'Ingeniero en Mecatrónica', 'perfil': '',
        'competencias': [], 'experiencia': [], 'proyectos': []}
    lang = lang or base.get('idioma', 'es')
    base['idioma'] = lang
    e = EDU.get(lang) or EDU.get('es') or {}
    if e:
        base['educacion'] = [{'titulo': e.get('titulo', ''), 'escuela': e.get('escuela', ''),
                              'fechas': e.get('fechas', '')}]
        base['certs'] = list(e.get('certs', []))
        base['idiomas'] = e.get('idiomas', '')
    base.setdefault('ubicacion', personal.CIUDAD['es'] if lang == 'es' else personal.CIUDAD['en'])
    for k in ('job_id', 'carta', 'respuestas', 'salario'):
        base.pop(k, None)
    base.update({'carta': '', 'respuestas': {}, 'salario': '', 'plantilla': base.get('plantilla', '')})
    return base


# --------------------------------------------------------------------------- #
#  Heurística                                                                 #
# --------------------------------------------------------------------------- #
LATAM = ('méxico', 'mexico', 'cdmx', 'monterrey', 'querétaro', 'queretaro', 'guadalajara', 'puebla',
         'nuevo león', 'coahuila', 'guanajuato', 'chihuahua', 'juárez', 'tijuana', 'colombia', 'argentina',
         'chile', 'perú', 'peru', 'españa', 'spain', 'latam', 'latin america', 'bogotá', 'santiago')
ES_WORDS = (' el ', ' la ', ' de ', ' para ', ' con ', ' experiencia ', ' requisitos ', ' empresa ')


def detect_lang(job):
    desc = ' ' + (job.get('description') or '').lower() + ' '
    es = sum(desc.count(w) for w in ES_WORDS)
    en = sum(desc.count(w) for w in (' the ', ' and ', ' with ', ' for ', ' experience ', ' you '))
    if desc.strip():
        return 'es' if es > en else 'en'
    loc = (job.get('location') or '').lower()
    return 'es' if any(k in loc for k in LATAM) else 'en'


def _tokens(s):
    return set(w for w in re.findall(r'[a-záéíóúñü0-9+#]{3,}', (s or '').lower()))


STOP = _tokens('the and with for you are our this that will from your have para con los las del una que por '
               'experience experiencia trabajo work team equipo')


def _overlap(text, jtok):
    return len((_tokens(text) - STOP) & jtok)


def matching_skills(job, doc, n=4):
    """Habilidades del DOC que aparecen en la oferta (para la carta)."""
    desc = ((job.get('title') or '') + ' ' + (job.get('description') or '')).lower()
    skills = []
    for _, items in doc.get('competencias', []):
        for s in re.split(r'[,;·]', items):
            s = s.strip()
            core = re.sub(r'\(.*?\)', '', s).strip()
            if core and len(core) > 1 and core.lower().split()[0] in desc and s not in skills:
                skills.append(core)
    return skills[:n]


def heuristic_tailor(job, base):
    lang = detect_lang(job)
    doc = copy.deepcopy(base if base.get('idioma') == lang else build_base_doc(lang))
    jtok = _tokens((job.get('title') or '') + ' ' + (job.get('description') or '')) - STOP
    # reordena competencias, bullets y proyectos por coincidencia (solo orden, no contenido)
    doc['competencias'] = sorted(doc.get('competencias', []), key=lambda c: -_overlap(' '.join(c), jtok))
    for e in doc.get('experiencia', []):
        e['bullets'] = sorted(e.get('bullets', []), key=lambda b: -_overlap(b, jtok))
    for p in doc.get('proyectos', []):
        p['bullets'] = sorted(p.get('bullets', []), key=lambda b: -_overlap(b, jtok))
    doc['proyectos'] = sorted(doc.get('proyectos', []),
                              key=lambda p: -_overlap(p.get('titulo', '') + ' ' + ' '.join(p.get('bullets', [])), jtok))
    # titular: carrera real + foco del puesto
    title = (job.get('title') or '').strip()
    pre = 'Ingeniero en Mecatrónica' if lang == 'es' else 'Mechatronics Engineer'
    top = [c[0] for c in doc['competencias'][:2]]
    doc['titular'] = f"{pre} | {title}" if title else f"{pre} | {' · '.join(top)}"
    doc['job_id'] = job.get('id', '')
    return doc


def heuristic_cover(job, doc):
    lang = doc.get('idioma') or detect_lang(job)
    comp = job.get('company') or ('su empresa' if lang == 'es' else 'your company')
    role = job.get('title') or ('el puesto' if lang == 'es' else 'the role')
    skills = matching_skills(job, doc) or [c[1].split(',')[0] for c in doc.get('competencias', [])[:3]]
    sk = ', '.join(skills[:-1]) + (f' y {skills[-1]}' if lang == 'es' else f' and {skills[-1]}') if len(skills) > 1 else (skills[0] if skills else '')
    exp = (doc.get('experiencia') or [{}])[0]
    b1 = (exp.get('bullets') or [''])[0].rstrip('.')
    proy = (doc.get('proyectos') or [{}])[0]
    pb = (proy.get('bullets') or [''])[0].rstrip('.')
    if lang == 'es':
        paras = [
            f"Estimado equipo de reclutamiento de {comp}:",
            f"Me dirijo a ustedes para postularme al puesto de {role}. Soy Ingeniero en Mecatrónica por la "
            f"{personal.ESCUELA_CORTA} y mi experiencia práctica en {sk} coincide directamente con lo que buscan.",
            f"Como {exp.get('puesto', 'practicante')} en {exp.get('empresa', '')}, {b1[0].lower() + b1[1:] if b1 else 'participé en proyectos de ingeniería de principio a fin'}. "
            f"Además, en el proyecto «{proy.get('titulo', '')}» {pb[0].lower() + pb[1:] if pb else 'integré mecánica, electrónica y software'}.",
            f"Me motiva aportar a {comp} con una forma de trabajar metódica, aprendizaje rápido y gusto por "
            f"llevar sistemas del diseño a la puesta en marcha. Quedo a su disposición para una entrevista.",
            f"Atentamente,\n{CONTACT['nombre']}",
        ]
    else:
        paras = [
            f"Dear {comp} Hiring Team,",
            f"I am writing to apply for the {role} position. I am a Mechatronics Engineer from "
            f"{personal.ESCUELA_CORTA}, and my hands-on experience with {sk} maps directly to what you are looking for.",
            f"As {exp.get('puesto', 'an engineering intern')} at {exp.get('empresa', '')}, {b1[0].lower() + b1[1:] if b1 else 'I worked on engineering projects end to end'}. "
            f"In my project \"{proy.get('titulo', '')}\", {pb[0].lower() + pb[1:] if pb else 'I integrated mechanics, electronics and software'}.",
            f"I would be glad to bring a methodical, fast-learning approach to {comp} and help take systems from "
            f"design to commissioning. Thank you for your time; I look forward to talking with you.",
            f"Sincerely,\n{CONTACT['nombre']}",
        ]
    return '\n\n'.join(paras)


# --------------------------------------------------------------------------- #
#  Gemini                                                                     #
# --------------------------------------------------------------------------- #
def _gemini(prompt, want_json=True):
    cfg = load_config()
    key = cfg.get('gemini_key')
    if not key:
        return None
    models = [m for m in (cfg.get('gemini_model'), 'gemini-2.5-flash') if m]
    for model in dict.fromkeys(models):
        try:
            url = f'https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}'
            body = {'contents': [{'parts': [{'text': prompt}]}],
                    'generationConfig': {'temperature': 0.3}}
            if want_json:
                body['generationConfig']['responseMimeType'] = 'application/json'
            req = urllib.request.Request(url, json.dumps(body).encode(), {'Content-Type': 'application/json'})
            with urllib.request.urlopen(req, timeout=90) as r:
                data = json.load(r)
            text = data['candidates'][0]['content']['parts'][0]['text']
            return json.loads(text) if want_json else text.strip()
        except Exception:
            continue
    return None


RULES = ("REGLAS ESTRICTAS: nunca inventes experiencia, empresas, fechas, métricas, certificaciones ni "
         "habilidades que no estén en el DOC base. Solo puedes reordenar y reformular. ")


def _job_txt(job):
    return (f"Puesto: {job.get('title')}\nEmpresa: {job.get('company')}\nUbicación: {job.get('location')}\n"
            f"Descripción:\n{(job.get('description') or '')[:6000]}")


def tailor_cv(job, base=None):
    lang = detect_lang(job)
    base = base if base and base.get('idioma') == lang else build_base_doc(lang)
    prompt = (RULES + f"Adapta este CV (formato DOC JSON) a la oferta. Idioma de salida: {lang}. "
              "Reordena competencias y bullets por relevancia, ajusta 'titular' y 'perfil' al puesto. "
              "Devuelve SOLO el DOC JSON con exactamente las mismas claves.\n\nOFERTA:\n" + _job_txt(job)
              + "\n\nDOC BASE:\n" + json.dumps(base, ensure_ascii=False))
    out = _gemini(prompt)
    if isinstance(out, dict) and out.get('experiencia'):
        # se conservan del base los campos fijos (veracidad)
        for k in ('educacion', 'certs', 'idiomas'):
            out[k] = base.get(k, out.get(k))
        out['idioma'], out['job_id'] = lang, job.get('id', '')
        out.setdefault('carta', ''); out.setdefault('respuestas', {}); out.setdefault('salario', '')
        out['_fuente'] = 'gemini'
        return out
    doc = heuristic_tailor(job, base)
    doc['_fuente'] = 'heuristica'
    return doc


def cover_letter(job, doc=None):
    doc = doc or tailor_cv(job)
    lang = doc.get('idioma') or detect_lang(job)
    prompt = (RULES + f"Escribe una carta de presentación en idioma '{lang}' (3-4 párrafos, saludo y firma "
              f"de {CONTACT['nombre']}), concreta, mencionando la empresa y el puesto y habilidades reales del "
              "CV que coincidan con la oferta. Devuelve solo el texto, párrafos separados por línea en blanco."
              "\n\nOFERTA:\n" + _job_txt(job) + "\n\nCV:\n" + json.dumps(doc, ensure_ascii=False))
    out = _gemini(prompt, want_json=False)
    if isinstance(out, str) and len(out) > 200:
        return out
    return heuristic_cover(job, doc)
