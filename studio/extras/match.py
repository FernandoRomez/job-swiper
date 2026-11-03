"""Módulo extra 'match' para Job Studio.

Rutas:
  GET /api/x/match/job/<id>          -> análisis ATS + red flags + TN + salario
  GET /api/x/match/radar?limit=40    -> ranking de empleos no aplicados
Solo lee la BD (jobs / applications). Sin dependencias externas.
"""
import glob
import json
import math
import os
import re
import sqlite3
import sys
import time
import unicodedata

HERE = os.path.dirname(os.path.abspath(__file__))
STUDIO = os.path.dirname(HERE)
ROOT = os.path.dirname(STUDIO)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
try:
    import db  # noqa
except Exception:  # pragma: no cover
    db = None


def _db_path():
    return os.environ.get('JOBSWIPER_DB') or (db.DB_PATH if db else os.path.join(ROOT, 'jobswiper.db'))


def _conn():
    c = sqlite3.connect(_db_path(), timeout=10)
    c.row_factory = sqlite3.Row
    return c


def norm(s):
    s = unicodedata.normalize('NFKD', (s or '').lower())
    return ''.join(ch for ch in s if not unicodedata.combining(ch))


# ---------------------------------------------------------------- diccionario
# canon -> (categoría, [sinónimos es/en normalizados, sin acentos])
SKILLS = {
    'PLC': ('Controles', ['plc', 'plcs', 'controlador logico programable']),
    'Siemens TIA Portal': ('Controles', ['tia portal', 'step 7', 's7-1200', 's7-1500', 'siemens']),
    'Allen-Bradley / Rockwell': ('Controles', ['allen-bradley', 'allen bradley', 'rockwell', 'studio 5000', 'rslogix', 'controllogix', 'compactlogix']),
    'Ladder': ('Controles', ['ladder', 'escalera']),
    'HMI': ('Controles', ['hmi', 'wincc', 'factorytalk', 'panelview']),
    'SCADA': ('Controles', ['scada', 'ignition']),
    'VFD / Variadores': ('Controles', ['vfd', 'variador', 'variadores', 'variable frequency drive', 'drives']),
    'Redes industriales': ('Controles', ['profinet', 'ethernet/ip', 'ethernet ip', 'modbus', 'profibus', 'devicenet', 'opc ua', 'opc-ua', 'redes industriales', 'industrial networks']),
    'Diagramas eléctricos': ('Controles', ['diagramas electricos', 'electrical schematics', 'schematics', 'esquematicos', 'wiring diagrams']),
    'Control de procesos / PID': ('Controles', ['pid', 'control loop', 'lazo de control', 'control de procesos', 'process control']),
    'Robótica industrial': ('Robótica', ['robot', 'robots', 'robotic', 'robotics', 'robotica', 'robotico', 'robotica industrial']),
    'FANUC': ('Robótica', ['fanuc']),
    'ABB': ('Robótica', ['abb', 'robotstudio']),
    'KUKA': ('Robótica', ['kuka']),
    'Yaskawa / Motoman': ('Robótica', ['yaskawa', 'motoman']),
    'Cobots / UR': ('Robótica', ['cobot', 'cobots', 'universal robots']),
    'ROS': ('Robótica', ['ros', 'ros2', 'ros 2']),
    'Visión artificial': ('Robótica', ['machine vision', 'computer vision', 'vision artificial', 'opencv', 'cognex', 'keyence', 'vision systems']),
    'Servomotores / motion': ('Robótica', ['servo', 'servomotor', 'servomotores', 'motion control', 'control de movimiento']),
    'C++': ('Programación', ['c++', 'cpp']),
    'C': ('Programación', [' c ', 'embedded c', 'c language', 'lenguaje c']),
    'Python': ('Programación', ['python']),
    'C#': ('Programación', ['c#', '.net']),
    'MATLAB / Simulink': ('Programación', ['matlab', 'simulink']),
    'LabVIEW': ('Programación', ['labview']),
    'Linux': ('Programación', ['linux', 'ubuntu']),
    'Git': ('Programación', ['git', 'github', 'gitlab']),
    'SQL': ('Programación', ['sql']),
    'Microcontroladores': ('Electrónica', ['microcontroller', 'microcontrolador', 'microcontroladores', 'mcu', 'stm32', 'pic', 'arm cortex']),
    'ESP32 / Arduino': ('Electrónica', ['esp32', 'arduino']),
    'Raspberry Pi': ('Electrónica', ['raspberry']),
    'Protocolos UART/SPI/I2C/CAN': ('Electrónica', ['uart', 'spi', 'i2c', 'i²c', 'can bus', 'canbus', 'rs-485', 'rs485']),
    'Sistemas embebidos': ('Electrónica', ['embedded', 'embebido', 'embebidos', 'firmware', 'rtos']),
    'Sensores': ('Electrónica', ['sensor', 'sensors', 'sensores', 'instrumentation', 'instrumentacion']),
    'Diseño de PCB': ('Electrónica', ['pcb', 'altium', 'kicad', 'eagle']),
    'Electrónica de potencia': ('Electrónica', ['power electronics', 'electronica de potencia']),
    'SolidWorks': ('Diseño', ['solidworks']),
    'AutoCAD': ('Diseño', ['autocad']),
    'Fusion 360': ('Diseño', ['fusion 360', 'fusion360']),
    'CATIA / NX / Creo': ('Diseño', ['catia', 'siemens nx', 'creo', 'inventor']),
    'GD&T': ('Diseño', ['gd&t', 'gdt', 'tolerancias geometricas']),
    'CAD': ('Diseño', ['cad', '3d modeling', 'modelado 3d']),
    'CNC': ('Manufactura', ['cnc', 'haas', 'cam', 'mecanizado', 'machining']),
    'Lean Manufacturing': ('Manufactura', ['lean', 'kaizen', '5s', 'smed', 'value stream']),
    'Six Sigma': ('Manufactura', ['six sigma', 'seis sigma', 'dmaic', 'green belt']),
    'PFMEA / FMEA': ('Manufactura', ['fmea', 'pfmea', 'amef']),
    'Control Plan / PPAP / APQP': ('Manufactura', ['ppap', 'apqp', 'control plan', 'plan de control']),
    'Análisis de causa raíz': ('Manufactura', ['root cause', 'causa raiz', '8d', 'a3', 'ishikawa', '5 why', '5 porques']),
    'Mantenimiento / TPM': ('Manufactura', ['mantenimiento', 'maintenance', 'tpm', 'preventive maintenance', 'predictivo']),
    'Ingeniería de procesos': ('Manufactura', ['process engineer', 'ingeniero de procesos', 'process improvement', 'mejora de procesos', 'mejora continua', 'continuous improvement']),
    'Automatización': ('Manufactura', ['automation', 'automatizacion', 'automated', 'automatizado']),
    'Neumática / Hidráulica': ('Manufactura', ['pneumatic', 'neumatica', 'hydraulic', 'hidraulica']),
    'ISO 9001 / IATF 16949': ('Manufactura', ['iso 9001', 'iatf', '16949', 'iso 13485']),
    'Commissioning / arranque': ('Manufactura', ['commissioning', 'puesta en marcha', 'start-up', 'startup', 'arranque']),
    'Validación / pruebas': ('Manufactura', ['validation', 'validacion', 'testing', 'pruebas', 'verification']),
    'SAP / ERP': ('Manufactura', ['sap', 'erp', 'mes']),
    'Inglés': ('Idiomas', ['ingles', 'english', 'bilingue', 'bilingual']),
}
_SHORT = {'c', 'pic', 'cam', 'mes', 'sap', 'abb', 'ros', 'pid', 'cad', 'spi', 'a3', '8d', 'mcu', 'git', 'sql', 'erp', 'hmi', 'plc', 'gdt', 'lean', 'robot'}

