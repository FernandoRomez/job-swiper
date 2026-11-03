"""Utilidades compartidas: HTTP con UA realista, limpieza de HTML, armado de jobs."""
import gzip, html, json, re, time, random, urllib.request, urllib.parse

UA = ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36')


def fetch(url, timeout=20, headers=None, retries=1):
    h = {'User-Agent': UA, 'Accept-Language': 'es-MX,es;q=0.9,en;q=0.8',
         'Accept': 'text/html,application/json,application/xml;q=0.9,*/*;q=0.8',
         'Accept-Encoding': 'gzip'}
    h.update(headers or {})
    last = None
    for i in range(retries + 1):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=timeout) as r:
                data = r.read()
                if r.headers.get('Content-Encoding') == 'gzip':
                    data = gzip.decompress(data)
                return data.decode(r.headers.get_content_charset() or 'utf-8', 'replace')
        except Exception as e:  # noqa
            last = e
            if getattr(e, 'code', None) in (403, 404, 401):
                break
            time.sleep(1.5 * (i + 1))
    raise last


def fetch_json(url, **kw):
    return json.loads(fetch(url, headers={'Accept': 'application/json'}, **kw))


def pause(a=0.8, b=1.8):
    time.sleep(random.uniform(a, b))


def strip_html(s):
    if not s:
        return ''
    s = re.sub(r'(?is)<(script|style)[^>]*>.*?</\1>', ' ', s)
    s = re.sub(r'(?i)<br\s*/?>|</p>|</li>|</div>|</h\d>', '\n', s)
    s = re.sub(r'(?i)<li[^>]*>', '• ', s)
    s = html.unescape(re.sub(r'<[^>]+>', ' ', s))
    s = re.sub(r'[ \t\xa0]+', ' ', s)
    s = re.sub(r'\s*\n\s*', '\n', s)
    return re.sub(r'\n{3,}', '\n\n', s).strip()


def text(s):
    return re.sub(r'\s+', ' ', strip_html(s)).strip()


def q(s):
    return urllib.parse.quote_plus(s or '')


def job(prefix, rid, title, company, location, description, link, source, query,
        employment_type=None, seniority=None, easy_apply=False):
    return {'id': f'{prefix}_{rid}', 'title': (title or '').strip()[:300],
            'company': (company or '').strip()[:200], 'location': (location or '').strip()[:200],
            'description': (description or '').strip()[:20000], 'link': link,
            'easy_apply': easy_apply, 'employment_type': employment_type, 'seniority': seniority,
            'query': query, 'source': source}


def matches(query, *fields):
    """Filtro laxo para APIs sin búsqueda: alguna palabra (>2 letras) del query aparece."""
    if not query:
        return True
    blob = ' '.join(f or '' for f in fields).lower()
    words = [w for w in re.findall(r'\w+', query.lower()) if len(w) > 2]
    return any(w in blob for w in words) if words else True
