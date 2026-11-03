from .common import fetch_json, strip_html, job, q
SOURCE = 'Remotive'

def scrape(query, location=None, limit=25):
    data = fetch_json(f'https://remotive.com/api/remote-jobs?search={q(query)}&limit={limit}')
    return [job('rmt', d['id'], d.get('title'), d.get('company_name'),
                d.get('candidate_required_location') or 'Remote', strip_html(d.get('description')),
                d.get('url'), SOURCE, query, employment_type=d.get('job_type'))
            for d in (data.get('jobs') or [])[:limit]]