STOP = set(norm(w) for w in """the and for with you are our will this that from your have has not all can job work team experience
role years year including such other more must able ability skills strong within new who what about their they them may also
de la el en y los las del con para por un una que se su sus es al lo como o a e u nuestra nuestro nuestros más trabajo empresa
ser experiencia años conocimiento conocimientos manejo requisitos ofrecemos area etc be or an to of in on as at is it we us
""".split())


def _find(text_n, syn):
    s = syn.strip()
    if syn == ' c ':
        return len(re.findall(r'(?<![\w#+/.-])c(?![\w#+/-])', text_n))
    if len(s) <= 4 or s in _SHORT:
        return len(re.findall(r'(?<![a-z0-9])' + re.escape(s) + r'(?![a-z0-9])', text_n))
    return text_n.count(s)


def extract_skills(text):
    t = norm(text)
    out = {}
    for canon, (cat, syns) in SKILLS.items():
        n = sum(_find(t, s) for s in syns)
        if n:
            out[canon] = {'skill': canon, 'cat': cat, 'count': n}
    return out


def top_terms(text, k=15):
    words = re.findall(r'[a-z][a-z+#\-]{2,}', norm(text))
    freq = {}
    for w in words:
        if w in STOP:
            continue
        freq[w] = freq.get(w, 0) + 1
    return sorted(freq.items(), key=lambda x: -x[1])[:k]


