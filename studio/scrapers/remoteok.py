from .common import fetch_json, strip_html, job, matches
SOURCE = 'RemoteOK'

def scrape(query, location=None, limit=25):
    data = fetch_json('https://remoteok.com/api')
    out = []
    for d in data:
        if not isinstance(d, dict) or 'id' not in d or 'position' not in d:
            continue
        tags = ' '.join(d.get('tags') or [])
        if not matches(query, d.get('position'), tags, d.get('description', '')[:2000]):
            continue
        out.append(job('rok', d['id'], d.get('position'), d.get('company'), d.get('location') or 'Remote',
                       strip_html(d.get('description')), d.get('url') or d.get('apply_url'), SOURCE, query,
                       employment_type='Remote'))
        if len(out) >= limit:
            break
    return out
