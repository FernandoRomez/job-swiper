"""Módulo extra 'pipeline': kanban de postulaciones, seguimiento, recordatorios y analítica.

Tabla propia x_pipeline_stage (no modifica applications). Rutas bajo /api/x/pipeline/.
"""
import json
import os
import sqlite3
import sys
import time
from datetime import datetime

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
import db  # noqa: E402

STAGES = ['guardado', 'aplicado', 'entrevista', 'prueba', 'oferta', 'rechazado', 'sin_respuesta']
STAGE_LABELS = {'guardado': 'Guardado', 'aplicado': 'Aplicado', 'entrevista': 'Entrevista',
                'prueba': 'Prueba técnica', 'oferta': 'Oferta', 'rechazado': 'Rechazado',
                'sin_respuesta': 'Sin respuesta'}
RESPONDED = {'entrevista', 'prueba', 'oferta', 'rechazado'}
APP_MAP = {'applied': 'aplicado', 'prepared': 'guardado', 'needs_review': 'guardado'}
FOLLOWUP_DAYS = 7


def _conn():
    c = sqlite3.connect(db.DB_PATH)
    c.row_factory = sqlite3.Row
    c.execute("""CREATE TABLE IF NOT EXISTS x_pipeline_stage (
        job_id TEXT PRIMARY KEY, stage TEXT, updated_at REAL, notes TEXT,
        next_action TEXT, next_date TEXT, contact TEXT, applied_at REAL)""")
    return c


def _app_time(row):
    try:
        d = json.loads(row['detail'] or '{}')
        if d.get('submitted_at'):
            return float(d['submitted_at'])
    except Exception:
        pass
    return row['created_at'] or time.time()


def _seed(c):
    """Crea filas para applications sin etapa propia (solo lectura de applications)."""
    have = {r[0] for r in c.execute("SELECT job_id FROM x_pipeline_stage")}
    for r in c.execute("SELECT job_id,status,detail,created_at FROM applications").fetchall():
        st = APP_MAP.get(r['status'])
        if not st or r['job_id'] in have:
            continue
        t = _app_time(r)
        c.execute("INSERT INTO x_pipeline_stage(job_id,stage,updated_at,notes,applied_at) VALUES(?,?,?,?,?)",
                  (r['job_id'], st, t, '[]', t if st == 'aplicado' else None))
    c.commit()


def _country(loc):
    l = (loc or '').lower()
    if any(k in l for k in ('remote', 'remoto', 'anywhere', 'worldwide')):
        return 'Remoto'
    if any(k in l for k in ('méxico', 'mexico', 'puebla', 'cdmx', 'jalisco', 'nuevo león', 'querétaro', ', mx')):
        return 'MX'
    if any(k in l for k in ('estados unidos', 'united states', ', us', 'usa')) or \
            (len(l) > 3 and l[-4:-2] == ', ' and l[-2:].isalpha()):
        return 'US'
    return 'Otro'


def _rows(c):
    return [dict(r) for r in c.execute(
        """SELECT p.*, j.title, j.company, j.location, j.source, j.link
           FROM x_pipeline_stage p LEFT JOIN jobs j ON j.id=p.job_id ORDER BY p.updated_at DESC""")]


def _row_out(r):
    r = dict(r)
    try:
        r['notes'] = json.loads(r.get('notes') or '[]')
    except Exception:
        r['notes'] = []
    try:
        r['contact'] = json.loads(r.get('contact') or '{}')
    except Exception:
        r['contact'] = {}
    return r


def _board(c):
    _seed(c)
    cols = {s: [] for s in STAGES}
    for r in _rows(c):
        r = _row_out(r)
        r['notes_count'] = len(r['notes'])
        r.pop('notes')
        cols.setdefault(r['stage'], []).append(r)
    return {'stages': [{'id': s, 'label': STAGE_LABELS[s]} for s in STAGES], 'columns': cols}


def _get(c, job_id):
    _seed(c)
    r = c.execute("""SELECT p.*, j.title, j.company, j.location, j.source, j.link FROM x_pipeline_stage p
                     LEFT JOIN jobs j ON j.id=p.job_id WHERE p.job_id=?""", (job_id,)).fetchone()
    if not r:
        return {'job_id': job_id, 'stage': None, 'notes': [], 'contact': {}, 'next_action': '', 'next_date': ''}
    return _row_out(r)


