#!/usr/bin/env python3
"""
Servidor MCP de JobSwiper.

Lo lanza tu cliente MCP (Claude Code, Antigravity, Claude Desktop...) y le da
dos cosas:

  1. EL PUENTE DE IA  — JobSwiper deja de necesitar una API key de Gemini.
     Cuando la app necesita IA (generar CV, evaluar ofertas, responder el
     formulario...), encola una tarea. Tú, desde el chat, la tomas con
     `tomar_tarea`, la resuelves y la devuelves con `responder_tarea`.

  2. EL MANDO A DISTANCIA — herramientas para controlar la app en tiempo real
     desde el chat: ver empleos, buscar en LinkedIn, dar like, generar
     documentos, escribir en el chat de la web, cambiar el perfil...

Requiere que `python3 app.py` esté corriendo (por defecto en el puerto 8777).
Habla JSON-RPC 2.0 por stdin/stdout: NUNCA escribas nada a stdout que no sea
un mensaje del protocolo (los logs van a stderr).

Sin dependencias externas: solo stdlib.
"""
import json
import os
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request

BASE_URL = (os.environ.get('JOBSWIPER_URL')
            or 'http://127.0.0.1:%s' % os.environ.get('JOBSWIPER_PORT', '8777')).rstrip('/')
PROTOCOL_VERSION = '2025-06-18'
SERVER_NAME = 'jobswiper'
SERVER_VERSION = '1.0.0'


def log(*a):
    print('[jobswiper-mcp]', *a, file=sys.stderr, flush=True)


# --------------------------------------------------------------------------- #
#  Cliente HTTP hacia app.py                                                  #
# --------------------------------------------------------------------------- #
class AppDown(Exception):
    pass


def _request(method, path, payload=None, timeout=180):
    url = BASE_URL + path
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read().decode('utf-8', 'replace')
    except urllib.error.HTTPError as e:
        body = e.read().decode('utf-8', 'replace')
        try:
            detail = json.loads(body).get('error', body)
        except Exception:
            detail = body
        raise RuntimeError('JobSwiper %s: %s' % (e.code, detail))
    except urllib.error.URLError as e:
        raise AppDown('No puedo hablar con JobSwiper en %s (%s). '
                      '¿Está corriendo `python3 app.py`?' % (BASE_URL, e.reason))
    return json.loads(body) if body else {}


def get(path, timeout=60):
    return _request('GET', path, None, timeout)


def post(path, payload=None, timeout=180):
    return _request('POST', path, payload or {}, timeout)


def post_async(path, payload=None):
    """Lanza una acción que a su vez necesitará IA (y que por tanto encolará una
    tarea que TÚ debes atender). Si esperásemos la respuesta aquí nos
    bloquearíamos a nosotros mismos: el resultado se guarda en la base de datos
    de la app, así que disparamos y volvemos enseguida."""
    def run():
        try:
            post(path, payload, timeout=1800)
        except Exception as e:  # noqa: BLE001 — solo informativo
            log('acción en segundo plano falló:', path, e)
    t = threading.Thread(target=run, daemon=True)
    t.start()


# --------------------------------------------------------------------------- #
#  Utilidades de formato                                                      #
# --------------------------------------------------------------------------- #
def fmt_job(j, full=False):
    line = '· [%s] %s @ %s — %s (id: %s)' % (
        j.get('status', '?'), j.get('title', '?'), j.get('company', '?'),
        j.get('location') or 's/ubicación', j.get('id'))
    if j.get('link'):
        line += '\n  %s' % j['link']
    if full:
        desc = (j.get('description') or '').strip()
        if desc:
            line += '\n  %s' % desc[:1500]
    return line


def as_json(obj):
    return json.dumps(obj, ensure_ascii=False, indent=2)


def describe_task(task):
    """Convierte una tarea de la cola en instrucciones claras para el modelo."""
    if not task:
        return 'No hay ninguna tarea pendiente ahora mismo.'
    quiere = ('SOLO un objeto JSON válido, sin ``` ni texto alrededor'
              if task.get('kind') == 'json' else 'texto plano')
    partes = [
        '📥 TAREA %s — %s' % (task['id'], task.get('label') or 'Petición de IA'),
        '',
        'Resuélvela tú mismo y devuélvela con la herramienta `responder_tarea`',
        '(tarea_id="%s"). Formato esperado: %s.' % (task['id'], quiere),
        'No inventes datos que no estén en el prompt.',
        '',
        '───────────────── PROMPT DE JOBSWIPER ─────────────────',
        task.get('prompt', ''),
        '───────────────────────────────────────────────────────',
    ]
    hist = task.get('history') or []
    if hist:
        partes.insert(-1, 'Historial previo: ' + as_json(hist)[:2000])
    return '\n'.join(partes)


