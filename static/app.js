'use strict';

/* ===================== estado (en memoria; la BD es la fuente de verdad) ===================== */
const state = { queue: [], matches: [], hlCache: {}, history: [] };

const $ = s => document.querySelector(s);
const $$ = s => [...document.querySelectorAll(s)];
const api = async (path, opts) => {
  const r = await fetch(path, opts);
  const d = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(d.error || `Error ${r.status}`);
  return d;
};
const jpost = (path, body) => api(path, {
  method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}),
});
function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, c => ({ '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;' }[c]));
}
function toast(msg, ms = 3200) {
  const t = $('#toast'); t.innerHTML = msg; t.classList.add('show');
  clearTimeout(t._h); t._h = setTimeout(() => t.classList.remove('show'), ms);
}

/* ===================== navegación ===================== */
$$('#nav button').forEach(b => b.addEventListener('click', () => {
  $$('#nav button').forEach(x => x.classList.remove('active'));
  $$('.panel').forEach(x => x.classList.remove('active'));
  b.classList.add('active');
  $('#panel-' + b.dataset.panel).classList.add('active');
  if (b.dataset.panel === 'matches') reloadMatches();
  if (b.dataset.panel === 'chat') loadChat();
  if (b.dataset.panel === 'datos') loadJobsTable();
  if (b.dataset.panel === 'aplicar') loadApplications();
  if (b.dataset.panel === 'perfil') loadMasterCv();
}));
function goPanel(name) { $(`#nav button[data-panel="${name}"]`).click(); }

/* ===================== chips (selección única) ===================== */
$$('.chips[data-single]').forEach(group => {
  group.addEventListener('click', e => {
    const chip = e.target.closest('.chip');
    if (!chip) return;
    group.querySelectorAll('.chip').forEach(c => c.classList.remove('sel'));
    chip.classList.add('sel');
  });
});
const chipValue = id => { const c = $(`#${id} .chip.sel`); return c ? c.dataset.v : ''; };
const setChip = (id, val) => $$(`#${id} .chip`).forEach(c => c.classList.toggle('sel', c.dataset.v === val));

/* ===================== config ===================== */
async function loadConfig() {
  try {
    const cfg = await api('/api/config');
    $('#base-cv').value = cfg.base_cv || '';
    $('#gemini-key').value = cfg.gemini_key || '';
    if (cfg.gemini_model) $('#gemini-model').value = cfg.gemini_model;
    setChip('ai-provider', cfg.ai_provider || 'gemini');
    if (cfg.mcp_timeout) $('#mcp-timeout').value = cfg.mcp_timeout;
    applyProviderUI();
    const p = cfg.profile || {};
    state.profile = p;
    $('#p-location').value = p.location || '';
    $('#p-experience').value = p.experience || '';
    $('#p-field').value = p.field || '';
    $('#p-authorization').value = p.authorization || '';
    setChip('p-education', p.education);
    setChip('p-relocate', p.relocate);
    setChip('p-modality', p.modality);
    setChip('p-seniority', p.seniority);
    if (p.location && !$('#s-location').value) $('#s-location').value = p.location;
    if (cfg.mock) toast('⚙️ Modo demo (MOCK): datos de ejemplo, sin LinkedIn real.');
  } catch (e) { /* servidor no disponible */ }
}

function collectProfile() {
  return {
    ...(state.profile || {}),  // conserva nombre, email, inglés, etc. (de la bienvenida / preguntas)
    location: $('#p-location').value.trim(),
    experience: $('#p-experience').value.trim(),
    field: $('#p-field').value.trim(),
    authorization: $('#p-authorization').value.trim(),
    education: chipValue('p-education'),
    relocate: chipValue('p-relocate'),
    modality: chipValue('p-modality'),
    seniority: chipValue('p-seniority'),
  };
}

$('#save-profile').addEventListener('click', async () => {
  const st = $('#profile-status');
  try {
    await jpost('/api/config', { base_cv: $('#base-cv').value.trim(), profile: collectProfile() });
    st.textContent = '✅ Perfil guardado.'; st.className = 'status ok';
  } catch (e) { st.textContent = '❌ ' + e.message; st.className = 'status err'; }
});
$('#goto-titles').addEventListener('click', async () => {
  await $('#save-profile').click(); goPanel('swipe'); $('#btn-suggest').click();
});
$('#save-settings').addEventListener('click', async () => {
  const st = $('#settings-status');
  try {
    await jpost('/api/config', {
      ai_provider: chipValue('ai-provider') || 'gemini',
      mcp_timeout: parseInt($('#mcp-timeout').value, 10) || 900,
      gemini_key: $('#gemini-key').value.trim(),
      gemini_model: $('#gemini-model').value,
    });
    applyProviderUI(); refreshMcp();
    st.textContent = '✅ Ajustes guardados.'; st.className = 'status ok';
  } catch (e) { st.textContent = '❌ ' + e.message; st.className = 'status err'; }
});

/* ===================== puente MCP (Claude sin API key) ===================== */
function providerIsMcp() { return chipValue('ai-provider') === 'mcp'; }

function applyProviderUI() {
  const mcp = providerIsMcp();
  $('#mcp-box').hidden = !mcp;
  $('#box-gemini').style.opacity = mcp ? '.45' : '';
}
$('#ai-provider').addEventListener('click', () => setTimeout(applyProviderUI, 0));

let mcpState = { waiting: 0, connected: false, provider: 'gemini' };

async function refreshMcp() {
  try {
    const st = await api('/api/mcp/status');
    mcpState = st;
    const banner = $('#mcp-banner');
    if (st.provider === 'mcp' && st.waiting > 0) {
      $('#mcp-banner-text').textContent =
        st.waiting === 1 ? 'Esperando a Claude — 1 tarea de IA en cola'
                         : `Esperando a Claude — ${st.waiting} tareas de IA en cola`;
      banner.hidden = false;
    } else { banner.hidden = true; }

    const pill = $('#mcp-pill');
    if (pill) {
      if (st.client_connected) {
        pill.className = 'pill ok';
        pill.innerHTML = '<span class="led"></span> Cliente MCP conectado' +
          (st.served ? ` · ${st.served} tarea(s) resueltas` : '');
      } else {
        pill.className = 'pill';
        pill.innerHTML = '<span class="led"></span> Ningún cliente MCP ha llamado todavía';
      }
    }
    const list = $('#mcp-tasks');
    if (list) {
      list.innerHTML = (st.tasks || []).length
        ? '<b>En cola:</b><br>' + st.tasks.map(t =>
            `· <code>${t.id}</code> [${t.status}] ${esc(t.label)} — ${Math.round(t.age)}s`).join('<br>')
        : '';
    }
  } catch (e) { /* servidor no disponible */ }
}
setInterval(refreshMcp, 3000);
refreshMcp();

/* ===================== LinkedIn ===================== */
async function refreshLinkedIn() {
  try {
    const s = await api('/api/linkedin/status');
    const pill = $('#li-pill');
    if (s.logged_in) { pill.className = 'pill ok'; pill.innerHTML = '<span class="led"></span> Sesión activa' + (s.mock ? ' (demo)' : ''); }
    else if (s.launched) { pill.className = 'pill'; pill.innerHTML = '<span class="led"></span> Navegador abierto — inicia sesión'; }
    else { pill.className = 'pill'; pill.innerHTML = '<span class="led"></span> Sin conectar'; }
    $('#li-pill-mini').className = 'pill' + (s.logged_in ? ' ok' : '');
  } catch (e) {}
}
$('#btn-connect').addEventListener('click', async () => {
  const st = $('#connect-status');
  st.innerHTML = '<span class="spinner"></span> Abriendo navegador...'; st.className = 'status';
  try {
    await jpost('/api/linkedin/connect');
    st.textContent = '✅ Navegador abierto. Inicia sesión en LinkedIn en esa ventana y vuelve aquí.';
    st.className = 'status ok';
    setTimeout(refreshLinkedIn, 1000);
  } catch (e) { st.textContent = '❌ ' + e.message; st.className = 'status err'; }
});

/* ===================== sugerir puestos ===================== */
$('#btn-suggest').addEventListener('click', async () => {
  const list = $('#suggest-list');
  list.innerHTML = '<div class="status"><span class="spinner"></span> Pensando los mejores puestos para tu perfil...</div>';
  try {
    const { queries } = await jpost('/api/suggest-titles', { profile: collectProfile(), base_cv: $('#base-cv').value.trim() });
    if (!queries || !queries.length) { list.innerHTML = '<div class="status">No hubo sugerencias.</div>'; return; }
    renderSuggestions(queries);
  } catch (e) { list.innerHTML = `<div class="status err">❌ ${esc(e.message)}</div>`; }
});

function renderSuggestions(queries) {
  const list = $('#suggest-list'); list.innerHTML = '';
  const bar = document.createElement('div');
  bar.style.cssText = 'display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-bottom:14px';
  bar.innerHTML = `<button class="primary" id="btn-search-all">🔎 Buscar TODAS en LinkedIn (${queries.length})</button>
    <span class="status" id="search-all-status" style="margin:0"></span>`;
  list.appendChild(bar);
  bar.querySelector('#btn-search-all').addEventListener('click', () => searchAll(queries));
  queries.forEach(q => {
    const el = document.createElement('div');
    el.className = 'qcard';
    el.innerHTML = `<div><div class="q-main">${esc(q.keywords)}</div>
      <div class="q-sub">📍 ${esc(q.location || '—')} ${q.why ? '· ' + esc(q.why) : ''}</div></div>
      <button class="ghost">🔍 Buscar</button>`;
    el.querySelector('button').addEventListener('click', ev => searchOne(q, ev.currentTarget));
    list.appendChild(el);
  });
}

async function searchOne(q, btn) {
  if (btn) { btn.disabled = true; btn.textContent = '⏳...'; }
  try {
    const { jobs } = await jpost('/api/search', { keywords: q.keywords, location: q.location || '', limit: +$('#s-limit').value });
    await reloadDeck();
    toast(`✅ ${jobs.length} ofertas nuevas de "${q.keywords}".`);
    if (jobs.length) goPanelToDeck();
  } catch (e) { toast('❌ ' + e.message, 5000); }
  finally { if (btn) { btn.disabled = false; btn.textContent = '🔍 Buscar'; } }
}

async function searchAll(queries) {
  if (!queries || !queries.length) return;
  const btn = $('#btn-search-all'), status = $('#search-all-status');
  btn.disabled = true; status.className = 'status';
  status.innerHTML = `<span class="spinner"></span> Buscando ${queries.length} puestos en LinkedIn... puede tardar varios minutos, no cierres la ventana.`;
  try {
    const { jobs } = await jpost('/api/search-batch', { queries, per_query_limit: 6 });
    await reloadDeck();
    status.className = 'status ok';
    status.textContent = `✅ ${jobs.length} ofertas nuevas en el mazo (de ${queries.length} búsquedas).`;
    notify('🔎 Búsqueda terminada', `${jobs.length} ofertas nuevas listas para deslizar.`);
    if (jobs.length) goPanelToDeck();
  } catch (e) { status.className = 'status err'; status.textContent = '❌ ' + e.message; }
  finally { btn.disabled = false; }
}

/* ===================== buscar / manual ===================== */
$('#btn-manual').addEventListener('click', () => {
  const b = $('#manual-box'); b.style.display = b.style.display === 'none' ? 'block' : 'none';
});
let scrapedJob = null;  // datos traídos del link (id real de LinkedIn, easy_apply, etc.)

$('#btn-scrape').addEventListener('click', async () => {
  const url = $('#m-url').value.trim();
  const st = $('#scrape-status');
  if (!url) { st.textContent = '⚠️ Pega el link del empleo.'; st.className = 'status err'; return; }
  st.innerHTML = '<span class="spinner"></span> Trayendo datos del link...';
  st.className = 'status'; $('#btn-scrape').disabled = true;
  try {
    const { job, warning } = await jpost('/api/scrape-job', { url });
    scrapedJob = job || null;
    if (job) {
      $('#m-title').value = job.title || '';
      $('#m-company').value = job.company || '';
      $('#m-location').value = job.location || '';
      $('#m-link').value = job.link || url;
      $('#m-desc').value = job.description || '';
    }
    st.textContent = warning ? '⚠️ ' + warning : '✅ Datos traídos. Revísalos y pulsa "Agregar al mazo".';
    st.className = warning ? 'status' : 'status ok';
  } catch (e) {
    st.textContent = '❌ ' + e.message; st.className = 'status err';
  } finally { $('#btn-scrape').disabled = false; }
});