# ---------------------------------------------------------------- perfil
def _flatten(o):
    if isinstance(o, str):
        return o + '\n'
    if isinstance(o, dict):
        return ''.join(_flatten(v) for k, v in o.items() if k not in ('carta', 'respuestas', 'job_id', 'plantilla', 'salario'))
    if isinstance(o, list):
        return ''.join(_flatten(v) for v in o)
    return ''


_base_cache = {}


def base_doc():
    """DOC más completo en cola/hechas, o cola/."""
    files = glob.glob(os.path.join(ROOT, 'cola', 'hechas', '*.json')) or glob.glob(os.path.join(ROOT, 'cola', '*.json'))
    key = tuple(sorted((f, os.path.getmtime(f)) for f in files))
    if _base_cache.get('k') == key:
        return _base_cache['v']
    best, best_n = None, -1
    for f in files:
        try:
            d = json.load(open(f, encoding='utf-8'))
        except Exception:
            continue
        n = len(_flatten(d))
        if n > best_n:
            best, best_n = d, n
    _base_cache.update(k=key, v=best)
    return best


def profile_text(job_id=None):
    """Texto del perfil: DOC a la medida si existe, si no DOC base + base_cv de config."""
    src = 'base'
    parts = []
    p = os.path.join(STUDIO, 'docs', f'{job_id}.json') if job_id else None
    if p and os.path.exists(p):
        try:
            parts.append(_flatten(json.load(open(p, encoding='utf-8'))))
            src = 'doc'
        except Exception:
            pass
    if not parts:
        b = base_doc()
        if b:
            parts.append(_flatten(b))
        try:
            cfg = json.load(open(os.path.join(ROOT, 'config.json'), encoding='utf-8'))
            parts.append(cfg.get('base_cv') or '')
        except Exception:
            pass
    return '\n'.join(parts), src


_prof_cache = {}


def profile_skills(job_id=None):
    txt, src = profile_text(job_id)
    if src == 'base':
        if 'base' not in _prof_cache or _prof_cache['base'][0] != len(txt):
            _prof_cache['base'] = (len(txt), extract_skills(txt))
        return _prof_cache['base'][1], src
    return extract_skills(txt), src


# ---------------------------------------------------------------- país / flags
US_STATES = ['estados unidos', 'united states', 'usa', ', us', 'carolina del norte', 'north carolina', 'texas', 'california',
             'michigan', 'ohio', 'arizona', 'kentucky', 'tennessee', 'georgia', 'florida', 'indiana', 'illinois', 'alabama',
             'south carolina', 'carolina del sur', 'new york', 'nueva york', 'pennsylvania', 'pensilvania', 'virginia',
             'wisconsin', 'minnesota', 'colorado', 'washington', 'oregon', 'massachusetts', 'raleigh', 'durham']
