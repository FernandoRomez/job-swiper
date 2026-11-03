import re
from .common import fetch, strip_html, text, job, pause
from .computrabajo import _slug
SOURCE = 'OCC'


def _detail(rid):
    try:
        h = fetch(f'https://www.occ.com.mx/empleo/oferta/{rid}/', timeout=15)
    except Exception:
        return ''
    m = re.search(r'(?s)<div[^>]*id="job-detail-container"[^>]*>(.*?)</section>', h) or \
        re.search(r'(?s)Descripci(?:ó|&#xF3;)n</h2>(.*?)</div>\s*</div>', h)
    if m:
        return strip_html(m.group(1))
    m = re.search(r'<meta name="description" content="([^"]*)"', h)
    return text(m.group(1)) if m else ''


def scrape(query, location=None, limit=25):
    loc = _slug(location) if location and _slug(location) not in ('mexico', 'remote') else ''
    url = f'https://www.occ.com.mx/empleos/de-{_slug(query)}/' + (f'en-{loc}/' if loc else '')
    out, page = [], 1
    while len(out) < limit and page <= 3:
        h = fetch(url + (f'?page={page}' if page > 1 else ''), timeout=25)
        parts = re.split(r'<div class="card-job-offer', h)[1:]
        if not parts:
            break
        for c in parts:
            rid = re.search(r"data-id='(\d+)'", c)
            if not rid:
                continue
            c = re.sub(r'(?s)<(svg|script|button)[\s\S]*?</\1>', '', c)
            title = text((re.search(r'(?s)<h2[^>]*>(.*?)</h2>', c) or [0, ''])[1])
            sal = text((re.search(r'(?s)</h2>\s*<span[^>]*>(.*?)</span>', c) or [0, ''])[1])
            comp = text((re.search(r'(?s)aria-label="Ver todos los empleos de[^"]*"[^>]*>(.*?)</a>', c) or [0, ''])[1])
            loc_ = text((re.search(r'(?s)no-alter-loc-text.*?<p[^>]*>(.*?)</p>', c) or [0, ''])[1])
            pause(0.5, 1.2)
            desc = _detail(rid.group(1))
            if sal:
                desc = f'Salario: {sal}\n\n{desc}'
            out.append(job('occ', rid.group(1), title, comp or 'Confidencial', loc_, desc,
                           f'https://www.occ.com.mx/empleo/oferta/{rid.group(1)}/', SOURCE, query))
            if len(out) >= limit:
                break
        page += 1
        pause()
    return out
