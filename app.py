#!/usr/bin/env python3
"""
JobSwiper — buscador de empleo estilo Tinder con IA.

Flujo:
  1. Rellenas tu perfil (CV base + preguntas) y tu API key de Google Gemini.
  2. Conectas LinkedIn: se abre un navegador REAL donde TÚ inicias sesión
     (nunca escribimos tu contraseña; la sesión queda guardada localmente).
  3. La IA sugiere los mejores títulos de puesto para tu perfil.
  4. Scrapeamos empleos de "Solicitud sencilla" (Easy Apply) desde tu sesión.
  5. Deslizas estilo Tinder. Al deslizar a la derecha, la IA genera
     Resume + CV + Cover Letter enfocados a esa oferta, con el link para aplicar.

Ejecutar:
    pip install playwright
    playwright install chromium
    python3 app.py
    # abre http://localhost:8777
"""
import base64
import html
import json
import os
import queue
import random
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import db
import latex_cv
import personal
from mcp_bridge import BRIDGE

HERE = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(HERE, 'static')
USER_DATA_DIR = os.path.join(HERE, '.linkedin-profile')  # sesión persistente de LinkedIn
PORT = int(os.environ.get('JOBSWIPER_PORT', '8777'))
MOCK = os.environ.get('JOBSWIPER_MOCK') == '1'

# IMPORTANTE: el modo demo (MOCK) usa archivos SEPARADOS para no tocar jamás tus
# datos reales (config.json / jobswiper.db). Así, limpiar pruebas nunca los borra.
CONFIG_PATH = os.path.join(HERE, 'config.mock.json' if MOCK else 'config.json')
if MOCK:
    db.DB_PATH = os.path.join(HERE, 'jobswiper.mock.db')
db.init()  # crea las tablas si no existen (idempotente)

GEMINI_URL = 'https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}'

# --------------------------------------------------------------------------- #
#  Config (persistente en config.json)                                        #
# --------------------------------------------------------------------------- #
def load_config():
    cfg = {
        # 'gemini' = API de Google (de pago/cuota) | 'mcp' = Claude por MCP (sin API key)
        'ai_provider': 'gemini',
        'gemini_key': os.environ.get('GOOGLE_API_KEY') or os.environ.get('GEMINI_API_KEY') or '',
        'gemini_model': 'gemini-3.5-flash',
        'mcp_timeout': 900,
        'base_cv': '',
        'profile': {},
    }
    if os.path.exists(CONFIG_PATH):
        try:
            cfg.update(json.load(open(CONFIG_PATH, encoding='utf-8')))
        except Exception:
            pass
    return cfg


