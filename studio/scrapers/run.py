"""Ejecuta scrapers y guarda en la BD.

  python3 -m studio.scrapers.run --sources all --query "PLC" --location "Mexico" --limit 25
  python3 studio/scrapers/run.py --sources occ,linkedin --loop 120
"""
import argparse, importlib, os, sys, time, traceback

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # job-swiper/
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
if __package__ in (None, ''):
    __package__ = 'studio.scrapers'

import db  # noqa: E402
from studio.scrapers import SOURCES  # noqa: E402

REMOTE = {'remoteok', 'remotive', 'arbeitnow', 'weworkremotely', 'getonboard'}
PRESETS = ['ingeniero automatización', 'PLC', 'controls engineer', 'robótica', 'embedded']
EN_PRESETS = {'ingeniero automatización': 'automation engineer', 'robótica': 'robotics'}


def run_once(sources, queries, location, limit, log=print):
    counts = {}
    seen = set()
    for src in sources:
        found = new = 0
        t0 = time.time()
        err = None
        try:
            mod = importlib.import_module(f'studio.scrapers.{src}')
            jobs = []
            for qq in queries:
                qx = EN_PRESETS.get(qq, qq) if src in REMOTE else qq
                try:
                    res = mod.scrape(qx, location, limit)
                except Exception as e:
                    err = f'{type(e).__name__}: {e}'
                    if src == 'indeed':   # bloqueo: no insistir con más queries
                        break
                    continue
                for j in res:
                    if j['id'] not in seen and j.get('title'):
                        seen.add(j['id']); jobs.append(j)
            found = len(jobs)
            new = len(db.upsert_jobs(jobs)) if jobs else 0
        except Exception as e:
            err = f'{type(e).__name__}: {e}'
            traceback.print_exc(file=sys.stderr)
        counts[src] = {'found': found, 'new': new, 'error': err}
        status = 'OK ' if found else ('ERR' if err else '-- ')
        log(f'[{status}] {src:<15} encontrados={found:<4} nuevos={new:<4} {time.time()-t0:5.1f}s'
            + (f'  ({err[:120]})' if err and not found else ''), flush=True)
    tf = sum(c['found'] for c in counts.values()); tn = sum(c['new'] for c in counts.values())
    log(f'== Resumen: {tf} encontrados, {tn} nuevos en {db.DB_PATH} '
        f'({sum(1 for c in counts.values() if c["found"])}/{len(counts)} fuentes OK)', flush=True)
    return counts


def main(argv=None):
    ap = argparse.ArgumentParser(description='Job Studio scrapers')
    ap.add_argument('--sources', default='all', help='all | lista separada por comas: ' + ','.join(SOURCES))
    ap.add_argument('--query', default='', help='sin query usa presets de mecatrónica')
    ap.add_argument('--location', default='Mexico')
    ap.add_argument('--limit', type=int, default=25, help='máximo por fuente y query')
    ap.add_argument('--loop', type=float, default=0, help='repetir cada N minutos')
    ap.add_argument('--db', default=None, help='ruta alternativa de BD (pruebas)')
    a = ap.parse_args(argv)
    if a.db:
        db.DB_PATH = a.db
    db.init()
    sources = SOURCES if a.sources == 'all' else [s.strip() for s in a.sources.split(',') if s.strip()]
    bad = [s for s in sources if s not in SOURCES]
    if bad:
        ap.error(f'fuentes desconocidas: {bad}')
    queries = [a.query] if a.query else PRESETS
    while True:
        print(f'--- {time.strftime("%Y-%m-%d %H:%M")} fuentes={",".join(sources)} queries={queries}', flush=True)
        counts = run_once(sources, queries, a.location, a.limit)
        if not a.loop:
            return counts
        time.sleep(a.loop * 60)


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        pass