def _update(c, job_id, body):
    now = time.time()
    cur = c.execute("SELECT * FROM x_pipeline_stage WHERE job_id=?", (job_id,)).fetchone()
    if not cur:
        c.execute("INSERT INTO x_pipeline_stage(job_id,stage,updated_at,notes) VALUES(?,?,?,?)",
                  (job_id, 'guardado', now, '[]'))
        cur = c.execute("SELECT * FROM x_pipeline_stage WHERE job_id=?", (job_id,)).fetchone()
    notes = json.loads(cur['notes'] or '[]')
    fields = {}
    st = body.get('stage')
    if st:
        if st not in STAGES:
            return 400, {'error': 'etapa inválida'}
        if st != cur['stage']:
            notes.append({'ts': now, 'kind': 'stage', 'text': f"{STAGE_LABELS.get(cur['stage'], '—')} → {STAGE_LABELS[st]}"})
            fields['stage'] = st
            if st == 'aplicado' and not cur['applied_at']:
                fields['applied_at'] = now
    if (body.get('note') or '').strip():
        notes.append({'ts': now, 'kind': 'note', 'text': body['note'].strip()})
    for k in ('next_action', 'next_date'):
        if k in body:
            fields[k] = body[k] or ''
    if 'contact' in body:
        fields['contact'] = json.dumps(body['contact'] or {}, ensure_ascii=False)
    fields['notes'] = json.dumps(notes, ensure_ascii=False)
    fields['updated_at'] = now
    sets = ','.join(f'{k}=?' for k in fields)
    c.execute(f"UPDATE x_pipeline_stage SET {sets} WHERE job_id=?", (*fields.values(), job_id))
    c.commit()
    return 200, _get(c, job_id)


def _reminders(c):
    _seed(c)
    now = time.time()
    today = datetime.now().strftime('%Y-%m-%d')
    stale, due = [], []
    for r in _rows(c):
        r = _row_out(r)
        r.pop('notes')
        if r['stage'] == 'aplicado':
            t = r.get('applied_at') or r['updated_at']
            days = int((now - t) // 86400)
            if days > FOLLOWUP_DAYS:
                r['days'] = days
                stale.append(r)
        if r.get('next_date') and r['next_date'] <= today and r['stage'] not in ('rechazado', 'oferta'):
            r['overdue'] = r['next_date'] < today
            due.append(r)
    stale.sort(key=lambda x: -x['days'])
    due.sort(key=lambda x: x['next_date'])
    return {'followup_days': FOLLOWUP_DAYS, 'stale': stale, 'due': due, 'count': len(stale) + len(due)}


def _analytics(c):
    _seed(c)
    rows = _rows(c)
    weeks, by_src, funnel, country = {}, {}, {s: 0 for s in STAGES}, {}
    for r in rows:
        funnel[r['stage']] = funnel.get(r['stage'], 0) + 1
        if r['stage'] == 'guardado':
            continue
        t = r.get('applied_at') or r['updated_at']
        d = datetime.fromtimestamp(t)
        y, w, _ = d.isocalendar()
        k = f'{y}-W{w:02d}'
        weeks[k] = weeks.get(k, 0) + 1
        s = r.get('source') or 'desconocido'
        b = by_src.setdefault(s, {'source': s, 'applied': 0, 'responded': 0})
        b['applied'] += 1
        if r['stage'] in RESPONDED:
            b['responded'] += 1
        cc = _country(r.get('location'))
        country[cc] = country.get(cc, 0) + 1
    for b in by_src.values():
        b['rate'] = round(b['responded'] / b['applied'], 3) if b['applied'] else 0
    # funnel acumulativo: cuántos llegaron al menos a cada etapa
    order = ['aplicado', 'entrevista', 'prueba', 'oferta']
    reach = []
    for i, s in enumerate(order):
        n = sum(funnel.get(x, 0) for x in order[i:])
        if s == 'aplicado':
            n += funnel.get('rechazado', 0) + funnel.get('sin_respuesta', 0)
        reach.append({'stage': s, 'label': STAGE_LABELS[s], 'count': n})
    total_applied = reach[0]['count']
    responded = sum(funnel.get(s, 0) for s in RESPONDED)
    return {
        'per_week': [{'week': k, 'count': weeks[k]} for k in sorted(weeks)][-16:],
        'by_source': sorted(by_src.values(), key=lambda b: -b['applied']),
        'funnel': reach,
        'by_stage': [{'stage': s, 'label': STAGE_LABELS[s], 'count': funnel.get(s, 0)} for s in STAGES],
        'by_country': [{'country': k, 'count': country.get(k, 0)} for k in ('MX', 'US', 'Remoto', 'Otro')],
        'totals': {'applied': total_applied, 'responded': responded,
                   'response_rate': round(responded / total_applied, 3) if total_applied else 0,
                   'saved': funnel.get('guardado', 0)},
    }


def handle(method, path, query, body):
    if not path.startswith('/api/x/pipeline'):
        return None
    sub = path[len('/api/x/pipeline'):].strip('/')
    body = body or {}
    c = _conn()
    try:
        if method == 'GET' and sub in ('', 'board'):
            return 200, _board(c)
        if method == 'GET' and sub == 'reminders':
            return 200, _reminders(c)
        if method == 'GET' and sub == 'analytics':
            return 200, _analytics(c)
        if sub.startswith('job/'):
            jid = sub[4:]
            if not jid:
                return 400, {'error': 'falta job_id'}
            if method == 'GET':
                return 200, _get(c, jid)
            if method in ('POST', 'PUT'):
                return _update(c, jid, body)
            if method == 'DELETE':
                c.execute("DELETE FROM x_pipeline_stage WHERE job_id=?", (jid,))
                c.commit()
                return 200, {'ok': True}
        return 404, {'error': 'ruta pipeline desconocida'}
    except Exception as e:  # noqa: BLE001
        return 500, {'error': str(e)}
    finally:
        c.close()