# --------------------------------------------------------------------------- #
#  Herramientas                                                               #
# --------------------------------------------------------------------------- #
TOOLS = []


def tool(name, description, schema):
    def deco(fn):
        TOOLS.append({
            'name': name,
            'description': description,
            'inputSchema': {'type': 'object', 'properties': schema.get('properties', {}),
                            'required': schema.get('required', [])},
            '_fn': fn,
        })
        return fn
    return deco


# ------------------------------- puente de IA ------------------------------ #
@tool('estado',
      'Estado de JobSwiper: proveedor de IA activo, empleos guardados, matches y '
      'cuántas tareas de IA están esperando tu respuesta. Úsalo primero.',
      {'properties': {}})
def t_estado(_):
    st = get('/api/mcp/status?touch=1')
    stats = get('/api/stats')
    out = ['🧭 JobSwiper en %s' % BASE_URL,
           'Proveedor de IA: %s%s' % (st.get('provider'), '  [MODO MOCK]' if st.get('mock') else ''),
           'Empleos: %s' % as_json(stats),
           'Tareas de IA esperando: %s (%s sin reclamar, %s reclamadas)' % (
               st.get('waiting'), st.get('pending'), st.get('taken')),
           'Resueltas por MCP en esta sesión: %s' % st.get('served')]
    if st.get('provider') != 'mcp':
        out.append('\n⚠️  El proveedor activo NO es MCP: la app está usando la API de '
                   'Gemini. Cámbialo en Ajustes o con la herramienta `usar_mcp`.')
    for t in st.get('tasks') or []:
        out.append('   - %s [%s] %s' % (t['id'], t['status'], t['label']))
    return '\n'.join(out)


@tool('usar_mcp',
      'Cambia el proveedor de IA de JobSwiper. activar=true → la app te pedirá a TI '
      '(Claude, por MCP) cada generación en vez de llamar a la API de Gemini.',
      {'properties': {'activar': {'type': 'boolean',
                                  'description': 'true = usar MCP (por defecto), false = volver a Gemini'}}})
def t_usar_mcp(args):
    on = args.get('activar', True)
    post('/api/config', {'ai_provider': 'mcp' if on else 'gemini'})
    return ('✅ JobSwiper usará ahora a Claude por MCP. Deja abierta esta conversación: '
            'cuando la web pida IA aparecerá una tarea que puedes tomar con `tomar_tarea`.'
            if on else '✅ JobSwiper vuelve a usar la API de Google Gemini.')


@tool('tareas_pendientes',
      'Lista (sin reclamarlas) las peticiones de IA que JobSwiper tiene en espera.',
      {'properties': {}})
def t_tareas_pendientes(_):
    st = get('/api/mcp/status?touch=1')
    tasks = st.get('tasks') or []
    if not tasks:
        return ('No hay tareas pendientes. Cuando pulses algo en la web que necesite IA '
                '(generar documentos, AutoJob, chat...) aparecerá aquí.')
    lines = ['%s tarea(s) en espera:' % len(tasks)]
    for t in tasks:
        lines.append('· %s [%s] %s — %s caracteres\n    %s'
                     % (t['id'], t['status'], t['label'], t['chars'], t.get('preview', '')))
    lines.append('\nUsa `tomar_tarea` para recibir el prompt completo de la primera.')
    return '\n'.join(lines)


@tool('tomar_tarea',
      'Reclama la siguiente petición de IA de JobSwiper y devuelve su prompt completo '
      'para que la resuelvas. Después responde con `responder_tarea`. Puede esperar '
      'hasta `esperar` segundos a que aparezca una (long-poll).',
      {'properties': {'esperar': {'type': 'number',
                                  'description': 'Segundos a esperar si la cola está vacía (0-120, por defecto 0)'}}})
def t_tomar_tarea(args):
    wait = args.get('esperar') or 0
    res = post('/api/mcp/pull', {'wait': wait, 'client': 'claude'}, timeout=float(wait) + 30)
    return describe_task(res.get('task'))


