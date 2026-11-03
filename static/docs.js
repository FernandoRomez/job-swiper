'use strict';
/* Plantillas HTML/CSS profesionales para Resume, CV y Cover Letter.
   El objetivo: que parezcan hechos en Word por un humano, con buen diseño,
   una sola columna y tipografía ATS-segura (texto real, seleccionable). */

const DOC_LABELS = {
  es: { summary: 'Perfil profesional', experience: 'Experiencia', education: 'Educación',
        skills: 'Habilidades', certifications: 'Certificaciones', languages: 'Idiomas' },
  en: { summary: 'Professional Summary', experience: 'Experience', education: 'Education',
        skills: 'Skills', certifications: 'Certifications', languages: 'Languages' },
};

function eH(s) {
  return String(s ?? '').replace(/[&<>"]/g, c => ({ '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;' }[c]));
}

function linkify(text) {
  if (!text) return '';
  const placeholders = [];
  let counter = 0;
  
  function addPlaceholder(url, href) {
    const placeholder = `___LINK_PLACEHOLDER_${counter++}___`;
    placeholders.push({
      placeholder,
      html: `<a href="${eH(href)}" target="_blank" rel="noopener" class="doc-link">${eH(url)}</a>`
    });
    return placeholder;
  }

  let temp = String(text);
  // Match URLs starting with http://, https://, or www.
  const urlRegex = /\b(?:https?:\/\/|www\.)[^\s<]+[^.,;?\s<]/gi;
  temp = temp.replace(urlRegex, (match) => {
    let href = match;
    if (/^www\./i.test(href)) {
      href = 'https://' + href;
    }
    return addPlaceholder(match, href);
  });

  // Match professional domain links (linkedin.com/in, github.com)
  const domainRegex = /\b(?:linkedin\.com\/in\/|github\.com\/|twitter\.com\/|x\.com\/)[^\s<]+[^.,;?\s<]/gi;
  temp = temp.replace(domainRegex, (match) => {
    let href = 'https://' + match;
    return addPlaceholder(match, href);
  });

  // Match email addresses
  const emailRegex = /\b[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}\b/gi;
  temp = temp.replace(emailRegex, (match) => {
    return addPlaceholder(match, 'mailto:' + match);
  });

  // HTML escape the text (placeholders won't match escape sequences)
  let escaped = eH(temp);

  // Restore the placeholders with the actual HTML links
  placeholders.forEach(({ placeholder, html }) => {
    escaped = escaped.replace(placeholder, html);
  });

  return escaped;
}

/* CSS compartido — tipografía tipo Word (Calibri/Helvetica), ATS-segura */
const DOC_CSS = `
  .page { font-family: Calibri, "Helvetica Neue", Arial, sans-serif; color: #1a1a1a;
    font-size: 10.8pt; line-height: 1.4; }
  .page * { box-sizing: border-box; }
  .doc-head { border-bottom: 2px solid #2a3b5f; padding-bottom: 10px; margin-bottom: 14px; }
  .doc-head h1 { font-size: 22pt; font-weight: 700; letter-spacing: .3px; color: #1c2a47; margin: 0; }
  .doc-head .headline { font-size: 11.5pt; color: #2a3b5f; font-weight: 600; margin-top: 2px; }
  .doc-head .contact { font-size: 9.5pt; color: #444; margin-top: 6px; }
  .doc-head .contact span:not(:last-child)::after { content: "  •  "; color: #9aa; }
  .doc-link { color: #1c2a47; text-decoration: underline; }
  .doc-link:hover { color: #2a3b5f; }
  .sec { margin-top: 14px; }
  .sec h2 { font-size: 10.5pt; text-transform: uppercase; letter-spacing: 1px; color: #2a3b5f;
    border-bottom: 1px solid #cdd4e0; padding-bottom: 3px; margin: 0 0 7px; font-weight: 700; }
  .sec p { margin: 0 0 6px; }
  .entry { margin-bottom: 9px; }
  .entry .row { display: flex; justify-content: space-between; align-items: baseline; gap: 12px; }
  .entry .role { font-weight: 700; color: #1a1a1a; }
  .entry .dates { font-size: 9.5pt; color: #555; white-space: nowrap; }
  .entry .sub { font-size: 9.8pt; color: #555; font-style: italic; margin-bottom: 2px; }
  .entry ul { margin: 3px 0 0; padding-left: 17px; }
  .entry li { margin: 1.5px 0; }
  .skills-list { margin: 0; }
  .cl-meta { margin-bottom: 14px; }
  .cl-meta .date { color: #555; margin-bottom: 12px; }
  .cl-body p { margin: 0 0 10px; text-align: justify; }
  .cl-close { margin-top: 16px; }
  .cl-close .name { font-weight: 700; margin-top: 2px; }
`;

function contactLine(c = {}) {
  const parts = [
    c.email ? `<span><a href="mailto:${eH(c.email)}" class="doc-link">${eH(c.email)}</a></span>` : '',
    c.phone ? `<span>${eH(c.phone)}</span>` : '',
    c.location ? `<span>${eH(c.location)}</span>` : '',
    c.linkedin ? `<span>${(c.linkedin.includes('http') || c.linkedin.includes('.com')) ? `<a href="${eH(c.linkedin.startsWith('http') ? c.linkedin : 'https://' + c.linkedin)}" target="_blank" rel="noopener" class="doc-link">${eH(c.linkedin)}</a>` : eH(c.linkedin)}</span>` : '',
    c.website ? `<span><a href="${eH(c.website.startsWith('http') ? c.website : 'https://' + c.website)}" target="_blank" rel="noopener" class="doc-link">${eH(c.website)}</a></span>` : ''
  ].filter(Boolean);
  return parts.join('');
}

/* Resume / CV comparten renderer (data = objeto resume o cv) */
function renderResumeInner(profile = {}, data = {}) {
  const L = DOC_LABELS[(profile.lang === 'en') ? 'en' : 'es'];
  const c = profile.contact || {};
  let h = `<div class="doc-head">
    <h1>${eH(profile.name || 'Nombre Apellido')}</h1>
    ${profile.headline ? `<div class="headline">${eH(profile.headline)}</div>` : ''}
    <div class="contact">${contactLine(c)}</div>
  </div>`;

  if (data.summary) h += `<div class="sec"><h2>${L.summary}</h2><p>${linkify(data.summary)}</p></div>`;

  if ((data.experience || []).length) {
    h += `<div class="sec"><h2>${L.experience}</h2>`;
    data.experience.forEach(e => {
      const dates = [e.start, e.end].filter(Boolean).join(' – ');
      const sub = [e.company, e.location].filter(Boolean).join(' · ');
      h += `<div class="entry">
        <div class="row"><span class="role">${eH(e.title || '')}</span><span class="dates">${eH(dates)}</span></div>
        ${sub ? `<div class="sub">${eH(sub)}</div>` : ''}
        ${(e.bullets || []).length ? `<ul>${e.bullets.map(b => `<li>${linkify(b)}</li>`).join('')}</ul>` : ''}
      </div>`;
    });
    h += `</div>`;
  }

  if ((data.education || []).length) {
    h += `<div class="sec"><h2>${L.education}</h2>`;
    data.education.forEach(e => {
      const dates = [e.start, e.end].filter(Boolean).join(' – ');
      const sub = [e.school, e.location].filter(Boolean).join(' · ');
      h += `<div class="entry">
        <div class="row"><span class="role">${eH(e.degree || '')}</span><span class="dates">${eH(dates)}</span></div>
        ${sub ? `<div class="sub">${eH(sub)}</div>` : ''}
        ${e.detail ? `<div>${linkify(e.detail)}</div>` : ''}
      </div>`;
    });
    h += `</div>`;
  }

  if ((data.skills || []).length)
    h += `<div class="sec"><h2>${L.skills}</h2><p class="skills-list">${data.skills.map(linkify).join('  ·  ')}</p></div>`;
  if ((data.certifications || []).length)
    h += `<div class="sec"><h2>${L.certifications}</h2><p>${data.certifications.map(linkify).join('  ·  ')}</p></div>`;
  if ((data.languages || []).length)
    h += `<div class="sec"><h2>${L.languages}</h2><p>${data.languages.map(linkify).join('  ·  ')}</p></div>`;

  return h;
}

function renderCoverLetterInner(profile = {}, cl = {}) {
  const c = profile.contact || {};
  return `<div class="doc-head">
      <h1>${eH(profile.name || '')}</h1>
      <div class="contact">${contactLine(c)}</div>
    </div>
    <div class="cl-meta">
      ${cl.date ? `<div class="date">${eH(cl.date)}</div>` : ''}
      ${cl.recipient ? `<div>${linkify(cl.recipient)}</div>` : ''}
    </div>
    <div class="cl-body">${(cl.paragraphs || []).map(p => `<p>${linkify(p)}</p>`).join('')}</div>
    <div class="cl-close">
      <div>${eH(cl.closing || 'Atentamente,')}</div>
      <div class="name">${eH(profile.name || '')}</div>
    </div>`;
}

/* devuelve el HTML interno según tipo de documento e idioma (en/es) */
function renderDocInner(kind, docs, lang = 'en') {
  // Si el usuario editó el texto a mano, usamos ese HTML tal cual (sin IA).
  const ov = docs && docs.overrides && docs.overrides[`${kind}_${lang}`];
  if (ov) return ov;
  let target = docs;
  if (docs && (docs.en || docs.es)) {
    target = docs[lang] || docs.en || docs.es || {};
  }
  const profile = target.profile || {};
  if (kind === 'cover_letter') return renderCoverLetterInner(profile, target.cover_letter || {});
  return renderResumeInner(profile, target[kind] || {});
}

/* documento completo e independiente (para iframe de vista previa y para imprimir/PDF).
   onePage=true fuerza que quepa en UNA sola hoja (para el Resume): al imprimir se mide
   la altura y, si se pasa, se reduce el zoom hasta que entre en una carta. */
function buildStandaloneDoc(innerHtml, forPrint, onePage, autoPrint = true) {
  // El ajuste a una hoja (fitScript) se aplica siempre que forPrint; el window.print()
  // solo cuando autoPrint (al generar el PDF en el servidor NO queremos que se imprima solo).
  const fitBody = onePage ? `
          var page = document.querySelector('.page');
          var avail = 9.8 * 96;   // alto útil de una carta (11in - 2*0.6in de margen) en px
          var h = page.getBoundingClientRect().height;
          if (h > avail) page.style.zoom = Math.max(0.6, (avail / h) * 0.985);` : '';
  const printCall = autoPrint ? 'setTimeout(function () { window.print(); }, 250);' : '';
  const printScript = (forPrint && (onePage || autoPrint)) ? `
    <script>
      window.addEventListener('load', function () {
        try {${fitBody}
        } catch (e) {}
        ${printCall}
      });
    <\/script>` : '';
  return `<!DOCTYPE html><html><head><meta charset="utf-8">
    <style>
      ${DOC_CSS}
      html,body { margin:0; padding:0; background:#e9ecf2; }
      .page { background:#fff; width:${forPrint ? '7.3in' : '8.5in'}; min-height:${forPrint ? '0' : '11in'};
        margin:${forPrint ? '0' : '0 auto'}; padding:${forPrint ? '0' : '0.7in 0.75in'};
        box-shadow:${forPrint ? 'none' : '0 4px 24px rgba(0,0,0,.15)'}; }
      .entry, .sec { break-inside: avoid; }
      @page { size: letter; margin: 0.6in; }
      @media screen { html,body{ padding: 16px 0; } }
    </style></head>
    <body><div class="page">${innerHtml}</div>${printScript}</body></html>`;
}

/* vista previa dentro de un iframe aislado con soporte de idioma */
function previewDoc(iframe, kind, docs, lang) {
  iframe.srcdoc = buildStandaloneDoc(renderDocInner(kind, docs, lang), false, kind === 'resume');
}

/* descarga a PDF de un clic con soporte de idioma */
function downloadDocPDF(kind, docs, filename, lang) {
  const w = window.open('', '_blank');
  if (!w) { alert('Permite las ventanas emergentes para descargar el PDF.'); return; }
  w.document.write(buildStandaloneDoc(renderDocInner(kind, docs, lang), true, kind === 'resume'));
  w.document.title = filename || 'documento';
  w.document.close();
  w.focus();  // el print se dispara solo dentro del documento, tras ajustar a una hoja
}