US_RE = re.compile(r',\s*(al|az|ca|co|fl|ga|il|in|ky|mi|mn|nc|ny|oh|pa|sc|tn|tx|va|wa|wi)\b')
MX_KEYS = ['mexico', 'puebla', 'cdmx', 'ciudad de mexico', 'jalisco', 'guadalajara', 'monterrey', 'nuevo leon', 'queretaro',
           'coahuila', 'saltillo', 'chihuahua', 'juarez', 'tijuana', 'baja california', 'sonora', 'hermosillo', 'guanajuato',
           'leon', 'celaya', 'silao', 'aguascalientes', 'san luis potosi', 'tlaxcala', 'hidalgo', 'estado de mexico',
           'toluca', 'veracruz', 'tamaulipas', 'reynosa', 'matamoros', 'nuevo laredo', 'yucatan', 'merida', 'durango']


def country_of(job):
    loc = norm(job.get('location'))
    src = norm(job.get('source'))
    jid = job.get('id') or ''
    remote = any(k in loc for k in ('remote', 'remoto', 'anywhere', 'latam', 'worldwide', 'latin america')) \
        or jid.split('_')[0] in ('rok', 'rmt', 'wwr') or 'remote' in src
    if any(k in loc for k in MX_KEYS):
        return 'MX', remote
    if any(k in loc for k in US_STATES) or US_RE.search(loc):
        return 'US', remote
    if remote:
        return 'REMOTE', True
    if jid.split('_')[0] in ('occ', 'ct'):
        return 'MX', remote
    return 'OTRO', remote


FLAG_RULES = [
    ('seniority', 'alta', r'\b(senior|sr\.?|lead|principal|staff|manager|gerente|jefe|director|head of)\b', 'title'),
    ('seniority', 'alta', r'\b([5-9]|1[0-9])\s*\+?\s*(years|yrs|anos)\b', 'desc'),
    ('seniority', 'media', r'\b([3-4])\s*\+?\s*(years|yrs|anos)\b', 'desc'),
    ('ciudadanía', 'alta', r'(u\.?s\.? citizen|us citizenship|citizenship required|must be a citizen|security clearance|secret clearance|itar|us person)', 'desc'),
    ('sponsorship', 'alta', r'(not (be )?(able to )?(offer|provide) (visa )?sponsorship|no (visa )?sponsorship|without (the need for )?(visa )?sponsorship|sponsorship (is )?not (available|offered|provided)|must be (legally )?authorized to work .{0,40}without)', 'desc'),
    ('idioma', 'media', r'(german|aleman|french|frances|japanese|japones|mandarin|chinese|chino|portuguese|portugues|korean|coreano|italian|italiano)\s*(\(|-|:)?\s*(required|requerido|indispensable|fluent|native|nativo|avanzado|c1|c2|b2)', 'desc'),
    ('idioma', 'baja', r'(ingles|english)\s*(\(|-|:)?\s*(avanzado|advanced|fluent|fluido|nativo|native|c1|c2|100%)', 'desc'),
    ('reubicación', 'baja', r'(relocat|reubica|cambio de residencia|disponibilidad para cambiar)', 'desc'),
    ('viajes', 'baja', r'(travel (up to )?\d{2}\s*%|disponibilidad (para|de) viajar|viajes? (frecuentes|nacionales|internacionales))', 'desc'),
]
FLAG_MSG = {
    'seniority': 'Pide perfil senior/experiencia alta',
    'ciudadanía': 'Requiere ciudadanía/clearance de EE.UU.',
    'sponsorship': 'No ofrece sponsorship (TN no necesita sponsor, pero ojo con el filtro)',
    'idioma': 'Requisito de idioma',
    'reubicación': 'Implica reubicación',
    'viajes': 'Viajes frecuentes',
}