$('#btn-add-manual').addEventListener('click', async () => {
  const title = $('#m-title').value.trim(), desc = $('#m-desc').value.trim();
  if (!title || !desc) { toast('⚠️ Pon al menos puesto y descripción (o trae los datos del link).'); return; }
  try {
    await jpost('/api/job/add', {
      title, company: $('#m-company').value.trim(), location: $('#m-location').value.trim(),
      link: $('#m-link').value.trim(), description: desc,
      // si vino del link, conserva el id real de LinkedIn y easy_apply para poder auto-aplicar
      id: scrapedJob && scrapedJob.link === $('#m-link').value.trim() ? scrapedJob.id : undefined,
      easy_apply: scrapedJob ? scrapedJob.easy_apply : false,
      employment_type: scrapedJob ? scrapedJob.employment_type : '',
      seniority: scrapedJob ? scrapedJob.seniority : '',
      source: scrapedJob ? 'LinkedIn' : 'manual',
    });
    await reloadDeck();
    ['#m-url','#m-title','#m-company','#m-location','#m-link','#m-desc'].forEach(s => $(s).value = '');
    scrapedJob = null;
    $('#scrape-status').textContent = '';
    toast('✅ Oferta guardada en la base de datos.');
  } catch (e) { toast('❌ ' + e.message, 5000); }
});

$('#btn-search').addEventListener('click', async () => {
  const kw = $('#s-keywords').value.trim(), loc = $('#s-location').value.trim();
  const st = $('#search-status');
  if (!kw) { st.textContent = '⚠️ Escribe un puesto.'; st.className = 'status err'; return; }
  st.innerHTML = '<span class="spinner"></span> Scrapeando LinkedIn... puede tardar por las pausas anti-bloqueo.';
  st.className = 'status'; $('#btn-search').disabled = true;
  try {
    const { jobs } = await jpost('/api/search', { keywords: kw, location: loc, limit: +$('#s-limit').value });
    await reloadDeck();
    st.textContent = jobs.length ? `✅ ${jobs.length} ofertas nuevas en el mazo.` : '🤷 No se encontraron ofertas nuevas.';
    st.className = 'status ok';
    if (jobs.length) goPanelToDeck();
  } catch (e) { st.textContent = '❌ ' + e.message; st.className = 'status err'; }
  finally { $('#btn-search').disabled = false; }
});
function goPanelToDeck() {
  if (!$('#search-drawer').hidden) $('#btn-drawer').click();  // al encontrar empleos, cerramos el cajón
  $('#deck').scrollIntoView({ behavior: 'smooth', block: 'center' });
}

/* ===================== deck / swipe ===================== */
const deck = $('#deck');
async function reloadDeck() {
  try { const { jobs } = await api('/api/deck'); state.queue = jobs || []; renderDeck(); } catch (e) {}
}
function renderDeck() {
  $('#deck-count').textContent = state.queue.length;
  $('#btn-undo').disabled = !state.history.length;
  deck.querySelectorAll('.jobcard').forEach(c => c.remove());
  $('#deck-empty').style.display = state.queue.length ? 'none' : 'flex';
  const top = state.queue.slice(0, 3).reverse();
  top.forEach((job, i) => {
    const isTop = i === top.length - 1;
    const depth = top.length - 1 - i;
    const el = document.createElement('div');
    el.className = 'jobcard';
    el.style.transform = `translateY(${depth * 11}px) scale(${1 - depth * 0.04})`;
    el.style.zIndex = 10 + i;
    el.innerHTML = `
      <div class="stamp like">ME GUSTA</div>
      <div class="stamp nope">PASO</div>
      <div class="jc-head">
        <div class="tags">
          ${job.easy_apply ? '<span class="tag easy">⚡ Easy Apply</span>' : ''}
          ${job.employment_type ? `<span class="tag emp">🕒 ${esc(job.employment_type)}</span>` : ''}
          <span class="tag src">${esc(job.source || 'LinkedIn')}</span>
        </div>
        <div class="company">${esc(job.company)}</div>
        <h3>${esc(job.title)}</h3>
        <div class="meta">📍 ${esc(job.location || 'No especificada')}${job.seniority ? ' · 📈 ' + esc(job.seniority) : ''}</div>
      </div>
      <div class="jc-body">
        <div class="hl-slot">${renderHLLoading()}</div>
        <details class="desc-full">
          <summary>📄 Ver descripción completa</summary>
          <div class="desc">${esc(job.description || 'Sin descripción.')}</div>
        </details>
      </div>`;
    if (isTop) attachDrag(el, job);
    deck.appendChild(el);
    // Solo la carta superior carga su resumen (ahorra llamadas a la IA).
    if (isTop) loadHighlights(job, el.querySelector('.hl-slot'));
  });
}

/* ===================== resumen de puntos clave (IA + fallback) ===================== */
function renderHLLoading() {
  return `<div class="hl-loading"><span class="hl-spark">✨</span> Resumiendo lo importante…
    <div class="hl-shimmer"></div><div class="hl-shimmer" style="width:80%"></div><div class="hl-shimmer" style="width:60%"></div></div>`;
}

async function loadHighlights(job, slot) {
  if (!slot) return;
  const cached = state.hlCache[job.id];
  if (cached !== undefined) { slot.innerHTML = renderHighlights(cached, job); return; }
  try {
    const { highlights } = await jpost('/api/highlights', { job_id: job.id });
    const hl = highlights || heuristicHighlights(job);
    state.hlCache[job.id] = hl;
    // La carta pudo cambiar mientras cargaba; comprobamos que siga en el DOM.
    if (slot.isConnected) slot.innerHTML = renderHighlights(hl, job);
  } catch (e) {
    const hl = heuristicHighlights(job);
    state.hlCache[job.id] = hl;
    if (slot.isConnected) slot.innerHTML = renderHighlights(hl, job);
  }
}

function renderHighlights(hl, job) {
  if (!hl) hl = heuristicHighlights(job);
  const chips = [];
  if (hl.salary) chips.push(`<span class="hl-chip money">💰 ${esc(hl.salary)}</span>`);
  if (hl.modality) chips.push(`<span class="hl-chip mode">🏠 ${esc(hl.modality)}</span>`);
  if (hl.seniority) chips.push(`<span class="hl-chip lvl">📈 ${esc(hl.seniority)}</span>`);
  const chipRow = chips.length ? `<div class="hl-chips">${chips.join('')}</div>` : '';

  const tldr = hl.tldr ? `<div class="hl-tldr">${esc(hl.tldr)}</div>` : '';

  const points = (hl.highlights || []).filter(Boolean);
  const pointsHtml = points.length
    ? `<ul class="hl-points">${points.map(p => `<li>${esc(p)}</li>`).join('')}</ul>` : '';

  const skills = (hl.skills || []).filter(Boolean);
  const skillsHtml = skills.length
    ? `<div class="hl-skills">${skills.map(s => `<span class="hl-skill">${esc(s)}</span>`).join('')}</div>` : '';

  const flags = (hl.flags || []).filter(Boolean);
  const flagsHtml = flags.length
    ? `<div class="hl-flags">${flags.map(f => `<div class="hl-flag">⚠️ ${esc(f)}</div>`).join('')}</div>` : '';

  return tldr + chipRow + (pointsHtml ? `<div class="hl-label">Lo importante</div>${pointsHtml}` : '')
       + (skillsHtml ? `<div class="hl-label">Habilidades / tech</div>${skillsHtml}` : '')
       + flagsHtml;
}

/* Fallback local sin IA: extrae salario, viñetas y banderas de la descripción. */
function heuristicHighlights(job) {
  const desc = job.description || '';
  const low = desc.toLowerCase();
  const salaryMatch = desc.match(/\$\s?\d[\d,\.]*\s?(k|mil)?\s?(-|–|a|to)\s?\$?\s?\d[\d,\.]*\s?(k|mil)?(\s?(\/|per|por)?\s?(year|año|hr|hour|hora|month|mes))?/i)
    || desc.match(/\$\s?\d[\d,\.]{2,}(\s?(k|mil))?(\s?(\/|per|por)?\s?(year|año|hr|hour|hora|month|mes))?/i);
  const bullets = desc.split('\n')
    .map(l => l.replace(/^[\s•\-*·]+/, '').trim())
    .filter(l => l.length > 3);
  const flags = [];
  if (low.includes('citizen') || low.includes('ciudadan')) flags.push('Puede exigir ciudadanía');
  if (low.includes('clearance')) flags.push('Requiere security clearance');
  if (low.includes('on-call') || low.includes('on call')) flags.push('Turnos on-call');
  const yrs = desc.match(/(\d+)\+?\s*(years|años|yrs)/i);
  if (yrs && parseInt(yrs[1], 10) >= 4) flags.push(`Requiere ${yrs[1]}+ años`);
  return {
    tldr: (desc.split('\n').find(l => l.trim().length > 20) || '').slice(0, 120) || null,
    salary: salaryMatch ? salaryMatch[0].trim() : null,
    modality: low.includes('hybrid') || low.includes('híbrid') ? 'Híbrido'
      : (low.includes('remote') || low.includes('remoto') ? 'Remoto'
        : (low.includes('on-site') || low.includes('presencial') ? 'Presencial' : null)),
    seniority: job.seniority || null,
    highlights: bullets.slice(0, 5),
    skills: [],
    flags,
  };
}

function attachDrag(el, job) {
  let sx = 0, dx = 0, dragging = false;
  const like = el.querySelector('.stamp.like'), nope = el.querySelector('.stamp.nope');
  const body = el.querySelector('.jc-body');
  el.addEventListener('pointerdown', e => {
    // Si el gesto empieza en el cuerpo desplazable, dejamos que haga scroll en vez de arrastrar.
    if (body && body.contains(e.target) && body.scrollHeight > body.clientHeight) return;
    dragging = true; sx = e.clientX; el.classList.add('dragging'); el.setPointerCapture(e.pointerId);
  });
  el.addEventListener('pointermove', e => {
    if (!dragging) return;
    dx = e.clientX - sx;
    el.style.transform = `translate(${dx}px, ${Math.abs(dx) * 0.06}px) rotate(${dx / 18}deg)`;
    like.style.opacity = Math.max(0, Math.min(1, dx / 90));
    nope.style.opacity = Math.max(0, Math.min(1, -dx / 90));
  });
  const end = () => {
    if (!dragging) return;
    dragging = false; el.classList.remove('dragging');
    if (dx > 110) swipe(job, true, el);
    else if (dx < -110) swipe(job, false, el);
    else { el.style.transform = ''; like.style.opacity = nope.style.opacity = 0; }
    dx = 0;
  };
  el.addEventListener('pointerup', end);
  el.addEventListener('pointercancel', end);
}

function swipe(job, liked, el) {
  if (el) {
    el.style.transform = `translate(${liked ? 720 : -720}px, -40px) rotate(${liked ? 26 : -26}deg)`;
    el.style.opacity = '0';
  }
  state.queue = state.queue.filter(j => j.id !== job.id);
  state.history.push({ job, liked });
  if (state.history.length > 30) state.history.shift();
  jpost('/api/swipe', { job_id: job.id, liked }).catch(() => {});
  if (liked) {
    const match = { id: job.id, job, docs: null, status: 'generating', error: '' };
    state.matches = state.matches.filter(m => m.id !== job.id);
    state.matches.unshift(match); renderMatches();
    toast(`💜 ¡Match con ${job.company}! Generando documentos...`);
    generateDocs(match);
  }
  setTimeout(renderDeck, 260);
  maybeAskQuestion();
}

