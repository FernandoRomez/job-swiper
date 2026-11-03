from .common import fetch_json, strip_html, job, matches, pause
SOURCE = 'Arbeitnow'

def scrape(query, location=None, limit=25):
    out = []
    for page in range(1, 4):
        data = fetch_json(f'https://www.arbeitnow.com/api/job-board-api?page={page}')
        for d in data.get('data') or []:
            if matches(query, d.get('title'), ' '.join(d.get('tags') or [])):
                out.append(job('arb', d.get('slug'), d.get('title'), d.get('company_name'),
                               d.get('location') + (' (Remote)' if d.get('remote') else ''),
                               strip_html(d.get('description')), d.get('url'), SOURCE, query,
                               employment_type=', '.join(d.get('job_types') or []) or None))
                if len(out) >= limit:
                    return out
        pause()
    return out