def red_flags(job):
    title = norm(job.get('title'))
    desc = norm(job.get('description'))
    sen = norm(job.get('seniority'))
    flags = []
    seen = set()
    for kind, sev, rx, where in FLAG_RULES:
        t = title if where == 'title' else desc
        m = re.search(rx, t)
        if not m:
            continue
        if (kind, sev) in seen or (kind == 'seniority' and kind in {k for k, _ in seen} and sev != 'alta'):
            continue
        if kind == 'idioma' and sev == 'baja' and not False:
            sev = 'baja'
        seen.add((kind, sev))
        i = m.start()
        snippet = (job.get('title') if where == 'title' else (job.get('description') or '')[max(0, i - 50): i + 90])
        flags.append({'kind': kind, 'severity': sev, 'msg': FLAG_MSG[kind], 'evidence': (snippet or '').replace('\n', ' ').strip()})
    if any(k in sen for k in ('senior', 'director', 'executive', 'ejecutivo', 'mid-senior', 'intermedio superior')) and not any(f['kind'] == 'seniority' for f in flags):
        flags.append({'kind': 'seniority', 'severity': 'media', 'msg': 'LinkedIn la marca como ' + (job.get('seniority') or ''), 'evidence': ''})
    return flags


# ---------------------------------------------------------------- TN
TN_ENG_TITLE = re.compile(r'\b(engineer|engineering|ingeniero|ingeniera)\b')
TN_RELATED = ['mechatronic', 'mechanical', 'electrical', 'electronic', 'controls', 'control', 'automation', 'robotic', 'robotics',
              'manufacturing', 'process', 'industrial', 'test', 'validation', 'quality', 'embedded', 'hardware', 'systems',
              'firmware', 'design', 'production', 'field service', 'commissioning', 'reliability', 'maintenance', 'plc']
TN_NOT = ['technician', 'tecnico', 'operator', 'operador', 'sales', 'ventas', 'assembler', 'machinist', 'electrician', 'installer']


def tn_verdict(job, country):
    if country != 'US':
        return None
    t = norm(job.get('title'))
    if any(k in t for k in TN_NOT) and not TN_ENG_TITLE.search(t):
        return {'verdict': 'no', 'label': 'Poco probable', 'why': 'Título de técnico/operador: no encaja en la categoría TN "Engineer".'}
    if TN_ENG_TITLE.search(t):
        rel = [k for k in TN_RELATED if k in t]
        if rel or re.search(r'\b(engineer i|engineer 1|associate engineer|junior engineer|entry)', t):
            return {'verdict': 'likely', 'label': 'Probable TN Engineer',
                    'why': 'El título dice "Engineer" y es afín a Mecatrónica/Mecánica/Eléctrica (' + ', '.join(rel[:3] or ['general']) + '). Llevar título, cédula y carta de oferta con funciones de ingeniería.'}
        if 'software' in t or 'data' in t:
            return {'verdict': 'maybe', 'label': 'Dudoso', 'why': 'Ingeniería de software/datos: el título de Mecatrónica podría cuestionarse en frontera.'}
        return {'verdict': 'maybe', 'label': 'Posible', 'why': 'Dice "Engineer" pero la disciplina no es claramente afín; la carta de oferta debe describir funciones de ingeniería mecatrónica.'}
    return {'verdict': 'no', 'label': 'Poco probable', 'why': 'El título no es "Engineer"; TN Engineer requiere un puesto de ingeniería profesional. Pedir que el título de la oferta sea "Engineer".'}


