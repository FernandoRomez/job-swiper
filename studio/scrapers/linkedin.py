"""LinkedIn vía endpoints guest (sin login). ids li_<n> compatibles con postular.py abrir."""
import re, html
from .common import fetch, strip_html, text, job, q, pause
SOURCE = 'LinkedIn'
BASE = 'https://www.linkedin.com/jobs-guest/jobs/api'


def _detail(jid):
    try:
        h = fetch(f'{BASE}/jobPosting/{jid}', timeout=15)
    except Exception:
        return '', None, None
    m = re.search(r'(?s)<div class="show-more-less-html__markup[^"]*">(.*?)</div>', h)
    crit = dict((text(a).lower(), text(b)) for a, b in re.findall(
        r'(?s)<h3 class="description__job-criteria-subheader">(.*?)</h3>\s*<span[^>]*>(.*?)</span>', h))
    sen = crit.get('seniority level') or crit.get('nivel de antigüedad')
    emp = crit.get('employment type') or crit.get('tipo de empleo')
    return strip_html(m.group(1)) if m else '', emp, sen


def scrape(query, location='Mexico', limit=25):
    out, seen, start = [], set(), 0
    while len(out) < limit and start < 200:
        h = fetch(f'{BASE}/seeMoreJobPostings/search?keywords={q(query)}&location={q(location or "Mexico")}'
                  f'&f_TPR=r1209600&start={start}', timeout=20)
        cards = re.split(r'<li>', h)[1:]
        if not cards:
            break
        for c in cards:
            m = re.search(r'urn:li:jobPosting:(\d+)', c)
            if not m or m.group(1) in seen:
                continue
            jid = m.group(1); seen.add(jid)
            g = lambda pat: text((re.search(pat, c, re.S) or [None, ''])[1])
            title = g(r'class="base-search-card__title">(.*?)</h3>')
            comp = g(r'class="base-search-card__subtitle">(.*?)</h4>')
            loc = g(r'class="job-search-card__location">(.*?)</span>')
            pause(0.6, 1.4)
            desc, emp, sen = _detail(jid)
            out.append(job('li', jid, title, comp, loc, desc, f'https://www.linkedin.com/jobs/view/{jid}/',
                           SOURCE, query, employment_type=emp, seniority=sen, easy_apply=True))
            if len(out) >= limit:
                break
        start += 10
        pause()
    return out