$('#btn-like').addEventListener('click', () => { if (state.queue[0]) swipe(state.queue[0], true, deck.querySelector('.jobcard:last-of-type')); });
$('#btn-nope').addEventListener('click', () => { if (state.queue[0]) swipe(state.queue[0], false, deck.querySelector('.jobcard:last-of-type')); });
document.addEventListener('keydown', e => {
  if (!$('#panel-swipe').classList.contains('active')) return;
  if (e.target.matches('input, textarea, select')) return;
  if (e.key === 'ArrowRight') $('#btn-like').click();
  if (e.key === 'ArrowLeft') $('#btn-nope').click();
  if (e.key === 'z' || e.key === 'Z' || e.key === 'Backspace') undoSwipe();
});

/* ↶ regresar: devuelve la última tarjeta al mazo y la deja como "nueva" */
async function undoSwipe() {
  const last = state.history.pop();
  if (!last) return;
  const { job, liked } = last;
  state.queue = [job, ...state.queue.filter(j => j.id !== job.id)];
  if (liked) { state.matches = state.matches.filter(m => m.id !== job.id); renderMatches(); }
  $('#q-card').hidden = true;
  renderDeck();
  const top = deck.querySelector('.jobcard:last-of-type');
  if (top) { top.classList.add('undo-in'); }
  try { await jpost('/api/job/status', { job_id: job.id, status: 'new' }); }
  catch (e) { toast('❌ ' + e.message); }
  toast(`↶ De vuelta: ${esc(job.title)}`, 1800);
}
$('#btn-undo').addEventListener('click', undoSwipe);
$('#btn-drawer').addEventListener('click', () => {
  const d = $('#search-drawer'); d.hidden = !d.hidden;
  $('#btn-drawer').classList.toggle('open', !d.hidden);
  $('#btn-drawer').textContent = d.hidden ? '🔍 Buscar más' : '✕ Cerrar búsqueda';
});

function checkDocPages(kind, docs, lang = 'en') {
  return new Promise((resolve) => {
    const iframe = document.createElement('iframe');
    iframe.style.position = 'absolute';
    iframe.style.top = '-9999px';
    iframe.style.left = '-9999px';
    iframe.style.width = '8.5in';
    iframe.style.height = '1500px';
    document.body.appendChild(iframe);
    iframe.srcdoc = buildStandaloneDoc(renderDocInner(kind, docs, lang), true, false);
    iframe.onload = () => {
      setTimeout(() => {
        try {
          const page = iframe.contentDocument.querySelector('.page');
          const height = page.getBoundingClientRect().height;
          const avail = 9.8 * 96; // 940.8px
          const isMoreThanOnePage = height > (avail + 1.0);
          document.body.removeChild(iframe);
          resolve({ isMoreThanOnePage, height, avail });
        } catch (e) {
          console.error(e);
          try { document.body.removeChild(iframe); } catch(ex) {}
          resolve({ isMoreThanOnePage: false, height: 0, avail: 9.8 * 96, error: e.message });
        }
      }, 80);
    };
  });
}

window.downloadMatchDoc = (matchId, kind, lang = 'en') => {
  const m = state.matches.find(x => String(x.id) === String(matchId));
  if (m && m.docs) {
    const label = (DOC_TABS.find(t => t[0] === kind) || [,''])[1];
    downloadDocPDF(kind, m.docs, `${label}_${lang.toUpperCase()}_${(m.job.company||'').replace(/\W+/g,'_')}`, lang);
  }
};

/* ===================== generación de documentos ===================== */
async function generateDocs(match) {
  match.status = 'generating'; match.error = ''; renderMatches();
  try {
    const docs = await jpost('/api/generate', { job_id: match.job.id });
    
    // Resume y CV deben caber en UNA página, en ambos idiomas
    for (const kind of ['resume', 'cv']) for (const lang of ['en', 'es']) {
      if (!(docs[lang] || docs)[kind]) continue;
      let check = await checkDocPages(kind, docs, lang);
      let attempts = 0;
      const maxAttempts = 3;
      while (check.isMoreThanOnePage && attempts < maxAttempts) {
        attempts++;
        toast(`📏 El ${kind === 'cv' ? 'CV' : 'Resume'} (${lang.toUpperCase()}) supera 1 página (${Math.round(check.height)}px > ${Math.round(check.avail)}px). Condensando con IA (Intento ${attempts}/${maxAttempts})...`);
        const res = await jpost('/api/refine', {
          job_id: match.job.id,
          doc_type: kind,
          lang: lang,
          instruction: 'El documento supera 1 página. Por favor, reduce y condensa el texto de las experiencias (acorta las viñetas, simplifica el resumen profesional) para que quepa en una sola página. IMPORTANTE: Devuelve el JSON completo y válido, con todos los campos rellenados. NO uses comentarios como "// ...", puntos suspensivos ni omitas datos.'
        });
        if (docs[lang]) {
          docs[lang][kind] = res.doc;
          if (res.profile) docs[lang].profile = res.profile;
        } else {
          docs[kind] = res.doc;
          if (res.profile) docs.profile = res.profile;
        }
        check = await checkDocPages(kind, docs, lang);
      }
    }
    
    match.docs = docs; match.status = 'done'; renderMatches();
    notify(`✅ Documentos listos: ${match.job.title}`, `Resume, CV y Cover Letter para ${match.job.company} están listos.`);
    
    // Auto-download Resume in English (default)
    const label = 'Resume';
    const lang = 'en';
    const filename = `${label}_${lang.toUpperCase()}_${(match.job.company||'').replace(/\W+/g,'_')}`;
    const w = window.open('', '_blank');
    if (w) {
      w.document.write(buildStandaloneDoc(renderDocInner('resume', docs, lang), true, true));
      w.document.title = filename;
      w.document.close();
      w.focus();
      toast(`✅ Documentos listos para ${match.job.company}. Descargando Resume en 1 página (EN)...`);
    } else {
      toast(`✅ Documentos listos para ${match.job.company}. <a href="javascript:void(0)" onclick="downloadMatchDoc('${match.job.id}', 'resume', 'en')" style="color:var(--like);font-weight:bold;text-decoration:underline">Descargar Resume en 1 página (EN)</a>`, 8000);
    }
  } catch (e) {
    match.status = 'error'; match.error = e.message; renderMatches();
    toast('❌ ' + e.message, 5000);
  }
}

/* ===================== matches ===================== */
const DOC_TABS = [['resume','Resume'],['cv','CV'],['cover_letter','Cover Letter']];
async function reloadMatches() {
  try {
    const { matches } = await api('/api/matches');
    // conserva los que están generándose ahora mismo (aún no en BD con docs)
    const generating = state.matches.filter(m => m.status === 'generating');
    state.matches = (matches || []).map(m => {
      const g = generating.find(x => x.id === m.job.id);
      if (g && !m.docs) return g;
      return { id: m.job.id, job: m.job, docs: m.docs, status: m.docs ? 'done' : 'pending', error: '' };
    });
    state.matches.forEach(m => indexJob(m.job, { has_docs: !!m.docs }));
    renderMatches();
  } catch (e) {}
}

function renderMatches() {
  $('#match-count').textContent = state.matches.length;
  $('#matches-empty').style.display = state.matches.length ? 'none' : 'block';
  const list = $('#matches-list'); list.innerHTML = '';
  state.matches.forEach(m => {
    const el = document.createElement('div');
    const currentLang = el.dataset.lang || 'en';
    el.className = 'match'; el.dataset.id = m.id; el.dataset.doc = 'resume'; el.dataset.lang = currentLang;
    let statusHtml;
    if (m.status === 'generating') statusHtml = '<span class="gen"><span class="spinner"></span> Generando...</span>';
    else if (m.status === 'error') statusHtml = `<span class="gen" style="color:var(--nope)">❌ ${esc(m.error)}</span> <button class="ghost" data-act="generate">Reintentar</button>`;
    else if (m.status === 'pending') statusHtml = '<button class="ghost" data-act="generate">✨ Generar documentos</button>';
    else statusHtml = '<button class="ghost" data-act="toggle">📄 Ver documentos</button> '
                    + '<button class="ghost" data-act="regenerate" title="Rehacer con tu CV/perfil actual">🔄 Regenerar</button>';

    const applyLink = (m.docs && m.docs.apply_link) || m.job.link || '';
    
    // Si los documentos no tienen el formato bilingüe nuevo, ocultamos el selector de idioma en la UI
    const isBilingual = m.docs && (m.docs.en || m.docs.es);
    const langSelectorHtml = isBilingual ? `
      <div class="lang-selector">
        <button class="lang-btn ${currentLang === 'en' ? 'active' : ''}" data-lang-set="en">🇺🇸 EN</button>
        <button class="lang-btn ${currentLang === 'es' ? 'active' : ''}" data-lang-set="es">🇪🇸 ES</button>
      </div>
    ` : '';

    el.innerHTML = `
      <div class="head">
        <div>
          <h3 class="jobpop-trigger" data-jobpop="${esc(m.id)}">${esc(m.job.title)}</h3>
          <div class="co">${esc(m.job.company)} · ${esc(m.job.location || '')}</div>
        </div>
        <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
          ${statusHtml}
          <button class="ghost" data-act="delete" title="Eliminar">🗑</button>
        </div>
      </div>
      ${m.status === 'done' && m.docs ? `
      <div class="docs">
        <div class="apply-banner">
          <div class="t">🎯 Cuando estés listo, aplica aquí con los documentos generados:</div>
          ${applyLink ? `<a class="apply-btn" href="${esc(applyLink)}" target="_blank">Aplicar en LinkedIn ↗</a>` : '<span class="t">(sin link)</span>'}
        </div>
        ${m.docs.match_notes ? `<div class="match-notes">${mdToHtml(m.docs.match_notes)}</div>` : ''}
        <div class="doc-toolbar">
          <div class="doc-tabs">${DOC_TABS.map((t,i)=>`<button class="${i===0?'active':''}" data-doc="${t[0]}">${t[1]}</button>`).join('')}</div>
          ${langSelectorHtml}
          <div class="doc-actions">
            <button class="ghost" data-act="edit">✏️ Editar texto</button>
            <button class="primary edit-only" data-act="save-edit" style="display:none">💾 Guardar</button>
            <button class="ghost edit-only" data-act="cancel-edit" style="display:none">✖ Cancelar</button>
            <button class="primary" data-act="pdf">⬇️ Descargar PDF</button>
            <button class="ghost" data-act="copy">📋 Copiar texto</button>
          </div>
        </div>
        <div class="edit-hint" style="display:none">✏️ Modo edición: haz clic en el texto y cámbialo. Pulsa <b>Guardar</b> cuando termines. No gasta IA.</div>
        <div class="doc-preview"><iframe class="doc-frame" title="Vista previa"></iframe></div>
        <div class="refine-bar">
          <input class="refine-input" placeholder="Pídele un cambio a la IA (ej: que quepa en una sola hoja, más formal, resalta Python)">
          <button class="ghost" data-act="refine">✨ Aplicar cambio</button>
        </div>
      </div>` : ''}`;
    list.appendChild(el);
  });
}