# ---------------------------------------------------------------- salario
SAL = {  # (min, max) mensual MXN bruto / anual USD; estimación entry-level
    'MX': {'default': (18000, 28000), 'automation': (22000, 35000), 'robotics': (22000, 36000), 'embedded': (22000, 38000),
           'process': (20000, 32000), 'quality': (18000, 28000), 'maintenance': (17000, 27000), 'design': (18000, 30000),
           'software': (25000, 45000), 'technician': (11000, 17000), 'intern': (6000, 12000)},
    'US': {'default': (65000, 80000), 'automation': (70000, 88000), 'robotics': (72000, 92000), 'embedded': (78000, 100000),
           'process': (68000, 85000), 'quality': (63000, 78000), 'maintenance': (60000, 78000), 'design': (65000, 82000),
           'software': (85000, 115000), 'technician': (45000, 60000), 'intern': (40000, 55000)},
    'REMOTE': {'default': (1500, 3000), 'automation': (1800, 3500), 'robotics': (2000, 3800), 'embedded': (2200, 4000),
               'process': (1500, 3000), 'quality': (1400, 2800), 'maintenance': (1200, 2400), 'design': (1600, 3200),
               'software': (2500, 5000), 'technician': (900, 1800), 'intern': (500, 1000)},
}
ROLE_KEYS = [('intern', ['intern', 'becario', 'practicante', 'trainee', 'pasante']),
             ('technician', ['technician', 'tecnico']),
             ('embedded', ['embedded', 'embebid', 'firmware']),
             ('robotics', ['robot']),
             ('automation', ['automation', 'automatiz', 'controls', 'control', 'plc']),
             ('software', ['software', 'developer', 'desarrollador']),
             ('process', ['process', 'proceso', 'manufactur', 'production', 'produccion']),
             ('quality', ['quality', 'calidad']),
             ('maintenance', ['maintenance', 'mantenimiento']),
             ('design', ['design', 'diseno', 'mechanical', 'mecanic'])]


def salary_estimate(job, country):
    t = norm(job.get('title'))
    role = 'default'
    for r, keys in ROLE_KEYS:
        if any(k in t for k in keys):
            role = r
            break
    table = SAL.get(country) or SAL['MX']
    lo, hi = table[role]
    unit = {'MX': 'MXN/mes bruto', 'US': 'USD/año', 'REMOTE': 'USD/mes (contractor LatAm)'}.get(country, 'MXN/mes bruto')
    return {'role': role, 'min': lo, 'max': hi, 'unit': unit, 'country': country if country in SAL else 'MX',
            'note': 'Estimación aproximada para nivel entry-level; no es dato de la vacante.'}


# ---------------------------------------------------------------- análisis
def analyze(job, prof=None, src=None):
    if prof is None:
        prof, src = profile_skills(job.get('id'))
    text = (job.get('title') or '') + '\n' + (job.get('description') or '')
    req = extract_skills(text)
    title_n = norm(job.get('title'))
    present, missing = [], []
    for canon, info in sorted(req.items(), key=lambda kv: -kv[1]['count']):
        w = 1 + min(info['count'], 4) + (2 if any(_find(title_n, s) for s in SKILLS[canon][1]) else 0)
        item = dict(info, weight=w)
        (present if canon in prof else missing).append(item)
    tot = sum(i['weight'] for i in present + missing)
    pct = round(100 * sum(i['weight'] for i in present) / tot) if tot else 0
    if len(req) < 3:  # descripción pobre: no penalizar demasiado
        pct = max(pct, 50) if present else 40
    country, remote = country_of(job)
    flags = red_flags(job)
    emph = [i['skill'] for i in present[:6]]
    sugg = []
    if emph:
        sugg.append('Pon al inicio del perfil y competencias: ' + ', '.join(emph[:4]) + '.')
    adj = [i['skill'] for i in missing if i['cat'] in {SKILLS[p][0] for p in prof}][:4]
    if adj:
        sugg.append('Te faltan (' + ', '.join(adj) + '); si tienes algo real relacionado (curso, proyecto), menciónalo con honestidad; si no, no lo inventes.')
    if 'Inglés' in req:
        sugg.append('Menciona Inglés B2 explícitamente en la sección de idiomas.')
    if country == 'US':
        sugg.append('Indica "TN Visa eligible (Mexican citizen, Mechatronics Engineer)" en el encabezado.')
    if not present:
        sugg.append('Pocas coincidencias técnicas: considera si vale la pena aplicar.')
    return {
        'job_id': job.get('id'), 'match': pct, 'profile_source': src,
        'present': present, 'missing': missing, 'top_terms': top_terms(text),
        'red_flags': flags, 'country': country, 'remote': remote,
        'tn': tn_verdict(job, country), 'salary': salary_estimate(job, 'REMOTE' if country in ('REMOTE', 'OTRO') and remote else country),
        'suggestions': sugg,
    }


