"""
latex_cv.py — genera Resume / CV / Cover Letter en PDF con LaTeX (tectonic / XeLaTeX).

API:
  resume_pdf(profile, data, out_path, one_page=False)
  cover_pdf(profile, cover_letter, out_path)
  generic_pdf(header, sections, out_path, one_page=False)   # bloques libres (postular.py)
  available()  → True si hay un motor LaTeX instalado

`profile`/`data` siguen el DOC_SCHEMA de app.py.
"""
import os
import re
import shutil
import subprocess
import tempfile

LABELS = {
    'es': {'summary': 'Perfil profesional', 'experience': 'Experiencia profesional',
           'education': 'Educación', 'skills': 'Habilidades', 'certifications': 'Certificaciones',
           'languages': 'Idiomas', 'closing': 'Atentamente,'},
    'en': {'summary': 'Professional Summary', 'experience': 'Professional Experience',
           'education': 'Education', 'skills': 'Skills', 'certifications': 'Certifications',
           'languages': 'Languages', 'closing': 'Sincerely,'},
}

_ESC = {'\\': r'\textbackslash{}', '&': r'\&', '%': r'\%', '$': r'\$', '#': r'\#', '_': r'\_',
        '{': r'\{', '}': r'\}', '~': r'\textasciitilde{}', '^': r'\textasciicircum{}'}
_SYM = {'↔': r'$\leftrightarrow$', '→': r'$\rightarrow$', '←': r'$\leftarrow$',
        '⇒': r'$\Rightarrow$', '≈': r'$\approx$', '≥': r'$\geq$', '≤': r'$\leq$', '×': r'$\times$'}
_ESC_RE = re.compile(r'[\\&%$#_{}~^]')


def esc(s):
    s = str(s or '').strip()
    s = re.sub(r'\*\*(.+?)\*\*', '\x00B\\1\x00E', s)  # **negritas** de la IA
    s = _ESC_RE.sub(lambda m: _ESC[m.group()], s)
    for u, t in _SYM.items():
        s = s.replace(u, t)
    return s.replace('\x00B', r'\textbf{').replace('\x00E', '}')


def _url(u):
    u = str(u or '').strip()
    full = u if u.startswith('http') else 'https://' + u
    return r'\href{' + full.replace('%', r'\%').replace('#', r'\#') + '}{' + esc(u) + '}'


def engine():
    for e in ('tectonic', 'xelatex'):
        p = shutil.which(e) or (f'/opt/homebrew/bin/{e}' if os.path.exists(f'/opt/homebrew/bin/{e}') else None)
        if p:
            return p
    return None


def available():
    return engine() is not None


# --------------------------------------------------------------------------- #
#  Preámbulo                                                                  #
# --------------------------------------------------------------------------- #
def _preamble(size_pt, margin_in, sec_space):
    lead = round(size_pt * 1.22, 2)
    return rf"""\documentclass{{article}}
\usepackage[letterpaper,margin={margin_in}in]{{geometry}}
\usepackage{{fontspec}}
\setmainfont{{texgyreheros}}[Extension=.otf,UprightFont=*-regular,BoldFont=*-bold,ItalicFont=*-italic,BoldItalicFont=*-bolditalic]
\usepackage{{xcolor}}
\definecolor{{acc}}{{HTML}}{{1F3A5F}}
\definecolor{{mut}}{{HTML}}{{555555}}
\usepackage[hidelinks]{{hyperref}}
\usepackage{{enumitem}}
\usepackage{{titlesec}}
\usepackage{{tabularx}}
\pagestyle{{empty}}
\setlength{{\parindent}}{{0pt}}
\setlength{{\parskip}}{{0pt}}
\fontsize{{{size_pt}}}{{{lead}}}\selectfont
\renewcommand{{\normalsize}}{{\fontsize{{{size_pt}}}{{{lead}}}\selectfont}}
\titleformat{{\section}}{{\color{{acc}}\bfseries}}{{}}{{0pt}}{{\MakeUppercase}}[\vspace{{1pt}}{{\color{{acc}}\titlerule[0.8pt]}}]
\titlespacing*{{\section}}{{0pt}}{{{sec_space}pt}}{{3pt}}
\setlist[itemize]{{leftmargin=1.2em,label=\textbullet,topsep=1pt,itemsep=0.5pt,parsep=0pt}}
\newcommand{{\entry}}[3]{{\par\vspace{{3pt}}\noindent\textbf{{#1}}\hfill{{\color{{mut}}#2}}\par
  \ifx&#3&\else{{\itshape #3}}\par\fi}}
\begin{{document}}\raggedright
"""


