#!/usr/bin/env python3
"""Capa de base de datos (SQLite, solo librería estándar).

Guarda de forma permanente los empleos scrapeados, el estado de cada swipe
(new/liked/passed), los documentos generados por la IA y el historial del chat.
El archivo vive en jobswiper.db (gitignored).
"""
import json
import os
import sqlite3
import threading
import time

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'jobswiper.db')
_lock = threading.Lock()  # serializa escrituras entre hilos del servidor

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id              TEXT PRIMARY KEY,
    title           TEXT,
    company         TEXT,
    location        TEXT,
    description     TEXT,
    link            TEXT,
    easy_apply      INTEGER DEFAULT 1,
    employment_type TEXT,
    seniority       TEXT,
    query           TEXT,
    source          TEXT,
    status          TEXT DEFAULT 'new',   -- new | liked | passed
    created_at      REAL
);
CREATE TABLE IF NOT EXISTS documents (
    job_id     TEXT PRIMARY KEY,
    data       TEXT,          -- JSON con {profile, resume, cv, cover_letter, match_notes, apply_link}
    created_at REAL,
    FOREIGN KEY (job_id) REFERENCES jobs(id)
);
CREATE TABLE IF NOT EXISTS highlights (
    job_id     TEXT PRIMARY KEY,
    data       TEXT,          -- JSON con {tldr, salary, modality, seniority, highlights[], skills[], flags[]}
    created_at REAL,
    FOREIGN KEY (job_id) REFERENCES jobs(id)
);
CREATE TABLE IF NOT EXISTS chat (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    role       TEXT,        -- user | assistant
    content    TEXT,
    created_at REAL
);
CREATE TABLE IF NOT EXISTS autojob_results (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    data       TEXT,          -- JSON con el resultado completo del pipeline
    created_at REAL
);
CREATE TABLE IF NOT EXISTS applications (
    job_id      TEXT PRIMARY KEY,
    status      TEXT,          -- prepared | applied | needs_review | error | skipped
    resume_pdf  TEXT,          -- ruta al PDF del currículum generado en disco
    cover_pdf   TEXT,          -- ruta al PDF del cover letter (para adjuntar)
    cover_text  TEXT,          -- texto del cover letter (para pegar en un textarea)
    detail      TEXT,          -- JSON {questions, answers, log, error, submitted_at, review_reason}
    created_at  REAL,
    FOREIGN KEY (job_id) REFERENCES jobs(id)
);
CREATE TABLE IF NOT EXISTS answer_memory (
    question_key TEXT PRIMARY KEY,  -- etiqueta de la pregunta normalizada
    answer       TEXT,
    created_at   REAL
);
"""

JOB_FIELDS = ('id', 'title', 'company', 'location', 'description', 'link',
              'easy_apply', 'employment_type', 'seniority', 'query', 'source')


def _conn():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def init():
    with _lock, _conn() as c:
        # migración: si existe una tabla documents con el esquema viejo (sin 'data'),
        # se recrea (los documentos se pueden regenerar; los empleos NO se tocan).
        cols = [r[1] for r in c.execute("PRAGMA table_info(documents)").fetchall()]
        if cols and 'data' not in cols:
            c.execute('DROP TABLE documents')
        c.executescript(SCHEMA)
        # migración: añadir columnas de cover letter a 'applications' si faltan
        acols = [r[1] for r in c.execute("PRAGMA table_info(applications)").fetchall()]
        if acols:
            for col in ('cover_pdf', 'cover_text'):
                if col not in acols:
                    c.execute(f'ALTER TABLE applications ADD COLUMN {col} TEXT')


# --------------------------------------------------------------------------- #
#  Empleos                                                                    #
# --------------------------------------------------------------------------- #
def upsert_jobs(jobs):
    """Inserta empleos nuevos (ignora los que ya existen por id).
    Devuelve la lista de los que realmente se insertaron (status 'new')."""
    inserted = []
    now = time.time()
    with _lock, _conn() as c:
        for j in jobs:
            jid = j.get('id')
            if not jid:
                continue
            exists = c.execute('SELECT 1 FROM jobs WHERE id=?', (jid,)).fetchone()
            if exists:
                continue
            c.execute(
                """INSERT INTO jobs
                   (id,title,company,location,description,link,easy_apply,
                    employment_type,seniority,query,source,status,created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?, 'new', ?)""",
                (jid, j.get('title'), j.get('company'), j.get('location'),
                 j.get('description'), j.get('link'), 1 if j.get('easy_apply', True) else 0,
                 j.get('employment_type'), j.get('seniority'), j.get('query'),
                 j.get('source', 'LinkedIn'), now))
            inserted.append(_row_to_job(c.execute('SELECT * FROM jobs WHERE id=?', (jid,)).fetchone()))
    return inserted


def _row_to_job(row):
    d = dict(row)
    d['easy_apply'] = bool(d.get('easy_apply'))
    return d


def get_jobs(status=None):
    with _conn() as c:
        if status:
            rows = c.execute('SELECT * FROM jobs WHERE status=? ORDER BY created_at', (status,)).fetchall()
        else:
            rows = c.execute('SELECT * FROM jobs ORDER BY created_at DESC').fetchall()
    return [_row_to_job(r) for r in rows]


def get_job(job_id):
    with _conn() as c:
        row = c.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()
    return _row_to_job(row) if row else None


def set_status(job_id, status):
    with _lock, _conn() as c:
        c.execute('UPDATE jobs SET status=? WHERE id=?', (status, job_id))


def delete_job(job_id):
    with _lock, _conn() as c:
        c.execute('DELETE FROM documents WHERE job_id=?', (job_id,))
        c.execute('DELETE FROM highlights WHERE job_id=?', (job_id,))
        c.execute('DELETE FROM applications WHERE job_id=?', (job_id,))
        c.execute('DELETE FROM jobs WHERE id=?', (job_id,))


# --------------------------------------------------------------------------- #
#  Documentos generados                                                       #
# --------------------------------------------------------------------------- #
def save_documents(job_id, docs):
    with _lock, _conn() as c:
        c.execute(
            'INSERT OR REPLACE INTO documents (job_id,data,created_at) VALUES (?,?,?)',
            (job_id, json.dumps(docs, ensure_ascii=False), time.time()))


def get_documents(job_id):
    with _conn() as c:
        row = c.execute('SELECT data FROM documents WHERE job_id=?', (job_id,)).fetchone()
    if not row or not row['data']:
        return None
    try:
        return json.loads(row['data'])
    except Exception:
        return None


def save_highlights(job_id, data):
    with _lock, _conn() as c:
        c.execute(
            'INSERT OR REPLACE INTO highlights (job_id,data,created_at) VALUES (?,?,?)',
            (job_id, json.dumps(data, ensure_ascii=False), time.time()))


def get_highlights(job_id):
    with _conn() as c:
        row = c.execute('SELECT data FROM highlights WHERE job_id=?', (job_id,)).fetchone()
    if not row or not row['data']:
        return None
    try:
        return json.loads(row['data'])
    except Exception:
        return None


def doc_job_ids():
    """Conjunto de job_id que YA tienen documentos generados."""
    with _conn() as c:
        rows = c.execute('SELECT job_id FROM documents').fetchall()
    return {r['job_id'] for r in rows}


def get_matches():
    """Empleos con 'me gusta', con sus documentos si ya se generaron."""
    with _conn() as c:
        rows = c.execute("SELECT * FROM jobs WHERE status='liked' ORDER BY created_at DESC").fetchall()
    out = []
    for r in rows:
        job = _row_to_job(r)
        out.append({'job': job, 'docs': get_documents(job['id'])})
    return out


# --------------------------------------------------------------------------- #
#  Chat                                                                       #
# --------------------------------------------------------------------------- #
def add_chat(role, content):
    with _lock, _conn() as c:
        c.execute('INSERT INTO chat (role,content,created_at) VALUES (?,?,?)',
                  (role, content, time.time()))


def get_chat():
    with _conn() as c:
        rows = c.execute('SELECT role,content FROM chat ORDER BY id').fetchall()
    return [dict(r) for r in rows]


def delete_jobs_by_status(status):
    """Borra los empleos con ese estado (y sus resúmenes). Devuelve cuántos."""
    with _lock, _conn() as c:
        ids = [r[0] for r in c.execute('SELECT id FROM jobs WHERE status=?', (status,))]
        c.executemany('DELETE FROM highlights WHERE job_id=?', [(i,) for i in ids])
        c.execute('DELETE FROM jobs WHERE status=?', (status,))
    return len(ids)


def clear_chat():
    with _lock, _conn() as c:
        c.execute('DELETE FROM chat')


def stats():
    with _conn() as c:
        row = c.execute(
            """SELECT
                 COUNT(*) AS total,
                 SUM(status='new') AS nuevos,
                 SUM(status='liked') AS gustados,
                 SUM(status='passed') AS descartados
               FROM jobs""").fetchone()
    return dict(row) if row else {}


# --------------------------------------------------------------------------- #
#  AutoJob results                                                             #
# --------------------------------------------------------------------------- #
def save_autojob_result(data):
    with _lock, _conn() as c:
        c.execute(
            'INSERT INTO autojob_results (data, created_at) VALUES (?, ?)',
            (json.dumps(data, ensure_ascii=False), time.time()))


# --------------------------------------------------------------------------- #
#  Aplicaciones automáticas (Easy Apply)                                      #
# --------------------------------------------------------------------------- #
def save_application(job_id, status, resume_pdf=None, detail=None,
                     cover_pdf=None, cover_text=None):
    with _lock, _conn() as c:
        c.execute(
            """INSERT INTO applications (job_id,status,resume_pdf,cover_pdf,cover_text,detail,created_at)
               VALUES (?,?,?,?,?,?,?)
               ON CONFLICT(job_id) DO UPDATE SET
                 status=excluded.status,
                 resume_pdf=COALESCE(excluded.resume_pdf, applications.resume_pdf),
                 cover_pdf=COALESCE(excluded.cover_pdf, applications.cover_pdf),
                 cover_text=COALESCE(excluded.cover_text, applications.cover_text),
                 detail=COALESCE(excluded.detail, applications.detail),
                 created_at=excluded.created_at""",
            (job_id, status, resume_pdf, cover_pdf, cover_text,
             json.dumps(detail, ensure_ascii=False) if detail is not None else None,
             time.time()))


def get_application(job_id):
    with _conn() as c:
        row = c.execute('SELECT * FROM applications WHERE job_id=?', (job_id,)).fetchone()
    if not row:
        return None
    d = dict(row)
    try:
        d['detail'] = json.loads(d['detail']) if d.get('detail') else {}
    except Exception:
        d['detail'] = {}
    return d


def get_applications():
    with _conn() as c:
        rows = c.execute('SELECT * FROM applications ORDER BY created_at DESC').fetchall()
    out = []
    for r in rows:
        d = dict(r)
        try:
            d['detail'] = json.loads(d['detail']) if d.get('detail') else {}
        except Exception:
            d['detail'] = {}
        out.append(d)
    return out


def delete_application(job_id):
    with _lock, _conn() as c:
        c.execute('DELETE FROM applications WHERE job_id=?', (job_id,))


def count_applied_since(cutoff_ts):
    """Cuántas solicitudes quedaron como 'applied' desde cutoff_ts (para el tope diario)."""
    with _conn() as c:
        row = c.execute(
            "SELECT COUNT(*) AS n FROM applications WHERE status='applied' AND created_at >= ?",
            (cutoff_ts,)).fetchone()
    return row['n'] if row else 0


def next_applied_expiry(cutoff_ts):
    """Marca de tiempo del 'applied' más antiguo dentro de la ventana (para saber
    cuándo se libera un cupo). Devuelve None si no hay ninguno."""
    with _conn() as c:
        row = c.execute(
            "SELECT MIN(created_at) AS t FROM applications WHERE status='applied' AND created_at >= ?",
            (cutoff_ts,)).fetchone()
    return row['t'] if row and row['t'] else None


def save_answer_memory(question_key, answer):
    if not question_key:
        return
    with _lock, _conn() as c:
        c.execute(
            'INSERT OR REPLACE INTO answer_memory (question_key,answer,created_at) VALUES (?,?,?)',
            (question_key, answer, time.time()))


def get_answer_memory():
    """Devuelve {question_key: answer} con todas las respuestas ya dadas."""
    with _conn() as c:
        rows = c.execute('SELECT question_key,answer FROM answer_memory').fetchall()
    return {r['question_key']: r['answer'] for r in rows}


def get_latest_autojob_result():
    with _conn() as c:
        row = c.execute(
            'SELECT data FROM autojob_results ORDER BY id DESC LIMIT 1').fetchone()
    if not row or not row['data']:
        return None
    try:
        return json.loads(row['data'])
    except Exception:
        return None
