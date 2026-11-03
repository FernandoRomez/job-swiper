/* Job Studio — frontend (vanilla JS) */
(() => {
'use strict';
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

const SOURCES = {
  linkedin:{name:'LinkedIn',pre:'li',c:'#0A66C2'}, occ:{name:'OCC',pre:'occ',c:'#E85D1A'},
  indeed:{name:'Indeed',pre:'ind',c:'#2557A7'}, computrabajo:{name:'Computrabajo',pre:'ct',c:'#1B8A5A'},
  remoteok:{name:'RemoteOK',pre:'rok',c:'#D6336C'}, remotive:{name:'Remotive',pre:'rmt',c:'#7048E8'},
  getonboard:{name:'GetOnBoard',pre:'gob',c:'#4A3AFF'}, weworkremotely:{name:'WeWorkRemotely',pre:'wwr',c:'#C9A100'},
  arbeitnow:{name:'Arbeitnow',pre:'arb',c:'#0C8599'},
};
const srcKey = j => {
  const s = (j.source || '').toLowerCase().replace(/[^a-z]/g, '');
  if (SOURCES[s]) return s;
  const pre = String(j.id || '').split('_')[0];
  return Object.keys(SOURCES).find(k => SOURCES[k].pre === pre) || s || 'otro';
};
const srcInfo = j => SOURCES[srcKey(j)] || {name: j.source || 'Otro', c:'#6B7280'};

const S = { jobs:[], cur:null, job:null, doc:null, contact:{}, base:null, tab:'li',
  filt:{q:'', source:'', status:'', country:''}, zoom:{cv:.8, cover:.8}, saveT:null, pollT:null };

/* ---------- utils ---------- */
async function api(path, opts = {}) {
  const o = {...opts};
  if (o.body && typeof o.body !== 'string') { o.body = JSON.stringify(o.body); o.headers = {'Content-Type':'application/json'}; }
  let r;
  try { r = await fetch(path, o); }
  catch { throw new Error('No hay conexión con el servidor. Ejecuta studio.py (puerto 8790).'); }
  let data = null;
  try { data = await r.json(); } catch {}
  if (!r.ok) throw new Error((data && (data.error || data.detail)) || `Error ${r.status} en ${path}`);
  return data;
}
function toast(msg, kind = '') {
  const t = document.createElement('div');
  t.className = 'toast ' + kind; t.textContent = msg;
  $('#toasts').append(t);
  setTimeout(() => t.remove(), kind === 'err' ? 6000 : 3500);
}
const debounce = (fn, ms) => { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; };
async function busy(btn, fn) {
  const txt = btn.textContent; btn.disabled = true; btn.textContent = '⏳ ' + txt.replace(/^\S+\s/, '');
  try { return await fn(); } catch (e) { toast(e.message, 'err'); } finally { btn.disabled = false; btn.textContent = txt; }
}
const initials = s => (s || '?').split(/\s+/).filter(Boolean).slice(0, 2).map(w => w[0]).join('').toUpperCase();
function setPath(obj, path, val) {
  const ks = path.split('.'); let o = obj;
  ks.slice(0, -1).forEach((k, i) => { if (o[k] == null) o[k] = /^\d+$/.test(ks[i + 1]) ? [] : {}; o = o[k]; });
  o[ks.at(-1)] = val;
}

/* ---------- theme ---------- */
try { const th = localStorage.getItem('studio-theme'); if (th) document.documentElement.dataset.theme = th; } catch {}
$('#theme-btn').onclick = () => {
  const dark = document.documentElement.dataset.theme
    ? document.documentElement.dataset.theme === 'dark' : matchMedia('(prefers-color-scheme: dark)').matches;
  document.documentElement.dataset.theme = dark ? 'light' : 'dark';
  try { localStorage.setItem('studio-theme', dark ? 'light' : 'dark'); } catch {}
};

/* ---------- stats + list ---------- */
async function loadStats() {
  try {
    const s = await api('/api/stats'), bs = s.by_status || {};
    $('#stats-line').textContent = `${s.total ?? 0} vacantes en ${Object.keys(s.by_source || {}).length} plataformas`;
    $('#stats').innerHTML = [[s.total, 'total'], [bs.liked, 'interesan'], [s.applied, 'aplicadas'], [bs.new, 'nuevas']]
      .map(([v, l]) => `<span class="stat"><b>${v ?? 0}</b>${l}</span>`).join('');
  } catch { $('#stats-line').textContent = 'Sin conexión con el servidor'; }
}
function renderChips() {
  $('#src-chips').innerHTML = `<button class="chip ${S.filt.source ? '' : 'on'}" data-s="">Todas</button>` +
    Object.entries(SOURCES).map(([k, v]) =>
      `<button class="chip ${S.filt.source === k ? 'on' : ''}" data-s="${k}" style="--c:${v.c}"><i></i>${v.name}</button>`).join('');
  $('#sc-sources').innerHTML = Object.entries(SOURCES).map(([k, v]) =>
    `<label><input type="checkbox" value="${k}" ${['linkedin','occ','computrabajo','indeed'].includes(k) ? 'checked' : ''}>${v.name}</label>`).join('');
}
$('#src-chips').onclick = e => { const b = e.target.closest('.chip'); if (!b) return; S.filt.source = b.dataset.s; renderChips(); loadJobs(); };

async function loadJobs() {
  $('#jobs').innerHTML = '<li class="skel"></li>'.repeat(7);
  const p = new URLSearchParams(Object.entries(S.filt).filter(([, v]) => v));
  try { S.jobs = await api('/api/jobs?' + p) || []; }
  catch (e) { S.jobs = []; $('#jobs').innerHTML = `<li class="list-empty">${esc(e.message)}</li>`; return; }
  renderList();
}
function renderList() {
  if (!S.jobs.length) {
    $('#jobs').innerHTML = '<li class="list-empty">Ninguna vacante coincide con los filtros. Cambia la búsqueda o usa “Buscar empleos” para traer nuevas.</li>';
    return;
  }
  $('#jobs').innerHTML = S.jobs.map(j => {
    const si = srcInfo(j), sc = Math.round(j.score ?? 0);
    const scc = sc >= 70 ? 'var(--good)' : sc >= 45 ? 'var(--signal)' : 'var(--bad)';
    const st = j.app_status ? `<span class="tag" style="--c:var(--good)">${esc(j.app_status)}</span>`
      : j.status === 'liked' ? '<span class="tag" style="--c:var(--signal)">Me interesa</span>' : '';
    return `<li class="job ${j.id === S.cur ? 'on' : ''} ${j.status === 'passed' ? 'passed' : ''}" data-id="${esc(j.id)}" style="--c:${si.c}" tabindex="0">
      <h3>${esc(j.title)}</h3>
      <div class="score" style="--v:${sc};--sc:${scc}" title="Afinidad ${sc}/100"><span>${sc}</span></div>
      <p>${esc(j.company)}${j.location ? ' — ' + esc(j.location) : ''}</p>
      <div class="tags"><span class="tag">${si.name}</span>${j.easy_apply ? '<span class="tag plain">Solicitud sencilla</span>' : ''}${st}</div></li>`;
  }).join('');
}
$('#jobs').onclick = e => { const li = e.target.closest('.job'); if (li) openJob(li.dataset.id); };
$('#jobs').onkeydown = e => { if (e.key === 'Enter') { const li = e.target.closest('.job'); if (li) openJob(li.dataset.id); } };
$('#q').oninput = debounce(e => { S.filt.q = e.target.value.trim(); loadJobs(); }, 300);
$('#f-status').onchange = e => { S.filt.status = e.target.value; loadJobs(); };
$('#f-country').onchange = e => { S.filt.country = e.target.value; loadJobs(); };

/* ---------- open job ---------- */
async function openJob(id) {
  if (S.saveT) { clearTimeout(S.saveT); await saveDoc(); }
  S.cur = id; renderList();
  $('#empty').hidden = true; $('#work').hidden = false;
  const lite = S.jobs.find(j => j.id === id) || {};
  $('#w-title').textContent = lite.title || ''; $('#w-sub').textContent = [lite.company, lite.location].filter(Boolean).join(' — ');
  $('#pane-li').innerHTML = '<div class="li"><div class="li-top"><div class="skel" style="height:180px"></div></div></div>';
  try { S.job = await api('/api/jobs/' + encodeURIComponent(id)); }
  catch (e) { S.job = {...lite}; toast(e.message, 'err'); }
  if (S.cur !== id) return;
  S.doc = S.job.doc || (S.base ? {...structuredClone(S.base), job_id: id} : null);
  $('#save-state').textContent = S.job.doc ? 'CV a la medida guardado' : (S.doc ? 'Usando CV base' : '');
  updateVotes(); renderLI(); renderDocs();
  $('.job.on')?.scrollIntoView({block:'nearest'});
}
function updateVotes() {
  $('#b-like').classList.toggle('on', S.job?.status === 'liked');
  $('#b-pass').classList.toggle('on', S.job?.status === 'passed');
}

/* ---------- LinkedIn preview ---------- */
function fmtDesc(t) {
  t = String(t || '').trim();
  if (!t) return '<p>Esta vacante no incluye descripción. Revisa la publicación original.</p>';
  if (/<(p|ul|li|br|strong|div)\b/i.test(t)) {
    const d = new DOMParser().parseFromString(t, 'text/html');
    d.querySelectorAll('script,style,iframe,img').forEach(n => n.remove());
    d.querySelectorAll('*').forEach(n => [...n.attributes].forEach(a => n.removeAttribute(a.name)));
    return d.body.innerHTML;
  }
  let html = '', list = false;
  for (const raw of t.split(/\n+/)) {
    const l = raw.trim(); if (!l) continue;
    const bullet = l.match(/^[-•*·▪●]\s*(.+)/);
    if (bullet) { if (!list) { html += '<ul>'; list = true; } html += `<li>${esc(bullet[1])}</li>`; continue; }
    if (list) { html += '</ul>'; list = false; }
    html += (l.length < 70 && /:$/.test(l)) ? `<h4>${esc(l)}</h4>` : `<p>${esc(l)}</p>`;
  }
  return html + (list ? '</ul>' : '');
}
function renderLI() {
  const j = S.job, si = srcInfo(j);
  const pills = [j.employment_type, j.seniority].filter(Boolean).map(p => `<span>${esc(p)}</span>`).join('');
  const ago = j.created_at ? `Publicado ${esc(String(j.created_at).slice(0, 10))}` : '';
  $('#pane-li').innerHTML = `<article class="li" style="--c:${si.c}">
    <div class="li-top">
      <div class="li-co"><div class="li-av">${esc(initials(j.company))}</div><b>${esc(j.company || 'Empresa')}</b></div>
      <h1>${esc(j.title)}</h1>
      <div class="li-meta">${[esc(j.location), ago, 'Vía ' + si.name].filter(Boolean).join(' &nbsp;·&nbsp; ')}</div>
      <div class="li-pills">${pills}${j.easy_apply ? '<span>✔ Solicitud sencilla</span>' : ''}</div>
      <div class="li-btns">
        <button class="li-btn solid" data-act="apply">${j.easy_apply ? '<b>in</b> Solicitud sencilla' : 'Solicitar'}</button>
        ${j.link ? `<a class="li-btn" href="${esc(j.link)}" target="_blank" rel="noopener">Ver original</a>` : ''}
      </div>
    </div>
    <div class="li-about"><h2>Acerca del empleo</h2>${fmtDesc(j.description)}</div></article>`;
}
$('#pane-li').onclick = e => { if (e.target.closest('[data-act=apply]')) $('#b-apply').click(); };

/* ---------- templates / doc rendering ---------- */
const tpls = kind => (window.STUDIO_TEMPLATES || []).filter(t => t.kind === kind);
const tplKey = kind => kind === 'cv' ? 'plantilla' : 'plantilla_carta';
function curTpl(kind) {
  const list = tpls(kind), want = S.doc?.[tplKey(kind)];
  return list.find(t => t.id === want) || list[0] || null;
}
function fallbackRender(kind, d, c) {
  if (kind === 'cover') return `<h2>${esc(c.nombre)}</h2><p>${esc(c.email)} · ${esc(c.tel_mx)}</p>
    <div data-field="carta">${esc(d.carta || 'Genera la carta con “✉️ Generar carta”.').split(/\n\n/).map(p => `<p>${p}</p>`).join('')}</div>`;
  const ex = (d.experiencia || []).map((x, i) => `<h3><span data-field="experiencia.${i}.puesto">${esc(x.puesto)}</span> — <span data-field="experiencia.${i}.empresa">${esc(x.empresa)}</span></h3>
    <ul>${(x.bullets || []).map((b, k) => `<li data-field="experiencia.${i}.bullets.${k}">${esc(b)}</li>`).join('')}</ul>`).join('');
  return `<h1>${esc(c.nombre || '')}</h1><p data-field="titular"><b>${esc(d.titular)}</b></p>
    <p data-field="perfil">${esc(d.perfil)}</p><h2>Experiencia</h2>${ex}`;
}
function renderHTML(kind, forThumb, tplOverride) {
  const t = tplOverride || curTpl(kind), d = S.doc || {}, c = S.contact || {};
  let inner;
  try { inner = t ? t.render(d, c) : fallbackRender(kind, d, c); }
  catch (e) { inner = `<p style="color:#b00">Error en la plantilla: ${esc(e.message)}</p>`; }
  const cls = t ? `tpl-${t.id}` : 'tpl-basic';
  const base = esc(new URL('.', location.href).href);
  return `<!doctype html><html><head><meta charset="utf-8"><base href="${base}">
    ${t ? `<link rel="stylesheet" href="${esc(t.css)}">` : ''}
    <style>html,body{margin:0}.page{width:210mm;min-height:297mm;box-sizing:border-box;outline:none}
    .tpl-basic{padding:18mm;font:11pt/1.45 Georgia,serif}
    ${forThumb ? '' : '[data-field]:hover{outline:1px dashed rgba(47,91,234,.45);outline-offset:2px}[data-field]:focus-within{outline:1px solid rgba(47,91,234,.8)}'}</style>
    </head><body><div class="page ${cls}" ${forThumb ? '' : 'contenteditable="true" spellcheck="true"'}>${inner}</div></body></html>`;
}
function renderDocs() {
  ['cv', 'cover'].forEach(kind => {
    const list = tpls(kind), cur = curTpl(kind);
    $('#tpls-' + kind).innerHTML = list.length ? list.map(t =>
      `<button class="tpl ${cur && cur.id === t.id ? 'on' : ''}" data-id="${esc(t.id)}" title="${esc(t.name)}">
        <div class="th"><iframe tabindex="-1" loading="lazy"></iframe></div><small>${esc(t.name)}</small></button>`).join('')
      : '<span style="color:var(--muted);font-size:13px">Sin plantillas cargadas (templates.js); se usa un diseño básico.</span>';
    $$('#tpls-' + kind + ' .tpl').forEach((b, i) => { b.querySelector('iframe').srcdoc = renderHTML(kind, true, list[i]); });
    mountFrame(kind);
  });
}
function mountFrame(kind) {
  const f = $('#frame-' + kind);
  if (!S.doc) {
    f.srcdoc = `<body style="font:15px system-ui;color:#555;display:grid;place-items:center;height:90vh;text-align:center;margin:0">
      <div><h2 style="margin:0 0 8px">Todavía no hay CV para esta vacante</h2>Pulsa “✨ Generar CV” para crear uno a la medida.</div></body>`;
    return;
  }
  f.onload = () => {
    const doc = f.contentDocument; if (!doc) return;
    doc.addEventListener('input', e => {
      const n = e.target.nodeType === 1 ? e.target : e.target.parentElement;
      const sel = doc.getSelection(), at = sel && sel.anchorNode;
      const el = (at && (at.nodeType === 1 ? at : at.parentElement))?.closest('[data-field]') || n?.closest?.('[data-field]');
      if (el) syncField(el); else $$('[data-field]', doc).forEach(syncField);
      scheduleSave();
    });
    doc.addEventListener('keydown', e => { if (e.key === 'Escape') doc.activeElement?.blur(); });
    applyZoom(kind);
  };
  f.srcdoc = renderHTML(kind, false);
}
function syncField(el) {
  const path = el.dataset.field; if (!path || !S.doc) return;
  let val;
  if (path === 'carta') val = [...el.querySelectorAll('p')].length
    ? [...el.querySelectorAll('p')].map(p => p.innerText.trim()).filter(Boolean).join('\n\n')
    : el.innerText.trim().replace(/\n{2,}/g, '\n\n');
  else val = el.innerText.replace(/\s+$/, '');
  setPath(S.doc, path, val);
}
function scheduleSave() {
  $('#save-state').textContent = 'Editando…';
  clearTimeout(S.saveT); S.saveT = setTimeout(saveDoc, 1200);
}
async function saveDoc() {
  S.saveT = null; if (!S.cur || !S.doc) return;
  try { await api(`/api/jobs/${encodeURIComponent(S.cur)}/doc`, {method:'PUT', body:S.doc});
    $('#save-state').textContent = 'Guardado ' + new Date().toLocaleTimeString('es-MX', {hour:'2-digit', minute:'2-digit'}); }
  catch (e) { $('#save-state').textContent = 'Sin guardar'; toast('No se guardaron los cambios: ' + e.message, 'err'); }
}
['cv', 'cover'].forEach(kind => {
  $('#tpls-' + kind).onclick = e => {
    const b = e.target.closest('.tpl'); if (!b || !S.doc) { if (b) toast('Primero genera el CV.'); return; }
    S.doc[tplKey(kind)] = b.dataset.id;
    $$('#tpls-' + kind + ' .tpl').forEach(x => x.classList.toggle('on', x === b));
    mountFrame(kind); scheduleSave();
  };
  const z = $(`.zoom[data-kind=${kind}]`);
  z.innerHTML = '<button data-z="-1" title="Alejar">−</button><span></span><button data-z="1" title="Acercar">+</button><button data-z="0" title="Ajustar al ancho">⤢</button>';
  z.onclick = e => {
    const b = e.target.closest('button'); if (!b) return;
    const d = +b.dataset.z;
    S.zoom[kind] = d === 0 ? Math.min(1.2, ($('#pane-' + kind + ' .stage').clientWidth - 48) / 794)
      : Math.max(.3, Math.min(2, Math.round((S.zoom[kind] + d * .1) * 10) / 10));
    applyZoom(kind);
  };
});
function applyZoom(kind) {
  const f = $('#frame-' + kind), z = S.zoom[kind];
  let h = 1123;
  try { h = Math.max(1123, f.contentDocument.documentElement.scrollHeight); } catch {}
  f.style.height = h + 'px';
  f.style.transform = `scale(${z})`;
  f.style.marginBottom = `${(z - 1) * h}px`;
  $(`.zoom[data-kind=${kind}] span`).textContent = Math.round(z * 100) + '%';
}

/* ---------- tabs ---------- */
function setTab(t) {
  S.tab = t;
  $$('.tabs [data-tab]').forEach(b => b.classList.toggle('on', b.dataset.tab === t));
  ['li', 'cv', 'cover'].forEach(k => $('#pane-' + k).hidden = k !== t);
  if (t !== 'li') applyZoom(t);
}
$('.tabs').onclick = e => { const b = e.target.closest('[data-tab]'); if (b) setTab(b.dataset.tab); };

/* ---------- actions ---------- */
const jid = () => encodeURIComponent(S.cur);
async function genCV() {
  if (!S.cur) return;
  await busy($('#b-cv'), async () => {
    const keep = S.doc && {plantilla:S.doc.plantilla, plantilla_carta:S.doc.plantilla_carta};
    S.doc = {...await api(`/api/jobs/${jid()}/cv`, {method:'POST'})};
    if (keep) Object.entries(keep).forEach(([k, v]) => { if (v && !S.doc[k]) S.doc[k] = v; });
    renderDocs(); setTab('cv'); $('#save-state').textContent = 'CV a la medida generado';
    toast('CV generado. Edítalo directamente en la hoja.', 'ok');
  });
}
$('#b-cv').onclick = genCV;
$('#b-cover').onclick = () => S.cur && busy($('#b-cover'), async () => {
  const r = await api(`/api/jobs/${jid()}/cover`, {method:'POST'});
  if (!S.doc) S.doc = {...(S.base ? structuredClone(S.base) : {}), job_id:S.cur};
  S.doc.carta = r.carta || ''; renderDocs(); setTab('cover'); scheduleSave();
  toast('Carta generada.', 'ok');
});
async function fullHTML(kind) {
  const t = curTpl(kind); let css = '';
  if (t) { try { const r = await fetch(t.css); if (r.ok) css = await r.text(); } catch {} }
  let body = '';
  try { body = $('#frame-' + kind).contentDocument.body.innerHTML; } catch {}
  if (!body) body = `<div class="page ${t ? 'tpl-' + t.id : 'tpl-basic'}">${t ? t.render(S.doc || {}, S.contact) : fallbackRender(kind, S.doc || {}, S.contact)}</div>`;
  body = body.replace(/\scontenteditable="true"/g, '').replace(/\sspellcheck="true"/g, '');
  return `<!doctype html><html lang="${S.doc?.idioma || 'es'}"><head><meta charset="utf-8"><title>${kind === 'cv' ? 'CV' : 'Carta'}</title>
    <style>@page{size:A4;margin:0}html,body{margin:0}.page{width:210mm;min-height:297mm;box-sizing:border-box}
    .tpl-basic{padding:18mm;font:11pt/1.45 Georgia,serif}\n${css}</style></head><body>${body}</body></html>`;
}
$('#b-pdf').onclick = () => S.cur && busy($('#b-pdf'), async () => {
  if (!S.doc) throw new Error('Genera el CV antes de descargar el PDF.');
  const kind = S.tab === 'cover' ? 'cover' : 'cv';
  if (S.saveT) { clearTimeout(S.saveT); await saveDoc(); }
  const r = await api(`/api/jobs/${jid()}/pdf`, {method:'POST', body:{kind, html: await fullHTML(kind)}});
  toast(`PDF de ${kind === 'cv' ? 'CV' : 'carta'} listo.`, 'ok');
  if (r?.url) window.open(r.url, '_blank');
});
async function apply() {
  if (!S.cur) return;
  await busy($('#b-apply'), async () => {
    if (S.saveT) { clearTimeout(S.saveT); await saveDoc(); }
    const r = await api(`/api/jobs/${jid()}/apply`, {method:'POST'}) || {};
    if (r.open_url) { window.open(r.open_url, '_blank', 'noopener'); toast('Se abrió la vacante original. Postúlate ahí con tu PDF.', 'ok'); }
    else toast(r.message || 'Abriendo LinkedIn con la Solicitud sencilla llena. Revisa y envía tú.', 'ok');
    const j = S.jobs.find(x => x.id === S.cur); if (j) { j.app_status = j.app_status || 'en proceso'; renderList(); }
    loadStats();
  });
}
$('#b-apply').onclick = apply;
async function setStatus(st) {
  if (!S.cur) return;
  const status = S.job?.status === st ? 'new' : st;
  try {
    await api(`/api/jobs/${jid()}/status`, {method:'POST', body:{status}});
    if (S.job) S.job.status = status;
    const j = S.jobs.find(x => x.id === S.cur); if (j) j.status = status;
    updateVotes(); renderList(); loadStats();
    toast(status === 'liked' ? 'Marcada como “Me interesa”.' : status === 'passed' ? 'Vacante descartada.' : 'Estado restablecido.');
  } catch (e) { toast(e.message, 'err'); }
}
$('#b-like').onclick = () => setStatus('liked');
$('#b-pass').onclick = () => setStatus('passed');

/* ---------- keyboard ---------- */
document.addEventListener('keydown', e => {
  if (e.metaKey || e.ctrlKey || e.altKey) return;
  const tg = e.target;
  if (tg.isContentEditable || /^(INPUT|SELECT|TEXTAREA)$/.test(tg.tagName)) { if (e.key === 'Escape') tg.blur(); return; }
  const i = S.jobs.findIndex(j => j.id === S.cur);
  const k = e.key;
  if (k === 'j' || k === 'k') { const n = S.jobs[Math.max(0, Math.min(S.jobs.length - 1, i + (k === 'j' ? 1 : -1)))]; if (n) openJob(n.id); }
  else if (k === 'g') genCV();
  else if (k === 'a') apply();
  else if (k === '/') { e.preventDefault(); $('#q').focus(); }
  else if (k === '1') setTab('li'); else if (k === '2') setTab('cv'); else if (k === '3') setTab('cover');
  else return;
});

/* ---------- scrape ---------- */
$('#sc-run').onclick = () => busy($('#sc-run'), async () => {
  const sources = $$('#sc-sources input:checked').map(i => i.value);
  if (!sources.length) throw new Error('Elige al menos una plataforma.');
  await api('/api/scrape', {method:'POST', body:{sources, query: $('#sc-query').value.trim(), location: $('#sc-loc').value.trim()}});
  toast('Búsqueda iniciada.'); $('#sc-log').hidden = false; $('#sc-log').textContent = 'Iniciando…'; poll();
});
async function poll() {
  clearTimeout(S.pollT);
  try {
    const s = await api('/api/scrape/status');
    const log = $('#sc-log');
    if (s.log?.length) { log.hidden = false; log.textContent = s.log.join('\n'); log.scrollTop = log.scrollHeight; }
    if (s.running) { $('#sc-run').disabled = true; S.pollT = setTimeout(poll, 1500); }
    else {
      $('#sc-run').disabled = false;
      if (S.wasRunning) {
        const n = Object.values(s.counts || {}).reduce((a, b) => a + (+b || 0), 0);
        toast(`Búsqueda terminada: ${n} vacantes encontradas.`, 'ok'); loadJobs(); loadStats();
      }
    }
    S.wasRunning = !!s.running;
  } catch {}
}

/* ---------- init ---------- */
(async function init() {
  renderChips(); setTab('li');
  if (!tpls('cv').length) setTimeout(() => S.doc && renderDocs(), 1500); // templates.js may load late
  try { const p = await api('/api/profile'); S.contact = p || {}; S.base = p?.base || null;
    const H = window.STUDIO_TEMPLATE_HELPERS; if (H && p?.edu) for (const l of ['es', 'en']) Object.assign(H.edu[l], p.edu[l] || {}); } catch {}
  await Promise.all([loadJobs(), loadStats()]);
  poll();
})();
})();