def _header(name, headline, contact_items):
    h = r'{\centering{\fontsize{20}{24}\selectfont\bfseries ' + esc(name) + r'}\par' + '\n'
    if headline:
        h += r'\vspace{2pt}{\color{acc}\large ' + esc(headline) + r'}\par' + '\n'
    if contact_items:
        h += r'\vspace{3pt}{\small\color{mut}' + r' \;$\cdot$\; '.join(contact_items) + r'}\par' + '\n'
    return h + '}\n'


def _contact(c):
    c = c or {}
    out = []
    if c.get('email'):
        out.append(r'\href{mailto:' + c['email'] + '}{' + esc(c['email']) + '}')
    for k in ('phone', 'location'):
        if c.get(k):
            out.append(esc(c[k]))
    for k in ('linkedin', 'website'):
        if c.get(k):
            v = c[k]
            out.append(_url(v) if ('.' in v or 'http' in v) else esc(v))
    return out


def _itemize(bullets):
    bullets = [b for b in (bullets or []) if str(b).strip()]
    if not bullets:
        return ''
    return '\\begin{itemize}\n' + ''.join(f'\\item {esc(b)}\n' for b in bullets) + '\\end{itemize}\n'


# --------------------------------------------------------------------------- #
#  Compilación                                                                 #
# --------------------------------------------------------------------------- #
def _compile(tex, out_path):
    """Compila y devuelve el número de páginas."""
    eng = engine()
    if not eng:
        raise RuntimeError('No hay motor LaTeX (instala tectonic: brew install tectonic).')
    with tempfile.TemporaryDirectory() as tmp:
        src = os.path.join(tmp, 'doc.tex')
        with open(src, 'w', encoding='utf-8') as f:
            f.write(tex)
        if os.path.basename(eng) == 'tectonic':
            cmd = [eng, '--keep-logs', '--chatter', 'minimal', '--outdir', tmp, src]
        else:
            cmd = [eng, '-interaction=nonstopmode', '-halt-on-error', '-output-directory', tmp, src]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=180, cwd=tmp)
        pdf = os.path.join(tmp, 'doc.pdf')
        if r.returncode != 0 or not os.path.exists(pdf):
            raise RuntimeError('LaTeX falló:\n' + (r.stderr or r.stdout)[-2000:])
        pages = 1
        try:
            log = open(os.path.join(tmp, 'doc.log'), encoding='utf-8', errors='ignore').read()
            m = re.search(r'Output written on .*?\((\d+) page', log, re.S)
            if m:
                pages = int(m.group(1))
        except OSError:
            pass
        os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
        shutil.copyfile(pdf, out_path)
    return pages


# tamaños de letra / márgenes probados en orden hasta que el documento quepa en 1 hoja
_FIT_STEPS = [(10.5, 0.6, 8), (10, 0.55, 6), (9.5, 0.5, 5), (9, 0.45, 4), (8.5, 0.4, 3)]


def _build(body_fn, out_path, one_page):
    steps = _FIT_STEPS if one_page else _FIT_STEPS[:1]
    for size, margin, space in steps:
        tex = _preamble(size, margin, space) + body_fn() + '\n\\end{document}\n'
        if _compile(tex, out_path) <= 1 or not one_page:
            break
    return out_path