def composite(job, a, now=None):
    now = now or time.time()
    age_d = max(0.0, (now - (job.get('created_at') or now)) / 86400)
    rec = math.exp(-age_d / 14)  # media vida ~10 días
    cprio = {'MX': 1.0, 'REMOTE': 0.85, 'US': 0.0, 'OTRO': 0.2}[a['country']]
    if a['country'] == 'US':
        cprio = {'likely': 0.7, 'maybe': 0.35}.get((a['tn'] or {}).get('verdict'), 0.05)
    if a['remote'] and a['country'] == 'MX':
        cprio = 1.0
    pen = sum({'alta': 18, 'media': 8, 'baja': 2}[f['severity']] for f in a['red_flags'])
    score = 0.55 * a['match'] + 25 * cprio + 20 * rec - pen
    if job.get('status') == 'liked':
        score += 5
    reasons = [f"{a['match']}% match"]
    reasons.append({'MX': 'México', 'REMOTE': 'Remoto', 'US': 'EE.UU. · ' + ((a['tn'] or {}).get('label') or ''), 'OTRO': 'Otro país'}[a['country']])
    reasons.append('hoy' if age_d < 1 else f'hace {int(age_d)} d')
    if a['present']:
        reasons.append('✓ ' + ', '.join(i['skill'] for i in a['present'][:3]))
    for f in a['red_flags']:
        if f['severity'] != 'baja':
            reasons.append('⚠ ' + f['msg'])
    return round(score, 1), reasons


def load_jobs(where='', args=()):
    with _conn() as c:
        rows = c.execute(
            "SELECT j.*, a.status AS app_status FROM jobs j LEFT JOIN applications a ON a.job_id=j.id " + where, args).fetchall()
    return [dict(r) for r in rows]


def radar(limit=40, include_passed=False, since=None, country=None):
    where = "WHERE (a.status IS NULL OR a.status NOT IN ('applied','prepared','skipped'))"
    args = []
    if not include_passed:
        where += " AND j.status != 'passed'"
    if since:
        where += " AND j.created_at > ?"
        args.append(since)
    jobs = load_jobs(where, args)
    prof, src = profile_skills(None)
    now = time.time()
    out = []
    for j in jobs:
        a = analyze(j, prof, src)
        if country and a['country'] != country:
            continue
        s, reasons = composite(j, a, now)
        out.append({'id': j['id'], 'title': j['title'], 'company': j['company'], 'location': j['location'],
                    'source': j['source'], 'link': j['link'], 'status': j['status'], 'app_status': j.get('app_status'),
                    'created_at': j['created_at'], 'score': s, 'match': a['match'], 'country': a['country'],
                    'tn': (a['tn'] or {}).get('verdict'), 'flags': len([f for f in a['red_flags'] if f['severity'] != 'baja']),
                    'reasons': reasons})
    out.sort(key=lambda x: -x['score'])
    return out[:limit]


def _q(query, k, default=None):
    v = (query or {}).get(k, default)
    if isinstance(v, list):
        v = v[0] if v else default
    return v


def handle(method, path, query, body):
    if not path.startswith('/api/x/match'):
        return None
    try:
        if method == 'GET' and path.rstrip('/') == '/api/x/match/radar':
            lim = int(_q(query, 'limit', 40) or 40)
            inc = str(_q(query, 'include_passed', '')).lower() in ('1', 'true', 'yes')
            ctry = (_q(query, 'country') or '').upper() or None
            return 200, radar(lim, inc, None, ctry)
        m = re.match(r'^/api/x/match/job/([^/]+)/?$', path)
        if method == 'GET' and m:
            jobs = load_jobs('WHERE j.id=?', (m.group(1),))
            if not jobs:
                return 404, {'error': 'empleo no encontrado'}
            return 200, analyze(jobs[0])
        return 404, {'error': 'ruta match desconocida'}
    except Exception as e:  # nunca tumbar el servidor
        return 500, {'error': f'{type(e).__name__}: {e}'}
