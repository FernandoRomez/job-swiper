import re
from .common import fetch, strip_html, text, job, pause
SOURCE = 'Computrabajo'
BASE = 'https://mx.computrabajo.com'


def _slug(s):
    import unicodedata
    s = unicodedata.normalize('NFKD', s or '').encode('ascii', 'ignore').decode().lower()
    return re.sub(r'[^a-z0-9]+', '-', s).strip('-')


def _detail(url):
    try:
        h = fetch(url, timeout=15)
    except Exception:
        return ''
    m = re.search(r'(?s)<div class="mb40 pb40 bb1"[^>]*>(.*?)</div>\s*</div>', h) or \
        re.search(r'(?s)<h3[^>]*>\s*Descripción de la oferta\s*</h3>(.*?)<div class="(?:fs16 )?mt\d+', h) or \
        re.search(r'(?s)<p class="mbB">(.*?)</p>', h)
    if m:
        return strip_html(m.group(1))
    m = re.search(r'<meta name="description" content="([^"]*)"', h)
    return text(m.group(1)) if m else ''


def scrape(query, location=None, limit=25):
    url = f'{BASE}/trabajo-de-{_slug(query)}' + (f'-en-{_slug(location)}' if location and _slug(location) not in ('mexico', 'remote') else '')
    out, page = [], 1
    while len(out) < limit and page <= 3:
        h = fetch(url + (f'?p={page}' if page > 1 else ''), timeout=20)
        arts = re.findall(r'(?s)<article class="box_offer.*?</article>', h)
        if not arts:
            break
        for a in arts:
            rid = re.search(r"data-id='([0-9A-F]+)'", a)
            href = re.search(r'href="(/ofertas-de-trabajo/[^"#]+)', a)
            if not rid or not href:
                continue
            title = text((re.search(r'(?s)<h2[^>]*>\s*<a[^>]*>(.*?)</a>', a) or [0, ''])[1])
            comp = text((re.search(r'(?s)offer-grid-article-company-url>(.*?)</a>', a) or [0, ''])[1])
            loc = text((re.search(r'(?s)<p class="fs16 fc_base mt5">\s*<span class="mr10">(.*?)</span>', a) or [0, ''])[1])
            sal = text((re.search(r'(?s)i_salary"></span>(.*?)</span>', a) or [0, ''])[1])
            link = BASE + href.group(1)
            pause(0.5, 1.2)
            desc = _detail(link)
            if sal:
                desc = f'Salario: {sal}\n\n{desc}'
            out.append(job('ct', rid.group(1), title, comp, loc, desc, link, SOURCE, query))
            if len(out) >= limit:
                break
        page += 1
        pause()
    return out