# --------------------------------------------------------------------------- #
#  Documentos                                                                  #
# --------------------------------------------------------------------------- #
def resume_pdf(profile, data, out_path, one_page=False):
    profile, data = profile or {}, data or {}
    L = LABELS['en' if profile.get('lang') == 'en' else 'es']

    def body():
        b = _header(profile.get('name') or '', profile.get('headline'), _contact(profile.get('contact')))
        if data.get('summary'):
            b += f'\\section{{{L["summary"]}}}\n{esc(data["summary"])}\\par\n'
        if data.get('experience'):
            b += f'\\section{{{L["experience"]}}}\n'
            for e in data['experience']:
                dates = ' -- '.join(esc(x) for x in (e.get('start'), e.get('end')) if x)
                sub = ' · '.join(esc(x) for x in (e.get('company'), e.get('location')) if x)
                b += f'\\entry{{{esc(e.get("title"))}}}{{{dates}}}{{{sub}}}\n' + _itemize(e.get('bullets'))
        if data.get('education'):
            b += f'\\section{{{L["education"]}}}\n'
            for e in data['education']:
                dates = ' -- '.join(esc(x) for x in (e.get('start'), e.get('end')) if x)
                sub = ' · '.join(esc(x) for x in (e.get('school'), e.get('location')) if x)
                b += f'\\entry{{{esc(e.get("degree"))}}}{{{dates}}}{{{sub}}}\n'
                if e.get('detail'):
                    b += esc(e['detail']) + '\\par\n'
        for key in ('skills', 'certifications', 'languages'):
            items = [esc(x) for x in (data.get(key) or []) if str(x).strip()]
            if items:
                b += f'\\section{{{L[key]}}}\n' + r' \;$\cdot$\; '.join(items) + '\\par\n'
        return b

    return _build(body, out_path, one_page)


def cover_pdf(profile, cl, out_path):
    profile, cl = profile or {}, cl or {}
    L = LABELS['en' if profile.get('lang') == 'en' else 'es']

    def body():
        b = _header(profile.get('name') or '', None, _contact(profile.get('contact')))
        b += '\\vspace{18pt}\n'
        if cl.get('date'):
            b += esc(cl['date']) + '\\par\\vspace{8pt}\n'
        if cl.get('recipient'):
            b += esc(cl['recipient']) + '\\par\\vspace{10pt}\n'
        b += '\\setlength{\\parskip}{8pt}\n'
        b += ''.join(esc(p) + '\\par\n' for p in (cl.get('paragraphs') or []) if str(p).strip())
        b += '\\vspace{6pt}' + esc(cl.get('closing') or L['closing']) + '\\par\\vspace{14pt}\n'
        b += '\\textbf{' + esc(profile.get('name') or '') + '}\\par\n'
        return b

    return _build(body, out_path, one_page=True)


def generic_pdf(header, sections, out_path, one_page=False):
    """header = {name, headline, contact:[str,...]}
    sections = [(titulo, [bloque, ...])] con bloque:
      ('p', texto) | ('kv', [(k, v), ...]) | ('entry', titulo, fechas, sub, [bullets])"""

    def body():
        b = _header(header.get('name', ''), header.get('headline'),
                    [esc(x) for x in header.get('contact', []) if x])
        for title, blocks in sections:
            b += f'\\section{{{esc(title)}}}\n'
            for blk in blocks:
                if blk[0] == 'p':
                    b += esc(blk[1]) + '\\par\\vspace{2pt}\n'
                elif blk[0] == 'kv':
                    b += _itemize([f'**{k}:** {v}' for k, v in blk[1]])
                elif blk[0] == 'entry':
                    _, t, d, s, bl = blk
                    b += f'\\entry{{{esc(t)}}}{{{esc(d)}}}{{{esc(s)}}}\n' + _itemize(bl)
        return b

    return _build(body, out_path, one_page)


def letter_pdf(header, date, paragraphs, out_path):
    def body():
        b = _header(header.get('name', ''), None, [esc(x) for x in header.get('contact', []) if x])
        b += '\\vspace{18pt}' + esc(date) + '\\par\\vspace{12pt}\\setlength{\\parskip}{8pt}\n'
        return b + ''.join(esc(p) + '\\par\n' for p in paragraphs if p.strip())

    return _build(body, out_path, one_page=True)