@tool('responder_tarea',
      'Entrega TU respuesta a una tarea de IA de JobSwiper. Si la tarea pedía JSON, '
      'manda solo el JSON. Con siguiente=true te devuelve además la próxima tarea '
      'pendiente para encadenarlas sin parar.',
      {'properties': {
          'tarea_id': {'type': 'string', 'description': 'ID que te dio `tomar_tarea`'},
          'respuesta': {'type': 'string', 'description': 'El contenido generado (JSON o texto)'},
          'error': {'type': 'string', 'description': 'Rellena esto en vez de `respuesta` si no puedes resolverla'},
          'siguiente': {'type': 'boolean', 'description': 'true = devolver también la siguiente tarea pendiente'},
      }, 'required': ['tarea_id']})
def t_responder_tarea(args):
    tid = args.get('tarea_id')
    payload = {'task_id': tid}
    if args.get('error'):
        payload['error'] = args['error']
    else:
        payload['content'] = args.get('respuesta') or ''
    res = post('/api/mcp/answer', payload)
    if not res.get('ok'):
        return '⚠️ %s' % res.get('error', 'No se pudo entregar la respuesta.')
    out = '✅ Respuesta entregada. JobSwiper ya la está usando (mira la web).'
    if args.get('siguiente'):
        nxt = post('/api/mcp/pull', {'wait': 2, 'client': 'claude'}, timeout=40).get('task')
        out += '\n\n' + describe_task(nxt)
    return out


@tool('cancelar_tarea',
      'Descarta una tarea de IA en espera (la web verá un error en lugar de quedarse colgada).',
      {'properties': {'tarea_id': {'type': 'string'},
                      'motivo': {'type': 'string'}}, 'required': ['tarea_id']})
def t_cancelar_tarea(args):
    res = post('/api/mcp/cancel', {'task_id': args.get('tarea_id'),
                                   'reason': args.get('motivo') or 'Cancelada desde Claude.'})
    return '✅ Tarea cancelada.' if res.get('ok') else '⚠️ Esa tarea ya no existía.'


# ------------------------------ mando a distancia -------------------------- #
@tool('empleos',
      'Lista los empleos guardados en JobSwiper, con filtros opcionales.',
      {'properties': {
          'estado': {'type': 'string', 'description': "new | liked | passed"},
          'texto': {'type': 'string', 'description': 'Filtra por título/descripción'},
          'ubicacion': {'type': 'string'},
          'limite': {'type': 'number', 'description': 'Por defecto 25'},
          'detalle': {'type': 'boolean', 'description': 'true = incluir descripción'},
      }})
def t_empleos(args):
    jobs = get('/api/jobs').get('jobs', [])
    est = (args.get('estado') or '').strip()
    txt = (args.get('texto') or '').strip().lower()
    loc = (args.get('ubicacion') or '').strip().lower()
    if est:
        jobs = [j for j in jobs if j.get('status') == est]
    if loc:
        jobs = [j for j in jobs if loc in (j.get('location') or '').lower()]
    if txt:
        jobs = [j for j in jobs
                if txt in ('%s %s' % (j.get('title', ''), j.get('description', ''))).lower()]
    total = len(jobs)
    limite = int(args.get('limite') or 25)
    jobs = jobs[:limite]
    if not jobs:
        return 'Ningún empleo coincide con ese filtro.'
    head = 'Mostrando %s de %s empleo(s):' % (len(jobs), total)
    return head + '\n' + '\n'.join(fmt_job(j, args.get('detalle')) for j in jobs)


@tool('ver_empleo',
      'Muestra un empleo concreto con su descripción completa.',
      {'properties': {'job_id': {'type': 'string'}}, 'required': ['job_id']})
def t_ver_empleo(args):
    jid = str(args['job_id'])
    for j in get('/api/jobs').get('jobs', []):
        if str(j.get('id')) == jid:
            return fmt_job(j, full=True)
    return 'No encontré ese empleo (id: %s).' % jid


@tool('buscar_empleos',
      'Busca ofertas de Easy Apply en LinkedIn y las guarda en JobSwiper. Necesita '
      'que la sesión de LinkedIn esté conectada. Puede tardar varios minutos.',
      {'properties': {
          'keywords': {'type': 'string', 'description': 'Título a buscar, p.ej. "controls engineer"'},
          'ubicacion': {'type': 'string', 'description': 'Ciudad o país'},
          'limite': {'type': 'number', 'description': 'Cuántas ofertas traer (por defecto 6)'},
      }, 'required': ['keywords']})