$('#matches-list').addEventListener('click', async e => {
  const btn = e.target.closest('button'); if (!btn) return;
  const matchEl = e.target.closest('.match');
  const m = state.matches.find(x => x.id === matchEl.dataset.id); if (!m) return;

  if (btn.dataset.doc) {
    matchEl.querySelectorAll('.doc-tabs button').forEach(b => b.classList.remove('active'));
    btn.classList.add('active'); matchEl.dataset.doc = btn.dataset.doc; showDoc(matchEl, m); return;
  }
  if (btn.dataset.langSet) {
    matchEl.querySelectorAll('.lang-selector button').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    matchEl.dataset.lang = btn.dataset.langSet;
    showDoc(matchEl, m);
    return;
  }
  const kind = matchEl.dataset.doc || 'resume';
  const lang = matchEl.dataset.lang || 'en';
  switch (btn.dataset.act) {
    case 'toggle':
      matchEl.classList.toggle('open');
      if (matchEl.classList.contains('open')) showDoc(matchEl, m);
      break;
    case 'generate': generateDocs(m); break;
    case 'regenerate':
      if (confirm('¿Regenerar los documentos con tu CV y perfil actuales? Se reemplazarán los actuales.')) generateDocs(m);
      break;
    case 'delete':
      if (confirm('¿Eliminar este match y sus documentos de la base de datos?')) {
        await jpost('/api/job/delete', { job_id: m.id }).catch(() => {});
        state.matches = state.matches.filter(x => x.id !== m.id); renderMatches();
      }
      break;
    case 'pdf': {
      const label = (DOC_TABS.find(t => t[0] === kind) || [,''])[1];
      const fname = `${label}_${lang.toUpperCase()}_${(m.job.company||'').replace(/\W+/g,'_')}`;
      const ov = m.docs && m.docs.overrides && m.docs.overrides[`${kind}_${lang}`];
      if (!ov) {
        // PDF generado con LaTeX en el servidor
        window.open(`/api/doc/pdf?job_id=${encodeURIComponent(m.id)}&kind=${kind}&lang=${lang}&name=${encodeURIComponent(fname)}`, '_blank');
        break;
      }
      downloadDocPDF(kind, m.docs, fname, lang);
      toast('🖨 En el diálogo elige "Guardar como PDF".');
      break;
    }
    case 'copy': {
      const frame = matchEl.querySelector('.doc-frame');
      const txt = frame && frame.contentDocument ? frame.contentDocument.body.innerText : '';
      navigator.clipboard.writeText(txt); toast('📋 Texto copiado.');
      break;
    }
    case 'edit': {
      const frame = matchEl.querySelector('.doc-frame');
      const page = frame && frame.contentDocument && frame.contentDocument.querySelector('.page');
      if (!page) { toast('⚠️ Abre la vista previa primero.'); return; }
      page.contentEditable = 'true';
      page.style.outline = '2px dashed rgba(124,92,255,.5)';
      page.focus();
      setEditMode(matchEl, true);
      break;
    }
    case 'cancel-edit': {
      setEditMode(matchEl, false);
      showDoc(matchEl, m);  // descarta y re-renderiza desde lo guardado
      break;
    }
    case 'save-edit': {
      const frame = matchEl.querySelector('.doc-frame');
      const page = frame && frame.contentDocument && frame.contentDocument.querySelector('.page');
      if (!page) { setEditMode(matchEl, false); return; }
      const html = page.innerHTML;
      btn.disabled = true; btn.textContent = '⏳ Guardando...';
      try {
        await jpost('/api/doc/save-override', { job_id: m.id, kind, lang, html });
        m.docs.overrides = m.docs.overrides || {};
        m.docs.overrides[`${kind}_${lang}`] = html;
        setEditMode(matchEl, false);
        showDoc(matchEl, m);
        toast('✅ Cambios guardados (sin IA).');
      } catch (e) {
        toast('❌ ' + e.message, 5000);
      } finally { btn.disabled = false; btn.textContent = '💾 Guardar'; }
      break;
    }
    case 'refine': {
      const input = matchEl.querySelector('.refine-input');
      const instruction = input.value.trim();
      if (!instruction) { toast('⚠️ Escribe qué cambio quieres.'); return; }
      btn.disabled = true; btn.textContent = '⏳ Aplicando...';
      try {
        const res = await jpost('/api/refine', { job_id: m.id, doc_type: kind, lang, instruction });
        if (m.docs.en && m.docs.es) {
          m.docs[lang][kind] = res.doc;
          if (res.profile) m.docs[lang].profile = res.profile;
        } else {
          m.docs[kind] = res.doc;
          if (res.profile) m.docs.profile = res.profile;
        }
        // la IA rehízo el doc → descartamos la edición manual previa
        if (m.docs.overrides) delete m.docs.overrides[`${kind}_${lang}`];
        input.value = '';

        if (kind === 'resume' || kind === 'cv') {
          let check = await checkDocPages(kind, m.docs, lang);
          let attempts = 0;
          const maxAttempts = 2;
          while (check.isMoreThanOnePage && attempts < maxAttempts) {
            attempts++;
            toast(`📏 El ${kind === 'cv' ? 'CV' : 'Resume'} (${lang.toUpperCase()}) supera 1 página (${Math.round(check.height)}px > ${Math.round(check.avail)}px). Condensando con IA (Intento ${attempts}/${maxAttempts})...`);
            const res = await jpost('/api/refine', {
              job_id: m.id,
              doc_type: kind,
              lang: lang,
              instruction: 'El documento supera 1 página. Por favor, reduce y condensa el texto de las experiencias (acorta las viñetas, simplifica el resumen profesional) para que quepa en una sola página. IMPORTANTE: Devuelve el JSON completo y válido, con todos los campos rellenados. NO uses comentarios como "// ...", puntos suspensivos ni omitas datos.'
            });
            if (m.docs.en && m.docs.es) {
              m.docs[lang][kind] = res.doc;
              if (res.profile) m.docs[lang].profile = res.profile;
            } else {
              m.docs[kind] = res.doc;
              if (res.profile) m.docs.profile = res.profile;
            }
            check = await checkDocPages(kind, m.docs, lang);
          }
        }

        showDoc(matchEl, m);
        toast('✅ Documento actualizado y optimizado.');
      } catch (e) { toast('❌ ' + e.message, 5000); }
      finally { btn.disabled = false; btn.textContent = '✨ Aplicar cambio'; }
      break;
    }
  }
});
function showDoc(matchEl, m) {
  const frame = matchEl.querySelector('.doc-frame');
  const lang = matchEl.dataset.lang || 'en';
  if (frame && m.docs) previewDoc(frame, matchEl.dataset.doc || 'resume', m.docs, lang);
}

/* activa/desactiva el modo de edición manual del documento */
function setEditMode(matchEl, on) {
  matchEl.classList.toggle('editing', on);
  matchEl.querySelectorAll('.edit-only').forEach(b => b.style.display = on ? '' : 'none');
  const editBtn = matchEl.querySelector('[data-act="edit"]');
  if (editBtn) editBtn.style.display = on ? 'none' : '';
  const hint = matchEl.querySelector('.edit-hint');
  if (hint) hint.style.display = on ? 'block' : 'none';
  // mientras se edita, bloquea cambiar de pestaña/idioma/IA para no perder el texto
  matchEl.querySelectorAll('.doc-tabs button, .lang-selector button, [data-act="refine"], [data-act="pdf"]')
    .forEach(b => b.disabled = on);
}

/* ===================== aplicar automáticamente (Easy Apply) ===================== */
const APPLY_STATUS_LABELS = {
  applied: ['✅ Enviada', 'ok'],
  needs_review: ['⚠️ Para revisar', 'warn'],
  prepared: ['📄 Preparada', 'muted'],
  skipped: ['⏭️ Saltada', 'muted'],
  error: ['❌ Error', 'bad'],
};

/* texto plano del cover letter (para pegar en un textarea de la solicitud) */
function coverLetterText(docs, lang) {
  const target = (docs && (docs.en || docs.es)) ? (docs[lang] || docs.en || docs.es || {}) : (docs || {});
  const cl = target.cover_letter || {};
  const profile = target.profile || {};
  const parts = [];
  if (cl.recipient) parts.push(cl.recipient);
  (cl.paragraphs || []).forEach(p => { if (p && p.trim()) parts.push(p.trim()); });
  parts.push(cl.closing || (lang === 'es' ? 'Atentamente,' : 'Sincerely,'));
  if (profile.name) parts.push(profile.name);
  return parts.join('\n\n').trim();
}

/* idioma de los documentos: lo decide el servidor (país de la empresa + IA si dudoso).
   Si el servidor falla, cae a una heurística local que prioriza el país. */
async function detectLang(job, jobId) {
  try {
    const { lang } = await jpost('/api/apply/detect-lang', { job_id: jobId });
    if (lang === 'en' || lang === 'es') return lang;
  } catch (e) {}
  return langForJob(job);
}

function langForJob(job) {
  const loc = (job.location || '').toLowerCase();
  const esCountries = ['méxico', 'mexico', 'españa', 'spain', 'argentina', 'colombia',
    'chile', 'perú', 'peru', 'venezuela', 'ecuador', 'guatemala', 'uruguay', 'bolivia'];
  const enCountries = ['united states', 'usa', ', us', 'united kingdom', ', uk', 'canada', 'australia'];
  if (esCountries.some(c => loc.includes(c))) return 'es';
  if (enCountries.some(c => loc.includes(c))) return 'en';
  // por el texto de la oferta como último recurso
  const d = (job.description || '').toLowerCase();
  const es = (d.match(/[áéíóúñ¿¡]/g) || []).length + ['experiencia', 'requisitos', 'conocimientos', 'habilidades'].filter(w => d.includes(w)).length * 2;
  const en = ['the ', 'and ', 'experience', 'requirements', 'skills', 'we are'].filter(w => d.includes(w)).length * 2;
  return es > en ? 'es' : 'en';
}

function renderQuota(q) {
  const el = $('#apply-quota');
  if (!el || !q) return;
  state.quota = q;
  const pct = Math.min(100, Math.round((q.used / q.limit) * 100));
  const full = q.remaining <= 0;
  const hrs = q.reset_in ? (q.reset_in / 3600).toFixed(1) : null;
  el.className = 'apply-quota' + (full ? ' full' : (q.remaining <= 8 ? ' low' : ''));
  el.innerHTML = `
    <div class="aq-top">
      <span class="aq-label">📊 Solicitudes hoy (límite de LinkedIn)</span>
      <span class="aq-count"><b>${q.used}</b> / ${q.limit}</span>
    </div>
    <div class="aq-bar"><div class="aq-fill" style="width:${pct}%"></div></div>
    <div class="aq-note">${full
      ? `⛔ Llegaste al tope diario. Se libera un cupo en ~${hrs} h.`
      : `Quedan <b>${q.remaining}</b> hoy${hrs ? ` · se renueva en ~${hrs} h` : ''}.`}</div>`;
  // ajusta el botón de aplicar al cupo restante
  const n = selectedApplyIds ? selectedApplyIds().length : 0;
  if (full) $('#btn-apply-start').disabled = true;
  else if (n > q.remaining) $('#apply-status').textContent =
    `⚠️ Seleccionaste ${n} pero solo quedan ${q.remaining} hoy; el resto se saltará.`;
}

async function loadQuota() {
  try { renderQuota(await api('/api/apply/quota')); } catch (e) {}
}

async function loadApplications() {
  await reloadMatches();
  let apps = [];
  try {
    const r = await api('/api/applications');
    apps = r.applications || [];
  } catch (e) {}
  state.applyStatusMap = {};
  apps.forEach(a => { state.applyStatusMap[a.job_id] = a.status; });
  renderApplySelector();
  renderApplications(apps);
  loadQuota();
  // si hay una tanda en curso, reanuda el sondeo
  try {
    const st = await api('/api/apply/status');
    if (st && !st.done) pollApply(st.task_id);
  } catch (e) {}
}

/* checklist de empleos a los que se puede aplicar (con documentos generados) */
function renderApplySelector() {
  const list = $('#apply-select-list');
  const statusMap = state.applyStatusMap || {};
  const ready = state.matches.filter(m => m.status === 'done' && m.docs);
  if (!ready.length) {
    list.innerHTML = '<div class="apply-select-empty">No hay matches con documentos. Genera los documentos en <b>Matches</b> primero.</div>';
    updateSelCount(); return;
  }
  list.innerHTML = ready.map(m => {
    const st = statusMap[m.id];
    const [label, cls] = st ? (APPLY_STATUS_LABELS[st] || [st, 'muted']) : ['', ''];
    const applied = st === 'applied';
    // por defecto se marcan los que aún NO se han aplicado
    const checked = !st ? 'checked' : '';
    return `<label class="apply-check-row ${applied ? 'is-applied' : ''}">
      <input type="checkbox" class="apply-check" value="${esc(m.id)}" ${checked}>
      <span class="apply-check-main">
        <span class="apply-check-title jobpop-trigger" data-jobpop="${esc(m.id)}">${esc(m.job.title)}</span>
        <span class="apply-check-co">${esc(m.job.company || '')}${m.job.location ? ' · ' + esc(m.job.location) : ''}</span>
      </span>
      ${st ? `<span class="apply-badge ${cls}">${label}</span>` : ''}
    </label>`;
  }).join('');
  updateSelCount();
}