def save_config(cfg):
    json.dump(cfg, open(CONFIG_PATH, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)


CONFIG = load_config()


# --------------------------------------------------------------------------- #
#  Proveedor de IA: 'gemini' (API REST) o 'mcp' (Claude Code / Antigravity)   #
# --------------------------------------------------------------------------- #
def ai_provider():
    """Proveedor activo. La variable de entorno JOBSWIPER_AI manda sobre config."""
    env = (os.environ.get('JOBSWIPER_AI') or '').strip().lower()
    prov = env or (CONFIG.get('ai_provider') or 'gemini').strip().lower()
    return 'mcp' if prov == 'mcp' else 'gemini'


def ai_ready():
    """¿Podemos pedirle algo a la IA ahora mismo?"""
    if MOCK:
        return True
    if ai_provider() == 'mcp':
        return True  # la tarea se encola aunque Claude aún no esté escuchando
    return bool(CONFIG.get('gemini_key', '').strip())


def ai_missing_msg():
    if ai_provider() == 'mcp':
        return ('Ningún cliente MCP está conectado. Abre Claude Code (o Antigravity) '
                'en esta carpeta y pídele que atienda las tareas de JobSwiper.')
    return 'Falta la API key de Google Gemini (guárdala en Ajustes).'


def mcp_ask(prompt, *, kind='text', label='', history=None, timeout=None):
    """Encola la petición en el puente MCP y espera a que Claude la responda."""
    try:
        timeout = float(timeout or CONFIG.get('mcp_timeout') or 900)
    except (TypeError, ValueError):
        timeout = 900.0
    return BRIDGE.submit(prompt, kind=kind, label=label, history=history, timeout=timeout)


# --------------------------------------------------------------------------- #
#  Gemini (llamadas REST desde el servidor; la key nunca sale de tu máquina)  #
# --------------------------------------------------------------------------- #
def gemini_json(prompt, *, temperature=0.7, max_tokens=8192, model=None, label='', timeout=None):
    """Pide un JSON a la IA. Según el proveedor activo va a la API de Gemini o
    al puente MCP, donde lo responde Claude desde tu editor."""
    if ai_provider() == 'mcp':
        return _extract_json(mcp_ask(prompt, kind='json', label=label or 'Petición JSON', timeout=timeout))
    key = CONFIG.get('gemini_key', '').strip()
    model = (model or CONFIG.get('gemini_model', 'gemini-3-flash')).strip()
    if not key:
        raise RuntimeError('Falta la API key de Google Gemini (guárdala en Ajustes).')
    url = GEMINI_URL.format(model=model, key=key)
    body = json.dumps({
        'contents': [{'parts': [{'text': prompt}]}],
        'generationConfig': {
            'responseMimeType': 'application/json',
            'temperature': temperature,
            'maxOutputTokens': max_tokens,
        },
    }).encode()
    req = urllib.request.Request(url, data=body, headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            data = json.loads(r.read())
    except urllib.error.HTTPError as e:
        detail = e.read().decode('utf-8', 'replace')
        try:
            detail = json.loads(detail).get('error', {}).get('message', detail)
        except Exception:
            pass
        raise RuntimeError(f'Gemini {e.code}: {detail}')
    try:
        text = data['candidates'][0]['content']['parts'][0]['text']
    except (KeyError, IndexError):
        raise RuntimeError('Gemini no devolvió contenido (posible bloqueo de seguridad o cuota).')
    return _extract_json(text)


def _extract_json(text):
    """Parsea JSON tolerando fences ```json, texto alrededor, comentarios, comas huérfanas y truncamiento."""
    text = (text or '').strip()

    def clean_json_str(s):
        # Elimina comentarios de una línea tipo // ... o # ...
        s = re.sub(r'^\s*(?://|#).*$', '', s, flags=re.MULTILINE)
        # Elimina comas flotantes / huérfanas antes de llaves o corchetes de cierre
        s = re.sub(r',\s*([\]}])', r'\1', s)
        return s.strip()

    def repair_truncated_json(s):
        s = s.strip()
        if not s:
            return s
        in_string = False
        escape = False
        stack = []
        i = 0
        while i < len(s):
            char = s[i]
            if escape:
                escape = False
            elif char == '\\':
                escape = True
            elif char == '"':
                in_string = not in_string
            elif not in_string:
                if char in '{[':
                    stack.append(char)
                elif char in '}]':
                    if stack:
                        stack.pop()
            i += 1
        if in_string:
            s += '"'
        while stack:
            open_char = stack.pop()
            if open_char == '{':
                s += '}'
            elif open_char == '[':
                s += ']'
        return s

    try:
        return json.loads(repair_truncated_json(clean_json_str(text)))
    except Exception:
        pass
    m = re.search(r'```(?:json)?\s*(.*?)```', text, re.S)
    if m:
        try:
            return json.loads(repair_truncated_json(clean_json_str(m.group(1))))
        except Exception:
            pass
    # último recurso: primer {...} balanceado
    start = text.find('{')
    if start != -1:
        try:
            candidate = text[start:]
            return json.loads(repair_truncated_json(clean_json_str(candidate)))
        except Exception:
            pass
    raise RuntimeError('La IA no devolvió un JSON válido. Reintenta.')


def gemini_text(prompt, history=None, *, temperature=0.6, max_tokens=4096, label=''):
    """Llamada a la IA que devuelve texto plano (para el chat)."""
    if ai_provider() == 'mcp':
        return mcp_ask(prompt, kind='text', label=label or 'Petición de texto',
                       history=history).strip()
    key = CONFIG.get('gemini_key', '').strip()
    model = CONFIG.get('gemini_model', 'gemini-2.5-flash').strip()
    if not key:
        raise RuntimeError('Falta la API key de Google Gemini (guárdala en Ajustes).')
    contents = []
    for m in (history or []):
        role = 'model' if m.get('role') == 'assistant' else 'user'
        contents.append({'role': role, 'parts': [{'text': m.get('content', '')}]})
    contents.append({'role': 'user', 'parts': [{'text': prompt}]})
    body = json.dumps({
        'contents': contents,
        'generationConfig': {'temperature': temperature, 'maxOutputTokens': max_tokens},
    }).encode()
    url = GEMINI_URL.format(model=model, key=key)
    req = urllib.request.Request(url, data=body, headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            data = json.loads(r.read())
    except urllib.error.HTTPError as e:
        detail = e.read().decode('utf-8', 'replace')
        try:
            detail = json.loads(detail).get('error', {}).get('message', detail)
        except Exception:
            pass
        raise RuntimeError(f'Gemini {e.code}: {detail}')
    try:
        return data['candidates'][0]['content']['parts'][0]['text'].strip()
    except (KeyError, IndexError):
        raise RuntimeError('Gemini no devolvió contenido (posible bloqueo de seguridad o cuota).')


def agent_plan(message, history):
    """El asistente decide QUÉ hacer con el mensaje del usuario: conversar,
    buscar empleos, o filtrar los ya guardados. Devuelve {reply, action}."""
    profile = CONFIG.get('profile', {})
    base_cv = CONFIG.get('base_cv', '')
    last_search = CONFIG.get('last_search', {})
    jobs = db.get_jobs()

    lines = []
    for j in jobs[:60]:
        lines.append(
            f"- [{j.get('status')}] {j.get('title')} @ {j.get('company')} | "
            f"{j.get('location') or 's/ub'} | {j.get('link')}\n"
            f"    {(j.get('description') or '')[:280].strip()}")
    jobs_ctx = '\n'.join(lines) if lines else '(vacía)'

    prompt = f"""Eres el asistente de JobSwiper, una app de búsqueda de empleo. Ayudas al usuario a: (a) definir qué busca a partir de su CV, (b) buscar empleos en LinkedIn, y (c) filtrar/analizar los empleos ya guardados en su base de datos.

Responde SIEMPRE con JSON válido con esta forma:
{{
  "reply": "mensaje en español para el usuario (Markdown). Cuando menciones un empleo guardado, enlázalo así: [Título @ Empresa](link) usando el link EXACTO.",
  "action": null
     | {{"type":"search","queries":[{{"keywords":"título","location":"ciudad/país"}}],"exclude":["texto que NO debe aparecer en la descripción"]}}
     | {{"type":"filter","location":"","text":"","status":"","exclude":[]}}
     | {{"type":"save_profile","profile":{{"location":"","field":"","seniority":"","relocate":"","authorization":""}}}}
}}

Cómo decidir la acción:
- Onboarding: si el usuario todavía no ha dicho qué busca y hay un CV, hazle 2-3 preguntas CONCRETAS basadas en su CV (qué tipo de puesto busca, en qué ciudad o si prefiere remoto, y qué modalidad o tipo de contrato quiere). No asumas ningún país: usa la ubicación de su perfil/CV. action=null.
- Buscar (ej: "busca ingeniero en Guadalajara", "ahora lo mismo pero en Alemania", "quiero trabajos de drones, robótica y programación"): action.type="search" con 1-4 queries realistas (título + ubicación que un reclutador usaría). Si dice "los mismos parámetros pero en X", reutiliza los keywords del "último search" cambiando la ubicación. Si pide evitar algo (ej: "que no pidan ciudadanía americana"), añade esos textos a "exclude" (ej: ["u.s. citizen","citizenship required","security clearance"]).
- Segmentar lo YA guardado (ej: "cuáles hay en mi ciudad", "cuáles no piden ciudadanía", "muéstrame los de me gusta"): action.type="filter" con criterios. "exclude"=textos que NO deben aparecer en la descripción.
- Si el usuario aporta datos de su perfil, añádelos en save_profile además del reply.

Último search del usuario: {json.dumps(last_search, ensure_ascii=False)}
Perfil actual: {json.dumps(profile, ensure_ascii=False)}
CV base (resumen): {base_cv[:2500]}

Empleos guardados ({len(jobs)}):
{jobs_ctx}

Mensaje del usuario: {message}"""
    return gemini_json(prompt, temperature=0.5, max_tokens=2048,
                       label='Chat: ' + (message or '')[:70])


def mock_agent_plan(q):
    ql = q.lower()
    if any(w in ql for w in ['__inicio__']):
        return {'reply': '(demo) ¡Hola! Veo tu CV. Para buscar los mejores empleos, cuéntame: '
                          '¿en qué áreas o tecnologías te quieres enfocar, en qué ubicación, y '
                          '¿prefieres algo remoto, híbrido o presencial?',
                'action': None}
    if any(w in ql for w in ['busca', 'buscar', 'search', 'encuentra', 'drone', 'robot', 'aleman', 'germany', 'programa']):
        loc = 'Alemania' if ('aleman' in ql or 'germany' in ql) else (CONFIG.get('profile', {}).get('location') or 'Remote')
        return {'reply': f'(demo) Buscando empleos para: "{q}".',
                'action': {'type': 'search',
                           'queries': [{'keywords': q[:40], 'location': loc}],
                           'exclude': (['u.s. citizen', 'citizenship'] if 'ciudadan' in ql else [])}}
    if any(w in ql for w in ['cuales', 'cuáles', 'filtra', 'muestra', 'tengo', 'ciudadan']):
        return {'reply': '(demo) Filtro tus empleos guardados.',
                'action': {'type': 'filter', 'location': '', 'text': '', 'status': '',
                           'exclude': (['citizen', 'citizenship'] if 'ciudadan' in ql else [])}}
    return {'reply': '(demo) Cuéntame qué tipo de empleo buscas y en qué ubicación, o pídeme que busque algo concreto.',
            'action': None}


def _links_block(jobs, limit=12):
    return ''.join(
        f"\n- [{j.get('title')} @ {j.get('company')}]({j.get('link')}) — {j.get('location') or ''}"
        for j in jobs[:limit])


def suggest_titles(profile, base_cv):
    prompt = f"""Eres un asesor de carrera experto en el mercado laboral. A partir del perfil y CV de un candidato, genera una lista de búsquedas de empleo OPTIMIZADAS para maximizar la probabilidad de ser aceptado, priorizando puestos realistas para su nivel y con alta disponibilidad de "Easy Apply" en LinkedIn.

Devuelve SOLO JSON válido con esta forma:
{{"queries": [{{"keywords": "título del puesto", "location": "ciudad, estado/país", "why": "1 frase de por qué encaja"}}]}}

Reglas:
- Entre 6 y 10 búsquedas.
- Varía los títulos (sinónimos y niveles) que un reclutador realmente usaría, no títulos inventados.
- Ajusta el nivel (junior/mid/senior) al perfil. Si tiene poca experiencia, prioriza entry-level.
- Si el candidato NO está dispuesto a reubicarse, usa solo su ubicación actual (o "Remote"). Si SÍ, incluye 2-3 ciudades con demanda para su campo.
- Considera su nivel educativo: no sugieras roles que exijan un título que no tiene.

=== PERFIL (respuestas del cuestionario) ===
{json.dumps(profile, ensure_ascii=False, indent=2)}

=== CV BASE ===
{base_cv[:6000]}"""
    return gemini_json(prompt, temperature=0.8,
                       label='Sugerir títulos de búsqueda').get('queries', [])


ATS_RULES = """REGLAS ATS (obligatorias para el resume y el CV):
- Una sola columna, sin tablas, sin gráficos, sin cajas ni iconos: los ATS no los leen.
- Encabezados de sección estándar y en el idioma de la oferta: "Professional Summary/Perfil", "Experience/Experiencia", "Education/Educación", "Skills/Habilidades", "Certifications/Certificaciones".
- Orden cronológico inverso (lo más reciente primero). Cada puesto: Título — Empresa — Ubicación — Fechas, y viñetas con verbos de acción y logros cuantificables.
- Incorpora de forma natural las palabras clave EXACTAS de la descripción de la oferta (títulos, tecnologías, herramientas) para maximizar el match ATS.
- Datos de contacto en texto plano (email, teléfono, ciudad, LinkedIn). Sin fotos.
- NUNCA inventes experiencia, títulos, certificaciones, empresas, tecnologías ni fechas que no estén en el CV base: solo reordena, prioriza y reformula lo real."""


# Estrategia de "habilidades transferibles": agresiva para no quedar filtrado por
# ATS, pero SIN mentir. La idea es mapear cada requisito de la oferta a la habilidad
# real más cercana del candidato y presentarla como transferible.
TRANSFERABLE_RULES = """ESTRATEGIA DE HABILIDADES TRANSFERIBLES (aplícala en TODO el documento/respuesta):
Objetivo: que el candidato no quede filtrado por no tener la palabra EXACTA que pide la oferta, sin afirmar jamás algo falso.

Para CADA requisito/herramienta/tecnología que pida la oferta:
1. Si el candidato YA lo tiene en su CV base → resáltalo, ponlo al frente y usa la palabra clave EXACTA de la oferta.
2. Si NO lo tiene, pero tiene algo CLARAMENTE análogo o de la misma familia (ej. Fusion 360 ↔ AutoCAD/SolidWorks; React ↔ Vue; PostgreSQL ↔ MySQL; Photoshop ↔ GIMP) → menciónalo como TRANSFERIBLE, nombrando la herramienta pedida, con fórmulas honestas como:
   - "Dominio de {herramienta_real}, base directamente transferible a {herramienta_pedida}".
   - "Sólida experiencia en {área}, incluyendo {herramienta_real}; capacidad demostrada de adoptar rápidamente {herramienta_pedida}".
   Así la palabra clave aparece para el ATS y es 100% defendible en entrevista.
3. Si NO tiene nada ni siquiera análogo → NO lo incluyas (no lo inventes).

PROHIBIDO SIEMPRE (esto sería mentir y expone al candidato):
- Afirmar experiencia DIRECTA, años, nivel "experto/avanzado", proyectos o certificaciones en una herramienta que el candidato NO ha usado.
- Escribir "AutoCAD: 3 años" o listar como habilidad propia una tecnología que no tiene ni análoga.
- Inventar empleos, títulos académicos, certificaciones o fechas.

Regla de oro: puedes CONECTAR y ENMARCAR lo real de forma favorable y agresiva, pero cada afirmación debe poder sostenerse en una entrevista o prueba técnica."""


# Esquema estructurado que la IA rellena; el frontend lo pinta con plantillas HTML/CSS.
DOC_SCHEMA = """{
  "en": {
    "profile": {
      "name": "Nombre Apellido",
      "headline": "job title in English (e.g. Data Center Technician)",
      "lang": "en",
      "contact": {"email":"","phone":"","location":"City, Country (in English)","linkedin":"","website":""}
    },
    "resume": {
      "summary": "2-3 sentences max professional summary in English, tailored to the job",
      "experience": [
        {"title":"","company":"","location":"","start":"MMM YYYY","end":"MMM YYYY or Present",
         "bullets":["achievement with action verb and metrics (in English)","..."]}
      ],
      "education": [{"degree":"","school":"","location":"","start":"","end":"","detail":""}],
      "skills": ["skill1","skill2"],
      "certifications": ["cert1"],
      "languages": ["Spanish (Native)","English (C1)"]
    },
    "cv": { "same structure as resume, a bit more detailed but still ONE PAGE max (in English)" },
    "cover_letter": {
      "date": "July 11, 2026",
      "recipient": "Hiring Team at <Company>",
      "paragraphs": ["paragraph 1 (in English)","paragraph 2 (in English)","paragraph 3 (in English)"],
      "closing": "Sincerely,"
    }
  },
  "es": {
    "profile": {
      "name": "Nombre Apellido",
      "headline": "título del puesto en español (ej: Técnico de Centro de Datos)",
      "lang": "es",
      "contact": {"email":"","phone":"","location":"Ciudad, País (en español)","linkedin":"","website":""}
    },
    "resume": {
      "summary": "2-3 frases (máx) de resumen profesional en español enfocadas a la oferta",
      "experience": [
        {"title":"","company":"","location":"","start":"MMM AAAA","end":"MMM AAAA o Presente",
         "bullets":["logro con verbo de acción y métrica (en español)","..."]}
      ],
      "education": [{"degree":"","school":"","location":"","start":"","end":"","detail":""}],
      "skills": ["habilidad1","habilidad2"],
      "certifications": ["cert1"],
      "languages": ["Español (nativo)","Inglés (C1)"]
    },
    "cv": { "igual estructura que resume, algo más detallado pero máximo UNA PÁGINA (en español)" },
    "cover_letter": {
      "date": "11 de julio de 2026",
      "recipient": "Equipo de contratación de <Empresa>",
      "paragraphs": ["párrafo 1 (en español)","párrafo 2 (en español)","párrafo 3 (en español)"],
      "closing": "Atentamente,"
    }
  },
  "match_notes": "2-3 viñetas honestas (Markdown) en español: qué requisitos cumple y cuáles reforzar"
}"""


def extract_highlights(job):
    """Resume una oferta en puntos clave para entenderla de un vistazo (sin leer todo)."""
    desc = (job.get('description') or '').strip()
    prompt = f"""Eres un analista de empleos. Resume esta oferta en puntos CLAROS y BREVES para que alguien entienda de qué va el trabajo en 10 segundos, sin leer todo.

Devuelve SOLO JSON válido con esta forma exacta:
{{
  "tldr": "1 frase (máx 18 palabras) que resuma el rol y qué haría la persona",
  "salary": "rango salarial si aparece en la oferta (ej '$60k–$80k/año') o null",
  "modality": "Remoto | Híbrido | Presencial | null",
  "seniority": "nivel si se deduce (ej 'Entry-level', 'Mid', 'Senior') o null",
  "highlights": ["3 a 5 puntos MÁS importantes, cada uno máx 9 palabras (responsabilidades o beneficios clave)"],
  "skills": ["hasta 6 tecnologías o habilidades concretas mencionadas, 1-2 palabras cada una"],
  "flags": ["hasta 3 requisitos que podrían descalificar o exigir atención (ej 'Exige ciudadanía USA', 'Requiere 5+ años', 'Turnos on-call'); [] si no hay"]
}}

Reglas:
- NO inventes datos que no estén en la oferta; usa null o [] cuando falte información.
- Español, conciso, sin relleno. Nada de frases genéricas de marketing.

PUESTO: {job.get('title')} @ {job.get('company')}
UBICACIÓN: {job.get('location') or 'N/D'}
TIPO: {job.get('employment_type') or 'N/D'} · NIVEL: {job.get('seniority') or 'N/D'}
DESCRIPCIÓN:
{desc[:6000]}"""
    return gemini_json(prompt, temperature=0.3, max_tokens=1400,
                       label='Highlights: %s @ %s' % (job.get('title'), job.get('company')))


def mock_highlights(job):
    """Resumen de ejemplo (modo demo) derivado de forma sencilla de la descripción."""
    desc = job.get('description') or ''
    bullets = [ln.strip(' •-*\t') for ln in desc.splitlines()
               if ln.strip().startswith(('•', '-', '*'))]
    flags = []
    low = desc.lower()
    if 'citizen' in low or 'ciudadan' in low:
        flags.append('Puede exigir ciudadanía')
    if 'on-call' in low or 'on call' in low:
        flags.append('Turnos on-call')
    return {
        'tldr': (desc.splitlines()[0][:110] if desc.strip() else f"{job.get('title')} en {job.get('company')}"),
        'salary': None,
        'modality': ('Híbrido' if 'hybrid' in low else ('Remoto' if 'remote' in low else 'Presencial')),
        'seniority': job.get('seniority'),
        'highlights': (bullets[:5] or [f"Puesto: {job.get('title')}", f"Empresa: {job.get('company')}"]),
        'skills': [],
        'flags': flags,
    }


def generate_documents(job, profile, base_cv):
    prompt = f"""Eres un redactor experto en documentos de empleo optimizados para sistemas ATS. A partir del CV base del candidato y de una oferta concreta, extrae y adapta su información a esa oferta.

Genera DOS versiones completas de los documentos:
1. Una versión 100% en INGLÉS ("en") para aplicar a vacantes globales.
2. Una versión 100% en ESPAÑOL ("es") para aplicar a vacantes locales.

{ATS_RULES}

{TRANSFERABLE_RULES}

Devuelve SOLO JSON válido con EXACTAMENTE esta estructura (rellena los valores):
{DOC_SCHEMA}

Importante:
- La versión en inglés ("en") y español ("es") deben estar traducidas/adaptadas perfectamente.
- En la sección "en", TODO (incluyendo nombres de meses, títulos, descripciones, cover letter, etc.) debe estar 100% en INGLÉS.
- En la sección "es", TODO (incluyendo nombres de meses, títulos, descripciones, cover letter, etc.) debe estar 100% en ESPAÑOL.
- "resume" y "cv" comparten el mismo "profile" dentro de su respectivo idioma. Parte de datos reales del CV base; aplica la ESTRATEGIA DE HABILIDADES TRANSFERIBLES para cubrir los requisitos de la oferta sin inventar. Deja "" o [] lo que no exista ni tenga análogo.
- Para el teléfono de contacto ('contact.phone'): si la oferta o empresa es de México (ej. Puebla, CDMX, Monterrey o empresa mexicana), usa obligatoriamente el número de México ({personal.TEL_MX}). Si la oferta es en Estados Unidos / empresa estadounidense, usa el número de EE.UU. ({personal.TEL_US}).
- El "resume" DEBE caber en UNA sola página: es la versión condensada. Máximo 3-4 experiencias (las más relevantes a la oferta) con 2-3 viñetas CORTAS cada una, resumen de 2-3 líneas y solo las habilidades/certificaciones clave. Sé breve.
- El "cv" TAMBIÉN debe caber en UNA sola página (puede ser algo más detallado que el resume, pero nunca pasar de 1 página).
- Si hay un CV GENERAL, úsalo como plantilla de partida (estructura, redacción y contacto) y adáptalo a la oferta; el CV BASE sigue siendo la fuente de la verdad.
- Para el contacto, si el PERFIL trae name/email/phone/location, úsalos.

=== PERFIL (respuestas previas) ===
{json.dumps(profile, ensure_ascii=False)}

=== CV BASE (fuente de la verdad) ===
{base_cv[:6000]}

=== CV GENERAL (plantilla de 1 página ya optimizada) ===
{json.dumps((CONFIG.get('master_cv') or {}).get('es') or {}, ensure_ascii=False)[:5000] or 'N/D'}

=== OFERTA ===
Puesto: {job.get('title')}
Empresa: {job.get('company')}
Ubicación: {job.get('location')}
Descripción:
{(job.get('description') or '')[:7000]}"""
    return gemini_json(prompt, temperature=0.6, max_tokens=8192,
                       label='Documentos (CV/Resume/Cover) para %s @ %s'
                             % (job.get('title'), job.get('company')))


DOC_NAMES = {'resume': 'Resume', 'cv': 'CV', 'cover_letter': 'Cover Letter'}


def refine_document(job, current_docs, doc_type, lang, instruction, base_cv):
    """Modifica UN documento (estructurado) según la instrucción del usuario
    para el idioma especificado ('en' o 'es')."""
    # Si es el formato viejo (sin en/es), hacemos fallback a current_docs.
    lang_docs = (current_docs or {}).get(lang)
    if not lang_docs or not isinstance(lang_docs, dict):
        current = (current_docs or {}).get(doc_type, {})
        profile = (current_docs or {}).get('profile', {})
    else:
        current = lang_docs.get(doc_type, {})
        profile = lang_docs.get('profile', {})

    label = DOC_NAMES.get(doc_type, doc_type)
    prompt = f"""Eres un redactor experto en documentos de empleo ATS. Vas a MODIFICAR el contenido estructurado del {label} en el idioma '{lang}' según la instrucción del usuario, manteniéndolo adaptado a la oferta.

ESCRÍBELO TODO EN EL IDIOMA '{lang}' (si lang='en', escribe 100% en inglés; si lang='es', escribe 100% en español).

{ATS_RULES}

{TRANSFERABLE_RULES}

Reglas:
- Aplica exactamente lo que pide el usuario (si pide "que quepa en una hoja", recorta y condensa).
- Puedes usar la estrategia de habilidades transferibles para cubrir requisitos, pero sin inventar experiencia directa, años ni certificaciones que no consten.
- El usuario puede pedir cambios TANTO al contenido del documento como al perfil/encabezado (nombre, teléfono, email, headline, ubicación, etc.). Aplica todos los cambios solicitados.
- Devuelve SOLO JSON con EXACTAMENTE esta estructura (sin texto extra):
  {{"profile": {{...perfil actualizado...}}, "{doc_type}": {{...documento actualizado...}}}}

=== INSTRUCCIÓN DEL USUARIO ===
{instruction}

=== OFERTA ===
{job.get('title')} @ {job.get('company')} — {job.get('location')}

=== PERFIL ACTUAL (modifícalo si el usuario lo pide) ===
{json.dumps(profile, ensure_ascii=False)}

=== CV BASE (fuente de la verdad) ===
{base_cv[:5000]}

=== {label} ACTUAL (JSON) ===
{json.dumps(current, ensure_ascii=False)[:7000]}"""
    return gemini_json(prompt, temperature=0.5, max_tokens=6000,
                       label='Refinar %s (%s) — %s' % (label, lang, job.get('title')))


FAST_MODEL = 'gemini-2.5-flash'  # modelo rápido para tareas de UI (elegir botón)


def ai_pick_apply_button(candidates):
    """Dado un listado de elementos clicables [{id,tag,text,aria}], pregunta a un
    modelo rápido cuál es el botón de 'Solicitud sencilla / Easy Apply'. Devuelve el id o ''."""
    if not candidates:
        return ''
    prompt = f"""De la siguiente lista de elementos clicables de una página de empleo de LinkedIn, elige el que INICIA la solicitud rápida dentro de LinkedIn ("Solicitud sencilla" o "Easy Apply"). NO elijas botones de navegación, "Guardar/Save", "Seguir/Follow", ni "Aplicar" que lleve a un sitio EXTERNO.

Devuelve SOLO JSON: {{"id": "<id del elemento o vacío si ninguno aplica>"}}

Elementos:
{json.dumps(candidates, ensure_ascii=False)[:6000]}"""
    try:
        res = gemini_json(prompt, temperature=0, max_tokens=120, model=FAST_MODEL,
                          label='Elegir botón de Easy Apply')
        return str(res.get('id') or '')
    except Exception:
        return ''


# --------------------------------------------------------------------------- #
#  Idioma de los documentos (según el país de la empresa)                      #
# --------------------------------------------------------------------------- #
_ES_COUNTRIES = (
    'méxico', 'mexico', 'españa', 'spain', 'argentina', 'colombia', 'chile',
    'perú', 'peru', 'venezuela', 'ecuador', 'guatemala', 'bolivia', 'uruguay',
    'paraguay', 'costa rica', 'panamá', 'panama', 'república dominicana',
    'honduras', 'el salvador', 'nicaragua', 'puerto rico', 'cdmx', 'méxico city',
    'mexico city', 'guadalajara', 'monterrey', 'bogotá', 'bogota', 'madrid',
    'barcelona', 'buenos aires', 'lima', 'santiago',
)
_EN_COUNTRIES = (
    'united states', 'u.s.a', 'u.s.', ', usa', ' usa', 'united kingdom', ', uk',
    ' uk', 'canada', 'australia', 'ireland', 'new zealand', 'singapore',
)
_US_STATES = (
    'alabama', 'alaska', 'arizona', 'arkansas', 'california', 'colorado',
    'connecticut', 'delaware', 'florida', 'georgia', 'hawaii', 'idaho', 'illinois',
    'indiana', 'iowa', 'kansas', 'kentucky', 'louisiana', 'maine', 'maryland',
    'massachusetts', 'michigan', 'minnesota', 'mississippi', 'missouri', 'montana',
    'nebraska', 'nevada', 'new hampshire', 'new jersey', 'new mexico', 'new york',
    'north carolina', 'north dakota', 'ohio', 'oklahoma', 'oregon', 'pennsylvania',
    'rhode island', 'south carolina', 'south dakota', 'tennessee', 'texas', 'utah',
    'vermont', 'virginia', 'washington', 'west virginia', 'wisconsin', 'wyoming',
)
_US_ABBR = (' al', ' ak', ' az', ' ar', ' ca', ' co', ' ct', ' de', ' fl', ' ga',
            ' hi', ' id', ' il', ' in', ' ia', ' ks', ' ky', ' la', ' me', ' md',
            ' ma', ' mi', ' mn', ' ms', ' mo', ' mt', ' ne', ' nv', ' nh', ' nj',
            ' nm', ' ny', ' nc', ' nd', ' oh', ' ok', ' or', ' pa', ' ri', ' sc',
            ' sd', ' tn', ' tx', ' ut', ' vt', ' va', ' wa', ' wv', ' wi', ' wy')


def _country_lang(location):
    """Idioma según el PAÍS de la ubicación: 'es', 'en' o None si no está claro."""
    l = ' ' + (location or '').lower().strip() + ' '
    if any(c in l for c in _ES_COUNTRIES):
        return 'es'
    if any(c in l for c in _EN_COUNTRIES):
        return 'en'
    if any(s in l for s in _US_STATES):
        return 'en'
    # abreviatura de estado de EEUU al final: "Raleigh, NC"
    m = re.search(r',\s*([a-z]{2})\b', l)
    if m and (' ' + m.group(1)) in _US_ABBR:
        return 'en'
    return None


def _text_lang(text):
    """Idioma del texto de la oferta por marcadores: 'es', 'en' o None."""
    t = (text or '').lower()
    if len(t) < 40:
        return None
    es = len(re.findall(r'[áéíóúñ¿¡]', t)) * 2
    es += sum(t.count(w) for w in (' que ', ' para ', ' con ', ' los ', ' las ',
              ' una ', ' experiencia', ' conocimientos', ' requisitos', ' empresa',
              ' habilidades', ' años', ' buscamos', ' trabajo ', ' desarrollo',
              ' responsabilidades', ' funciones'))
    en = sum(t.count(w) for w in (' the ', ' and ', ' with ', ' for ', ' you ',
             ' our ', ' experience', ' requirements', ' skills', ' years', ' we are',
             ' looking', ' ability', ' team ', ' responsibilities', ' knowledge'))
    if es >= 3 and es > en * 1.3:
        return 'es'
    if en >= 3 and en > es * 1.3:
        return 'en'
    return None


def _ai_language(job):
    """Desempate con un modelo rápido cuando la heurística no está segura."""
    if MOCK or not ai_ready():
        return None
    prompt = f"""¿En qué idioma se debe ENVIAR esta solicitud de empleo? Regla: INGLÉS si la empresa/vacante es de EEUU u otro país anglófono; ESPAÑOL si es de un país hispanohablante (México, España, Argentina, etc.). Guíate por el país de la ubicación y, si ayuda, por el idioma de la descripción.

Devuelve SOLO JSON: {{"lang": "en"}} o {{"lang": "es"}}.

Empresa: {job.get('company')}
Ubicación: {job.get('location')}
Descripción (extracto): {(job.get('description') or '')[:800]}"""
    try:
        res = gemini_json(prompt, temperature=0, max_tokens=40, model=FAST_MODEL,
                          label='Detectar idioma de la oferta')
        lang = str(res.get('lang') or '').lower()
        return lang if lang in ('en', 'es') else None
    except Exception:
        return None


def decide_doc_language(job):
    """Decide 'en'/'es' para los documentos, priorizando el país de la empresa.
    Devuelve (lang, reason)."""
    cl = _country_lang(job.get('location'))
    tl = _text_lang(job.get('description'))
    if cl and tl and cl != tl:
        # el país dice una cosa y el texto otra → que la IA confirme
        ai = _ai_language(job)
        return (ai or cl), f'país={cl}/texto={tl}→{ai or cl}'
    if cl:
        return cl, f'país={cl}'
    if tl:
        return tl, f'texto={tl}'
    ai = _ai_language(job)
    return (ai or 'en'), (f'IA={ai}' if ai else 'por defecto=en')


def answer_application_questions(questions, job, profile, base_cv):
    """Responde en bloque las preguntas de una solicitud de Easy Apply según el perfil.

    `questions` = [{"key","label","type","options"(opcional)}]. Devuelve
    {"answers": [{"key","answer","confidence":"high|low","sensitive":bool,"reason"}]}.
    - type: 'text' | 'textarea' | 'select' | 'radio' | 'checkbox' | 'number'
    - Para select/radio la respuesta DEBE ser exactamente una de las opciones.
    """
    prompt = f"""Eres el asistente que rellena una solicitud de empleo (LinkedIn Easy Apply) EN NOMBRE del candidato, usando su perfil y CV reales. Responde cada pregunta para maximizar que avance la solicitud, siendo agresivo con las habilidades transferibles pero SIN afirmar nada falso que no pueda sostener en una entrevista.

{TRANSFERABLE_RULES}

Cómo aplicar lo anterior a las PREGUNTAS:
- Preguntas ABIERTAS (text/textarea: "describe tu experiencia con X", "por qué encajas"): usa el encuadre transferible, nombra la herramienta pedida y conéctala con la habilidad real más cercana. confidence="high".
- Preguntas SÍ/NO o de AÑOS sobre una herramienta ESPECÍFICA que el candidato NO tiene ni siquiera análoga: responde con el valor real/defendible (no inventes años ni "sí" de experiencia directa que no existe) y marca confidence="low" y sensitive=true para que el usuario lo revise.
- Si tiene una habilidad de la MISMA familia (ej. Fusion 360 para AutoCAD) y la pregunta es de años en general del área, puedes contar la experiencia del área análoga, pero marca confidence="low" para que se revise.

Devuelve SOLO JSON válido:
{{"answers": [
  {{"key": "la key EXACTA de la pregunta",
    "answer": "respuesta a poner en el campo (string). Para 'select'/'radio' debe ser EXACTAMENTE una de las opciones dadas. Para 'number' un número. Para 'checkbox' 'true' o 'false'.",
    "confidence": "high | low",
    "sensitive": true/false,
    "reason": "máx 8 palabras"}}
]}}

Reglas de confianza:
- confidence="low" o sensitive=true cuando la respuesta pueda DESCALIFICAR o dependa de datos que NO están claros en el perfil: expectativas salariales, disposición a reubicarse, security clearance/visa/sponsorship, ciudadanía, años de experiencia si no se deducen del CV, o afirmar una herramienta específica vía habilidad transferible.
- confidence="high" solo cuando la respuesta se deduce con claridad del perfil/CV (nombre, email, teléfono, sí/no obvios, años que sí constan) o es un encuadre transferible en texto abierto.
- Nunca dejes 'answer' vacío: da tu mejor estimación aunque marques confidence="low".
- Para años de experiencia usa un número entero razonable según el CV; no infles años en una herramienta concreta que no consta.
- Si preguntan autorización para trabajar / sponsorship, usa el campo 'authorization' del perfil.
- Si preguntan número de teléfono: para vacantes o empresas en México (ej. Puebla, México), usa obligatoriamente el teléfono de México ({personal.TEL_MX}). Para vacantes en EE.UU., usa el de EE.UU. ({personal.TEL_US}).

=== PERFIL ===
{json.dumps(profile, ensure_ascii=False)}

=== CV BASE ===
{base_cv[:3500]}

=== OFERTA ===
{job.get('title')} @ {job.get('company')} — {job.get('location')}

=== PREGUNTAS ({len(questions)}) ===
{json.dumps(questions, ensure_ascii=False)}"""
    return gemini_json(prompt, temperature=0.2, max_tokens=2048,
                       label='Responder preguntas del formulario de %s' % job.get('company'))


def mock_answer_questions(questions, job, profile, base_cv):
    ans = []
    for q in questions:
        label = (q.get('label') or '').lower()
        opts = q.get('options') or []
        sensitive = any(w in label for w in
                        ('salary', 'salario', 'relocat', 'reubic', 'clearance', 'sponsor',
                         'visa', 'citizen', 'ciudadan', 'travel', 'viaj'))
        if opts:
            a = next((o for o in opts if o.lower() in ('yes', 'sí', 'si')), opts[0])
        elif 'year' in label or 'año' in label or q.get('type') == 'number':
            a = str(profile.get('experience') or '3')
        elif 'email' in label:
            a = 'demo@email.com'
        elif 'phone' in label or 'teléfono' in label:
            loc = ((job.get('location') or '') + ' ' + (job.get('company') or '')).lower()
            if any(m in loc for m in ('mexico', 'méxico', 'puebla', 'cdmx', 'guadalajara', 'monterrey', 'queretaro')):
                a = profile.get('phone_mexico') or profile.get('phone') or personal.TEL_MX
            else:
                a = profile.get('phone_us') or profile.get('phone') or personal.TEL_US
        else:
            a = 'Sí'
        ans.append({'key': q.get('key'), 'answer': a,
                    'confidence': 'low' if sensitive else 'high',
                    'sensitive': sensitive, 'reason': 'demo'})
    return {'answers': ans}


def pre_filter_jobs(jobs, profile):
    """
    Realiza un filtrado determinista previo basado en ubicación y seniority del perfil.
    Devuelve (jobs_filtrados, num_descartados_seniority, num_descartados_location)
    """
    seniority = (profile.get('seniority') or 'Entry-level').lower()
    location_pref = (profile.get('location') or '').lower()
    relocate = (profile.get('relocate') or '').lower() # 'No, solo mi ciudad', 'Sí, a otras ciudades', 'Solo remoto'
    
    filtered = []
    discarded_seniority = 0
    discarded_location = 0
    
    # Palabras clave excluyentes para niveles Senior si el usuario es Junior/Mid
    senior_keywords = ['senior', 'sr.', 'sr ', 'lead', 'principal', 'staff', 'architect', 'manager', 'director', 'vp', 'director']
    
    for j in jobs:
        title = (j.get('title') or '').lower()
        desc = (j.get('description') or '').lower()
        loc = (j.get('location') or '').lower()
        
        # 1. Filtro de Seniority
        if seniority in ('entry-level', 'junior', 'entry'):
            if any(k in title for k in senior_keywords) or any(k + ' ' in desc[:400] for k in senior_keywords):
                discarded_seniority += 1
                continue
                
        # 2. Filtro de Ubicación
        # Si no quiere reubicarse, y no es remoto, y la oferta no está en su ubicación/ciudad preferida:
        if relocate == 'no, solo mi ciudad':
            if location_pref and location_pref not in loc and 'remote' not in loc and 'remoto' not in loc:
                discarded_location += 1
                continue
                
        filtered.append(j)
        
    return filtered, discarded_seniority, discarded_location


def consolidate_questions(candidates_evals):
    """
    Consolida las preguntas propuestas por los evaluadores en 3 preguntas clave usando Gemini.
    """
    all_questions = []
    for c in candidates_evals[:10]:
        questions = c['evaluation'].get('candidate_questions') or []
        for q in questions:
            if q and q not in all_questions:
                all_questions.append(q)
                
    if not all_questions:
        return [
            "¿Tienes disponibilidad para reubicarte de forma inmediata si se aprueba tu visa?",
            "¿Cuál es tu nivel de familiaridad con el desarrollo de software bajo metodología ágil?",
            "¿Estás de acuerdo con esquemas de guardia o soporte en fines de semana (on-call)?"
        ]
        
    prompt = f"""Vas a consolidar una lista de preguntas técnicas y laborales propuestas por varios agentes evaluadores de empleo.
Selecciona las 3 preguntas más importantes, críticas o relevantes que deberíamos hacerle al candidato para desempatar y afinar el ranking de estos empleos.
Evita duplicados o preguntas muy parecidas. Mantén las preguntas cortas, directas y en español.

Devuelve estrictamente un JSON con esta estructura:
{{
  "questions": [
    "pregunta 1 consolidada y clara",
    "pregunta 2 consolidada y clara",
    "pregunta 3 consolidada y clara"
  ]
}}

=== PREGUNTAS PROPUESTAS ===
{json.dumps(all_questions[:15], ensure_ascii=False)}
"""
    try:
        res = gemini_json(prompt, temperature=0.3, label='Consolidar preguntas del candidato')
        return res.get('questions') or all_questions[:3]
    except Exception:
        return all_questions[:3]


def autojob_evaluate(job, profile, base_cv):
    """
    Evalúa un empleo contra el perfil y el CV.
    Determina compatibilidad técnica, situación migratoria, TN-1, pros/contras, y score.
    """
    prompt = f"""Eres un asesor de carrera experto e ingeniero especialista en reclutamiento técnico.
Vas a evaluar la compatibilidad de una oferta de empleo para el perfil de un candidato.

Presta especial atención a:
1. Requisitos técnicos del puesto frente a los conocimientos del candidato.
2. Requisitos migratorios: ¿pide ciudadanía de EE.UU. o "security clearance" (autorización de seguridad)? Si pide esto, es una traba migratoria importante para un extranjero.
3. Elegibilidad de visa TN (para profesionales mexicanos/canadienses bajo el T-MEC): la vacante debe ser apta para la categoría correspondiente (Ingeniería, Soporte Técnico, Analista de Sistemas, etc.) y la empresa debe estar abierta a la contratación. Si se solicita expresamente ciudadanía o clearance, es probable que no sea elegible para TN.
4. Modalidad de trabajo y ubicación solicitada frente a las preferencias del candidato.

Devuelve estrictamente un JSON válido con esta estructura (no agregues texto fuera del JSON):
{{
  "score": <número entero de 0 a 100 indicando compatibilidad real>,
  "fit_summary": "<resumen del fit en 2-3 oraciones>",
  "pros": ["<pro 1>", "<pro 2>", "<pro 3>"],
  "cons": ["<contra 1>", "<contra 2>", "..."],
  "migration_analysis": "<análisis sobre patrocinio, visa TN-1 y requerimientos de ciudadanía/clearance>",
  "tn1_eligible": <true si es apto/posible para visa TN, false si pide explícitamente ciudadanía/clearance o no encaja, null si es incierto>,
  "candidate_questions": ["<pregunta aclaratoria 1 basada en requisitos específicos del puesto como guardias, tecnologías requeridas, shift nocturno, o visa>", "<pregunta aclaratoria 2>"]
}}

=== PERFIL DEL CANDIDATO ===
{json.dumps(profile, ensure_ascii=False)}

=== CV BASE DEL CANDIDATO ===
{base_cv[:5000]}

=== OFERTA DE EMPLEO ===
Puesto: {job.get('title')}
Empresa: {job.get('company')}
Ubicación: {job.get('location')}
Descripción:
{job.get('description', '')[:7000]}
"""
    return gemini_json(prompt, temperature=0.3,
                       label='AutoJob · evaluar %s @ %s' % (job.get('title'), job.get('company')))


def autojob_compare(candidates, profile, base_cv, user_answers):
    """
    Compara los candidatos pre-seleccionados y los ordena de mejor a peor usando las respuestas del usuario.
    candidates es una lista de dicts, cada uno es {job, evaluation}.
    """
    simplified_candidates = []
    for idx, c in enumerate(candidates):
        simplified_candidates.append({
            "index": idx,
            "job_id": c['job']['id'],
            "title": c['job']['title'],
            "company": c['job']['company'],
            "location": c['job']['location'],
            "score": c['evaluation']['score'],
            "fit_summary": c['evaluation']['fit_summary'],
            "tn1_eligible": c['evaluation']['tn1_eligible']
        })

    prompt = f"""Eres un asesor de carrera experto. Vas a comparar un conjunto de ofertas de empleo pre-seleccionadas para un candidato y determinar cuáles son las mejores 5, ordenándolas del 1 al 5.
Considera que el candidato es mexicano, busca patrocinio o visa TN-1, y tiene el siguiente perfil.

Toma en cuenta de forma crítica las respuestas que el usuario dio a las preguntas de afinación:
- Usa las respuestas para bonificar los empleos donde encaja o penalizar severamente aquellos donde sus respuestas entren en conflicto (ej: si no quiere turnos nocturnos y la oferta lo pide).

Devuelve estrictamente un JSON válido con esta estructura (no agregues texto fuera del JSON):
{{
  "ranking": [
    {{
      "index": <índice original de la lista>,
      "rank": <1 a 5, siendo 1 el mejor>,
      "comparison_reason": "<1-2 oraciones explicando por qué este puesto está por encima o debajo de los otros, haciendo referencia explícita a sus respuestas de afinación si influyeron>"
    }}
  ]
}}

=== PERFIL DEL CANDIDATO ===
{json.dumps(profile, ensure_ascii=False)}

=== CV BASE DEL CANDIDATO ===
{base_cv[:4000]}

=== RESPUESTAS DEL CANDIDATO A LAS PREGUNTAS CLAVE ===
{json.dumps(user_answers, ensure_ascii=False)}

=== CANDIDATOS A COMPARAR ===
{json.dumps(simplified_candidates, ensure_ascii=False)}
"""
    return gemini_json(prompt, temperature=0.4, label='AutoJob · comparar candidatos')


def autojob_final_report(top5_with_reasons, profile, base_cv):
    """
    Genera el reporte final estructurado.
    top5_with_reasons es una lista de dicts {job, evaluation, rank, comparison_reason}.
    """
    prompt = f"""Eres un asesor de carrera experto. Genera un reporte estratégico final resumido y recomendaciones generales para las 5 ofertas de empleo seleccionadas como el Top 5 del candidato.
Enfócate en la estrategia para aplicar a la visa TN-1 para los puestos de este top, qué destacar y cómo abordar a los reclutadores en cada caso.

Devuelve estrictamente un JSON válido con esta estructura:
{{
  "overall_strategy": "<estrategia de postulación general y migratoria en 2-3 párrafos>",
  "recommendations": [
    "<recomendación 1>",
    "<recomendación 2>",
    "<recomendación 3>"
  ]
}}

=== PERFIL DEL CANDIDATO ===
{json.dumps(profile, ensure_ascii=False)}
{json.dumps([{
    "rank": c['rank'],
    "title": c['job']['title'],
    "company": c['job']['company'],
    "location": c['job']['location'],
    "fit_summary": c['evaluation']['fit_summary'],
    "comparison_reason": c['comparison_reason']
} for c in top5_with_reasons], ensure_ascii=False)}
"""
    return gemini_json(prompt, temperature=0.4, label='AutoJob · reporte final')


# Estado global de AutoJob
AUTOJOB_STATUS = {
    'task_id': None,
    'phase': 'idle', # idle, evaluating, waiting_input, comparing, finalizing, done, error
    'progress': 0,
    'total': 0,
    'current_job': '',
    'error': None,
    'result': None,
    'done': True,
    
    # Campos interactivos
    'questions': [],
    'candidates': [],
    'user_answers': {},
    
    # Stats de pre-filtrado
    'discarded_seniority': 0,
    'discarded_location': 0
}

AUTOJOB_LOCK = threading.Lock()


def run_autojob_pipeline(task_id, profile, base_cv, is_mock=False):
    global AUTOJOB_STATUS
    try:
        jobs = db.get_jobs()
        jobs_to_analyze = [j for j in jobs if j.get('status') in ('new', 'liked')]
        if not jobs_to_analyze:
            with AUTOJOB_LOCK:
                AUTOJOB_STATUS.update({
                    'phase': 'done',
                    'error': 'No hay empleos en la base de datos (con estado Nuevo o Me gusta) para analizar.',
                    'done': True
                })
            return

        # 1. Pre-filtrado determinista
        filtered_jobs, disc_seniority, disc_location = pre_filter_jobs(jobs_to_analyze, profile)
        
        with AUTOJOB_LOCK:
            AUTOJOB_STATUS.update({
                'discarded_seniority': disc_seniority,
                'discarded_location': disc_location
            })

        if not filtered_jobs:
            with AUTOJOB_LOCK:
                AUTOJOB_STATUS.update({
                    'phase': 'done',
                    'error': f'Todos los empleos ({len(jobs_to_analyze)}) fueron descartados por filtros previos de experiencia ({disc_seniority}) o ubicación ({disc_location}). Modifica tus ajustes en Perfil.',
                    'done': True
                })
            return

        total_jobs = len(filtered_jobs)
        with AUTOJOB_LOCK:
            AUTOJOB_STATUS.update({
                'phase': 'evaluating',
                'progress': 0,
                'total': total_jobs,
                'current_job': '',
                'error': None,
                'result': None,
                'done': False,
                'questions': [],
                'candidates': [],
                'user_answers': {}
            })

        evaluated = []
        for idx, job in enumerate(filtered_jobs):
            if AUTOJOB_STATUS['task_id'] != task_id:
                return

            job_name = f"{job.get('title')} @ {job.get('company')}"
            with AUTOJOB_LOCK:
                AUTOJOB_STATUS.update({
                    'progress': idx,
                    'current_job': job_name
                })

            if is_mock:
                time.sleep(0.4)
                import random
                score = random.randint(45, 95)
                tn1 = "Raleigh" in job.get('location', '') or "remote" in job.get('location', '').lower()
                eval_res = {
                    'score': score,
                    'fit_summary': f"Este puesto de {job.get('title')} en {job.get('company')} ofrece una buena alineación técnica.",
                    'pros': ["Ubicación favorable", "Requisitos técnicos cubiertos"],
                    'cons': ["Alta competencia esperada"],
                    'migration_analysis': "Soporta visa TN-1 debido a la descripción y requerimientos generales." if tn1 else "Podría requerir patrocinio tradicional H-1B.",
                    'tn1_eligible': tn1,
                    'candidate_questions': [f"¿Qué nivel tienes en la tecnología principal requerida para {job.get('title')}?", "¿Tienes disponibilidad inmediata para reubicarte?"]
                }
            else:
                try:
                    eval_res = autojob_evaluate(job, profile, base_cv)
                except Exception as e:
                    eval_res = {
                        'score': 50,
                        'fit_summary': f"Error al evaluar: {str(e)}",
                        'pros': [],
                        'cons': ["Error de evaluación IA"],
                        'migration_analysis': "No se pudo determinar.",
                        'tn1_eligible': None,
                        'candidate_questions': ["¿Tienes experiencia con los requerimientos técnicos del puesto?"]
                    }
            
            evaluated.append({
                'job': job,
                'evaluation': eval_res
            })

        # Al finalizar la evaluación, recolectamos preguntas de las mejores ofertas
        evaluated.sort(key=lambda x: x['evaluation'].get('score', 0), reverse=True)
        candidates_to_compare = evaluated[:12]

        # Consolidar preguntas a realizar
        if is_mock:
            questions = [
                "¿Tienes experiencia trabajando en turnos rotativos o de noche?",
                "¿Qué nivel de experiencia tienes utilizando frameworks avanzados en producción?",
                "¿Cuentas con pasaporte vigente para viajar de inmediato?"
            ]
        else:
            questions = consolidate_questions(candidates_to_compare)

        with AUTOJOB_LOCK:
            AUTOJOB_STATUS.update({
                'progress': total_jobs,
                'phase': 'waiting_input',
                'current_job': 'Esperando tus respuestas de afinación...',
                'questions': questions,
                'candidates': candidates_to_compare  # Guardar candidatos para Ronda 2
            })

    except Exception as e:
        with AUTOJOB_LOCK:
            AUTOJOB_STATUS.update({
                'phase': 'done',
                'error': f"Error en pipeline (Ronda 1): {str(e)}",
                'done': True
            })


def run_autojob_phase2(task_id, profile, base_cv, is_mock=False):
    global AUTOJOB_STATUS
    try:
        with AUTOJOB_LOCK:
            candidates_to_compare = AUTOJOB_STATUS['candidates']
            user_answers = AUTOJOB_STATUS['user_answers']
            AUTOJOB_STATUS.update({
                'phase': 'comparing',
                'current_job': 'Comparando candidatos usando tus respuestas...'
            })

        if is_mock:
            time.sleep(1.2)
            ranking_res = {
                'ranking': [
                    {'index': i, 'rank': i + 1, 'comparison_reason': f"Ajustado según tu respuesta: '{list(user_answers.values())[0] if user_answers else ''}'."}
                    for i in range(min(5, len(candidates_to_compare)))
                ]
            }
        else:
            try:
                ranking_res = autojob_compare(candidates_to_compare, profile, base_cv, user_answers)
            except Exception as e:
                ranking_res = {
                    'ranking': [
                        {'index': i, 'rank': i + 1, 'comparison_reason': f"Ordenado por evaluación inicial. Error en comparación: {str(e)}"}
                        for i in range(min(5, len(candidates_to_compare)))
                    ]
                }

        top5_list = []
        ranking_map = {item['index']: item for item in ranking_res.get('ranking', [])}
        
        for idx, cand in enumerate(candidates_to_compare):
            if idx in ranking_map:
                rank_info = ranking_map[idx]
                top5_list.append({
                    'rank': rank_info['rank'],
                    'comparison_reason': rank_info['comparison_reason'],
                    'job_id': cand['job']['id'],
                    'title': cand['job']['title'],
                    'company': cand['job']['company'],
                    'location': cand['job']['location'],
                    'link': cand['job']['link'],
                    'score': cand['evaluation']['score'],
                    'fit_summary': cand['evaluation']['fit_summary'],
                    'pros': cand['evaluation']['pros'],
                    'cons': cand['evaluation']['cons'],
                    'migration_analysis': cand['evaluation']['migration_analysis'],
                    'tn1_eligible': cand['evaluation']['tn1_eligible'],
                    'seniority': cand['job'].get('seniority'),
                    'employment_type': cand['job'].get('employment_type')
                })

        top5_list.sort(key=lambda x: x['rank'])
        top5_list = top5_list[:5]

        with AUTOJOB_LOCK:
            AUTOJOB_STATUS.update({
                'phase': 'finalizing',
                'current_job': 'Generando reporte final...'
            })

        if is_mock:
            time.sleep(1.0)
            final_res = {
                'overall_strategy': "Estrategia de postulación adaptada a visa TN-1 en Raleigh.",
                'recommendations': [
                    "Asegura cartas de soporte.",
                    "Destaca habilidades en C++."
                ]
            }
        else:
            try:
                final_res = autojob_final_report(top5_list, profile, base_cv)
            except Exception as e:
                final_res = {
                    'overall_strategy': f"No se pudo generar reporte detallado: {str(e)}",
                    'recommendations': ["Aplica con cuidado.", "Verifica los requisitos de visa directamente."]
                }

        result_data = {
            'top5': top5_list,
            'overall_strategy': final_res.get('overall_strategy', ''),
            'recommendations': final_res.get('recommendations', []),
            'created_at': time.time()
        }

        db.save_autojob_result(result_data)

        with AUTOJOB_LOCK:
            AUTOJOB_STATUS.update({
                'phase': 'done',
                'result': result_data,
                'done': True
            })

    except Exception as e:
        with AUTOJOB_LOCK:
            AUTOJOB_STATUS.update({
                'phase': 'done',
                'error': f"Error en pipeline (Ronda 2): {str(e)}",
                'done': True
            })


# --------------------------------------------------------------------------- #
#  Auto-aplicar (Easy Apply): estado global + pipeline                         #
# --------------------------------------------------------------------------- #
# carpeta visible con un PDF por empleo; el modo demo usa una aparte para no
# tocar jamás los currículums reales del usuario.
GEN_DIR = os.path.join(HERE, 'resumes_mock' if MOCK else 'resumes')


def latex_doc_pdf(docs, kind, lang, dest):
    """Genera el PDF de un documento (resume|cv|cover_letter) con LaTeX a partir de los
    datos estructurados. Devuelve None si no aplica (edición manual en HTML o sin LaTeX)."""
    if not docs or not latex_cv.available():
        return None
    if (docs.get('overrides') or {}).get(f'{kind}_{lang}'):
        return None  # el usuario editó el HTML a mano: se respeta tal cual
    target = docs.get(lang) or docs.get('en') or docs.get('es') or docs
    profile = dict(target.get('profile') or {})
    profile.setdefault('lang', lang)
    if kind == 'cover_letter':
        return latex_cv.cover_pdf(profile, target.get('cover_letter') or {}, dest)
    return latex_cv.resume_pdf(profile, target.get(kind) or {}, dest, one_page=True)  # resume y CV: siempre 1 página

DAILY_LIMIT = 64        # tope de solicitudes que LinkedIn permite por día (24 h)
QUOTA_WINDOW = 24 * 3600

APPLY_STATUS = {
    'task_id': None,
    'phase': 'idle',      # idle | applying | done | error
    'progress': 0,
    'total': 0,
    'current_job': '',
    'results': [],        # [{job_id,title,company,status,review_reason,error}]
    'done': True,
    'error': None,
}
APPLY_LOCK = threading.Lock()


def apply_quota():
    """Estado del cupo diario: usados en las últimas 24 h, restantes y cuándo se libera uno."""
    cutoff = time.time() - QUOTA_WINDOW
    used = db.count_applied_since(cutoff)
    oldest = db.next_applied_expiry(cutoff)
    reset_in = int((oldest + QUOTA_WINDOW) - time.time()) if oldest else 0
    return {'used': used, 'limit': DAILY_LIMIT,
            'remaining': max(0, DAILY_LIMIT - used),
            'reset_in': max(0, reset_in)}


def _numeric_job_id(job):
    """De 'li_123456' o del link saca el id numérico que usa la URL de LinkedIn."""
    jid = str(job.get('id') or '')
    if jid.startswith('li_'):
        return jid[3:]
    m = re.search(r'/jobs/view/(\d+)', job.get('link') or '')
    return m.group(1) if m else jid


def run_apply_pipeline(task_id, job_ids, submit_mode, is_mock=False):
    global APPLY_STATUS
    profile = CONFIG.get('profile', {})
    base_cv = CONFIG.get('base_cv', '')
    allow_submit = submit_mode != 'review'   # 'hybrid'/'auto' pueden enviar; 'review' nunca
    results = []
    try:
        with APPLY_LOCK:
            APPLY_STATUS.update({'phase': 'applying', 'progress': 0, 'total': len(job_ids),
                                 'results': [], 'done': False, 'error': None})
        for idx, job_id in enumerate(job_ids):
            job = db.get_job(job_id)
            if not job:
                continue
            # tope diario de LinkedIn: si vamos a ENVIAR y ya se llegó a 64/24 h, paramos
            if allow_submit:
                q = apply_quota()
                if q['remaining'] <= 0:
                    hrs = round(q['reset_in'] / 3600, 1)
                    for rest_id in job_ids[idx:]:
                        rj = db.get_job(rest_id) or {}
                        results.append({'job_id': rest_id, 'title': rj.get('title'),
                                        'company': rj.get('company'), 'status': 'skipped',
                                        'review_reason': f'Tope diario de LinkedIn alcanzado (64). '
                                                         f'Se libera un cupo en ~{hrs} h.', 'error': ''})
                    with APPLY_LOCK:
                        APPLY_STATUS.update({'error': f'Alcanzaste el tope diario de LinkedIn '
                                                      f'(64 solicitudes/24 h). Reintenta en ~{hrs} h.',
                                             'results': list(results)})
                    break
            row = db.get_application(job_id) or {}
            resume_pdf = row.get('resume_pdf')
            with APPLY_LOCK:
                APPLY_STATUS.update({'progress': idx,
                                     'current_job': f"{job.get('title')} @ {job.get('company')}"})
            res_item = {'job_id': job_id, 'title': job.get('title'),
                        'company': job.get('company'), 'status': 'error',
                        'review_reason': '', 'error': ''}
            try:
                if is_mock:
                    res = _mock_apply(job, profile, base_cv, allow_submit)
                else:
                    if not resume_pdf or not os.path.isfile(resume_pdf):
                        raise RuntimeError('El currículum no se preparó (PDF no encontrado). Prepara primero.')
                    answer_memory = db.get_answer_memory()
                    res = WORKER.call('apply_job', timeout=360,
                                      jid=_numeric_job_id(job), resume_pdf=resume_pdf,
                                      cover_pdf=row.get('cover_pdf'),
                                      cover_text=row.get('cover_text'),
                                      profile=profile, base_cv=base_cv,
                                      answer_memory=answer_memory, is_mock=False,
                                      allow_submit=allow_submit,
                                      own_tab=(submit_mode == 'review'),
                                      job_meta={'title': job.get('title'),
                                                'company': job.get('company'),
                                                'location': job.get('location')})
                    # persistimos lo aprendido (respuestas de alta confianza)
                    for k, v in answer_memory.items():
                        db.save_answer_memory(k, v)
                res_item['status'] = res.get('status', 'error')
                res_item['review_reason'] = res.get('review_reason', '')
                res_item['error'] = res.get('error', '')
                db.save_application(job_id, res.get('status', 'error'),
                                    detail={'answers': res.get('answers', []),
                                            'log': res.get('log', []),
                                            'error': res.get('error', ''),
                                            'review_reason': res.get('review_reason', ''),
                                            'submitted_at': time.time() if res.get('status') == 'applied' else None})
            except Exception as e:
                res_item['status'] = 'error'
                res_item['error'] = str(e)
                db.save_application(job_id, 'error', detail={'error': str(e)})
            results.append(res_item)
            with APPLY_LOCK:
                APPLY_STATUS.update({'progress': idx + 1, 'results': list(results)})
            time.sleep(1.0)  # respiro entre solicitudes
        with APPLY_LOCK:
            APPLY_STATUS.update({'phase': 'done', 'done': True, 'current_job': '',
                                 'results': list(results)})
    except Exception as e:
        with APPLY_LOCK:
            APPLY_STATUS.update({'phase': 'error', 'done': True,
                                 'error': str(e), 'results': list(results)})


def _mock_apply(job, profile, base_cv, allow_submit):
    """Simula el resultado de aplicar (modo demo, sin navegador)."""
    time.sleep(1.2)
    low = (job.get('description') or '').lower()
    sensitive = any(w in low for w in ('citizen', 'clearance', 'relocat', 'salary'))
    if sensitive or not allow_submit:
        return {'status': 'needs_review', 'answers': [], 'log': ['(demo) formulario rellenado'],
                'review_reason': '(demo) pregunta sensible detectada'}
    return {'status': 'applied', 'answers': [], 'log': ['(demo) solicitud enviada']}


# --------------------------------------------------------------------------- #
#  Worker de LinkedIn (Playwright en su propio hilo)                          #
# --------------------------------------------------------------------------- #
class LinkedInWorker(threading.Thread):
    """Posee el navegador Playwright. Toda operación pasa por su cola (Playwright
    sync API está atada al hilo que la creó)."""

    daemon = True

    def __init__(self):
        super().__init__()
        self.cmd_q = queue.Queue()
        self.ctx = None
        self.page = None
        self._pw = None
        self.error = None
        self._ctx_closed = False

    # API pública (llamada desde los handlers HTTP) ------------------------- #
    def call(self, action, timeout=180, **kwargs):
        result = {'event': threading.Event()}
        self.cmd_q.put((action, kwargs, result))
        if not result['event'].wait(timeout=timeout):
            raise RuntimeError('La operación de LinkedIn tardó demasiado y se canceló. '
                               'Intenta con menos ofertas o revisa el navegador.')
        if 'error' in result:
            raise RuntimeError(result['error'])
        return result.get('value')

    # bucle del hilo ------------------------------------------------------- #
    def run(self):
        while True:
            action, kwargs, result = self.cmd_q.get()
            try:
                result['value'] = getattr(self, f'_{action}')(**kwargs)
            except Exception as e:
                result['error'] = str(e)
            finally:
                result['event'].set()

    def _browser_alive(self):
        """True si el Chrome persistente sigue abierto y usable."""
        if self.ctx is None or self._ctx_closed:
            return False
        try:
            pages = self.ctx.pages          # falla si el contexto está cerrado
            if not pages:
                return False
            # una página viva; si la actual se cerró, toma otra
            if self.page is None or self.page.is_closed():
                self.page = next((p for p in pages if not p.is_closed()), None) \
                    or self.ctx.new_page()
            return True
        except Exception:
            return False

    def _teardown_browser(self):
        for closer in (lambda: self.page and self.page.close(),
                       lambda: self.ctx and self.ctx.close(),
                       lambda: self._pw and self._pw.stop()):
            try:
                closer()
            except Exception:
                pass
        self.page = self.ctx = self._pw = None
        self._ctx_closed = False

    def _ensure_browser(self):
        # Si ya hay un Chrome vivo, listo. Si el usuario lo cerró, lo relanzamos
        # solo (sin reiniciar el servidor) reutilizando la sesión persistente.
        if self._browser_alive():
            return
        if self.ctx is not None or self._pw is not None:
            self._teardown_browser()   # limpia restos de un Chrome cerrado
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            raise RuntimeError(
                'Playwright no está instalado. Ejecuta:\n'
                '  pip install playwright\n'
                '  playwright install chromium')
        self._pw = sync_playwright().start()
        self.ctx = self._pw.chromium.launch_persistent_context(
            USER_DATA_DIR,
            headless=False,
            viewport={'width': 1280, 'height': 900},
            args=['--disable-blink-features=AutomationControlled'],
        )
        self._ctx_closed = False
        try:
            self.ctx.on('close', lambda *_: setattr(self, '_ctx_closed', True))
        except Exception:
            pass
        self.page = self.ctx.pages[0] if self.ctx.pages else self.ctx.new_page()

    # acciones ------------------------------------------------------------- #
    def _open_login(self):
        self._ensure_browser()
        # No forzamos /login si ya hay sesión: solo llevamos el navegador a un
        # punto útil. Si no hay sesión, LinkedIn muestra el login; si la hay,
        # muestra los empleos. Nunca escribimos credenciales.
        target = ('https://www.linkedin.com/jobs/'
                  if self._is_logged_in() else 'https://www.linkedin.com/login')
        try:
            self.page.goto(target, wait_until='domcontentloaded', timeout=30000)
        except Exception:
            pass
        return {'launched': True, 'logged_in': self._is_logged_in()}

    def _status(self):
        # no fuerza la apertura de Chrome; solo informa si sigue vivo
        if not self._browser_alive():
            return {'launched': False, 'logged_in': False}
        return {
            'launched': True,
            'logged_in': self._is_logged_in(),
            'url': self.page.url if self.page else None,
            'title': self.page.title() if self.page else None,
        }

    def _is_logged_in(self):
        """Comprueba la sesión leyendo la cookie 'li_at' SIN navegar la página,
        para no interrumpir al usuario mientras inicia sesión."""
        if not self.ctx:
            return False
        try:
            for c in self.ctx.cookies('https://www.linkedin.com'):
                if c.get('name') == 'li_at' and c.get('value'):
                    return True
        except Exception:
            pass
        return False

    def _require_login(self):
        self._ensure_browser()
        if not self._is_logged_in():
            raise RuntimeError('No has iniciado sesión en LinkedIn. Pulsa "Conectar LinkedIn" '
                               'y entra a tu cuenta en la ventana que se abre.')

    def _search(self, keywords, location, limit):
        self._require_login()
        return self._scrape_query(keywords, location, limit, seen_ids=set(), query_label=keywords)

    def _search_batch(self, queries, per_query_limit):
        """Busca en LinkedIn TODAS las sugerencias, una tras otra, deduplicando.
        Cada empleo se guarda en la BD en cuanto se scrapea, así que si el proceso
        se interrumpe o se agota el tiempo, lo obtenido hasta ese punto NO se pierde."""
        self._require_login()
        all_jobs, seen = [], set()
        for q in queries:
            kw = (q.get('keywords') or '').strip()
            if not kw:
                continue
            loc = (q.get('location') or '').strip()
            try:
                jobs = self._scrape_query(kw, loc, per_query_limit, seen_ids=seen, query_label=kw,
                                          extra=q.get('extra'))
            except Exception:
                jobs = []
            all_jobs.extend(jobs)
            time.sleep(1.0)  # respiro entre búsquedas
        return all_jobs

    def _scrape_query(self, keywords, location, limit, seen_ids, query_label='', extra=None):
        # extra: filtros adicionales de LinkedIn (p. ej. f_E=nivel, f_TPR=fecha, f_WT=remoto)
        params = urllib.parse.urlencode({
            'keywords': keywords,
            'location': location,
            'f_AL': 'true',      # solo Easy Apply / Solicitud sencilla
            'sortBy': 'R',       # más relevantes
            **(extra or {}),
        })
        self.page.goto(f'https://www.linkedin.com/jobs/search/?{params}', wait_until='domcontentloaded')
        time.sleep(2.5)

        # cargar más resultados haciendo scroll de la lista
        job_ids = []
        url_match = re.search(r'currentJobId=(\d+)', self.page.url or '')
        if url_match and url_match.group(1) not in job_ids and url_match.group(1) not in seen_ids:
            job_ids.append(url_match.group(1))

        for _ in range(6):
            hrefs = self.page.eval_on_selector_all(
                "a[href*='/jobs/view/'], a[href*='currentJobId='], [data-occludable-job-id], [data-job-id]",
                """els => els.map(e => {
                    return e.getAttribute('href') || e.getAttribute('data-occludable-job-id') || e.getAttribute('data-job-id') || '';
                })""")
            for h in hrefs:
                m = (re.search(r'/jobs/view/(\d+)', h or '')
                     or re.search(r'currentJobId=(\d+)', h or '')
                     or re.search(r'^(\d{7,})$', (h or '').strip()))
                if m and m.group(1) not in job_ids and m.group(1) not in seen_ids:
                    job_ids.append(m.group(1))
            if len(job_ids) >= limit:
                break
            try:
                self.page.mouse.wheel(0, 2200)
            except Exception:
                pass
            time.sleep(1.2)

        saved = []
        for jid in job_ids[:limit]:
            seen_ids.add(jid)
            try:
                job = self._scrape_detail(jid)
            except Exception:
                continue
            job['query'] = query_label
            # Se guarda AQUÍ, inmediatamente, empleo por empleo.
            new = db.upsert_jobs([job])
            if new:
                saved.append(new[0])
            else:
                existing = db.get_job('li_' + jid)
                if existing:
                    saved.append(existing)
            time.sleep(0.8)  # cortesía anti-bloqueo
        return saved

    def _scrape_detail(self, jid):
        data = None
        # 1) endpoint público de la oferta: devuelve ubicación + descripción completas
        #    de forma fiable (usa la sesión del navegador, sin depender del DOM del SPA).
        try:
            resp = self.ctx.request.get(
                f'https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{jid}',
                timeout=20000)
            if resp.ok:
                data = parse_job_html(jid, resp.text())
        except Exception:
            data = None

        # 2) fallback: si faltó la descripción, raspa la página del SPA (ya con sesión).
        if not data or not data.get('description'):
            try:
                self.page.goto(f'https://www.linkedin.com/jobs/view/{jid}/', wait_until='domcontentloaded')
                self.page.wait_for_timeout(1500)
                try:
                    self.page.click("button:has-text('ver más'), button:has-text('see more'), "
                                    "button.jobs-description__footer-button", timeout=1200)
                except Exception:
                    pass
                spa = self.page.evaluate(JS_EXTRACT)
                data = data or {}
                for k in ('title', 'company', 'location', 'description'):
                    if not data.get(k) and spa.get(k):
                        data[k] = spa[k]
            except Exception:
                pass

        if not data:
            data = {'title': 'Sin título', 'company': 'Empresa', 'location': '', 'description': ''}
        data['id'] = 'li_' + jid
        data['link'] = f'https://www.linkedin.com/jobs/view/{jid}/'
        data.setdefault('easy_apply', True)
        return data

    def _scrape_one(self, jid):
        """Scrapea UN empleo por su id (para 'Agregar manual' pegando el link)."""
        self._ensure_browser()
        job = self._scrape_detail(jid)
        job['query'] = 'manual'
        return job

    # ---------------------------------------------------------------- #
    #  Generación de PDF (usa el mismo Chromium vía CDP, funciona headful) #
    # ---------------------------------------------------------------- #
    def _render_pdf(self, html_doc, out_path):
        self._ensure_browser()
        page = self.ctx.new_page()
        try:
            page.set_content(html_doc, wait_until='load')
            page.wait_for_timeout(400)  # deja que el fitScript ajuste el zoom
            cdp = self.ctx.new_cdp_session(page)
            res = cdp.send('Page.printToPDF', {
                'printBackground': True,
                'paperWidth': 8.5, 'paperHeight': 11,
                'marginTop': 0.5, 'marginBottom': 0.5,
                'marginLeft': 0.5, 'marginRight': 0.5,
                'preferCSSPageSize': True,
            })
            with open(out_path, 'wb') as f:
                f.write(base64.b64decode(res['data']))
        finally:
            try:
                page.close()
            except Exception:
                pass
        return out_path

    # ---------------------------------------------------------------- #
    #  Easy Apply: rellena y (opcionalmente) envía la solicitud        #
    # ---------------------------------------------------------------- #
    def _load_job_page(self, jid, log):
        """Navega a la oferta y espera a que su contenido (no solo la barra global)
        esté en el DOM. Si no aparece, recarga una vez. Devuelve True si cargó."""
        page = self.page
        ready_sel = (".jobs-unified-top-card, .job-details-jobs-unified-top-card, "
                     ".jobs-apply-button, .jobs-details__main-content, .job-view-layout, "
                     ".jobs-save-button, button[aria-label*='Guardar empleo' i], "
                     "button[aria-label*='Save' i][aria-label*='job' i], "
                     "button[aria-label*='Solicitud sencilla' i], button[aria-label*='Easy Apply' i]")
        for attempt in range(2):
            try:
                page.goto(f'https://www.linkedin.com/jobs/view/{jid}/',
                          wait_until='domcontentloaded', timeout=30000)
            except Exception:
                pass
            try:
                page.wait_for_selector(ready_sel, timeout=15000)
                page.wait_for_timeout(1000)
                return True
            except Exception:
                # a veces basta con dar tiempo/scroll al SPA
                try:
                    page.mouse.wheel(0, 800)
                except Exception:
                    pass
                page.wait_for_timeout(1500)
                if page.query_selector(ready_sel):
                    return True
            if attempt == 0:
                log.append('El empleo no cargó a la primera; recargando...')
        return bool(page.query_selector(ready_sel))

    def _apply_failure_reason(self, ready):
        """Construye un mensaje de error con contexto (URL, título, clicables vistos)."""
        page = self.page
        parts = ['No pude abrir el formulario de Solicitud sencilla (Easy Apply).']
        try:
            url = page.url
            title = page.title()
        except Exception:
            url = title = ''
        if not ready:
            parts.append('La página del empleo no llegó a cargar su contenido '
                         '(posible oferta expirada/retirada, sesión de LinkedIn distinta '
                         'en la ventana de la automatización, o carga muy lenta).')
        else:
            parts.append('Cargó la oferta pero no hallé el botón: puede ser solicitud en '
                         'sitio EXTERNO ("Aplicar") o que ya hayas aplicado.')
        try:
            cands = page.evaluate(JS_COLLECT_CLICKABLES) or []
            vis = [c for c in cands if c.get('visible')]
            labels = [(c.get('aria') or c.get('text')) for c in vis][:12]
            if labels:
                parts.append('Clicables visibles: ' + ' · '.join(l for l in labels if l))
        except Exception:
            pass
        if url:
            parts.append(f'URL: {url}')
        if title:
            parts.append(f'Título: {title}')
        return ' '.join(parts)

    def _find_easy_apply(self):
        """Localiza el botón de Easy Apply / Solicitud sencilla probando varias
        estrategias. LinkedIn es un SPA: el botón aparece tarde, así que esperamos
        de forma activa hasta ~14s y hacemos scroll para forzar su render.
        Como último recurso, un modelo rápido de IA elige el botón entre los clicables."""
        page = self.page
        selectors = [
            "button.jobs-apply-button",
            ".jobs-apply-button--top-card button",
            "button[aria-label*='Easy Apply' i]",
            "button[aria-label*='Solicitud sencilla' i]",
        ]
        deadline = time.time() + 14
        while time.time() < deadline:
            for sel in selectors:
                try:
                    loc = page.locator(sel).first
                    if loc.count() and loc.is_visible():
                        return loc
                except Exception:
                    continue
            # por rol/texto (case-insensitive, coincidencia parcial)
            try:
                loc = page.get_by_role(
                    "button", name=re.compile(r"easy apply|solicitud sencilla", re.I)).first
                if loc.count() and loc.is_visible():
                    return loc
            except Exception:
                pass
            # empujar el render del top-card
            try:
                page.mouse.wheel(0, 600)
            except Exception:
                pass
            page.wait_for_timeout(700)

        # Fallback con IA: recoge los clicables y deja que un modelo rápido elija
        try:
            cands = page.evaluate(JS_COLLECT_CLICKABLES) or []
            visible = [c for c in cands if c.get('visible')]
            if visible:
                pick = ai_pick_apply_button(visible)
                if pick:
                    loc = page.locator(f"[data-jswbtn='{pick}']").first
                    if loc.count():
                        return loc
        except Exception:
            pass
        return None

    def _click_easy_apply(self):
        btn = self._find_easy_apply()
        if not btn:
            return False
        try:
            btn.scroll_into_view_if_needed(timeout=3000)
        except Exception:
            pass
        for attempt in ('normal', 'force', 'js'):
            try:
                if attempt == 'js':
                    btn.evaluate("el => el.click()")
                else:
                    btn.click(timeout=4000, force=(attempt == 'force'))
                # confirmar que abrió el modal
                try:
                    self.page.wait_for_selector(
                        "div.jobs-easy-apply-modal, div[role='dialog']", timeout=6000)
                    return True
                except Exception:
                    continue
            except Exception:
                continue
        return False

    def _modal(self):
        for sel in ("div.jobs-easy-apply-modal", "div[role='dialog']", ".artdeco-modal"):
            m = self.page.query_selector(sel)
            if m:
                return m
        return None

    def _easyapply_modal_open(self):
        """True si el panel flotante de Solicitud sencilla está visible."""
        try:
            m = self.page.query_selector(
                "div.jobs-easy-apply-modal, div[role='dialog'] .jobs-easy-apply-content, "
                "div[data-test-modal] .jobs-easy-apply-content")
            if m and m.is_visible():
                return True
            # respaldo: un diálogo con el botón Siguiente/Enviar del asistente
            dlg = self.page.query_selector("div[role='dialog']")
            if dlg and dlg.is_visible():
                txt = (dlg.inner_text() or '').lower()
                if any(w in txt for w in ('siguiente', 'next', 'revisar', 'review',
                                          'enviar solicitud', 'submit application')):
                    return True
        except Exception:
            pass
        return False

    def _open_apply_modal(self, jid, log):
        """Abre el asistente de Solicitud sencilla yendo directo a <oferta>/apply/
        y esperando a que aparezca el panel flotante (unos segundos)."""
        page = self.page
        try:
            page.goto(f'https://www.linkedin.com/jobs/view/{jid}/apply/',
                      wait_until='domcontentloaded', timeout=30000)
        except Exception:
            pass
        for _ in range(14):  # ~14s esperando el panel
            if self._easyapply_modal_open():
                log.append('Panel de Solicitud sencilla abierto (vía /apply).')
                return True
            page.wait_for_timeout(1000)
        return False

    def _maybe_upload_resume(self, resume_pdf, log):
        """Sube el PDF correcto de ESTE empleo en el paso de currículum.
        Si el selector de archivo no está visible, pulsa "Cargar currículum"
        para revelarlo. Devuelve True si subió el archivo."""
        if not resume_pdf or not os.path.isfile(resume_pdf):
            return False
        page = self.page
        # excluye un posible input ya marcado como de cover letter
        file_sel = ("div[role='dialog'] input[type='file']:not([data-jswcover='1']), "
                    ".jobs-easy-apply-modal input[type='file']:not([data-jswcover='1'])")
        try:
            inp = page.query_selector(file_sel)
            if not inp:
                # revelar el input pulsando el botón de cargar/subir currículum
                for t in ('Cargar curr', 'Subir curr', 'Cargar CV', 'Subir CV',
                          'Upload resume', 'Upload cv'):
                    b = page.query_selector(
                        f"div[role='dialog'] button:has-text('{t}'), "
                        f".jobs-easy-apply-modal button:has-text('{t}')")
                    if b and b.is_visible():
                        b.click()
                        page.wait_for_timeout(700)
                        break
                inp = page.query_selector(file_sel)
            if not inp:
                return False
            inp.set_input_files(resume_pdf)
            page.wait_for_timeout(1800)
            log.append(f'Currículum seleccionado: {os.path.basename(resume_pdf)}')
            return True
        except Exception as e:
            log.append(f'No pude subir el currículum: {e}')
            return False

    def _maybe_upload_cover(self, cover_pdf, log):
        """Adjunta el PDF del cover letter SOLO si hay un campo de archivo cuyo
        contexto menciona 'cover letter / carta de presentación'. Devuelve True si lo adjuntó."""
        if not cover_pdf or not os.path.isfile(cover_pdf):
            return False
        page = self.page
        try:
            found = page.evaluate(JS_TAG_COVER_FILE)
            if not found:
                return False
            inp = page.query_selector("input[data-jswcover='1']")
            if not inp:
                return False
            inp.set_input_files(cover_pdf)
            page.wait_for_timeout(1500)
            log.append(f'Cover letter adjuntado: {os.path.basename(cover_pdf)}')
            return True
        except Exception as e:
            log.append(f'No pude adjuntar el cover letter: {e}')
            return False

    def _apply_job(self, jid, resume_pdf, profile, base_cv, answer_memory,
                   is_mock, allow_submit, job_meta, own_tab=False,
                   cover_pdf=None, cover_text=None):
        """Automatiza el asistente de Easy Apply. Si own_tab=True (modo "solo
        preparar"), abre una PESTAÑA nueva para este empleo y la deja abierta con
        el formulario lleno para que el usuario lo revise; si no, reutiliza la
        pestaña principal y cierra cada solicitud. Devuelve un dict con el resultado."""
        self._require_login()
        main_page = self.page
        new_tab = None
        if own_tab:
            try:
                new_tab = self.ctx.new_page()
                self.page = new_tab
            except Exception:
                own_tab = False  # sin poder abrir pestaña, seguimos en la principal
        try:
            result = self._run_apply_flow(jid, resume_pdf, profile, base_cv,
                                          answer_memory, is_mock, allow_submit,
                                          job_meta, own_tab, cover_pdf, cover_text)
        except Exception as e:
            result = {'status': 'error', 'log': [], 'error': str(e)}
        finally:
            self.page = main_page  # restaurar la pestaña principal
        # si abrimos pestaña pero NO quedó lista para revisión, la cerramos
        if new_tab is not None and not result.get('kept_open'):
            try:
                new_tab.close()
            except Exception:
                pass
        return result

    def _finish_prepared(self, own_tab, log, all_answers, all_questions, reason):
        """Cierre del flujo cuando NO se envía. En own_tab deja la ventana abierta
        para revisión; si no, guarda la solicitud como borrador y la cierra."""
        if own_tab:
            try:
                self.page.bring_to_front()
            except Exception:
                pass
            log.append('Ventana dejada abierta para tu revisión.')
            return {'status': 'needs_review', 'log': log, 'answers': all_answers,
                    'questions': all_questions, 'kept_open': True,
                    'review_reason': reason}
        self._close_and_save(log)
        return {'status': 'needs_review', 'log': log, 'answers': all_answers,
                'questions': all_questions, 'review_reason': reason}

    def _run_apply_flow(self, jid, resume_pdf, profile, base_cv, answer_memory,
                        is_mock, allow_submit, job_meta, own_tab,
                        cover_pdf=None, cover_text=None):
        log = []
        page = self.page

        # CAMINO PRINCIPAL: ir directo a <oferta>/apply/ y esperar el panel flotante.
        if not self._open_apply_modal(jid, log):
            # RESPALDO: cargar la oferta y pulsar el botón (por si /apply no abre el panel)
            ready = self._load_job_page(jid, log)
            if ready:
                try:
                    if page.get_by_text(re.compile(r"\b(applied|solicitud enviada)\b", re.I)).first.count():
                        if not self._find_easy_apply():
                            return {'status': 'skipped', 'log': log,
                                    'review_reason': 'Ya habías aplicado a esta oferta.'}
                except Exception:
                    pass
            if not self._click_easy_apply():
                return {'status': 'error', 'log': log,
                        'error': self._apply_failure_reason(ready)}
        page.wait_for_timeout(1600)

        all_answers, all_questions, review_reasons = [], [], []
        needs_review = False
        uploaded = False
        answerer = mock_answer_questions if is_mock else answer_application_questions

        cover_done = False
        for _step in range(14):  # tope de pasos del asistente
            # cover primero: etiqueta su input para que el del currículum no lo tome
            if cover_pdf and not cover_done:
                if self._maybe_upload_cover(cover_pdf, log):
                    cover_done = True
            if not uploaded:
                uploaded = self._maybe_upload_resume(resume_pdf, log)
            try:
                fields = page.evaluate(JS_EASYAPPLY_FIELDS) or []
            except Exception:
                fields = []

            # 1) resolver respuestas (memoria primero, luego IA para lo nuevo)
            answers_by_id = {}
            to_ask = []
            for f in fields:
                norm = _norm_question(f.get('label'))
                if _is_cover_letter_field(f.get('label')) and f.get('type') in ('textarea', 'text'):
                    # campo de texto de cover letter → pegamos el cover generado
                    if cover_text:
                        answers_by_id[f['id']] = cover_text
                        cover_done = True
                        log.append('Cover letter pegado en el campo de texto.')
                    else:
                        to_ask.append(f)
                elif norm and norm in answer_memory and answer_memory[norm]:
                    answers_by_id[f['id']] = answer_memory[norm]
                elif f.get('value') and not re.match(r'(select|selecciona)', f['value'], re.I):
                    answers_by_id[f['id']] = f['value']  # ya venía prellenado por LinkedIn
                else:
                    to_ask.append(f)

            if to_ask:
                qs = [{'key': str(f['id']), 'label': f.get('label') or '',
                       'type': f.get('type'), 'options': f.get('options') or []} for f in to_ask]
                try:
                    res = answerer(qs, job_meta, profile, base_cv)
                except Exception as e:
                    res = {'answers': []}
                    log.append(f'IA no pudo responder este paso: {e}')
                for a in (res.get('answers') or []):
                    fid = a.get('key')
                    ans = str(a.get('answer', '')).strip()
                    answers_by_id[fid] = ans
                    fld = next((f for f in to_ask if str(f['id']) == str(fid)), None)
                    label = fld.get('label') if fld else ''
                    all_answers.append({'label': label, 'answer': ans,
                                        'confidence': a.get('confidence'),
                                        'sensitive': bool(a.get('sensitive'))})
                    norm = _norm_question(label)
                    if norm and a.get('confidence') == 'high' and not a.get('sensitive'):
                        answer_memory[norm] = ans  # solo memorizamos lo seguro
                    if a.get('sensitive') or a.get('confidence') == 'low':
                        needs_review = True
                        review_reasons.append(label or 'pregunta sensible')

            all_questions.extend([f.get('label') for f in fields if f.get('label')])

            # 2) rellenar el DOM (setters nativos para que React lo registre)
            if answers_by_id:
                try:
                    page.evaluate(JS_EASYAPPLY_FILL, answers_by_id)
                    page.wait_for_timeout(500)
                except Exception as e:
                    log.append(f'Error rellenando el formulario: {e}')

            # 3) decidir el botón de acción del paso
            kind = self._easyapply_action_kind()
            if kind == 'submit':
                if allow_submit and not needs_review:
                    self._uncheck_follow()
                    ok = self._click_by_text(["Submit application", "Enviar solicitud", "Enviar"])
                    page.wait_for_timeout(1800)
                    self._dismiss_after_submit()
                    if ok:
                        return {'status': 'applied', 'log': log, 'answers': all_answers,
                                'questions': all_questions}
                    return {'status': 'error', 'log': log, 'error': 'No pude pulsar Enviar.',
                            'answers': all_answers, 'questions': all_questions}
                # no se envía (modo revisión o respuestas dudosas)
                reason = ('Lista para tu revisión (ventana abierta).' if own_tab
                          else ('Requiere tu revisión: ' + ', '.join(dict.fromkeys(review_reasons))
                                if review_reasons else 'Requiere tu revisión antes de enviar.'))
                return self._finish_prepared(own_tab, log, all_answers, all_questions, reason)
            elif kind in ('next', 'review'):
                if not self._click_by_text(["Review", "Revisar", "Next", "Siguiente", "Continue", "Continuar"]):
                    reason = ('Rellenada; revísala en la ventana abierta.' if own_tab
                              else 'No pude avanzar el asistente.')
                    return self._finish_prepared(own_tab, log, all_answers, all_questions, reason)
                page.wait_for_timeout(1300)
            else:
                reason = ('Rellenada; revísala en la ventana abierta.' if own_tab
                          else 'El asistente pidió un paso que no pude completar automáticamente.')
                return self._finish_prepared(own_tab, log, all_answers, all_questions, reason)

        return self._finish_prepared(own_tab, log, all_answers, all_questions,
                                     'La solicitud tenía muchos pasos; revísala en la ventana abierta.'
                                     if own_tab else 'La solicitud tenía demasiados pasos.')

    def _easyapply_action_kind(self):
        """Devuelve 'submit' | 'review' | 'next' | '' según los botones visibles del modal."""
        try:
            labels = self.page.evaluate(JS_EASYAPPLY_BUTTONS) or []
        except Exception:
            labels = []
        low = ' '.join(labels).lower()
        if 'submit application' in low or 'enviar solicitud' in low or low.strip() in ('enviar', 'submit'):
            return 'submit'
        if 'review' in low or 'revisar' in low:
            return 'review'
        if 'next' in low or 'siguiente' in low or 'continue' in low or 'continuar' in low:
            return 'next'
        return ''

    def _click_by_text(self, texts):
        for t in texts:
            try:
                btn = self.page.query_selector(
                    f"div[role='dialog'] button:has-text('{t}'), .jobs-easy-apply-modal button:has-text('{t}')")
                if btn and btn.is_visible() and btn.is_enabled():
                    btn.click()
                    return True
            except Exception:
                continue
        return False

    def _uncheck_follow(self):
        try:
            cb = self.page.query_selector("div[role='dialog'] label:has-text('Follow') input[type='checkbox'], "
                                          "div[role='dialog'] label:has-text('Seguir') input[type='checkbox']")
            if cb and cb.is_checked():
                cb.uncheck()
        except Exception:
            pass

    def _dismiss_after_submit(self):
        # cierra el diálogo de confirmación ("Solicitud enviada")
        for t in ["Done", "Listo", "Aceptar", "Close", "Cerrar"]:
            if self._click_by_text([t]):
                break
        try:
            self.page.keyboard.press('Escape')
        except Exception:
            pass
        self.page.wait_for_timeout(600)

    def _close_and_save(self, log):
        """Cierra el modal dejando la solicitud guardada como borrador en LinkedIn."""
        try:
            btn = self.page.query_selector("div[role='dialog'] button[aria-label*='Dismiss'], "
                                           "div[role='dialog'] button[aria-label*='Cerrar'], "
                                           "div[role='dialog'] button[aria-label*='cancel']")
            if btn:
                btn.click()
                self.page.wait_for_timeout(700)
        except Exception:
            pass
        # LinkedIn pregunta si guardar el borrador → "Save"
        if not self._click_by_text(["Save", "Guardar"]):
            try:
                self.page.keyboard.press('Escape')
            except Exception:
                pass
        self.page.wait_for_timeout(600)
        log.append('Solicitud guardada como borrador para revisión.')


def _norm_question(label):
    if not label:
        return ''
    s = re.sub(r'\s+', ' ', str(label)).strip().lower()
    s = re.sub(r'[\*\:\?\.•]+$', '', s).strip()
    return s[:160]


_COVER_KEYWORDS = ('cover letter', 'carta de presentaci', 'carta de motivaci',
                   'motivation letter', 'carta de present', 'covering letter')


def _is_cover_letter_field(label):
    l = (label or '').lower()
    return any(k in l for k in _COVER_KEYWORDS)


def html_to_text(markup):
    """Convierte el HTML de la descripción en texto legible conservando viñetas y saltos."""
    s = re.sub(r'(?i)<br\s*/?>', '\n', markup)
    s = re.sub(r'(?i)<li[^>]*>', '\n• ', s)
    s = re.sub(r'(?i)</(p|ul|ol|div|h[1-6])>', '\n', s)
    s = re.sub(r'<[^>]+>', '', s)
    s = html.unescape(s)
    s = re.sub(r'[ \t]+\n', '\n', s)
    s = re.sub(r'\n{3,}', '\n\n', s)
    return s.strip()


def parse_job_html(jid, page):
    """Extrae título, empresa, ubicación, descripción y criterios del HTML público
    del endpoint jobPosting de LinkedIn (mucho más estable que el DOM del SPA)."""
    def find(pat):
        m = re.search(pat, page, re.S)
        return html.unescape(re.sub(r'<[^>]+>', '', m.group(1))).strip() if m else ''

    title = find(r'topcard__title[^>]*>(.*?)</h2>') or find(r'topcard__title[^>]*>(.*?)<')
    company = find(r'topcard__org-name-link[^>]*>(.*?)</a>') or find(r'topcard__flavor[^"]*"[^>]*>(.*?)<')
    location = find(r'topcard__flavor--bullet[^>]*>(.*?)</span>')

    m = re.search(r'show-more-less-html__markup[^"]*"[^>]*>(.*?)</div>', page, re.S)
    desc = html_to_text(m.group(1)) if m else ''

    crit = re.findall(
        r'job-criteria-subheader[^>]*>(.*?)</h3>.*?job-criteria-text[^>]*>(.*?)</span>',
        page, re.S)
    crit = [(html.unescape(re.sub(r'<[^>]+>', '', a)).strip(),
             html.unescape(re.sub(r'<[^>]+>', '', c)).strip()) for a, c in crit]
    seniority = employment = ''
    for label, val in crit:
        low = label.lower()
        if 'senior' in low:
            seniority = val
        elif 'employment' in low or 'empleo' in low:
            employment = val

    full = desc
    if desc and crit:
        full = desc + '\n\n— Detalles —\n' + '\n'.join(f'{a}: {c}' for a, c in crit)

    return {
        'title': title or 'Sin título',
        'company': company or 'Empresa',
        'location': location,
        'description': full[:8000],
        'seniority': seniority,
        'employment_type': employment,
    }


# JS que corre dentro de la página de la oferta para extraer campos con selectores tolerantes.
JS_EXTRACT = r"""
() => {
  const pick = (sels) => {
    for (const s of sels) {
      const el = document.querySelector(s);
      if (el && el.innerText && el.innerText.trim()) return el.innerText.trim();
    }
    return '';
  };
  const title = pick([
    '.job-details-jobs-unified-top-card__job-title',
    '.jobs-unified-top-card__job-title',
    '.top-card-layout__title', 'h1']) || (document.title || '').split(' | ')[0];
  const company = pick([
    '.job-details-jobs-unified-top-card__company-name',
    '.jobs-unified-top-card__company-name',
    '.topcard__org-name-link', 'a[href*="/company/"]']);
  const loc = pick([
    '.job-details-jobs-unified-top-card__primary-description-container',
    '.jobs-unified-top-card__bullet',
    '.topcard__flavor--bullet']);
  const desc = pick([
    '#job-details', '.jobs-description__content',
    '.jobs-description-content__text', '.show-more-less-html__markup',
    'article']);
  const bodyText = document.body ? document.body.innerText : '';
  const easy = /easy apply|solicitud sencilla/i.test(bodyText);
  return {
    title: title || 'Sin título',
    company: company || 'Empresa',
    location: (loc || '').split('\n')[0].slice(0, 120),
    description: (desc || '').slice(0, 8000),
    easy_apply: easy,
  };
}
"""


# --- Easy Apply: extracción/relleno de campos del modal (se ejecutan en la página) --- #
JS_EASYAPPLY_MODAL = "document.querySelector(\"div.jobs-easy-apply-modal, div[role='dialog'], .artdeco-modal\")"

JS_EASYAPPLY_FIELDS = r"""
() => {
  const modal = """ + JS_EASYAPPLY_MODAL + r""";
  if (!modal) return [];
  const labelFor = (el) => {
    if (el.id) {
      const l = modal.querySelector('label[for="' + CSS.escape(el.id) + '"]');
      if (l && l.innerText.trim()) return l.innerText.trim();
    }
    const wrap = el.closest('label');
    if (wrap && wrap.innerText.trim()) return wrap.innerText.trim();
    const grp = el.closest('.jobs-easy-apply-form-element, .fb-dash-form-element, fieldset, div');
    if (grp) {
      const l = grp.querySelector('label, legend, .fb-dash-form-element__label');
      if (l && l.innerText.trim()) return l.innerText.trim();
    }
    return el.getAttribute('aria-label') || el.name || '';
  };
  const clean = (s) => (s || '').replace(/\s+/g, ' ').trim().slice(0, 220);
  const out = [];
  let n = 0;
  const seenRadio = new Set();
  modal.querySelectorAll('input, textarea, select').forEach((el) => {
    if (el.type === 'hidden' || el.type === 'file' || el.type === 'submit' || el.type === 'button') return;
    if (el.offsetParent === null && el.type !== 'radio') return; // invisible
    if (el.type === 'radio') {
      if (!el.name || seenRadio.has(el.name)) return;
      seenRadio.add(el.name);
      const radios = [...modal.querySelectorAll('input[type=radio][name="' + CSS.escape(el.name) + '"]')];
      const options = radios.map(r => clean(labelFor(r)));
      const checked = radios.find(r => r.checked);
      out.push({ id: 'radio:' + el.name, label: clean(labelFor(el.closest('fieldset') || el)),
                 type: 'radio', options, value: checked ? clean(labelFor(checked)) : '' });
      return;
    }
    n += 1;
    const id = 'f' + n;
    el.setAttribute('data-jsw', id);
    let type = el.tagName.toLowerCase() === 'select' ? 'select'
             : el.tagName.toLowerCase() === 'textarea' ? 'textarea'
             : (el.type === 'checkbox' ? 'checkbox' : (el.type === 'number' ? 'number' : 'text'));
    let options = [];
    if (type === 'select') options = [...el.options].map(o => clean(o.textContent)).filter(Boolean);
    let value = type === 'checkbox' ? (el.checked ? 'true' : '') : (el.value || '');
    out.push({ id, label: clean(labelFor(el)), type, options, value: clean(value) });
  });
  return out;
}
"""

JS_EASYAPPLY_FILL = r"""
(answers) => {
  const modal = """ + JS_EASYAPPLY_MODAL + r""";
  if (!modal) return 0;
  const fire = (el) => { ['input','change','blur'].forEach(t =>
      el.dispatchEvent(new Event(t, { bubbles: true }))); };
  const setNative = (el, val) => {
    const proto = el.tagName === 'TEXTAREA' ? window.HTMLTextAreaElement.prototype
                : el.tagName === 'SELECT' ? window.HTMLSelectElement.prototype
                : window.HTMLInputElement.prototype;
    const setter = Object.getOwnPropertyDescriptor(proto, 'value').set;
    setter.call(el, val); fire(el);
  };
  let done = 0;
  for (const [id, rawAns] of Object.entries(answers)) {
    const ans = (rawAns == null ? '' : String(rawAns)).trim();
    if (id.startsWith('radio:')) {
      const name = id.slice(6);
      const radios = [...modal.querySelectorAll('input[type=radio][name="' + CSS.escape(name) + '"]')];
      let target = radios.find(r => {
        const lab = (r.closest('label') || {}).innerText || '';
        return lab.trim().toLowerCase() === ans.toLowerCase();
      }) || radios.find(r => {
        const lab = (r.closest('label') || {}).innerText || '';
        return lab.trim().toLowerCase().includes(ans.toLowerCase());
      }) || radios.find(r => (r.value || '').toLowerCase() === ans.toLowerCase());
      if (target) { target.click(); done++; }
      continue;
    }
    const el = modal.querySelector('[data-jsw="' + id + '"]');
    if (!el) continue;
    if (el.tagName === 'SELECT') {
      const opt = [...el.options].find(o =>
        o.textContent.trim().toLowerCase() === ans.toLowerCase()) ||
        [...el.options].find(o => o.textContent.trim().toLowerCase().includes(ans.toLowerCase()) && ans);
      if (opt) { el.value = opt.value; fire(el); done++; }
    } else if (el.type === 'checkbox') {
      const want = ['true','yes','sí','si','1'].includes(ans.toLowerCase());
      if (el.checked !== want) { el.click(); }
      done++;
    } else {
      setNative(el, ans); done++;
    }
  }
  return done;
}
"""

JS_COLLECT_CLICKABLES = r"""
() => {
  const els = [...document.querySelectorAll('button, a[role=button], a.jobs-apply-button, [role=button]')];
  const out = [];
  let n = 0;
  for (const el of els) {
    const text = (el.innerText || '').replace(/\s+/g, ' ').trim();
    const aria = (el.getAttribute('aria-label') || '').replace(/\s+/g, ' ').trim();
    if (!text && !aria) continue;
    n += 1;
    const id = 'jswbtn' + n;
    el.setAttribute('data-jswbtn', id);
    out.push({ id, tag: el.tagName.toLowerCase(),
               text: text.slice(0, 80), aria: aria.slice(0, 80),
               visible: el.offsetParent !== null });
  }
  return out;
}
"""

JS_TAG_COVER_FILE = r"""
() => {
  const modal = """ + JS_EASYAPPLY_MODAL + r""";
  if (!modal) return false;
  const kws = ['cover letter','carta de presentaci','carta de motivaci','motivation letter','covering letter'];
  const inputs = [...modal.querySelectorAll("input[type='file']")];
  for (const inp of inputs) {
    const grp = inp.closest('.jobs-easy-apply-form-element, .fb-dash-form-element, fieldset, section, div');
    const txt = (((grp && grp.innerText) || '') + ' ' + (inp.getAttribute('aria-label') || '')).toLowerCase();
    if (kws.some(k => txt.includes(k))) { inp.setAttribute('data-jswcover', '1'); return true; }
  }
  return false;
}
"""

JS_EASYAPPLY_BUTTONS = r"""
() => {
  const modal = """ + JS_EASYAPPLY_MODAL + r""";
  if (!modal) return [];
  return [...modal.querySelectorAll('button')]
    .filter(b => b.offsetParent !== null && !b.disabled)
    .map(b => (b.getAttribute('aria-label') || b.innerText || '').trim())
    .filter(Boolean);
}
"""


WORKER = LinkedInWorker()
WORKER.start()


# --------------------------------------------------------------------------- #
#  Datos de ejemplo (modo MOCK, para probar sin LinkedIn)                     #
# --------------------------------------------------------------------------- #
MOCK_JOBS = [
    {'id': 'mock1', 'title': 'Data Center Technician', 'company': 'CloudScale',
     'location': 'Raleigh, NC', 'easy_apply': True,
     'employment_type': 'Full-time', 'seniority': 'Entry level',
     'link': 'https://www.linkedin.com/jobs/view/0000000001/',
     'description': 'Install, maintain and troubleshoot data center hardware.\n\n'
                    '• A+ certification, cabling, hardware break/fix\n'
                    '• Ticketing systems (ServiceNow)\n'
                    '• Ability to lift 50 lbs\n\n— Detalles —\n'
                    'Seniority level: Entry level\nEmployment type: Full-time'},
    {'id': 'mock2', 'title': 'Data Center Engineer', 'company': 'NorthBridge Systems',
     'location': 'Durham, NC', 'easy_apply': True,
     'employment_type': 'Full-time', 'seniority': 'Mid-Senior level',
     'link': 'https://www.linkedin.com/jobs/view/0000000002/',
     'description': 'Support Linux/Windows servers, Cisco networking, Dell PowerEdge, '
                    'VMware. On-call rotation. 2+ years experience preferred.'},
    {'id': 'mock3', 'title': 'IT Support Specialist', 'company': 'Piedmont Health',
     'location': 'Raleigh, NC (Hybrid)', 'easy_apply': True,
     'employment_type': 'Contract', 'seniority': 'Entry level',
     'link': 'https://www.linkedin.com/jobs/view/0000000003/',
     'description': 'Provide tier-1/2 support, image laptops, manage Active Directory, '
                    'Office 365. Great entry-level role with growth path.'},
]


def mock_documents(job):
    prof_es = {'name': 'Juan Pérez', 'headline': 'Técnico de Datacenter', 'lang': 'es',
               'contact': {'email': 'juan.perez@email.com', 'phone': '+1 555 0123',
                           'location': 'Raleigh, NC', 'linkedin': 'linkedin.com/in/juanperez', 'website': ''}}
    section_es = {
        'summary': f"Técnico con 3 años de experiencia enfocado en {job.get('title')}, con base en {job.get('location')}.",
        'experience': [
            {'title': 'Técnico de Datacenter', 'company': 'TechCorp', 'location': 'Raleigh, NC',
             'start': 'Ene 2022', 'end': 'Presente',
             'bullets': ['Mantenimiento de 200+ servidores con 99.9% de uptime.',
                         'Resolución de tickets en ServiceNow reduciendo el tiempo de respuesta 30%.',
                         'Cableado estructurado y break/fix de hardware Dell y HP.']},
            {'title': 'Soporte IT Junior', 'company': 'HelpDesk Inc', 'location': 'Durham, NC',
             'start': 'Jun 2020', 'end': 'Dic 2021',
             'bullets': ['Soporte tier-1/2 a 500 usuarios.', 'Imagen y despliegue de equipos con SCCM.']},
        ],
        'education': [{'degree': 'Técnico en Redes', 'school': 'Wake Tech CC', 'location': 'Raleigh, NC',
                       'start': '2018', 'end': '2020', 'detail': 'CompTIA A+, Network+'}],
        'skills': ['ServiceNow', 'Cableado estructurado', 'Dell/HP hardware', 'Linux', 'Windows Server', 'Redes Cisco'],
        'certifications': ['CompTIA A+', 'CompTIA Network+'],
        'languages': ['Español (nativo)', 'Inglés (C1)'],
    }
    
    prof_en = {'name': 'Juan Perez', 'headline': 'Data Center Technician', 'lang': 'en',
               'contact': {'email': 'juan.perez@email.com', 'phone': '+1 555 0123',
                           'location': 'Raleigh, NC', 'linkedin': 'linkedin.com/in/juanperez', 'website': ''}}
    section_en = {
        'summary': f"Technician with 3 years of experience focused on {job.get('title')}, based in {job.get('location')}.",
        'experience': [
            {'title': 'Data Center Technician', 'company': 'TechCorp', 'location': 'Raleigh, NC',
             'start': 'Jan 2022', 'end': 'Present',
             'bullets': ['Maintained 200+ servers with 99.9% uptime.',
                         'Resolved tickets in ServiceNow, reducing response times by 30%.',
                         'Structured cabling and hardware break/fix for Dell and HP systems.']},
            {'title': 'Junior IT Support', 'company': 'HelpDesk Inc', 'location': 'Durham, NC',
             'start': 'Jun 2020', 'end': 'Dec 2021',
             'bullets': ['Provided tier-1/2 support to 500 users.', 'Imaged and deployed PCs using SCCM.']},
        ],
        'education': [{'degree': 'Network Technician', 'school': 'Wake Tech CC', 'location': 'Raleigh, NC',
                       'start': '2018', 'end': '2020', 'detail': 'CompTIA A+, Network+'}],
        'skills': ['ServiceNow', 'Structured cabling', 'Dell/HP hardware', 'Linux', 'Windows Server', 'Cisco Networking'],
        'certifications': ['CompTIA A+', 'CompTIA Network+'],
        'languages': ['Spanish (Native)', 'English (C1)'],
    }
    return {
        'en': {
            'profile': prof_en,
            'resume': section_en,
            'cv': section_en,
            'cover_letter': {
                'date': 'July 11, 2026',
                'recipient': f"Hiring Team at {job.get('company')}",
                'paragraphs': [
                    f"I am writing to express my strong interest in the {job.get('title')} position at {job.get('company')}.",
                    "My experience in data center maintenance and infrastructure support aligns directly with your requirements.",
                    "I look forward to discussing how my skills fit your needs. Thank you for your time and consideration."
                ],
                'closing': 'Sincerely,'
            }
        },
        'es': {
            'profile': prof_es,
            'resume': section_es,
            'cv': section_es,
            'cover_letter': {
                'date': '11 de julio de 2026',
                'recipient': f"Equipo de contratación de {job.get('company')}",
                'paragraphs': [
                    f"Me dirijo a ustedes con gran interés en la posición de {job.get('title')} en {job.get('company')}.",
                    'Mi experiencia en mantenimiento de datacenter y soporte de infraestructura encaja directamente con lo que buscan.',
                    'Quedo a su disposición para una entrevista. Gracias por su tiempo y consideración.',
                ],
                'closing': 'Atentamente,',
            }
        },
        'match_notes': '- Cumples los requisitos técnicos clave (hardware, ticketing).\n- Refuerza certificaciones específicas del puesto al aplicar.',
    }


# --------------------------------------------------------------------------- #
#  Servidor HTTP                                                              #
# --------------------------------------------------------------------------- #
# --------------------------------------------------------------------------- #
#  Scraping multi-plataforma en segundo plano (botón «Scrapear empleos»)       #
# --------------------------------------------------------------------------- #
import subprocess  # noqa: E402
import sys  # noqa: E402

SCRAPE = {'running': False, 'log': [], 'started': None, 'finished': None}
_scrape_lock = threading.Lock()


def run_scrape_all(query='', location='', sources='all'):
    cmd = [sys.executable, '-u', '-m', 'studio.scrapers.run', '--sources', sources or 'all',
           '--db', db.DB_PATH]
    if query:
        cmd += ['--query', query]
    if location:
        cmd += ['--location', location]
    try:
        p = subprocess.Popen(cmd, cwd=HERE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             text=True, bufsize=1)
        for line in p.stdout:
            SCRAPE['log'].append(line.rstrip())
            del SCRAPE['log'][:-200]
        p.wait()
    except Exception as e:
        SCRAPE['log'].append(f'ERROR: {e}')
    finally:
        SCRAPE['running'] = False
        SCRAPE['finished'] = time.time()


# --------------------------------------------------------------------------- #
#  Onboarding, perfil progresivo y CV general                                  #
# --------------------------------------------------------------------------- #
# Preguntas que se hacen poco a poco (una tras cada swipe) para completar lo que
# no se pudo sacar del CV. Los valores de las opciones coinciden con los chips
# del panel Perfil, así que todo queda sincronizado.
PROFILE_QUESTIONS = [
    {'key': 'relocate', 'q': '¿Estarías dispuesto a reubicarte por un buen empleo?',
     'options': ['No, solo mi ciudad', 'Sí, a otras ciudades', 'Solo remoto']},
    {'key': 'modality', 'q': '¿Qué modalidad de trabajo prefieres?',
     'options': ['Presencial', 'Híbrido', 'Remoto', 'Indiferente']},
    {'key': 'seniority', 'q': '¿Qué nivel de puesto estás buscando?',
     'options': ['Entry-level', 'Mid', 'Senior']},
    {'key': 'english', 'q': '¿Qué nivel de inglés tienes?',
     'options': ['Básico', 'Intermedio', 'Avanzado', 'Nativo / bilingüe']},
    {'key': 'availability', 'q': '¿Cuándo podrías empezar a trabajar?',
     'options': ['De inmediato', 'En 2 semanas', 'En 1 mes o más']},
    {'key': 'salary', 'q': '¿Cuál es tu expectativa salarial mensual?',
     'placeholder': 'Monto y moneda, ej. 20,000 al mes'},
    {'key': 'travel', 'q': '¿Tienes disponibilidad para viajar?',
     'options': ['Sí', 'Ocasionalmente', 'No']},
    {'key': 'authorization', 'q': '¿En qué países puedes trabajar legalmente?',
     'placeholder': 'Ej. solo en mi país'},
    {'key': 'experience', 'q': '¿Cuántos años de experiencia profesional tienes?',
     'placeholder': 'Ej. 3'},
    {'key': 'education', 'q': '¿Cuál es tu nivel educativo?',
     'options': ['Sin título', 'Carrera técnica', 'Licenciatura', 'Posgrado']},
    {'key': 'field', 'q': '¿En qué área profesional te quieres desarrollar?',
     'placeholder': 'Ej. Ventas, diseño, ingeniería, salud…'},
    {'key': 'contract', 'q': '¿Qué tipo de contrato buscas?',
     'options': ['Tiempo completo', 'Medio tiempo', 'Por proyecto / freelance', 'Prácticas', 'Me da igual']},
    {'key': 'schedule', 'q': '¿Qué horario te funciona mejor?',
     'options': ['Matutino', 'Vespertino', 'Mixto / flexible', 'Turnos rotativos', 'Cualquiera']},
    {'key': 'company_size', 'q': '¿En qué tipo de empresa te gustaría trabajar?',
     'options': ['Startup', 'Empresa mediana', 'Gran corporativo', 'Gobierno / ONG', 'Me da igual']},
    {'key': 'priority', 'q': '¿Qué es lo que más te importa en un nuevo empleo?',
     'options': ['Sueldo', 'Crecimiento', 'Estabilidad', 'Ambiente de trabajo', 'Flexibilidad']},
    {'key': 'commute', 'q': '¿Cuánto tiempo estás dispuesto a trasladarte al trabajo?',
     'options': ['Menos de 30 min', 'Hasta 1 hora', 'Más de 1 hora', 'Solo remoto']},
    {'key': 'languages_extra', 'q': '¿Hablas otros idiomas además de español e inglés?',
     'placeholder': 'Ej. Francés intermedio (o «ninguno»)'},
    {'key': 'license', 'q': '¿Tienes licencia de conducir o vehículo propio?',
     'options': ['Licencia y auto', 'Solo licencia', 'No']},
    {'key': 'avoid', 'q': '¿Hay algo que prefieras evitar en un empleo?',
     'placeholder': 'Ej. turnos nocturnos, ventas por comisión… (o «nada»)'},
]


def missing_questions(profile):
    profile = profile or {}
    skipped = set(profile.get('_skipped') or [])
    miss = [q for q in PROFILE_QUESTIONS
            if not str(profile.get(q['key']) or '').strip() and q['key'] not in skipped]
    random.shuffle(miss)  # orden aleatorio: cada vez una pregunta distinta
    return miss


def extract_profile_from_cv(base_cv, basics):
    """Lee el CV y saca todo lo que pueda del perfil + las mejores búsquedas."""
    keys = ', '.join(q['key'] for q in PROFILE_QUESTIONS)
    opts = '\n'.join(f"- {q['key']}: " + (' | '.join(q['options']) if q.get('options') else 'texto libre')
                     for q in PROFILE_QUESTIONS)
    prompt = f"""Analiza el CV de un candidato y extrae su perfil laboral.

Devuelve SOLO JSON válido:
{{"profile": {{"location": "ciudad, país", {', '.join(f'"{k}": ""' for k in keys.split(', '))}}},
  "queries": ["3 a 5 títulos de puesto realistas para buscar empleo, en el idioma del mercado del candidato"]}}

Reglas:
- Rellena SOLO lo que el CV diga o permita deducir con seguridad; deja "" lo demás (se le preguntará luego).
- Para los campos con opciones usa EXACTAMENTE uno de estos valores:
{opts}
- Las búsquedas deben encajar con su experiencia y con el puesto que dice buscar: "{basics.get('target') or 'no indicado'}".

=== DATOS QUE DIO EL CANDIDATO ===
{json.dumps(basics, ensure_ascii=False)}

=== CV ===
{base_cv[:8000]}"""
    return gemini_json(prompt, temperature=0.3, max_tokens=2000, label='Analizar CV (bienvenida)',
                       timeout=120)


def mock_extract_profile(base_cv, basics):
    low = base_cv.lower()
    yrs = re.search(r'(\d+)\+?\s*(años|years)', low)
    return {'profile': {
        'experience': yrs.group(1) if yrs else '',
        'education': 'Licenciatura' if ('ingenier' in low or 'licenciatura' in low) else '',
        'english': 'Avanzado' if ('inglés' in low or 'english' in low) else '',
    }, 'queries': [basics.get('target') or 'Data Center Technician']}


MASTER_SCHEMA = """{
  "en": {"profile": {"name":"","headline":"","lang":"en","contact":{"email":"","phone":"","location":"","linkedin":"","website":""}},
         "resume": {"summary":"","experience":[{"title":"","company":"","location":"","start":"","end":"","bullets":[""]}],
                    "education":[{"degree":"","school":"","location":"","start":"","end":"","detail":""}],
                    "skills":[""],"certifications":[""],"languages":[""]}},
  "es": { "misma estructura, todo en español" }
}"""


def generate_master_cv(profile, base_cv):
    """CV general de UNA página: la base pulida de la que parten todos los CVs adaptados."""
    prompt = f"""Eres un redactor experto de CVs optimizados para ATS. Crea el CV GENERAL del candidato:
la mejor versión posible de su perfil, sin enfocarlo a una oferta concreta, pero orientado al
área y nivel que busca. Servirá como base para adaptar después un CV a cada oferta.

{ATS_RULES}

REGLA CRÍTICA: el CV debe caber en UNA SOLA PÁGINA tamaño carta. Resumen de 2-3 líneas,
máximo 3-4 experiencias (las más relevantes) con 2-4 viñetas cortas y cuantificadas,
habilidades agrupadas y concisas. Prioriza y recorta; no rellenes.

Usa los datos de contacto del perfil (name, email, phone, location) tal cual.
Genera dos versiones (en inglés "en" y en español "es"), cada una 100% en su idioma.

Devuelve SOLO JSON válido con esta estructura:
{MASTER_SCHEMA}

=== PERFIL ===
{json.dumps({k: v for k, v in (profile or {}).items() if not k.startswith('_')}, ensure_ascii=False)}

=== CV ORIGINAL (fuente de la verdad) ===
{base_cv[:8000]}"""
    return gemini_json(prompt, temperature=0.4, max_tokens=8192, label='CV general (1 página)')


def mock_master_cv(profile, base_cv):
    lines = [l.strip() for l in base_cv.splitlines() if l.strip()]
    out = {}
    for lang in ('en', 'es'):
        out[lang] = {
            'profile': {'name': profile.get('name') or 'Candidato', 'lang': lang,
                        'headline': profile.get('field') or ('Professional' if lang == 'en' else 'Profesional'),
                        'contact': {'email': profile.get('email', ''), 'phone': profile.get('phone', ''),
                                    'location': profile.get('location', ''), 'linkedin': '', 'website': ''}},
            'resume': {'summary': ' '.join(lines[:2])[:300], 'experience': [], 'education': [],
                       'skills': [], 'certifications': [],
                       'languages': [profile['english']] if profile.get('english') else []},
        }
    return out


def _tokens(text):
    stop = {'de', 'la', 'el', 'en', 'y', 'a', 'the', 'and', 'of', 'for', 'con', 'para', 'jr', 'sr'}
    return {w for w in re.findall(r'[a-záéíóúñü0-9+#]{2,}', str(text or '').lower()) if w not in stop}


def rank_jobs(jobs):
    """Primero los empleos que más se parecen a lo que busca la persona."""
    prof = CONFIG.get('profile', {})
    want = _tokens(' '.join(CONFIG.get('search_terms') or []) + ' ' + str(prof.get('field') or ''))
    if not want:
        return jobs
    loc = _tokens(prof.get('location'))

    def score(j):
        t = _tokens(j.get('title'))
        sc = 3 * len(t & want) + 0.5 * len(_tokens((j.get('description') or '')[:1500]) & want)
        if loc and loc & _tokens(j.get('location')):
            sc += 1
        return sc
    return sorted(jobs, key=score, reverse=True)


MASTER = {'running': False, 'error': None}


def run_master_cv():
    MASTER.update(running=True, error=None)
    try:
        profile, base_cv = CONFIG.get('profile', {}), CONFIG.get('base_cv', '')
        if MOCK:
            time.sleep(2)
            cv = mock_master_cv(profile, base_cv)
        else:
            cv = generate_master_cv(profile, base_cv)
        if not isinstance(cv, dict) or not (cv.get('en') or cv.get('es')):
            raise ValueError('La IA no devolvió un CV válido.')
        CONFIG['master_cv'] = cv
        CONFIG['master_cv_at'] = time.time()
        CONFIG['master_cv_stale'] = False
        save_config(CONFIG)
    except Exception as e:
        MASTER['error'] = str(e)
    finally:
        MASTER['running'] = False


ONBOARD = {'running': False, 'done': False, 'error': None, 'steps': [], 'started': 0}
ONBOARD_STEPS = [
    ('analyze', 'Leyendo tu CV'),
    ('search', 'Eligiendo los puestos que mejor te quedan'),
    ('scrape', 'Buscando empleos en todas las plataformas'),
    ('master', 'Creando tu CV general (1 página)'),
]


def _step(key, state, detail=''):
    for s in ONBOARD['steps']:
        if s['key'] == key:
            s['state'] = state
            if detail:
                s['detail'] = detail


def run_onboarding(basics):
    try:
        base_cv = CONFIG.get('base_cv', '')
        _step('analyze', 'running')
        try:
            info = (mock_extract_profile(base_cv, basics) if MOCK
                    else extract_profile_from_cv(base_cv, basics)) or {}
        except Exception as e:
            info = {'profile': {}, 'queries': []}
            _step('analyze', 'running', f'No se pudo analizar con IA ({e}); seguimos.')
        prof = CONFIG.setdefault('profile', {})
        for k, v in (info.get('profile') or {}).items():
            if str(v or '').strip() and not str(prof.get(k) or '').strip():
                prof[k] = v
        save_config(CONFIG)
        _step('analyze', 'done')

        _step('search', 'running')
        queries = [q for q in (info.get('queries') or []) if isinstance(q, str) and q.strip()]
        if basics.get('target'):
            queries = [basics['target']] + [q for q in queries if q.lower() != basics['target'].lower()]
        queries = queries[:3]
        CONFIG['search_terms'] = queries
        save_config(CONFIG)
        _step('search', 'done', ', '.join(queries))

        # El CV general se genera en paralelo mientras se scrapea.
        master_t = threading.Thread(target=run_master_cv, daemon=True)
        _step('master', 'running')
        master_t.start()

        _step('scrape', 'running')
        location = prof.get('location') or basics.get('location') or 'Mexico'
        # Las bolsas remotas (en inglés) solo si la persona busca remoto: si no, traen ruido.
        wants_remote = any('remot' in str(x).lower()
                           for x in (location, basics.get('target'), prof.get('relocate'), prof.get('modality')))
        from studio.scrapers import SOURCES as _ALL
        from studio.scrapers.run import REMOTE as _REMOTE
        sources = ','.join(_ALL if wants_remote else [x for x in _ALL if x not in _REMOTE])
        if MOCK:
            for j in MOCK_JOBS:
                db.upsert_jobs([dict(j)])
                time.sleep(1.2)
        else:
            for q in queries:
                with _scrape_lock:
                    if SCRAPE['running']:
                        continue
                    SCRAPE.update(running=True, log=[], started=time.time(), finished=None)
                _step('scrape', 'running', f'Buscando «{q}» en {location}…')
                run_scrape_all(q, location, sources)
        _step('scrape', 'done')

        master_t.join()
        _step('master', 'error' if MASTER['error'] else 'done', MASTER['error'] or '')
        CONFIG['onboarded'] = True
        save_config(CONFIG)
    except Exception as e:
        ONBOARD['error'] = str(e)
    finally:
        ONBOARD['running'] = False
        ONBOARD['done'] = True


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype='application/json; charset=utf-8', extra_headers=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False).encode()
        elif isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        for k, v in (extra_headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self):
        length = int(self.headers.get('Content-Length', 0))
        return json.loads(self.rfile.read(length) or b'{}')

    # ------------------------------------------------------------------ GET #
    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        if path == '/':
            return self._serve_static('index.html')
        if path.startswith('/static/'):
            return self._serve_static(path[len('/static/'):])
        if path == '/api/config':
            return self._send(200, {
                'ai_provider': ai_provider(),
                'ai_provider_locked': bool((os.environ.get('JOBSWIPER_AI') or '').strip()),
                'mcp_timeout': CONFIG.get('mcp_timeout', 900),
                'gemini_key': CONFIG.get('gemini_key', ''),
                'gemini_model': CONFIG.get('gemini_model', 'gemini-3.5-flash'),
                'base_cv': CONFIG.get('base_cv', ''),
                'profile': CONFIG.get('profile', {}),
                'mock': MOCK,
            })
        if path == '/api/scrape-all/status':
            return self._send(200, SCRAPE)
        if path == '/api/onboarding/status':
            st = dict(ONBOARD)
            st['onboarded'] = bool(CONFIG.get('onboarded') or CONFIG.get('base_cv', '').strip())
            st['jobs_new'] = len(db.get_jobs('new'))
            return self._send(200, st)
        if path == '/api/profile/question':
            miss = missing_questions(CONFIG.get('profile', {}))
            return self._send(200, {'question': miss[0] if miss else None, 'remaining': len(miss)})
        if path == '/api/master-cv':
            return self._send(200, {'master_cv': CONFIG.get('master_cv'),
                                    'stale': bool(CONFIG.get('master_cv_stale')),
                                    'updated_at': CONFIG.get('master_cv_at'),
                                    'running': MASTER['running'], 'error': MASTER['error']})
        if path == '/api/linkedin/status':
            if MOCK:
                return self._send(200, {'launched': True, 'logged_in': True, 'mock': True})
            try:
                return self._send(200, WORKER.call('status'))
            except Exception as e:
                return self._send(200, {'launched': False, 'logged_in': False, 'error': str(e)})
        if path == '/api/deck':
            return self._send(200, {'jobs': rank_jobs(db.get_jobs('new'))})
        if path == '/api/jobs':
            jobs = db.get_jobs()
            have = db.doc_job_ids()
            for j in jobs:
                j['has_docs'] = j['id'] in have
            return self._send(200, {'jobs': jobs, 'stats': db.stats()})
        if path == '/api/matches':
            return self._send(200, {'matches': db.get_matches()})
        if path == '/api/chat':
            return self._send(200, {'messages': db.get_chat()})
        if path == '/api/stats':
            return self._send(200, db.stats())
        if path == '/api/autojob/status':
            query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            task_id = query.get('task_id', [None])[0]
            with AUTOJOB_LOCK:
                if AUTOJOB_STATUS['task_id'] != task_id:
                    return self._send(200, {'done': True, 'error': 'Tarea cancelada o expirada', 'phase': 'error', 'progress': 0, 'total': 0})
                return self._send(200, {
                    'task_id': AUTOJOB_STATUS['task_id'],
                    'phase': AUTOJOB_STATUS['phase'],
                    'progress': AUTOJOB_STATUS['progress'],
                    'total': AUTOJOB_STATUS['total'],
                    'current_job': AUTOJOB_STATUS['current_job'],
                    'error': AUTOJOB_STATUS['error'],
                    'done': AUTOJOB_STATUS['done'],
                    'result': AUTOJOB_STATUS['result'],
                    'questions': AUTOJOB_STATUS['questions'],
                    'discarded_seniority': AUTOJOB_STATUS['discarded_seniority'],
                    'discarded_location': AUTOJOB_STATUS['discarded_location']
                })
        if path == '/api/autojob/result':
            res = db.get_latest_autojob_result()
            return self._send(200, res or {})
        if path == '/api/applications':
            return self._send(200, {'applications': db.get_applications()})
        if path == '/api/doc/pdf':
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            jid = q.get('job_id', [''])[0]
            kind = q.get('kind', ['resume'])[0]
            lang = q.get('lang', ['en'])[0]
            if kind not in ('resume', 'cv', 'cover_letter') or lang not in ('en', 'es'):
                return self._send(400, {'error': 'Parámetros inválidos.'})
            docs = db.get_documents(jid) if jid else None
            if not docs:
                return self._send(404, {'error': 'No hay documentos para este empleo.'})
            import tempfile
            with tempfile.TemporaryDirectory() as tmp:
                out = os.path.join(tmp, 'doc.pdf')
                try:
                    if not latex_doc_pdf(docs, kind, lang, out):
                        return self._send(409, {'error': 'Documento editado a mano o LaTeX no disponible.'})
                except Exception as e:
                    return self._send(500, {'error': str(e)[-800:]})
                with open(out, 'rb') as f:
                    pdf = f.read()
            fname = re.sub(r'[^\w.-]+', '_', q.get('name', ['documento'])[0]) + '.pdf'
            return self._send(200, pdf, 'application/pdf',
                              {'Content-Disposition': f'inline; filename="{fname}"'})
        if path == '/api/apply/quota':
            return self._send(200, apply_quota())
        if path == '/api/mcp/status':
            # Lo consulta la web (badge "esperando a Claude") y el servidor MCP.
            query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            touch = query.get('touch', ['0'])[0] == '1'
            snap = BRIDGE.snapshot(touch=touch)
            snap['provider'] = ai_provider()
            snap['mock'] = MOCK
            return self._send(200, snap)
        if path == '/api/apply/status':
            with APPLY_LOCK:
                st = dict(APPLY_STATUS)
            st['quota'] = apply_quota()
            return self._send(200, st)
        return self._send(404, {'error': 'not found'})

    # ----------------------------------------------------------------- POST #
    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        try:
            if path == '/api/config':
                data = self._read_json()
                for k in ('ai_provider', 'mcp_timeout', 'gemini_key', 'gemini_model',
                          'base_cv', 'profile'):
                    if k in data:
                        CONFIG[k] = data[k]
                save_config(CONFIG)
                return self._send(200, {'ok': True})

            if path == '/api/onboarding':
                data = self._read_json()
                cv = (data.get('base_cv') or '').strip()
                name = (data.get('name') or '').strip()
                if not name or len(cv) < 40:
                    return self._send(400, {'error': 'Necesitamos tu nombre y tu CV (pégalo o súbelo).'})
                if not (data.get('target') or '').strip():
                    return self._send(400, {'error': 'Dinos qué puesto buscas para traerte empleos que sí te sirvan.'})
                if ONBOARD['running']:
                    return self._send(409, {'error': 'Ya estamos configurando tu perfil.'})
                if not ai_ready():
                    return self._send(400, {'error': ai_missing_msg()})
                basics = {k: (data.get(k) or '').strip()
                          for k in ('name', 'email', 'phone', 'location', 'target')}
                prof = CONFIG.setdefault('profile', {})
                for k in ('name', 'email', 'phone', 'location'):
                    if basics[k]:
                        prof[k] = basics[k]
                CONFIG['base_cv'] = cv
                CONFIG['onboarded'] = True  # ya vio la bienvenida: no se la volvemos a mostrar
                save_config(CONFIG)
                ONBOARD.update(running=True, done=False, error=None, started=time.time(),
                               steps=[{'key': k, 'label': l, 'state': 'pending', 'detail': ''}
                                      for k, l in ONBOARD_STEPS])
                threading.Thread(target=run_onboarding, args=(basics,), daemon=True).start()
                return self._send(200, {'ok': True})

            if path == '/api/reset':
                data = self._read_json()
                if ONBOARD['running'] or MASTER['running']:
                    return self._send(409, {'error': 'Espera a que termine la configuración en curso.'})
                for k in ('base_cv', 'profile', 'master_cv', 'master_cv_at', 'master_cv_stale',
                          'onboarded', 'search_terms', 'last_search'):
                    CONFIG.pop(k, None)
                CONFIG['profile'] = {}
                save_config(CONFIG)
                db.clear_chat()
                removed = db.delete_jobs_by_status('new') if data.get('jobs') else 0
                ONBOARD.update(running=False, done=False, error=None, steps=[], started=0)
                return self._send(200, {'ok': True, 'removed_jobs': removed})

            if path == '/api/profile/answer':
                data = self._read_json()
                key = data.get('key')
                if key not in {q['key'] for q in PROFILE_QUESTIONS}:
                    return self._send(400, {'error': 'Pregunta desconocida.'})
                prof = CONFIG.setdefault('profile', {})
                if data.get('skip'):
                    prof['_skipped'] = sorted(set(prof.get('_skipped') or []) | {key})
                else:
                    val = str(data.get('value') or '').strip()
                    if not val:
                        return self._send(400, {'error': 'Respuesta vacía.'})
                    prof[key] = val
                    CONFIG['master_cv_stale'] = True
                save_config(CONFIG)
                miss = missing_questions(prof)
                # Cuando ya respondió todo, el CV general se rehace con la info nueva.
                if not miss and CONFIG.get('master_cv_stale') and not MASTER['running'] \
                        and CONFIG.get('base_cv') and ai_ready():
                    MASTER['running'] = True
                    threading.Thread(target=run_master_cv, daemon=True).start()
                return self._send(200, {'ok': True, 'remaining': len(miss)})

            if path == '/api/master-cv/regenerate':
                if not CONFIG.get('base_cv', '').strip():
                    return self._send(400, {'error': 'Falta tu CV base.'})
                if not ai_ready():
                    return self._send(400, {'error': ai_missing_msg()})
                if not MASTER['running']:
                    MASTER['running'] = True
                    threading.Thread(target=run_master_cv, daemon=True).start()
                return self._send(200, {'ok': True})

            if path == '/api/autojob/start':
                if not ai_ready() and not MOCK:
                    return self._send(400, {'error': ai_missing_msg()})
                base_cv = CONFIG.get('base_cv', '').strip()
                if not base_cv:
                    return self._send(400, {'error': 'Falta tu CV base (guárdala en Perfil).'})
                
                jobs = db.get_jobs()
                jobs_to_analyze = [j for j in jobs if j.get('status') in ('new', 'liked')]
                if not jobs_to_analyze:
                    return self._send(400, {'error': 'No hay empleos en la base de datos (con estado Nuevo o Me gusta) para analizar.'})
                
                task_id = str(int(time.time() * 1000))
                with AUTOJOB_LOCK:
                    AUTOJOB_STATUS.update({
                        'task_id': task_id,
                        'phase': 'evaluating',
                        'progress': 0,
                        'total': len(jobs_to_analyze),
                        'current_job': '',
                        'error': None,
                        'result': None,
                        'done': False,
                        'questions': [],
                        'candidates': [],
                        'user_answers': {},
                        'discarded_seniority': 0,
                        'discarded_location': 0
                    })
                
                profile = CONFIG.get('profile', {})
                t = threading.Thread(target=run_autojob_pipeline, args=(task_id, profile, base_cv, MOCK))
                t.daemon = True
                t.start()
                
                return self._send(200, {'task_id': task_id, 'total': len(jobs_to_analyze)})

            if path == '/api/autojob/answer':
                data = self._read_json()
                task_id = data.get('task_id')
                answers = data.get('answers') or {}
                
                with AUTOJOB_LOCK:
                    if AUTOJOB_STATUS['task_id'] != task_id or AUTOJOB_STATUS['phase'] != 'waiting_input':
                        return self._send(400, {'error': 'La tarea no existe o no está esperando respuestas.'})
                    
                    AUTOJOB_STATUS['user_answers'] = answers
                    AUTOJOB_STATUS['phase'] = 'comparing'
                
                profile = CONFIG.get('profile', {})
                base_cv = CONFIG.get('base_cv', '')
                t = threading.Thread(target=run_autojob_phase2, args=(task_id, profile, base_cv, MOCK))
                t.daemon = True
                t.start()
                
                return self._send(200, {'ok': True})

            if path == '/api/linkedin/connect':
                if MOCK:
                    return self._send(200, {'launched': True, 'logged_in': True, 'mock': True})
                return self._send(200, WORKER.call('open_login'))

            if path == '/api/suggest-titles':
                data = self._read_json()
                profile = data.get('profile', CONFIG.get('profile', {}))
                base_cv = data.get('base_cv', CONFIG.get('base_cv', ''))
                if MOCK:
                    return self._send(200, {'queries': [
                        {'keywords': 'Data Center Technician', 'location': 'Raleigh, NC', 'why': 'Encaja con tu experiencia técnica.'},
                        {'keywords': 'IT Support Specialist', 'location': 'Raleigh, NC', 'why': 'Buen entry-level con Easy Apply.'},
                    ]})
                return self._send(200, {'queries': suggest_titles(profile, base_cv)})

            if path == '/api/scrape-all':
                data = self._read_json()
                with _scrape_lock:
                    if SCRAPE['running']:
                        return self._send(409, {'error': 'Ya hay un scraping en curso.'})
                    SCRAPE.update(running=True, log=[], started=time.time(), finished=None)
                t = threading.Thread(target=run_scrape_all, daemon=True,
                                     args=((data.get('query') or '').strip(),
                                           (data.get('location') or '').strip(),
                                           (data.get('sources') or 'all').strip()))
                t.start()
                return self._send(200, {'ok': True})

            if path == '/api/search':
                data = self._read_json()
                kw = (data.get('keywords') or '').strip()
                loc = (data.get('location') or '').strip()
                limit = min(int(data.get('limit', 10)), 25)
                if not kw:
                    return self._send(400, {'error': 'Falta keywords.'})
                if MOCK:
                    time.sleep(1)
                    new = db.upsert_jobs([dict(j) for j in MOCK_JOBS])
                else:
                    # el worker guarda cada empleo en la BD según lo scrapea
                    new = WORKER.call('search', keywords=kw, location=loc, limit=limit)
                return self._send(200, {'jobs': new, 'found': len(new)})

            if path == '/api/search-batch':
                data = self._read_json()
                queries = data.get('queries') or []
                per = min(int(data.get('per_query_limit', 6)), 15)
                if not queries:
                    return self._send(400, {'error': 'No hay sugerencias que buscar.'})
                if MOCK:
                    time.sleep(1.5)
                    new = db.upsert_jobs([dict(j, query=queries[0].get('keywords', '')) for j in MOCK_JOBS])
                else:
                    # el worker guarda cada empleo en la BD según lo scrapea
                    new = WORKER.call('search_batch', timeout=1200, queries=queries, per_query_limit=per)
                return self._send(200, {'jobs': new, 'found': len(new)})

            if path == '/api/scrape-job':
                # Pega el link de un empleo de LinkedIn y trae sus datos (sin guardarlo aún).
                data = self._read_json()
                url = (data.get('url') or '').strip()
                m = (re.search(r'/jobs/view/(\d+)', url)
                     or re.search(r'currentJobId=(\d+)', url)
                     or re.search(r'/jobs/(?:collections|search)[^\d]*?(\d{6,})', url)
                     or re.search(r'\b(\d{8,})\b', url))
                if not url or not m:
                    return self._send(400, {'error': 'Pega un link de empleo de LinkedIn válido '
                                                     '(por ejemplo …/jobs/view/1234567890/).'})
                jid = m.group(1)
                if MOCK:
                    time.sleep(1.0)
                    job = {'id': 'li_' + jid, 'title': '(demo) Puesto desde link',
                           'company': 'Empresa Demo', 'location': 'Remoto',
                           'description': 'Descripción de ejemplo scrapeada del link en modo demo.',
                           'link': f'https://www.linkedin.com/jobs/view/{jid}/', 'easy_apply': True}
                else:
                    job = WORKER.call('scrape_one', timeout=90, jid=jid)
                if not job or not job.get('title') or job.get('title') == 'Sin título':
                    return self._send(200, {'job': job, 'warning':
                        'No pude leer todos los datos (puede requerir tener LinkedIn abierto/con sesión). '
                        'Revisa y completa lo que falte.'})
                return self._send(200, {'job': job})

            if path == '/api/job/add':
                data = self._read_json()
                if not (data.get('title') and data.get('description')):
                    return self._send(400, {'error': 'Faltan puesto o descripción.'})
                job = {
                    'id': data.get('id') or ('man_' + str(int(time.time() * 1000))),
                    'title': data.get('title'), 'company': data.get('company') or 'Empresa',
                    'location': data.get('location', ''), 'description': data.get('description'),
                    'link': data.get('link', ''),
                    'easy_apply': bool(data.get('easy_apply', False)),
                    'employment_type': data.get('employment_type', ''),
                    'seniority': data.get('seniority', ''),
                    'source': data.get('source') or 'manual',
                }
                new = db.upsert_jobs([job])
                return self._send(200, {'jobs': new})

            if path == '/api/swipe':
                data = self._read_json()
                jid = data.get('job_id')
                liked = bool(data.get('liked'))
                if not jid:
                    return self._send(400, {'error': 'Falta job_id.'})
                db.set_status(jid, 'liked' if liked else 'passed')
                return self._send(200, {'ok': True})

            if path == '/api/job/status':
                # cambia el estado del empleo: new (pendiente) | liked | passed (no interesa)
                data = self._read_json()
                jid = data.get('job_id')
                status = data.get('status')
                if not jid or status not in ('new', 'liked', 'passed'):
                    return self._send(400, {'error': 'job_id o status inválido.'})
                db.set_status(jid, status)
                return self._send(200, {'ok': True})

            if path == '/api/apply/mark':
                # marca/desmarca una solicitud manualmente como aplicada
                data = self._read_json()
                jid = data.get('job_id')
                status = data.get('status', 'applied')  # applied | clear
                if not jid:
                    return self._send(400, {'error': 'Falta job_id.'})
                if status == 'clear':
                    db.delete_application(jid)
                else:
                    db.save_application(jid, status,
                                        detail={'manual': True, 'review_reason': '',
                                                'submitted_at': time.time() if status == 'applied' else None})
                return self._send(200, {'ok': True})

            if path == '/api/generate':
                data = self._read_json()
                jid = data.get('job_id')
                job = db.get_job(jid) if jid else None
                if not job:
                    return self._send(404, {'error': 'Empleo no encontrado en la base de datos.'})
                profile = CONFIG.get('profile', {})
                base_cv = CONFIG.get('base_cv', '')
                if not base_cv:
                    return self._send(400, {'error': 'Falta tu CV base (guárdalo en Perfil).'})
                if MOCK:
                    time.sleep(1.5)
                    docs = mock_documents(job)
                else:
                    docs = generate_documents(job, profile, base_cv)
                docs['apply_link'] = job.get('link', '')
                db.set_status(jid, 'liked')
                db.save_documents(jid, docs)
                return self._send(200, docs)

            if path == '/api/highlights':
                data = self._read_json()
                jid = data.get('job_id')
                job = db.get_job(jid) if jid else None
                if not job:
                    return self._send(404, {'error': 'Empleo no encontrado en la base de datos.'})
                cached = db.get_highlights(jid)
                if cached:
                    return self._send(200, {'highlights': cached, 'cached': True})
                try:
                    if MOCK:
                        hl = mock_highlights(job)
                    elif not ai_ready():
                        # Sin IA disponible: el cliente usará su heurística local.
                        return self._send(200, {'highlights': None, 'reason': 'no_key'})
                    else:
                        hl = extract_highlights(job)
                except Exception as e:
                    return self._send(200, {'highlights': None, 'error': str(e)})
                db.save_highlights(jid, hl)
                return self._send(200, {'highlights': hl})

            if path == '/api/apply/detect-lang':
                data = self._read_json()
                jid = data.get('job_id')
                job = db.get_job(jid) if jid else None
                if not job:
                    return self._send(404, {'error': 'Empleo no encontrado.'})
                lang, reason = decide_doc_language(job)
                return self._send(200, {'lang': lang, 'reason': reason})

            if path == '/api/apply/prepare':
                # Recibe el HTML del currículum (ya renderizado por el cliente) y
                # lo convierte en un PDF real en disco para subirlo a LinkedIn.
                data = self._read_json()
                jid = data.get('job_id')
                resume_html = data.get('resume_html') or ''
                cover_html = data.get('cover_html') or ''
                cover_text = data.get('cover_text') or ''
                job = db.get_job(jid) if jid else None
                if not job:
                    return self._send(404, {'error': 'Empleo no encontrado.'})
                if not resume_html.strip():
                    return self._send(400, {'error': 'Falta el HTML del currículum.'})
                os.makedirs(GEN_DIR, exist_ok=True)
                # nombres legibles únicos por empleo
                company = re.sub(r'\W+', '_', (job.get('company') or 'cv')).strip('_')[:40] or 'cv'
                title = re.sub(r'\W+', '_', (job.get('title') or '')).strip('_')[:30]
                suffix = f'_{title}' if title else ''
                ext = 'html' if MOCK else 'pdf'

                def _dest(kind):
                    base = f'{kind}_{company}{suffix}'
                    p = os.path.join(GEN_DIR, f'{base}.{ext}')
                    if os.path.exists(p):
                        prev = db.get_application(jid) or {}
                        if prev.get(f'{kind}_pdf') != p:
                            p = os.path.join(GEN_DIR, f'{base}_{str(jid)[-5:]}.{ext}')
                    return p

                lang = data.get('lang') or 'en'
                docs = db.get_documents(jid)

                def _write(html_doc, dest, kind=None):
                    if not MOCK and kind:
                        try:
                            if latex_doc_pdf(docs, kind, lang, dest):
                                return
                        except Exception as e:
                            print(f'[latex] {kind}: {e} — uso HTML')
                    if MOCK:
                        with open(dest, 'w', encoding='utf-8') as f:
                            f.write(html_doc)
                    else:
                        WORKER.call('render_pdf', timeout=90, html_doc=html_doc, out_path=dest)

                resume_out = _dest('resume')
                _write(resume_html, resume_out, 'resume')

                cover_out = None
                if cover_html.strip():
                    cover_out = _dest('cover')
                    _write(cover_html, cover_out, 'cover_letter')

                db.save_application(jid, 'prepared', resume_pdf=resume_out,
                                    cover_pdf=cover_out, cover_text=cover_text or None)
                return self._send(200, {'ok': True, 'path': resume_out, 'cover': cover_out})

            if path == '/api/apply/start':
                data = self._read_json()
                job_ids = data.get('job_ids') or []
                submit_mode = data.get('submit_mode', 'hybrid')  # hybrid | review
                if not job_ids:
                    return self._send(400, {'error': 'No hay empleos seleccionados para aplicar.'})
                with APPLY_LOCK:
                    if not APPLY_STATUS['done']:
                        return self._send(409, {'error': 'Ya hay una tanda de solicitudes en curso.'})
                    task_id = str(int(time.time() * 1000))
                    APPLY_STATUS.update({'task_id': task_id, 'phase': 'applying',
                                         'progress': 0, 'total': len(job_ids),
                                         'results': [], 'done': False, 'error': None,
                                         'current_job': ''})
                t = threading.Thread(target=run_apply_pipeline,
                                     args=(task_id, job_ids, submit_mode, MOCK), daemon=True)
                t.start()
                return self._send(200, {'task_id': task_id, 'total': len(job_ids)})

            if path == '/api/job/delete':
                data = self._read_json()
                jid = data.get('job_id')
                if jid:
                    db.delete_job(jid)
                return self._send(200, {'ok': True})

            if path == '/api/doc/save-override':
                # Guarda el texto editado a mano de un documento (kind+idioma), sin IA.
                data = self._read_json()
                jid = data.get('job_id')
                kind = data.get('kind', 'resume')
                lang = data.get('lang', 'en')
                html = data.get('html') or ''
                docs = db.get_documents(jid) if jid else None
                if not docs:
                    return self._send(404, {'error': 'No hay documentos para este empleo.'})
                if not html.strip():
                    return self._send(400, {'error': 'El contenido está vacío.'})
                if len(html) > 200000:
                    return self._send(400, {'error': 'El contenido es demasiado largo.'})
                docs.setdefault('overrides', {})[f'{kind}_{lang}'] = html
                db.save_documents(jid, docs)
                return self._send(200, {'ok': True})

            if path == '/api/refine':
                data = self._read_json()
                jid = data.get('job_id')
                doc_type = data.get('doc_type', 'resume')
                instruction = (data.get('instruction') or '').strip()
                lang = data.get('lang', 'en')
                job = db.get_job(jid) if jid else None
                docs = db.get_documents(jid) if jid else None
                if not job or not docs:
                    return self._send(404, {'error': 'No hay documentos generados para este empleo.'})
                if not instruction:
                    return self._send(400, {'error': 'Escribe qué cambio quieres.'})
                if MOCK:
                    time.sleep(1.2)
                    lang_docs = docs.get(lang) or {}
                    new_doc = lang_docs.get(doc_type) or {}
                    if isinstance(new_doc, dict):
                        new_doc = dict(new_doc)
                        new_doc['summary'] = (new_doc.get('summary', '') + f' [demo: {instruction}]').strip()
                    new_profile = lang_docs.get('profile')
                else:
                    result = refine_document(job, docs, doc_type, lang, instruction, CONFIG.get('base_cv', ''))
                    # refine_document returns {"profile": {...}, "<doc_type>": {...}}
                    if isinstance(result, dict) and doc_type in result:
                        new_doc = result[doc_type]
                        new_profile = result.get('profile')
                    else:
                        # Fallback: AI returned only the sub-document (old format)
                        new_doc = result
                        new_profile = None
                
                # Guardamos en la estructura bilingüe o en la normal como fallback
                if 'en' in docs and 'es' in docs:
                    if lang not in docs:
                        docs[lang] = {}
                    docs[lang][doc_type] = new_doc
                    if new_profile and isinstance(new_profile, dict):
                        docs[lang]['profile'] = new_profile
                else:
                    docs[doc_type] = new_doc
                    if new_profile and isinstance(new_profile, dict):
                        docs['profile'] = new_profile

                # la IA rehízo este doc → descartamos la edición manual previa (si la había)
                if isinstance(docs.get('overrides'), dict):
                    docs['overrides'].pop(f'{doc_type}_{lang}', None)

                db.save_documents(jid, docs)
                
                # Determinamos el profile de retorno
                ret_profile = docs[lang]['profile'] if ('en' in docs and 'es' in docs) else docs.get('profile')
                return self._send(200, {'doc': new_doc, 'doc_type': doc_type, 'profile': ret_profile, 'lang': lang})

            if path == '/api/chat':
                data = self._read_json()
                q = (data.get('question') or '').strip()
                if not q:
                    return self._send(400, {'error': 'Escribe un mensaje.'})
                history = db.get_chat()[-10:]
                # mensaje interno de arranque → onboarding basado en el CV
                agent_msg = q
                if q == '__INICIO__':
                    agent_msg = ('El usuario acaba de abrir la app. Preséntate en una frase y, '
                                 'basándote en su CV, hazle 2-3 preguntas concretas para definir su '
                                 'búsqueda de empleo. No busques todavía (action=null).')
                # 1) el agente decide qué hacer
                plan = mock_agent_plan(q) if MOCK else agent_plan(agent_msg, history)
                reply = plan.get('reply', '') or ''
                action = plan.get('action') or None
                extra = ''

                # 2) ejecutar la acción
                if action and action.get('type') == 'search':
                    queries = action.get('queries') or []
                    exclude = [x for x in (action.get('exclude') or []) if x]
                    if queries:
                        if MOCK:
                            time.sleep(1.2)
                            found = db.upsert_jobs([dict(j, query=queries[0].get('keywords', '')) for j in MOCK_JOBS])
                        else:
                            found = WORKER.call('search_batch', timeout=1200, queries=queries, per_query_limit=6)
                        if exclude:
                            kept = []
                            for j in found:
                                d = (j.get('description') or '').lower()
                                if any(x.lower() in d for x in exclude):
                                    db.delete_job(j['id'])  # descarta lo que pide algo excluido
                                else:
                                    kept.append(j)
                            found = kept
                        CONFIG['last_search'] = {'queries': queries}
                        save_config(CONFIG)
                        extra = (f"\n\n✅ Guardé **{len(found)}** empleos"
                                 + (f" (excluidos los que piden: {', '.join(exclude)})" if exclude else '')
                                 + ". Ve a **Swipe** para deslizarlos." + _links_block(found, 8))

                elif action and action.get('type') == 'filter':
                    exclude = [x for x in (action.get('exclude') or []) if x]
                    loc = (action.get('location') or '').lower()
                    txt = (action.get('text') or '').lower()
                    stt = action.get('status') or ''

                    def _ok(j):
                        if loc and loc not in (j.get('location') or '').lower():
                            return False
                        if stt and j.get('status') != stt:
                            return False
                        hay = (f"{j.get('title', '')} {j.get('description', '')}").lower()
                        if txt and txt not in hay:
                            return False
                        d = (j.get('description') or '').lower()
                        if any(x.lower() in d for x in exclude):
                            return False
                        return True

                    res = [j for j in db.get_jobs() if _ok(j)]
                    extra = f"\n\n**{len(res)}** empleos coinciden:" + _links_block(res, 15)

                elif action and action.get('type') == 'save_profile':
                    prof = CONFIG.get('profile', {})
                    prof.update({k: v for k, v in (action.get('profile') or {}).items() if v})
                    CONFIG['profile'] = prof
                    save_config(CONFIG)

                answer = reply + extra
                if q != '__INICIO__':
                    db.add_chat('user', q)
                db.add_chat('assistant', answer)
                return self._send(200, {'answer': answer, 'action': action.get('type') if action else None})

            # ---------------- Puente MCP (lo usa mcp_server.py) ------------- #
            if path == '/api/mcp/pull':
                data = self._read_json()
                try:
                    wait = min(float(data.get('wait') or 0), 120.0)
                except (TypeError, ValueError):
                    wait = 0.0
                task = BRIDGE.pull(wait=wait, client=str(data.get('client') or 'mcp'))
                return self._send(200, {'task': task})

            if path == '/api/mcp/answer':
                data = self._read_json()
                tid = str(data.get('task_id') or '')
                err = data.get('error')
                ok = BRIDGE.deliver(tid, content=data.get('content'), error=err)
                if not ok:
                    return self._send(200, {'ok': False,
                                            'error': 'Esa tarea ya no existe (expiró, se canceló o ya fue respondida).'})
                return self._send(200, {'ok': True, 'pending': BRIDGE.snapshot()['pending']})

            if path == '/api/mcp/cancel':
                data = self._read_json()
                ok = BRIDGE.cancel(str(data.get('task_id') or ''),
                                   str(data.get('reason') or 'Cancelada desde el cliente MCP.'))
                return self._send(200, {'ok': ok})

            if path == '/api/mcp/chat-post':
                # Deja que Claude escriba directamente en el chat de la web.
                data = self._read_json()
                content = (data.get('content') or '').strip()
                if not content:
                    return self._send(400, {'error': 'Falta el contenido.'})
                role = 'user' if data.get('role') == 'user' else 'assistant'
                db.add_chat(role, content)
                return self._send(200, {'ok': True})

            if path == '/api/chat/clear':
                db.clear_chat()
                return self._send(200, {'ok': True})

            return self._send(404, {'error': 'not found'})
        except Exception as e:
            return self._send(500, {'error': str(e)})

    # ---------------------------------------------------------------- utils #
    def _serve_static(self, rel):
        rel = rel.lstrip('/')
        full = os.path.normpath(os.path.join(STATIC, rel))
        if not full.startswith(STATIC) or not os.path.isfile(full):
            return self._send(404, {'error': 'not found'})
        ctype = {
            '.html': 'text/html; charset=utf-8',
            '.js': 'application/javascript; charset=utf-8',
            '.css': 'text/css; charset=utf-8',
        }.get(os.path.splitext(full)[1], 'application/octet-stream')
        # sin caché: el navegador siempre toma la última versión (adiós Cmd+Shift+R)
        self._send(200, open(full, 'rb').read(), ctype, {'Cache-Control': 'no-cache, must-revalidate'})

    def log_message(self, *a):
        pass


def main():
    db.init()
    server = ThreadingHTTPServer(('127.0.0.1', PORT), Handler)
    print(f'✅ JobSwiper en  http://localhost:{PORT}' + ('   [MODO MOCK]' if MOCK else ''))
    print(f'   Base de datos: {db.DB_PATH}')
    if ai_provider() == 'mcp':
        print('   🤖 IA: Claude por MCP (sin API key). Conecta el servidor MCP '
              '"jobswiper" en Claude Code / Antigravity')
        print(f'      y pídele: «atiende las tareas de JobSwiper». Puente: '
              f'http://127.0.0.1:{PORT}/api/mcp/status')
    else:
        print('   🤖 IA: API de Google Gemini')
    print('   Ctrl+C para detener.')
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\n👋 Detenido.')


if __name__ == '__main__':
    main()
