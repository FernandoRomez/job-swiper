/* Job Studio — plantillas de CV y carta. Define window.STUDIO_TEMPLATES (ver studio/CONTRACT.md).
   render(doc, contact) -> HTML interior de <body>, raíz <div class="tpl-<id> sheet">. */
(function (root) {
  'use strict';
  var T = {
    es: { perfil: 'Perfil profesional', comp: 'Competencias técnicas', exp: 'Experiencia profesional',
          proy: 'Proyectos', edu: 'Educación', cert: 'Certificaciones', idiomas: 'Idiomas', contacto: 'Contacto',
          ubic: 'Disponible para reubicarme', saludo: 'Estimado equipo de selección:' },
    en: { perfil: 'Professional Summary', comp: 'Technical Skills', exp: 'Professional Experience',
          proy: 'Projects', edu: 'Education', cert: 'Certifications', idiomas: 'Languages', contacto: 'Contact',
          ubic: 'Open to relocate', saludo: 'Dear Hiring Team,' }
  };
  var EDU = { es: { certs: [] }, en: { certs: [] } };   // studio.js lo rellena desde /api/profile (perfil.json)
  var DEF_CONTACT = { nombre: '', email: '', tel_mx: '', tel_us: '', linkedin: '', web: '' };

  function e(s) {
    return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }
  function arr(x) { return Array.isArray(x) ? x : []; }
  function f(tag, path, val, cls) {               // elemento editable con data-field
    return '<' + tag + (cls ? ' class="' + cls + '"' : '') + ' data-field="' + e(path) + '">' + e(val) + '</' + tag + '>';
  }
  function ctx(doc, contact) {
    doc = doc || {};
    var lang = doc.idioma === 'es' ? 'es' : 'en';
    var c = {}, k; contact = contact || {};
    for (k in DEF_CONTACT) c[k] = contact[k] || DEF_CONTACT[k];
    c.tel = contact.tel || c.tel_mx;
    var edu = arr(doc.educacion).length ? doc.educacion
      : [{ titulo: EDU[lang].titulo, escuela: EDU[lang].escuela, fechas: EDU[lang].fechas }];
    return { d: doc, lang: lang, t: T[lang], c: c, edu: edu,
      eduDefault: !arr(doc.educacion).length,
      certs: arr(doc.certs).length ? doc.certs : EDU[lang].certs, certsDefault: !arr(doc.certs).length,
      idiomas: doc.idiomas || EDU[lang].idiomas,
      ubic: doc.ubicacion || T[lang].ubic };
  }
  function contactItems(x) {
    return [x.c.email, x.c.tel, x.c.linkedin, x.c.web].filter(Boolean);
  }
  function comps(x) { return arr(x.d.competencias).map(function (p, i) {
    p = Array.isArray(p) ? p : ['', String(p || '')]; return { k: p[0] || '', v: p[1] || '', i: i }; }); }
  function bullets(list, base) {
    list = arr(list); if (!list.length) return '';
    return '<ul>' + list.map(function (b, j) { return f('li', base + '.bullets.' + j, b); }).join('') + '</ul>';
  }
  function expBlock(x, opt) {
    opt = opt || {};
    return arr(x.d.experiencia).map(function (it, i) {
      it = it || {}; var b = 'experiencia.' + i;
      return '<div class="item"><div class="row">' + f('span', b + '.puesto', it.puesto, 'role') +
        f('span', b + '.fechas', it.fechas, 'date') + '</div>' + f('div', b + '.empresa', it.empresa, 'org') +
        bullets(it.bullets, b) + '</div>';
    }).join('');
  }
  function proyBlock(x) {
    return arr(x.d.proyectos).map(function (it, i) {
      it = it || {}; var b = 'proyectos.' + i;
      return '<div class="item"><div class="row">' + f('span', b + '.titulo', it.titulo, 'role') + '</div>' +
        bullets(it.bullets, b) + '</div>';
    }).join('');
  }
  function eduBlock(x) {
    return x.edu.map(function (it, i) {
      it = it || {}; var b = 'educacion.' + i, ro = x.eduDefault;
      var g = function (tag, k, cls) { return ro ? '<' + tag + ' class="' + cls + '">' + e(it[k]) + '</' + tag + '>' : f(tag, b + '.' + k, it[k], cls); };
      return '<div class="item"><div class="row">' + g('span', 'titulo', 'role') + g('span', 'fechas', 'date') +
        '</div>' + g('div', 'escuela', 'org') + '</div>';
    }).join('');
  }
  function certList(x, tag) {
    return x.certs.map(function (c, i) {
      return x.certsDefault ? '<' + tag + '>' + e(c) + '</' + tag + '>' : f(tag, 'certs.' + i, c);
    }).join('');
  }
  function sec(title, body, cls) { return body ? '<section class="sec ' + (cls || '') + '"><h2>' + e(title) + '</h2>' + body + '</section>' : ''; }
  function perfil(x) { return x.d.perfil ? f('p', 'perfil', x.d.perfil, 'summary') : ''; }
  function compList(x) {
    var c = comps(x); if (!c.length) return '';
    return '<ul class="skills">' + c.map(function (s) {
      return '<li>' + f('b', 'competencias.' + s.i + '.0', s.k) + '<span class="sep">: </span>' + f('span', 'competencias.' + s.i + '.1', s.v) + '</li>';
    }).join('') + '</ul>';
  }
  function compTags(x) {
    var c = comps(x); if (!c.length) return '';
    return c.map(function (s) {
      var tags = s.v.split(/\s*,\s*/).filter(Boolean);
      return '<div class="skgroup">' + f('div', 'competencias.' + s.i + '.0', s.k, 'skname') +
        '<div class="tags" data-field="competencias.' + s.i + '.1" data-join=", ">' +
        tags.map(function (t) { return '<span class="tag">' + e(t) + '</span>'; }).join('<span class="tsep">, </span>') +
        '</div></div>';
    }).join('');
  }
  function eduFull(x) {
    return eduBlock(x) +
      '<p class="line"><b>' + e(x.t.cert) + ':</b> <span class="certs-inline">' + certList(x, 'span') + '</span></p>' +
      '<p class="line"><b>' + e(x.t.idiomas) + ':</b> ' + f('span', 'idiomas', x.idiomas) + '</p>';
  }
  function head(x, cls) {
    return '<header class="' + (cls || 'head') + '"><h1>' + e(x.c.nombre) + '</h1>' +
      (x.d.titular ? f('div', 'titular', x.d.titular, 'titular') : '') +
      '<div class="contact">' + contactItems(x).map(function (s) { return '<span>' + e(s) + '</span>'; }).join('<i class="dot"> · </i>') +
      '</div>' + f('div', 'ubicacion', x.ubic, 'ubic') + '</header>';
  }
  function wrap(id, x, inner) { return '<div class="tpl-' + id + ' sheet" lang="' + x.lang + '">' + inner + '</div>'; }

  /* ---------- CVs ---------- */
  function linear(id) {
    return function (doc, contact) {
      var x = ctx(doc, contact), t = x.t;
      return wrap(id, x, head(x) + sec(t.perfil, perfil(x)) + sec(t.comp, compList(x)) +
        sec(t.exp, expBlock(x)) + sec(t.proy, proyBlock(x)) + sec(t.edu, eduFull(x)));
    };
  }
  function ingeniero(doc, contact) {
    var x = ctx(doc, contact), t = x.t;
    var side = '<aside class="side"><h1>' + e(x.c.nombre) + '</h1>' + (x.d.titular ? f('div', 'titular', x.d.titular, 'titular') : '') +
      sec(t.contacto, '<ul class="clist">' + contactItems(x).map(function (s) { return '<li>' + e(s) + '</li>'; }).join('') + '</ul>' + f('div', 'ubicacion', x.ubic, 'ubic')) +
      sec(t.comp, compTags(x)) +
      sec(t.idiomas, f('p', 'idiomas', x.idiomas)) +
      sec(t.cert, '<ul class="certs">' + certList(x, 'li') + '</ul>') + '</aside>';
    var main = '<main class="main">' + sec(t.perfil, perfil(x)) + sec(t.exp, expBlock(x)) + sec(t.proy, proyBlock(x)) + sec(t.edu, eduBlock(x)) + '</main>';
    return wrap('ingeniero', x, side + main);
  }
  function tecnico(doc, contact) {
    var x = ctx(doc, contact), t = x.t;
    var top = '<div class="grid2">' + sec(t.perfil, perfil(x)) + sec(t.edu, eduBlock(x) +
      '<p class="line"><b>' + e(t.idiomas) + ':</b> ' + f('span', 'idiomas', x.idiomas) + '</p>') + '</div>';
    return wrap('tecnico', x, head(x) + top + sec(t.comp, '<div class="skgrid">' + compTags(x) + '</div>') +
      sec(t.exp, expBlock(x)) + sec(t.proy, proyBlock(x)) +
      sec(t.cert, '<ul class="certs">' + certList(x, 'li') + '</ul>'));
  }
  function minimal(doc, contact) {
    var x = ctx(doc, contact), t = x.t;
    var r = function (title, body) { return body ? '<section class="sec"><h2>' + e(title) + '</h2><div class="body">' + body + '</div></section>' : ''; };
    return wrap('minimal', x, head(x) + r(t.perfil, perfil(x)) + r(t.comp, compList(x)) + r(t.exp, expBlock(x)) +
      r(t.proy, proyBlock(x)) + r(t.edu, eduFull(x)));
  }

  /* ---------- Cartas ---------- */
  function cover(id) {
    return function (doc, contact) {
      var x = ctx(doc, contact);
      var paras = String(x.d.carta || '').split(/\n\s*\n/).map(function (p) { return p.trim(); }).filter(Boolean);
      var d = new Date(), fecha = x.lang === 'es'
        ? d.toLocaleDateString('es-MX', { day: 'numeric', month: 'long', year: 'numeric' })
        : d.toLocaleDateString('en-US', { month: 'long', day: 'numeric', year: 'numeric' });
      var body = paras.length ? paras.map(function (p, i) {
        return '<p data-field="carta.p.' + i + '">' + e(p).replace(/\n/g, '<br>') + '</p>'; }).join('')
        : '<p data-field="carta.p.0">' + e(x.t.saludo) + '</p>';
      return wrap(id, x, head(x) + '<div class="letter"><p class="date">' + e(fecha) + '</p>' + body + '</div>');
    };
  }

  var L = [
    { id: 'ats-clasico', name: 'ATS Clásico', kind: 'cv', render: linear('ats-clasico'),
      thumb: { bg: '#ffffff', accent: '#111111', desc: 'Una columna, Helvetica negra, reglas finas. Máxima compatibilidad ATS (default).' } },
    { id: 'moderno', name: 'Moderno', kind: 'cv', render: linear('moderno'),
      thumb: { bg: '#ffffff', accent: '#1f4e79', desc: 'Encabezado azul petróleo a sangre, Inter, títulos en acento.' } },
    { id: 'ingeniero', name: 'Ingeniero', kind: 'cv', render: ingeniero,
      thumb: { bg: '#f1f4f7', accent: '#0f766e', desc: 'Dos columnas: barra lateral gris con contacto y competencias como etiquetas.' } },
    { id: 'ejecutivo', name: 'Ejecutivo', kind: 'cv', render: linear('ejecutivo'),
      thumb: { bg: '#fffdf8', accent: '#7a5c2e', desc: 'Serif elegante (Cormorant/EB Garamond), nombre centrado, filetes dorados.' } },
    { id: 'minimal', name: 'Minimal', kind: 'cv', render: minimal,
      thumb: { bg: '#ffffff', accent: '#e4572e', desc: 'Suizo: mucho blanco, títulos en columna izquierda, punto naranja.' } },
    { id: 'tecnico', name: 'Técnico', kind: 'cv', render: tecnico,
      thumb: { bg: '#ffffff', accent: '#2563eb', desc: 'Compacto y denso, IBM Plex, competencias en etiquetas, rejilla de 2 columnas.' } },
    { id: 'carta-ats-clasico', name: 'Carta ATS Clásica', kind: 'cover', render: cover('carta-ats-clasico'),
      thumb: { bg: '#ffffff', accent: '#111111', desc: 'Carta sobria a juego con ATS Clásico.' } },
    { id: 'carta-moderno', name: 'Carta Moderna', kind: 'cover', render: cover('carta-moderno'),
      thumb: { bg: '#ffffff', accent: '#1f4e79', desc: 'Carta con encabezado azul a juego con Moderno.' } },
    { id: 'carta-ejecutivo', name: 'Carta Ejecutiva', kind: 'cover', render: cover('carta-ejecutivo'),
      thumb: { bg: '#fffdf8', accent: '#7a5c2e', desc: 'Carta serif a juego con Ejecutivo.' } }
  ];
  L.forEach(function (tp) { tp.css = 'templates/' + tp.id + '.css'; tp.preview = tp.thumb.desc; });
  root.STUDIO_TEMPLATES = L;
  root.STUDIO_TEMPLATE_HELPERS = { escape: e, labels: T, edu: EDU, defaultContact: DEF_CONTACT };
})(typeof window !== 'undefined' ? window : this);