function selectedApplyIds() {
  return [...document.querySelectorAll('#apply-select-list .apply-check:checked')].map(c => c.value);
}
function updateSelCount() {
  const n = selectedApplyIds().length;
  $('#apply-sel-count').textContent = n;
  const full = state.quota && state.quota.remaining <= 0;
  $('#btn-apply-start').disabled = n === 0 || full;
  if (state.quota) renderQuota(state.quota);  // actualiza el aviso "quedan Y hoy"
}

function renderApplications(apps) {
  const wrap = $('#apply-results');
  $('#apply-empty').style.display = 'none';
  if (!apps.length) { wrap.innerHTML = ''; return; }
  wrap.innerHTML = `<div class="box"><h2 style="margin:0 0 12px">📋 Historial de solicitudes</h2>${
    apps.map(a => {
      const [label, cls] = APPLY_STATUS_LABELS[a.status] || [a.status, 'muted'];
      const job = (state.matches.find(m => m.id === a.job_id) || {}).job || {};
      const reason = a.detail?.review_reason || a.detail?.error || '';
      const link = job.link || '';
      return `<div class="apply-row ${cls}">
        <div class="apply-row-main">
          <div class="apply-row-title jobpop-trigger" data-jobpop="${esc(a.job_id)}">${esc(job.title || a.job_id)}</div>
          <div class="apply-row-co">${esc(job.company || '')}</div>
        </div>
        <div class="apply-row-status">
          <span class="apply-badge ${cls}">${label}</span>
          ${reason ? `<div class="apply-reason">${esc(reason)}</div>` : ''}
          ${a.status === 'needs_review' && link ? `<a class="apply-finish" href="${esc(link)}" target="_blank">Revisar y enviar en LinkedIn ↗</a>` : ''}
        </div>
      </div>`;
    }).join('')}</div>`;
}

async function startApply() {
  // aplicar SOLO a los empleos marcados en el checklist
  const ids = selectedApplyIds();
  const selected = ids.map(id => state.matches.find(m => m.id === id))
    .filter(m => m && m.status === 'done' && m.docs);
  if (!selected.length) {
    toast('⚠️ Marca al menos un empleo en la lista para aplicar.');
    return;
  }
  const mode = $('#apply-mode').value;
  const btn = $('#btn-apply-start');
  btn.disabled = true;
  const setStatus = t => { $('#apply-status').textContent = t; };

  // 2) preparar el PDF del currículum de cada empleo (render en el navegador → PDF en servidor)
  setStatus(`Preparando currículums (0/${selected.length})...`);
  const jobIds = [];
  for (let i = 0; i < selected.length; i++) {
    const m = selected[i];
    setStatus(`Detectando idioma de ${m.job.company}... (${i + 1}/${selected.length})`);
    const lang = await detectLang(m.job, m.id);
    const langName = lang === 'es' ? '🇪🇸 español' : '🇺🇸 inglés';
    const resumeHtml = buildStandaloneDoc(renderDocInner('resume', m.docs, lang), true, true, false);
    const coverHtml = buildStandaloneDoc(renderDocInner('cover_letter', m.docs, lang), true, false, false);
    const coverText = coverLetterText(m.docs, lang);
    try {
      await jpost('/api/apply/prepare', {
        job_id: m.id, lang, resume_html: resumeHtml,
        cover_html: coverHtml, cover_text: coverText,
      });
      jobIds.push(m.id);
    } catch (e) {
      toast(`❌ No pude preparar ${m.job.company}: ${e.message}`);
    }
    setStatus(`Preparado ${m.job.company} en ${langName} (${i + 1}/${selected.length})`);
  }
  if (!jobIds.length) { btn.disabled = false; setStatus('No se pudo preparar ningún currículum.'); return; }

  // 3) lanzar la tanda de solicitudes
  try {
    const { task_id } = await jpost('/api/apply/start', { job_ids: jobIds, submit_mode: mode });
    setStatus('');
    $('#apply-progress').style.display = 'block';
    pollApply(task_id);
  } catch (e) {
    setStatus('❌ ' + e.message); btn.disabled = false;
  }
}

async function pollApply(taskId) {
  const fill = $('#apply-fill'), phase = $('#apply-phase'), detail = $('#apply-detail');
  $('#apply-progress').style.display = 'block';
  try {
    const st = await api('/api/apply/status');
    const pct = st.total ? Math.round((st.progress / st.total) * 100) : 0;
    fill.style.width = pct + '%';
    phase.textContent = st.done
      ? (st.error ? '❌ ' + st.error : '✅ Tanda terminada')
      : `Aplicando ${st.progress}/${st.total}...`;
    detail.textContent = st.current_job || '';
    if (st.quota) renderQuota(st.quota);
    if (st.results && st.results.length) renderApplyLiveResults(st.results);
    if (!st.done) { setTimeout(() => pollApply(taskId), 1500); return; }
    // terminado
    $('#btn-apply-start').disabled = false;
    const applied = st.results.filter(r => r.status === 'applied').length;
    const review = st.results.filter(r => r.status === 'needs_review').length;
    toast(`📮 Tanda terminada: ${applied} enviadas, ${review} para revisar.`);
    await loadApplications();
  } catch (e) {
    phase.textContent = '❌ ' + e.message;
    $('#btn-apply-start').disabled = false;
  }
}

function renderApplyLiveResults(results) {
  const wrap = $('#apply-results');
  wrap.innerHTML = `<div class="box"><h2 style="margin:0 0 12px">⏳ En progreso</h2>${
    results.map(r => {
      const [label, cls] = APPLY_STATUS_LABELS[r.status] || [r.status, 'muted'];
      return `<div class="apply-row ${cls}">
        <div class="apply-row-main">
          <div class="apply-row-title">${esc(r.title || r.job_id)}</div>
          <div class="apply-row-co">${esc(r.company || '')}</div>
        </div>
        <div class="apply-row-status">
          <span class="apply-badge ${cls}">${label}</span>
          ${(r.review_reason || r.error) ? `<div class="apply-reason">${esc(r.review_reason || r.error)}</div>` : ''}
        </div>
      </div>`;
    }).join('')}</div>`;
}

$('#btn-apply-start').addEventListener('click', startApply);
$('#btn-apply-refresh').addEventListener('click', loadApplications);

// mantener el contador al marcar/desmarcar
$('#apply-select-list').addEventListener('change', e => {
  if (e.target.classList.contains('apply-check')) updateSelCount();
});

// botones rápidos de selección (ninguno / todos los no aplicados / N aleatorios)
$('.apply-select-toolbar').addEventListener('click', e => {
  const btn = e.target.closest('button[data-sel]'); if (!btn) return;
  const boxes = [...document.querySelectorAll('#apply-select-list .apply-check')];
  const statusMap = state.applyStatusMap || {};
  const pending = boxes.filter(b => !statusMap[b.value]);   // aún no aplicados
  if (btn.dataset.sel === 'none') {
    boxes.forEach(b => { b.checked = false; });
  } else if (btn.dataset.sel === 'pending') {
    boxes.forEach(b => { b.checked = !statusMap[b.value]; });
  } else if (btn.dataset.sel === 'random') {
    boxes.forEach(b => { b.checked = false; });
    const n = Math.max(1, parseInt($('#apply-random-n').value, 10) || 1);
    const pool = pending.slice();
    for (let i = pool.length - 1; i > 0; i--) {  // Fisher-Yates
      const j = Math.floor(Math.random() * (i + 1));
      [pool[i], pool[j]] = [pool[j], pool[i]];
    }
    pool.slice(0, n).forEach(b => { b.checked = true; });
    if (pool.length < n) toast(`Solo hay ${pool.length} empleos sin aplicar disponibles.`);
  }
  updateSelCount();
});

/* ===================== base de datos (tabla) ===================== */
let dbJobs = [];
const ST_LABEL = { new: ['Nuevo', 'st-new'], liked: ['Me gusta 💜', 'st-liked'], passed: ['Descartado', 'st-passed'] };
async function loadJobsTable() {
  try {
    const { jobs, stats } = await api('/api/jobs');
    dbJobs = jobs || [];
    dbJobs.forEach(j => indexJob(j, { has_docs: j.has_docs }));
    $('#db-stats').textContent =
      `${stats.total || 0} empleos · ${stats.nuevos || 0} nuevos · ${stats.gustados || 0} con me gusta · ${stats.descartados || 0} descartados`;
    renderJobsTable();
  } catch (e) { $('#db-stats').textContent = '❌ ' + e.message; }
}
function renderJobsTable() {
  const status = $('#db-filter').value;
  const txt = ($('#db-text').value || '').trim().toLowerCase();
  const loc = ($('#db-loc').value || '').trim().toLowerCase();
  const rows = dbJobs.filter(j => {
    if (status && j.status !== status) return false;
    if (loc && !(j.location || '').toLowerCase().includes(loc)) return false;
    if (txt) {
      const hay = `${j.title||''} ${j.company||''} ${j.description||''}`.toLowerCase();
      if (!hay.includes(txt)) return false;
    }
    return true;
  });
  $('#db-empty').style.display = rows.length ? 'none' : 'block';
  $('#db-table').style.display = rows.length ? '' : 'none';
  $('#db-tbody').innerHTML = rows.map(j => {
    const [lbl, cls] = ST_LABEL[j.status] || ['—', ''];
    const docBtn = j.has_docs
      ? `<button class="t-doc view" data-act="view">📄 Ver</button>`
      : `<button class="t-doc gen" data-act="gen">✨ Generar</button>`;
    return `<tr data-id="${esc(j.id)}">
      <td class="t-title jobpop-trigger" data-jobpop="${esc(j.id)}">${esc(j.title)}</td>
      <td>${esc(j.company)}</td>
      <td>${esc(j.location || '—')}</td>
      <td><span class="st-badge ${cls}">${lbl}</span></td>
      <td>${docBtn}</td>
      <td>${j.link ? `<a class="t-link" href="${esc(j.link)}" target="_blank">Aplicar ↗</a>` : '—'}</td>
      <td><button class="t-del" data-act="del" title="Eliminar de la base de datos">🗑</button></td>
    </tr>`;
  }).join('');
}
$('#db-filter').addEventListener('change', renderJobsTable);
$('#db-text').addEventListener('input', renderJobsTable);
$('#db-loc').addEventListener('input', renderJobsTable);
$('#db-refresh').addEventListener('click', loadJobsTable);
$('#db-tbody').addEventListener('click', async e => {
  const btn = e.target.closest('button'); if (!btn) return;
  const id = e.target.closest('tr').dataset.id;
  const act = btn.dataset.act;

  if (act === 'del') {
    if (!confirm('¿Eliminar este empleo de la base de datos?')) return;
    await jpost('/api/job/delete', { job_id: id }).catch(() => {});
    dbJobs = dbJobs.filter(j => j.id !== id); renderJobsTable();
    reloadDeck(); reloadMatches();
    return;
  }
  if (act === 'gen') {
    // generar documentos directamente desde la BD (sin swipear)
    btn.disabled = true; btn.textContent = '⏳ Generando...';
    try {
      const job = dbJobs.find(x => x.id === id);
      const match = { id, job, docs: null, status: 'generating', error: '' };
      state.matches = state.matches.filter(m => m.id !== id);
      state.matches.unshift(match);
      renderMatches();
      await generateDocs(match);
      await loadJobsTable(); reloadDeck();
    } catch (err) {
      toast('❌ ' + err.message, 5000);
    } finally {
      btn.disabled = false;
      btn.textContent = '✨ Generar';
    }
    return;
  }
  if (act === 'view') { openMatch(id); }
});

// abre un empleo concreto en la pestaña Matches y despliega sus documentos
async function openMatch(id) {
  goPanel('matches');
  await reloadMatches();
  const el = $(`#matches-list .match[data-id="${id}"]`);
  if (el) {
    el.classList.add('open');
    showDoc(el, state.matches.find(m => m.id === id));
    el.scrollIntoView({ behavior: 'smooth', block: 'center' });
  }
}

