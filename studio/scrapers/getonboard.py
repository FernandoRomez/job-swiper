from .common import fetch_json, strip_html, job, q
SOURCE = 'GetOnBoard'

def scrape(query, location=None, limit=25):
    data = fetch_json(f'https://www.getonbrd.com/api/v0/search/jobs?query={q(query)}&per_page={min(limit,100)}&expand=["company"]')
    out = []
    for d in (data.get('data') or [])[:limit]:
        a = d.get('attributes') or {}
        comp = (((a.get('company') or {}).get('data') or {}).get('attributes') or {}).get('name', '')
        loc = ', '.join(filter(None, [a.get('countries') and ', '.join(a['countries']) if isinstance(a.get('countries'), list) else a.get('countries'),
                                      'Remote' if a.get('remote') else None])) or 'LatAm'
        desc = '\n\n'.join(strip_html(a.get(k)) for k in ('description', 'functions', 'desirable', 'benefits') if a.get(k))
        out.append(job('gob', d.get('id'), a.get('title'), comp, loc, desc,
                       (d.get('links') or {}).get('public_url') or f"https://www.getonbrd.com/jobs/{d.get('id')}",
                       SOURCE, query, seniority=str(((a.get('seniority') or {}).get('data') or {}).get('id') or '') or None))
    return out