def t_buscar_empleos(args):
    payload = {'keywords': args['keywords'],
               'location': args.get('ubicacion') or '',
               'limit': int(args.get('limite') or 6)}
    res = post('/api/search', payload, timeout=1200)
    jobs = res.get('jobs') or []
    if not jobs:
        return 'La búsqueda no devolvió ofertas (o LinkedIn pidió verificación).'
    return ('✅ Guardé %s oferta(s) — ya están en la web, pestaña Swipe:\n' % len(jobs)
            + '\n'.join(fmt_job(j) for j in jobs))


@tool('swipe',
      'Marca un empleo como "me gusta" (like) o descartado (pass), igual que deslizar '
      'en la web. Con like=true además puedes generar los documentos.',
      {'properties': {'job_id': {'type': 'string'},
                      'like': {'type': 'boolean', 'description': 'true = me gusta, false = descartar'}},
       'required': ['job_id', 'like']})
def t_swipe(args):
    post('/api/swipe', {'job_id': str(args['job_id']), 'liked': bool(args.get('like'))})
    return ('❤️ Marcado como "me gusta".' if args.get('like') else '👋 Descartado.')


@tool('generar_documentos',
      'Pide a JobSwiper que genere Resume + CV + Cover Letter para un empleo. OJO: la '
      'generación te la pedirá a TI — se lanza en segundo plano y después debes llamar '
      'a `tomar_tarea` para resolver el prompt. Al terminar, míralos con `ver_documentos`.',
      {'properties': {'job_id': {'type': 'string'}}, 'required': ['job_id']})
def t_generar_documentos(args):
    post_async('/api/generate', {'job_id': str(args['job_id'])})
    return ('🚀 Generación lanzada para el empleo %s.\n'
            'Ahora llama a `tomar_tarea` (esperar=30) para recibir el prompt y '
            'redactar los documentos.' % args['job_id'])


@tool('ver_documentos',
      'Devuelve los documentos ya generados (Resume/CV/Cover Letter) de un empleo.',
      {'properties': {'job_id': {'type': 'string'},
                      'idioma': {'type': 'string', 'description': "'es' o 'en' (por defecto ambos)"}},
       'required': ['job_id']})
def t_ver_documentos(args):
    jid = str(args['job_id'])
    for m in get('/api/matches').get('matches', []):
        job = m.get('job') or {}
        if str(job.get('id')) != jid:
            continue
        docs = m.get('docs')
        if not docs:
            return ('El empleo "%s" está en matches pero aún no tiene documentos '
                    '(¿sigue generándose?). Usa `generar_documentos`.' % job.get('title'))
        lang = args.get('idioma')
        if lang and isinstance(docs, dict) and lang in docs:
            docs = docs[lang]
        return '📄 %s @ %s\n%s' % (job.get('title'), job.get('company'), as_json(docs)[:12000])
    return ('Ese empleo no está en tus matches todavía. Dale like con `swipe` y luego '
            'usa `generar_documentos`.')


@tool('sugerir_titulos',
      'Pide a JobSwiper los mejores títulos de puesto para el CV del usuario. Se lanza '
      'en segundo plano; atiende la tarea con `tomar_tarea`.',
      {'properties': {}})
def t_sugerir_titulos(_):
    post_async('/api/suggest-titles', {})
    return ('🚀 Lanzado. Llama a `tomar_tarea` (esperar=30) para resolver la sugerencia; '
            'el resultado aparecerá en la pestaña Swipe de la web.')


@tool('perfil',
      'Lee o actualiza el perfil y el CV base del usuario que JobSwiper usa en todos '
      'los prompts. Sin argumentos, solo lo lee.',
      {'properties': {
          'cv_base': {'type': 'string', 'description': 'Texto completo del CV base (reemplaza el actual)'},
          'perfil': {'type': 'object', 'description': 'Campos a fusionar: location, field, seniority, relocate, authorization, experience, education, modality'},
      }})
def t_perfil(args):
    cfg = get('/api/config')
    cambios = {}
    if args.get('cv_base'):
        cambios['base_cv'] = args['cv_base']
    if args.get('perfil'):
        prof = dict(cfg.get('profile') or {})
        prof.update({k: v for k, v in args['perfil'].items() if v not in (None, '')})
        cambios['profile'] = prof
    if cambios:
        post('/api/config', cambios)
        cfg = get('/api/config')
        prefijo = '✅ Perfil actualizado.\n\n'
    else:
        prefijo = ''
    return prefijo + 'Perfil: %s\n\nCV base (%s caracteres):\n%s' % (
        as_json(cfg.get('profile') or {}), len(cfg.get('base_cv') or ''),
        (cfg.get('base_cv') or '(vacío)')[:3000])