/* ===================== asistente con IA ===================== */
let chatLoaded = false;
async function loadChat() {
  try {
    const { messages } = await api('/api/chat');
    renderChat(messages || []);
    // onboarding: si no hay conversación y hay CV, el asistente saluda y pregunta
    if (!chatLoaded && !(messages || []).length && ($('#base-cv').value || '').trim()) {
      chatLoaded = true;
      sendChat('__INICIO__', true);
    }
    chatLoaded = true;
  } catch (e) {}
}
function renderChat(messages) {
  const box = $('#chat-messages');
  if (!messages.length) {
    box.innerHTML = `<div class="chat-empty">🤖 Soy tu asistente de búsqueda de empleo.<br>
      Dime qué buscas y yo lo busco en LinkedIn. Ej: <i>"busca empleos de mi área en mi ciudad"</i>,
      <i>"lo mismo pero remoto"</i>, <i>"¿cuáles guardados me recomiendas?"</i></div>`;
    return;
  }
  box.innerHTML = messages.map(m =>
    `<div class="msg ${m.role}"><div class="bubble">${m.role === 'assistant' ? mdToHtml(m.content) : esc(m.content)}</div></div>`
  ).join('');
  box.scrollTop = box.scrollHeight;
}
function appendChat(role, contentHtml) {
  const box = $('#chat-messages');
  if (box.querySelector('.chat-empty')) box.innerHTML = '';
  const div = document.createElement('div');
  div.className = `msg ${role}`;
  div.innerHTML = `<div class="bubble">${contentHtml}</div>`;
  box.appendChild(div); box.scrollTop = box.scrollHeight;
  return div;
}
async function sendChat(forced, hidden) {
  const input = $('#chat-input');
  const q = forced || input.value.trim();
  if (!q) return;
  if (!hidden) { input.value = ''; input.style.height = 'auto'; appendChat('user', esc(q)); }
  else if ($('#chat-messages').querySelector('.chat-empty')) $('#chat-messages').innerHTML = '';
  const typing = appendChat('assistant', '<span class="spinner"></span> Pensando...');
  $('#chat-send').disabled = true;
  try {
    const { answer, action } = await jpost('/api/chat', { question: q });
    typing.querySelector('.bubble').innerHTML = mdToHtml(answer);
    $('#chat-messages').scrollTop = $('#chat-messages').scrollHeight;
    if (action === 'search') { reloadDeck(); notify('🔎 Búsqueda lista', 'Nuevos empleos en el mazo.'); }
  } catch (e) {
    typing.querySelector('.bubble').innerHTML = `<span style="color:var(--nope)">❌ ${esc(e.message)}</span>`;
  } finally { $('#chat-send').disabled = false; if (!hidden) input.focus(); }
}
$('#chat-send').addEventListener('click', () => sendChat());
$('#chat-input').addEventListener('keydown', e => {
  if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendChat(); }
});
$('#chat-input').addEventListener('input', e => {
  e.target.style.height = 'auto'; e.target.style.height = Math.min(e.target.scrollHeight, 140) + 'px';
});
$('#chat-clear').addEventListener('click', async () => {
  if (!confirm('¿Borrar el historial del chat?')) return;
  await jpost('/api/chat/clear').catch(() => {});
  renderChat([]);
});
$$('.chat-suggestion').forEach(b => b.addEventListener('click', () => {
  $('#chat-input').value = b.textContent; sendChat();
}));

/* ===================== notificaciones ===================== */
$('#btn-notif').addEventListener('click', async () => {
  const st = $('#notif-status');
  if (!('Notification' in window)) { st.textContent = 'No soportado en este navegador.'; return; }
  const perm = await Notification.requestPermission();
  st.textContent = perm === 'granted' ? '✅ Activadas.' : '⚠️ Permiso denegado.';
});
function notify(title, body) {
  if ('Notification' in window && Notification.permission === 'granted') {
    try { new Notification(title, { body }); } catch (e) {}
  }
}

