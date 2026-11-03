import xml.etree.ElementTree as ET, re
from .common import fetch, strip_html, job, matches
SOURCE = 'WeWorkRemotely'

def scrape(query, location=None, limit=25):
    root = ET.fromstring(fetch('https://weworkremotely.com/remote-jobs.rss').encode())
    out = []
    for it in root.iter('item'):
        g = lambda t: (it.findtext(t) or '').strip()
        title, desc, link = g('title'), g('description'), g('link')
        if not matches(query, title, g('category'), desc[:3000]):
            continue
        company, _, role = title.partition(': ')
        rid = re.sub(r'\W+', '-', link.rstrip('/').split('/')[-1])[:120]
        out.append(job('wwr', rid, role or title, company if role else '', g('region') or 'Remote',
                       strip_html(desc), link, SOURCE, query, employment_type=g('type') or None))
        if len(out) >= limit:
            break
    return out