@tool('escribir_en_chat',
      'Escribe un mensaje en el chat de la página web de JobSwiper, para que el usuario '
      'lo vea allí (no dispara ninguna acción, solo lo deja escrito).',
      {'properties': {'mensaje': {'type': 'string'},
                      'como_usuario': {'type': 'boolean', 'description': 'true = aparecer como el usuario'}},
       'required': ['mensaje']})
def t_escribir_en_chat(args):
    post('/api/mcp/chat-post', {'content': args['mensaje'],
                                'role': 'user' if args.get('como_usuario') else 'assistant'})
    return '✅ Escrito en el chat de la web.'


@tool('linkedin_estado',
      'Comprueba si la sesión de LinkedIn está conectada (necesaria para buscar).',
      {'properties': {'conectar': {'type': 'boolean',
                                   'description': 'true = abrir el navegador para iniciar sesión'}}})
def t_linkedin_estado(args):
    if args.get('conectar'):
        post('/api/linkedin/connect', {}, timeout=120)
        return ('🌐 Abrí el navegador. Inicia sesión tú mismo en LinkedIn; la sesión '
                'queda guardada en .linkedin-profile/.')
    return as_json(get('/api/linkedin/status'))


# --------------------------------------------------------------------------- #
#  Bucle JSON-RPC por stdio                                                   #
# --------------------------------------------------------------------------- #
def call_tool(name, args):
    for t in TOOLS:
        if t['name'] == name:
            return t['_fn'](args or {})
    raise RuntimeError('Herramienta desconocida: %s' % name)


def handle(msg):
    """Devuelve la respuesta JSON-RPC, o None si es una notificación."""
    method = msg.get('method')
    mid = msg.get('id')

    if method == 'initialize':
        return {'jsonrpc': '2.0', 'id': mid, 'result': {
            'protocolVersion': msg.get('params', {}).get('protocolVersion') or PROTOCOL_VERSION,
            'capabilities': {'tools': {'listChanged': False}},
            'serverInfo': {'name': SERVER_NAME, 'version': SERVER_VERSION},
            'instructions': (
                'JobSwiper delega en ti toda su IA. Si el usuario pide generar CVs, '
                'evaluar ofertas o cualquier cosa de la app, usa `estado` para ver si '
                'hay tareas en espera, `tomar_tarea` para recibir el prompt, resuélvelo '
                'tú y devuélvelo con `responder_tarea` (encadena con siguiente=true '
                'mientras queden tareas).'),
        }}

    if method in ('notifications/initialized', 'notifications/cancelled'):
        return None

    if method == 'ping':
        return {'jsonrpc': '2.0', 'id': mid, 'result': {}}

    if method == 'tools/list':
        return {'jsonrpc': '2.0', 'id': mid, 'result': {
            'tools': [{k: v for k, v in t.items() if not k.startswith('_')} for t in TOOLS]}}

    if method == 'tools/call':
        params = msg.get('params') or {}
        try:
            text = call_tool(params.get('name'), params.get('arguments'))
            is_error = False
        except AppDown as e:
            text, is_error = '🔌 %s' % e, True
        except Exception as e:  # noqa: BLE001 — se lo devolvemos al modelo
            text, is_error = '❌ %s' % e, True
        return {'jsonrpc': '2.0', 'id': mid, 'result': {
            'content': [{'type': 'text', 'text': str(text)}], 'isError': is_error}}

    if method in ('resources/list', 'prompts/list'):
        key = 'resources' if method.startswith('resources') else 'prompts'
        return {'jsonrpc': '2.0', 'id': mid, 'result': {key: []}}

    if mid is None:
        return None
    return {'jsonrpc': '2.0', 'id': mid,
            'error': {'code': -32601, 'message': 'Método no soportado: %s' % method}}


def main():
    log('escuchando; JobSwiper en', BASE_URL)
    out = sys.stdout
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        try:
            resp = handle(msg)
        except Exception as e:  # noqa: BLE001 — nunca tumbamos el servidor
            log('error interno:', e)
            resp = {'jsonrpc': '2.0', 'id': msg.get('id'),
                    'error': {'code': -32603, 'message': str(e)}}
        if resp is not None:
            out.write(json.dumps(resp, ensure_ascii=False) + '\n')
            out.flush()
    log('stdin cerrado, saliendo')


if __name__ == '__main__':
    main()