/* ===================== markdown mínimo ===================== */
function mdToHtml(md) {
  if (!md) return '';
  const lines = esc(md).split('\n'); let html = '', inList = false;
  const inline = s => s
    .replace(/\*\*(.+?)\*\*/g, '<b>$1</b>')
    .replace(/(^|[^*])\*([^*\n]+)\*/g, '$1<i>$2</i>')
    .replace(/\[(.+?)\]\((.+?)\)/g, '<a href="$2" target="_blank">$1</a>');
  for (const raw of lines) {
    const line = raw.trimEnd();
    const li = line.match(/^\s*[-*•]\s+(.*)/);
    if (li) { if (!inList) { html += '<ul>'; inList = true; } html += `<li>${inline(li[1])}</li>`; continue; }
    if (inList) { html += '</ul>'; inList = false; }
    if (/^###\s/.test(line)) html += `<h3>${inline(line.slice(4))}</h3>`;
    else if (/^##\s/.test(line)) html += `<h2>${inline(line.slice(3))}</h2>`;
    else if (/^#\s/.test(line)) html += `<h1>${inline(line.slice(2))}</h1>`;
    else if (/^(---|___|\*\*\*)\s*$/.test(line)) html += '<hr>';
    else if (line === '') html += '';
    else html += `<p>${inline(line)}</p>`;
  }
  if (inList) html += '</ul>';
  return html;
}

/* ===================== AutoJob ===================== */
let _autojobPolling = null;

$('#btn-autojob-start').addEventListener('click', startAutoJob);
$('#btn-autojob-last').addEventListener('click', loadLastAutoJob);
$('#btn-autojob-submit-answers').addEventListener('click', submitAutoJobAnswers);

async function startAutoJob() {
  const btn = $('#btn-autojob-start');
  btn.disabled = true; btn.textContent = '⏳ Iniciando...';
  $('#autojob-empty').style.display = 'none';
  $('#autojob-results').innerHTML = '';
  $('#autojob-strategy-box').style.display = 'none';
  $('#autojob-questionnaire').style.display = 'none';
  const prog = $('#autojob-progress');
  prog.style.display = 'block';
  $('#autojob-fill').style.width = '0%';
  $('#autojob-phase').textContent = 'Preparando...';
  $('#autojob-detail').textContent = '';
  try {
    const { task_id, total } = await jpost('/api/autojob/start');
    btn.dataset.taskId = task_id;
    toast(`🚀 AutoJob iniciado: analizando ${total} empleos...`);
    pollAutoJob(task_id);
  } catch (e) {
    toast('❌ ' + e.message, 5000);
    prog.style.display = 'none';
    btn.disabled = false; btn.textContent = '🚀 Iniciar AutoJob';
    $('#autojob-empty').style.display = 'block';
  }
}

function pollAutoJob(taskId) {
  if (_autojobPolling) clearInterval(_autojobPolling);
  _autojobPolling = setInterval(async () => {
    try {
      const st = await api(`/api/autojob/status?task_id=${taskId}`);
      const fill = $('#autojob-fill');
      const phase = $('#autojob-phase');
      const detail = $('#autojob-detail');
      
      const pct = st.total > 0 ? Math.round((st.progress / st.total) * 100) : 0;
      fill.style.width = pct + '%';
      
      const phaseLabels = {
        evaluating: '🔍 Evaluando empleos...',
        waiting_input: '🧙‍♂️ Esperando tus respuestas...',
        comparing: '⚖️ Comparando candidatos...',
        finalizing: '🏆 Generando reporte final...',
        done: '✅ ¡Análisis completo!',
        error: '❌ Error en el análisis',
      };
      
      phase.textContent = phaseLabels[st.phase] || st.phase;
      
      let detailHtml = st.current_job || `${st.progress}/${st.total}`;
      if (st.discarded_seniority > 0 || st.discarded_location > 0) {
        detailHtml += `<br><small style="color:var(--nope)">Pre-filtros: descartados ${st.discarded_seniority} por experiencia, ${st.discarded_location} por ubicación.</small>`;
      }
      detail.innerHTML = detailHtml;
      
      if (st.phase === 'waiting_input') {
        clearInterval(_autojobPolling);
        _autojobPolling = null;
        $('#btn-autojob-start').disabled = false;
        $('#btn-autojob-start').textContent = '🚀 Iniciar AutoJob';
        $('#autojob-progress').style.display = 'none';
        
        toast('🧙‍♂️ Cuestionario listo. Responde para afinar el análisis.', 4000);
        renderQuestionnaire(st.questions || []);
        $('#autojob-questionnaire').style.display = 'block';
        return;
      }
      
      if (st.done) {
        clearInterval(_autojobPolling);
        _autojobPolling = null;
        fill.style.width = '100%';
        const btn = $('#btn-autojob-start');
        btn.disabled = false; btn.textContent = '🚀 Iniciar AutoJob';
        if (st.error) {
          toast('❌ ' + st.error, 5000);
          $('#autojob-progress').style.display = 'none';
          $('#autojob-empty').style.display = 'block';
        } else {
          toast('🏆 ¡AutoJob terminó! Revisa tu Top 5.', 4000);
          setTimeout(() => { $('#autojob-progress').style.display = 'none'; }, 2000);
          renderAutoJobResults(st.result);
        }
      }
    } catch (e) {
      // ignore transient errors
    }
  }, 2500);
}

function renderQuestionnaire(questions) {
  const list = $('#autojob-questions-list');
  list.innerHTML = '';
  questions.forEach((q, idx) => {
    const div = document.createElement('div');
    div.className = 'aj-question-item';
    div.dataset.question = q;
    div.innerHTML = `
      <div class="aj-question-text">${esc(q)}</div>
      <div class="aj-quick-answers">
        <button class="aj-quick-btn" type="button">Sí</button>
        <button class="aj-quick-btn" type="button">No</button>
        <button class="aj-quick-btn" type="button">Básico / Intermedio</button>
        <button class="aj-quick-btn" type="button">Avanzado</button>
      </div>
      <textarea class="aj-question-input" rows="2" placeholder="Escribe tu respuesta aquí o usa las opciones rápidas..."></textarea>
    `;
    
    div.querySelectorAll('.aj-quick-btn').forEach(btn => {
      btn.addEventListener('click', () => {
        div.querySelector('.aj-question-input').value = btn.textContent;
      });
    });
    
    list.appendChild(div);
  });
}

async function submitAutoJobAnswers() {
  const btn = $('#btn-autojob-submit-answers');
  btn.disabled = true; btn.textContent = '⏳ Enviando...';
  
  const answers = {};
  const taskId = $('#btn-autojob-start').dataset.taskId;
  
  $$('.aj-question-item').forEach(item => {
    const q = item.dataset.question;
    const ans = item.querySelector('.aj-question-input').value.trim();
    answers[q] = ans;
  });
  
  try {
    await jpost('/api/autojob/answer', { task_id: taskId, answers });
    $('#autojob-questionnaire').style.display = 'none';
    
    const prog = $('#autojob-progress');
    prog.style.display = 'block';
    $('#autojob-phase').textContent = '⚖️ Comparando candidatos...';
    $('#autojob-detail').textContent = 'Analizando respuestas del cuestionario...';
    
    toast('🚀 Respuestas enviadas. Continuando análisis...');
    pollAutoJob(taskId);
  } catch (e) {
    toast('❌ ' + e.message, 5000);
  } finally {
    btn.disabled = false; btn.textContent = 'Continuar Análisis ➔';
  }
}

async function loadLastAutoJob() {
  try {
    const data = await api('/api/autojob/result');
    if (data && data.top5) {
      renderAutoJobResults(data);
      $('#autojob-empty').style.display = 'none';
    } else {
      toast('No hay resultados previos de AutoJob.');
    }
  } catch (e) { toast('❌ ' + e.message); }
}

function renderAutoJobResults(result) {
  if (!result || !result.top5 || !result.top5.length) {
    $('#autojob-results').innerHTML = '';
    $('#autojob-strategy-box').style.display = 'none';
    $('#autojob-empty').style.display = 'block';
    return;
  }
  $('#autojob-empty').style.display = 'none';
  
  if (result.overall_strategy) {
    $('#autojob-strategy').textContent = result.overall_strategy;
    const recs = $('#autojob-recommendations');
    recs.innerHTML = (result.recommendations || []).map(r => `<li>${esc(r)}</li>`).join('');
    $('#autojob-strategy-box').style.display = 'block';
  } else {
    $('#autojob-strategy-box').style.display = 'none';
  }

  let html = '';
  result.top5.forEach((item, i) => {
    const isWinner = i === 0;
    const tn1Class = item.tn1_eligible === true ? 'eligible'
                   : item.tn1_eligible === false ? 'ineligible' : 'maybe';
    const tn1Text = item.tn1_eligible === true ? '✅ Elegible TN-1'
                  : item.tn1_eligible === false ? '❌ No elegible TN-1' : '⚠️ TN-1 incierto';
    const score = item.score || 0;
    html += `
    <div class="autojob-card${isWinner ? ' winner' : ''}">
      <div class="rank-badge">${isWinner ? '🏆 #1 MEJOR' : '#' + (i + 1)}</div>
      <div class="aj-head">
        <div>
          <div class="aj-title">${esc(item.title || '')}</div>
          <div class="aj-company">${esc(item.company || '')}</div>
          <div class="aj-location">📍 ${esc(item.location || 'Sin ubicación')}</div>
        </div>
        <div class="score-ring" style="--score:${score}">
          <span>${score}</span>
        </div>
      </div>
      <div class="aj-badges">
        <span class="tn1-badge ${tn1Class}">${tn1Text}</span>
        ${item.seniority ? `<span class="tag emp">${esc(item.seniority)}</span>` : ''}
        ${item.employment_type ? `<span class="tag emp">${esc(item.employment_type)}</span>` : ''}
      </div>
      <div class="aj-section">
        <div class="aj-section-title">Análisis de compatibilidad</div>
        <div class="aj-summary">${esc(item.fit_summary || '')}</div>
      </div>
      <div class="aj-section">
        <div class="aj-section-title" style="color:#e9d5ff">Comparación del Asistente</div>
        <div class="aj-summary" style="font-style:italic;">"${esc(item.comparison_reason || '')}"</div>
      </div>
      <div class="aj-pros-cons">
        <div class="aj-pros">
          <div class="aj-section-title">A favor</div>
          <ul>${(item.pros || []).map(p => `<li>${esc(p)}</li>`).join('')}</ul>
        </div>
        <div class="aj-cons">
          <div class="aj-section-title">En contra</div>
          <ul>${(item.cons || []).map(c => `<li>${esc(c)}</li>`).join('')}</ul>
        </div>
      </div>
      <div class="aj-migration">
        <div class="aj-section-title" style="color:var(--accent)">🛂 Análisis migratorio</div>
        ${esc(item.migration_analysis || 'Sin análisis disponible.')}
      </div>
      <div class="aj-actions">
        ${item.link ? `<a href="${esc(item.link)}" target="_blank" class="apply-banner" style="display:inline-flex;padding:8px 16px;margin:0"><span class="t" style="color:#fff;">Aplicar →</span></a>` : ''}
        ${item.job_id ? `<button class="ghost aj-gen-docs" data-job-id="${esc(item.job_id)}">📄 Generar documentos</button>` : ''}
      </div>
    </div>`;
  });
  $('#autojob-results').innerHTML = html;

  $$('.aj-gen-docs').forEach(btn => {
    btn.addEventListener('click', async () => {
      const jid = btn.dataset.jobId;
      btn.disabled = true; btn.textContent = '⏳ Generando...';
      try {
        await jpost('/api/generate', { job_id: jid });
        toast('✅ Documentos generados. Ve a Matches para verlos.');
        btn.textContent = '✅ Generados';
      } catch (e) { toast('❌ ' + e.message, 4000); btn.disabled = false; btn.textContent = '📄 Generar documentos'; }
    });
  });
}

/* ===================== popover de acciones al pasar el mouse ===================== */
state.jobIndex = state.jobIndex || {};
function indexJob(job, extra) {
  if (!job || !job.id) return;
  state.jobIndex[job.id] = Object.assign({}, state.jobIndex[job.id], job, extra || {});
}
async function refreshApplyStatusMap() {
  try {
    const { applications } = await api('/api/applications');
    state.applyStatusMap = {};
    (applications || []).forEach(a => { state.applyStatusMap[a.job_id] = a.status; });
  } catch (e) {}
}

const jobPop = document.createElement('div');
jobPop.id = 'job-pop'; jobPop.className = 'job-pop'; jobPop.style.display = 'none';
document.body.appendChild(jobPop);
let jpHideTimer = null;

function buildJobPopHtml(id) {
  const j = state.jobIndex[id] || {};
  const appSt = (state.applyStatusMap || {})[id];
  const [slbl, scls] = ST_LABEL[j.status] || ['—', ''];
  const applied = appSt === 'applied';
  const appBadge = appSt
    ? `<span class="apply-badge ${(APPLY_STATUS_LABELS[appSt] || ['', 'muted'])[1]}">${(APPLY_STATUS_LABELS[appSt] || [appSt])[0]}</span>`
    : '';
  const b = [];
  if (j.link) b.push(`<button data-act="apply">📮 Aplicar en LinkedIn</button>`);
  b.push(j.has_docs ? `<button data-act="view">📄 Ver documentos</button>`
                    : `<button data-act="gen">✨ Generar documentos</button>`);
  b.push(applied ? `<button data-act="unapply">↩️ Quitar “aplicado”</button>`
                 : `<button data-act="applied">✅ Marcar como aplicado</button>`);
  b.push(`<button data-act="pending">⏳ Marcar pendiente</button>`);
  b.push(`<button data-act="notint">🚫 No me interesa</button>`);
  b.push(`<button class="jp-danger" data-act="del">🗑 Borrar</button>`);
  return `<div class="jp-head">
      <div class="jp-title">${esc(j.title || id)}</div>
      <div class="jp-co">${esc(j.company || '')}${j.location ? ' · ' + esc(j.location) : ''}</div>
      <div class="jp-badges"><span class="st-badge ${scls}">${slbl}</span>${appBadge}</div>
    </div>
    <div class="jp-actions">${b.join('')}</div>`;
}

function positionJobPop(trigger) {
  const r = trigger.getBoundingClientRect();
  jobPop.style.visibility = 'hidden'; jobPop.style.display = 'block';
  const pw = jobPop.offsetWidth, ph = jobPop.offsetHeight;
  let left = r.left;
  let top = r.bottom + 6;
  if (left + pw > window.innerWidth - 10) left = window.innerWidth - pw - 10;
  if (top + ph > window.innerHeight - 10) top = r.top - ph - 6;   // arriba si no cabe abajo
  jobPop.style.left = Math.max(10, left) + 'px';
  jobPop.style.top = Math.max(10, top) + 'px';
  jobPop.style.visibility = 'visible';
}

function showJobPop(trigger) {
  const id = trigger.dataset.jobpop;
  if (!state.jobIndex[id]) return;
  jobPop.innerHTML = buildJobPopHtml(id);
  jobPop.dataset.id = id;
  positionJobPop(trigger);
  jobPop.style.display = 'block';
}
function hideJobPop() { jobPop.style.display = 'none'; jobPop.dataset.id = ''; }

document.addEventListener('mouseover', e => {
  const trig = e.target.closest && e.target.closest('.jobpop-trigger');
  if (trig) {
    clearTimeout(jpHideTimer);
    if (jobPop.style.display === 'none' || jobPop.dataset.id !== trig.dataset.jobpop) showJobPop(trig);
  }
});
document.addEventListener('mouseout', e => {
  const trig = e.target.closest && e.target.closest('.jobpop-trigger');
  if (!trig) return;
  const to = e.relatedTarget;
  if (to && to.closest && (to.closest('#job-pop') || to.closest('.jobpop-trigger') === trig)) return;
  jpHideTimer = setTimeout(hideJobPop, 250);
});
jobPop.addEventListener('mouseenter', () => clearTimeout(jpHideTimer));
jobPop.addEventListener('mouseleave', () => { jpHideTimer = setTimeout(hideJobPop, 250); });

async function refreshAfterJobAction() {
  await refreshApplyStatusMap();
  await reloadMatches();
  if ($('#panel-datos').classList.contains('active')) await loadJobsTable();
  if ($('#panel-aplicar').classList.contains('active')) await loadApplications();
  loadQuota();
  reloadDeck();
}

jobPop.addEventListener('click', async e => {
  const btn = e.target.closest('button'); if (!btn) return;
  const id = jobPop.dataset.id;
  const act = btn.dataset.act;
  const job = state.jobIndex[id] || {};
  try {
    if (act === 'apply') { if (job.link) window.open(job.link, '_blank'); hideJobPop(); return; }
    if (act === 'view') { hideJobPop(); openMatch(id); return; }
    if (act === 'gen') {
      hideJobPop();
      const match = { id, job, docs: null, status: 'generating', error: '' };
      state.matches = state.matches.filter(m => m.id !== id);
      state.matches.unshift(match); renderMatches();
      toast('✨ Generando documentos...');
      await generateDocs(match); indexJob(job, { has_docs: true });
    } else if (act === 'applied') {
      await jpost('/api/apply/mark', { job_id: id, status: 'applied' });
      toast('✅ Marcado como aplicado.');
    } else if (act === 'unapply') {
      await jpost('/api/apply/mark', { job_id: id, status: 'clear' });
      toast('↩️ Ya no está marcado como aplicado.');
    } else if (act === 'pending') {
      await jpost('/api/job/status', { job_id: id, status: 'new' });
      await jpost('/api/apply/mark', { job_id: id, status: 'clear' });
      toast('⏳ Marcado como pendiente.');
    } else if (act === 'notint') {
      await jpost('/api/job/status', { job_id: id, status: 'passed' });
      toast('🚫 Marcado como no interesado.');
    } else if (act === 'del') {
      if (!confirm('¿Borrar este empleo de la base de datos?')) return;
      await jpost('/api/job/delete', { job_id: id });
      delete state.jobIndex[id];
      toast('🗑 Empleo borrado.');
    }
  } catch (err) {
    toast('❌ ' + err.message, 4000);
  }
  hideJobPop();
  await refreshAfterJobAction();
});

/* ===================== init ===================== */
(async () => {
  await loadConfig();
  checkOnboarding();
  refreshLinkedIn();
  setInterval(refreshLinkedIn, 8000);
  reloadDeck();
  reloadMatches();
  refreshApplyStatusMap();
  if ($('#panel-chat').classList.contains('active')) loadChat();  // asistente = pantalla inicial
})();


// ---------- Botón global «Scrapear empleos» ----------
(() => {
  const btn = document.getElementById('btn-scrape-all');
  if (!btn) return;
  const label = btn.textContent;
  async function poll() {
    try {
      const s = await api('/api/scrape-all/status');
      const last = (s.log || []).filter(Boolean).slice(-1)[0] || '';
      if (s.running) {
        btn.disabled = true; btn.textContent = '⏳ Scrapeando…'; btn.title = last;
        return setTimeout(poll, 2000);
      }
      btn.disabled = false; btn.textContent = label;
      if (s.finished) {
        const resumen = (s.log || []).find(l => l.startsWith('== Resumen')) || last;
        toast('✅ Scraping terminado. ' + resumen.replace(/^== /, ''), 6000);
      }
    } catch (e) { btn.disabled = false; btn.textContent = label; }
  }
  btn.addEventListener('click', async () => {
    const query = prompt('¿Qué puesto buscar? (vacío = búsquedas predeterminadas)', '');
    if (query === null) return;
    const location = prompt('¿Ubicación?', 'Mexico');
    if (location === null) return;
    try {
      await jpost('/api/scrape-all', { query, location });
      toast('🔎 Scraping iniciado en todas las plataformas…');
      poll();
    } catch (e) { toast('❌ ' + (e.message || e)); }
  });
  api('/api/scrape-all/status').then(s => s.running && poll()).catch(() => {});
})();


/* ===================== bienvenida (onboarding) ===================== */
const onb = { step: 0, seen: new Set(), timer: null };

function onbShow(step) {
  onb.step = step;
  $$('#onb .onb-step').forEach(el => { el.hidden = +el.dataset.step !== step; });
  $$('#onb .onb-dots i').forEach((d, i) => d.classList.toggle('on', i <= step));
  const first = $(`#onb .onb-step[data-step="${step}"] input`);
  if (first) setTimeout(() => first.focus(), 50);
}

async function checkOnboarding() {
  try {
    const st = await api('/api/onboarding/status');
    let seen = false;
    try { seen = localStorage.getItem('jobswiper_onb_seen') === '1'; } catch (e) {}
    if (!st.onboarded) { $('#onb').hidden = false; onbShow(0); }
    // Si ya pulsó «Empezar», la búsqueda sigue en segundo plano sin volver a taparle la app.
    else if (st.running && !seen) { $('#onb').hidden = false; onbShow(3); onbPoll(); }
  } catch (e) {}
}

$$('#onb .onb-next').forEach(b => b.addEventListener('click', () => {
  if (onb.step === 1 && !$('#o-name').value.trim()) { $('#o-name').focus(); $('#o-name').style.borderColor = 'var(--nope)'; return; }
  onbShow(onb.step + 1);
}));
$$('#onb .onb-back').forEach(b => b.addEventListener('click', () => onbShow(Math.max(0, onb.step - 1))));
$('#onb').addEventListener('keydown', e => {
  if (e.key === 'Enter' && e.target.tagName === 'INPUT' && onb.step === 1) $('#onb .onb-step[data-step="1"] .onb-next').click();
});

// --- leer el CV desde archivo (PDF con pdf.js, o texto plano) ---
async function loadPdfJs() {
  if (window.pdfjsLib) return window.pdfjsLib;
  await new Promise((ok, fail) => {
    const sc = document.createElement('script');
    sc.src = 'https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.min.js';
    sc.onload = ok; sc.onerror = () => fail(new Error('No se pudo cargar el lector de PDF.'));
    document.head.appendChild(sc);
  });
  window.pdfjsLib.GlobalWorkerOptions.workerSrc = 'https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.worker.min.js';
  return window.pdfjsLib;
}
async function readCvFile(file) {
  const drop = $('#o-drop'), txt = $('#o-drop-text');
  txt.textContent = '⏳ Leyendo ' + file.name + '…';
  try {
    let text = '';
    if (/\.pdf$/i.test(file.name) || file.type === 'application/pdf') {
      const pdfjs = await loadPdfJs();
      const pdf = await pdfjs.getDocument({ data: await file.arrayBuffer() }).promise;
      for (let i = 1; i <= pdf.numPages; i++) {
        const page = await pdf.getPage(i);
        const c = await page.getTextContent();
        let line = '', lastY = null;
        for (const it of c.items) {
          const y = it.transform[5];
          if (lastY !== null && Math.abs(y - lastY) > 2) { text += line.trim() + '\n'; line = ''; }
          line += it.str + (it.hasEOL ? '' : ' '); lastY = y;
        }
        text += line.trim() + '\n\n';
      }
    } else {
      text = await file.text();
    }
    text = text.replace(/[ \t]+/g, ' ').replace(/\n{3,}/g, '\n\n').trim();
    if (text.length < 40) throw new Error('No encontramos texto en el archivo (¿es un PDF escaneado?). Pégalo como texto.');
    $('#o-cv').value = text;
    drop.classList.add('ok'); txt.textContent = '✅ ' + file.name + ' leído — revisa el texto abajo';
  } catch (e) {
    drop.classList.remove('ok'); txt.textContent = '❌ ' + e.message;
  }
}
$('#o-file').addEventListener('change', e => { const f = e.target.files[0]; if (f) readCvFile(f); });
['dragover', 'dragenter'].forEach(ev => $('#o-drop').addEventListener(ev, e => { e.preventDefault(); $('#o-drop').classList.add('over'); }));
['dragleave', 'drop'].forEach(ev => $('#o-drop').addEventListener(ev, e => { e.preventDefault(); $('#o-drop').classList.remove('over'); }));
$('#o-drop').addEventListener('drop', e => { const f = e.dataTransfer.files[0]; if (f) readCvFile(f); });

$('#o-submit').addEventListener('click', async () => {
  const st = $('#o-status');
  const body = {
    name: $('#o-name').value.trim(), email: $('#o-email').value.trim(), phone: $('#o-phone').value.trim(),
    location: $('#o-location').value.trim(), target: $('#o-target').value.trim(), base_cv: $('#o-cv').value.trim(),
  };
  if (body.base_cv.length < 40) { st.textContent = '❌ Sube o pega tu CV para continuar.'; st.className = 'status err'; return; }
  if (!body.target) { st.textContent = '❌ Dinos qué puesto buscas.'; st.className = 'status err'; $('#o-target').focus(); return; }
  $('#o-submit').disabled = true;
  try {
    await jpost('/api/onboarding', body);
    try { localStorage.removeItem('jobswiper_onb_seen'); } catch (e) {}
    if (body.target) $('#s-keywords').value = body.target;
    onbShow(3); onbPoll();
  } catch (e) { st.textContent = '❌ ' + e.message; st.className = 'status err'; }
  finally { $('#o-submit').disabled = false; }
});

const ONB_ICON = { pending: '○', running: '⏳', done: '✅', error: '⚠️' };
async function onbPoll() {
  clearTimeout(onb.timer);
  try {
    const [st, deckRes] = await Promise.all([api('/api/onboarding/status'), api('/api/deck')]);
    $('#o-steps').innerHTML = (st.steps || []).map(s =>
      `<li class="${s.state}"><span class="ic">${ONB_ICON[s.state] || '○'}</span><div>${esc(s.label)}${s.detail ? `<small>${esc(s.detail)}</small>` : ''}</div></li>`).join('');
    // Solo los empleos que encontró ESTA búsqueda (no los que ya había en la base)
    const jobs = (deckRes.jobs || []).filter(j => !st.started || (j.created_at || 0) >= st.started - 1);
    const feed = $('#o-feed');
    const fresh = jobs.filter(j => !onb.seen.has(j.id));
    if (fresh.length) {
      feed.querySelector('.onb-feed-empty')?.remove();
      // De a poco, para que se note que van llegando
      fresh.slice(0, 40).forEach((j, i) => {
        onb.seen.add(j.id);
        setTimeout(() => {
          const el = document.createElement('div'); el.className = 'onb-job';
          el.innerHTML = `<b>${esc(j.title)}</b><span>${esc(j.company || '')} · ${esc(j.location || '')} · ${esc(j.source || '')}</span>`;
          feed.prepend(el);
        }, i * 140);
      });
    }
    $('#o-count').textContent = jobs.length;
    const go = $('#o-go');
    if (st.done || !st.running) {
      $('#o-cfg-title').textContent = st.error ? 'Hubo un problema, pero puedes continuar' : '¡Tu perfil está listo! 🎉';
      $('.onb-orb').classList.add('done');
      if (st.error) { $('#o-cfg-status').textContent = '⚠️ ' + st.error; $('#o-cfg-status').className = 'status err'; }
      go.disabled = false;
      go.textContent = jobs.length ? `🔥 Empezar a swipear (${jobs.length})` : 'Ir a la app →';
      return;
    }
    if (jobs.length >= 3) { go.disabled = false; go.textContent = `🔥 Empezar ya (${jobs.length}) — seguimos buscando`; }
  } catch (e) {}
  onb.timer = setTimeout(onbPoll, 2000);
}

$('#o-go').addEventListener('click', async () => {
  $('#onb').hidden = true;
  clearTimeout(onb.timer);
  try { localStorage.setItem('jobswiper_onb_seen', '1'); } catch (e) {}
  await loadConfig();
  await reloadDeck();
  goPanel('swipe');
  // Si la búsqueda sigue en segundo plano, refrescamos el deck mientras llegan empleos
  const refill = setInterval(async () => {
    const st = await api('/api/onboarding/status').catch(() => ({}));
    if (state.queue.length < 3) reloadDeck();
    if (!st.running) { clearInterval(refill); reloadDeck(); }
  }, 5000);
});


$('#btn-reset').addEventListener('click', async () => {
  const jobs = $('#reset-jobs').checked;
  if (!confirm('¿Seguro? Se borrarán tu CV, tu perfil y el chat' + (jobs ? ', y los empleos pendientes' : '') + '.')) return;
  try {
    await jpost('/api/reset', { jobs });
    try { localStorage.removeItem('jobswiper_onb_seen'); } catch (e) {}
    location.reload();
  } catch (e) { toast('❌ ' + e.message); }
});

/* ===================== una pregunta tras cada swipe ===================== */
let qBusy = false;
async function maybeAskQuestion() {
  const card = $('#q-card');
  if (qBusy || !card.hidden) return;
  qBusy = true;
  try {
    const { question: q, remaining } = await api('/api/profile/question');
    if (!q) return;
    card.innerHTML = `
      <div class="q-top"><span>💡 Para afinar tus CVs</span><span>${remaining} pendiente${remaining === 1 ? '' : 's'}</span></div>
      <div class="q-text">${esc(q.q)}</div>
      ${q.options
        ? `<div class="chips">${q.options.map(o => `<div class="chip" data-v="${esc(o)}">${esc(o)}</div>`).join('')}</div>`
        : `<div class="q-input"><input placeholder="${esc(q.placeholder || '')}"><button class="primary" style="flex:0">OK</button></div>`}
      <button class="q-skip">Prefiero no responder</button>`;
    card.hidden = false;
    const send = async (payload) => {
      card.hidden = true;
      try {
        const r = await jpost('/api/profile/answer', { key: q.key, ...payload });
        if (!payload.skip) state.profile = { ...(state.profile || {}), [q.key]: payload.value };
        if (r.remaining === 0) toast('🎉 Perfil completo. Actualizando tu CV general…', 4500);
      } catch (e) { toast('❌ ' + e.message); }
    };
    card.querySelectorAll('.chip').forEach(c => c.addEventListener('click', () => send({ value: c.dataset.v })));
    const inp = card.querySelector('input');
    if (inp) {
      const ok = () => inp.value.trim() && send({ value: inp.value.trim() });
      card.querySelector('.q-input button').addEventListener('click', ok);
      inp.addEventListener('keydown', e => { if (e.key === 'Enter') ok(); e.stopPropagation(); });
    }
    card.querySelector('.q-skip').addEventListener('click', () => send({ skip: true }));
  } catch (e) {} finally { qBusy = false; }
}


/* ===================== CV general ===================== */
let masterCv = null, masterTimer = null;
async function loadMasterCv() {
  clearTimeout(masterTimer);
  try {
    const r = await api('/api/master-cv');
    masterCv = r.master_cv;
    const el = $('#master-state');
    if (r.running) el.innerHTML = '⏳ Generando tu CV general…';
    else if (r.error) el.innerHTML = '⚠️ ' + esc(r.error);
    else if (!masterCv) el.innerHTML = 'Aún no tienes CV general. Pulsa <b>Regenerar</b> para crearlo.';
    else el.innerHTML = '✅ Listo' + (r.updated_at ? ' · actualizado ' + new Date(r.updated_at * 1000).toLocaleString() : '')
      + (r.stale ? ' · <b>hay respuestas nuevas</b>, regenéralo para incluirlas' : '');
    $$('#master-box [data-master="view"], #master-box [data-master="pdf"]').forEach(b => b.disabled = !masterCv);
    if (r.running) masterTimer = setTimeout(loadMasterCv, 3000);
  } catch (e) {}
}
$('#master-box').addEventListener('click', async e => {
  const b = e.target.closest('button[data-master]'); if (!b) return;
  const lang = b.dataset.lang || 'es';
  if (b.dataset.master === 'regen') {
    try { await jpost('/api/master-cv/regenerate'); loadMasterCv(); } catch (err) { toast('❌ ' + err.message); }
  } else if (masterCv && b.dataset.master === 'view') {
    const w = window.open('', '_blank');
    if (w) { w.document.write(buildStandaloneDoc(renderDocInner('resume', masterCv, lang), true, true, false)); w.document.title = 'CV general'; w.document.close(); }
  } else if (masterCv && b.dataset.master === 'pdf') {
    downloadDocPDF('resume', masterCv, `CV_General_${lang.toUpperCase()}`, lang);
  }
});
