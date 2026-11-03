"""Indeed MX con playwright headless. Indeed suele bloquear (Cloudflare); falla con elegancia."""
import re
from .common import strip_html, text, job, q
SOURCE = 'Indeed'


def scrape(query, location='México', limit=25):
    from playwright.sync_api import sync_playwright
    out = []
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        try:
            pg = b.new_context(locale='es-MX').new_page()
            pg.goto(f'https://mx.indeed.com/jobs?q={q(query)}&l={q(location or "México")}&fromage=14',
                    timeout=30000, wait_until='domcontentloaded')
            pg.wait_for_timeout(3000)
            h = pg.content()
            if 'cf-challenge' in h or 'Just a moment' in h or 'Additional Verification' in h:
                raise RuntimeError('bloqueado por Cloudflare')
            cards = re.findall(r'(?s)data-jk="([0-9a-f]+)".*?</td>', h)
            jks = list(dict.fromkeys(re.findall(r'data-jk="([0-9a-f]+)"', h)))[:limit]
            for jk in jks:
                try:
                    pg.goto(f'https://mx.indeed.com/viewjob?jk={jk}', timeout=25000, wait_until='domcontentloaded')
                    pg.wait_for_timeout(1500)
                    d = pg.content()
                except Exception:
                    continue
                g = lambda pat: text((re.search(pat, d, re.S) or [0, ''])[1])
                title = g(r'<h1[^>]*>(.*?)</h1>')
                comp = g(r'data-company-name="true"[^>]*>(.*?)</') or g(r'data-testid="inlineHeader-companyName"[^>]*>(.*?)</div>')
                loc = g(r'data-testid="(?:inlineHeader-companyLocation|job-location)"[^>]*>(.*?)</div>')
                m = re.search(r'(?s)id="jobDescriptionText"[^>]*>(.*?)</div>\s*</div>', d)
                if not title:
                    continue
                out.append(job('ind', jk, title, comp, loc, strip_html(m.group(1)) if m else '',
                               f'https://mx.indeed.com/viewjob?jk={jk}', SOURCE, query))
            if not jks:
                raise RuntimeError('sin resultados (posible bloqueo)')
        finally:
            b.close()
    return out
