// GEO-tab — Generative Engine Optimization (AI-zichtbaarheid).
// Toont per-site GEO-score (5 pijlers), ICP-persona's en entity-block-generator.
// Leest /api/geo/* (backend/domains/geo). Deterministisch, geen LLM.

async function renderGeoTab(el) {
  el.innerHTML = `<div class="geo-wrap">
    <div class="geo-head">
      <h2>GEO — Generative Engine Optimization</h2>
      <p class="muted">Hoe 'AI-ready' is elk merk voor ChatGPT / Perplexity / Bing?
      KPI: <b>genoemd als bron</b>, niet 'rank #1'. Gebouwd naar Goldie×Float (15-08-2026).</p>
    </div>
    <div id="geo-summary" class="geo-cards">Laden…</div>
    <div id="geo-detail"></div>
  </div>`;
  loadGeoSummary(el);
}

function geoGradeClass(score) {
  if (score === null || score === undefined) return 'geo-unknown';
  if (score >= 85) return 'geo-a';
  if (score >= 70) return 'geo-b';
  if (score >= 50) return 'geo-c';
  return 'geo-d';
}

async function loadGeoSummary(el) {
  const box = el.querySelector('#geo-summary');
  try {
    const data = await (await fetch('/api/geo/summary')).json();
    const sites = data.sites || [];
    box.innerHTML = sites.map(s => {
      const g = geoGradeClass(s.score);
      const label = s.score === null ? '—' : s.score;
      return `<button class="geo-card ${g}" onclick="loadGeoSite('${s.site_id}','${escHtml(s.name)}')">
        <div class="geo-score">${label}</div>
        <div class="geo-name">${escHtml(s.name)}</div>
        <div class="geo-grade">${escHtml(s.grade)}</div>
      </button>`;
    }).join('');
  } catch (e) {
    box.innerHTML = `<div class="empty-state">GEO-domein niet bereikbaar: ${escHtml(String(e))}</div>`;
  }
}

async function loadGeoSite(siteId, name) {
  const detail = document.querySelector('#geo-detail');
  detail.innerHTML = `<div class="geo-detail-head"><h3>${escHtml(name)} — GEO-scan</h3>
    <button class="btn" onclick="geoRunScan('${siteId}')">Scan nu</button></div>
    <div id="geo-scan-result">Laatste scan laden…</div>
    <div class="geo-citation" id="geo-citation"></div>
    <div class="geo-two-col">
      <div id="geo-personas"><h4>ICP-persona's</h4><div class="muted">Laden…</div></div>
      <div id="geo-entity"><h4>Entity-block (anti-hallucinatie)</h4><div id="geo-entity-body"></div></div>
    </div>`;
  await geoLoadLatest(siteId);
  await geoLoadCitation(siteId);
  await geoLoadPersonas(siteId);
  geoRenderEntityForm(siteId, name);
}

async function geoRunScan(siteId) {
  const res = document.querySelector('#geo-scan-result');
  res.innerHTML = 'Scan draait…';
  try {
    const data = await (await fetch(`/api/geo/scan/${siteId}`)).json();
    res.innerHTML = geoScanHtml(data);
  } catch (e) {
    res.innerHTML = `<div class="empty-state">Scan mislukt: ${escHtml(String(e))}</div>`;
  }
}

async function geoLoadLatest(siteId) {
  const res = document.querySelector('#geo-scan-result');
  try {
    const data = await (await fetch(`/api/geo/latest/${siteId}`)).json();
    if (data.error) { res.innerHTML = `<div class="muted">Nog niet gescand — klik 'Scan nu'.</div>`; return; }
    res.innerHTML = geoScanHtml(data);
  } catch (e) {
    res.innerHTML = `<div class="muted">Nog niet gescand — klik 'Scan nu'.</div>`;
  }
}

async function geoLoadCitation(siteId) {
  const box = document.querySelector('#geo-citation');
  try {
    const data = await (await fetch(`/api/geo/citation/latest`)).json();
    const site = (data.sites || []).find(s => s.site_id === siteId);
    if (!site) {
      box.innerHTML = `<div class="geo-cit-empty muted">Nog geen citatie-check gedraaid voor dit merk.
        Plan een ICP-persona in (zie hieronder) en de wekelijkse check meet of AI dit merk noemt als bron.</div>`;
      return;
    }
    const g = geoGradeClass(site.citation_score);
    box.innerHTML = `<h4>Citatie-score (wordt dit merk genoemd als bron in AI?)</h4>
      <div class="geo-cit-row">
        <span class="geo-cit-score ${g}">${site.citation_score}</span>
        <span class="muted">${site.cited}/${site.queries} queries — week ${escHtml(data.week || '?')}</span>
      </div>
      <div class="muted" style="font-size:11px">Dit is het echte GEO-KPI (ipv 'rank #1'). Wekelijks automatisch gemeten.</div>`;
  } catch (e) {
    box.innerHTML = `<div class="muted">citatie-check niet beschikbaar: ${escHtml(String(e))}</div>`;
  }
}


function geoScanHtml(d) {
  const p = d.pillars || {};
  const bars = [
    ['Bing / live retrieval', p.bing],
    ['Structured data', p.structured],
    ['Direct answer', p.direct_answer],
    ['Entity / negations', p.entity],
    ['UGC-signaal', p.ugc],
  ].map(([k, v]) => {
    const val = v === undefined ? 0 : v;
    return `<div class="geo-bar-row"><span class="geo-bar-label">${escHtml(k)}</span>
      <span class="geo-bar"><span class="geo-bar-fill ${geoGradeClass(val)}" style="width:${val}%"></span></span>
      <span class="geo-bar-val">${val}</span></div>`;
  }).join('');
  const recs = (d.recommendations || []).map(r => `<li>${escHtml(r)}</li>`).join('');
  return `<div class="geo-scan">
    <div class="geo-scan-score ${geoGradeClass(d.score)}">${d.score} <small>${escHtml(d.grade || '')}</small></div>
    <div class="geo-bars">${bars}</div>
    <div class="geo-bing-status muted">${escHtml(d.bing_status || '')}</div>
    <div class="geo-recs"><b>Aanbevelingen</b><ul>${recs || '<li class="muted">—</li>'}</ul></div>
  </div>`;
}

async function geoLoadPersonas(siteId) {
  const box = document.querySelector('#geo-personas');
  const body = box.querySelector('div') || box;
  try {
    const data = await (await fetch(`/api/geo/personas/${siteId}`)).json();
    const list = (data.personas || []).map(p => `
      <div class="geo-persona">
        <b>${escHtml(p.name)}</b>
        <p class="muted">${escHtml(p.description || '')}</p>
        ${(p.queries || []).map(q => `<span class="geo-q">${escHtml(q)}</span>`).join('')}
      </div>`).join('') || '<div class="muted">Nog geen persona — voeg de ICP toe.</div>';
    box.innerHTML = `<h4>ICP-persona's</h4>${list}
      <button class="btn" onclick="geoPersonaForm('${siteId}')">+ Persona</button>
      <div id="geo-persona-form"></div>`;
  } catch (e) {
    box.innerHTML = `<h4>ICP-persona's</h4><div class="muted">fout: ${escHtml(String(e))}</div>`;
  }
}

function geoPersonaForm(siteId) {
  const f = document.querySelector('#geo-persona-form');
  f.innerHTML = `<div class="geo-form">
    <input id="pf-name" placeholder="Persona-naam (bv. 'Zorgmanager gemeente')">
    <textarea id="pf-desc" placeholder="Beschrijving + pijnpunten"></textarea>
    <textarea id="pf-queries" placeholder="ICP-vragen, één per regel"></textarea>
    <button class="btn" onclick="geoSavePersona('${siteId}')">Opslaan</button>
  </div>`;
}

async function geoSavePersona(siteId) {
  const name = document.querySelector('#pf-name').value.trim();
  const desc = document.querySelector('#pf-desc').value.trim();
  const queries = document.querySelector('#pf-queries').value.split('\n').map(s => s.trim()).filter(Boolean);
  if (!name) return;
  await fetch('/api/geo/personas', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ site_id: siteId, name, description: desc, queries })
  });
  await geoLoadPersonas(siteId);
}

function geoRenderEntityForm(siteId, name) {
  const body = document.querySelector('#geo-entity-body');
  body.innerHTML = `<div class="geo-form">
    <input id="eb-is" placeholder="Wat is ${escHtml(name)}? (korte omschrijving)">
    <textarea id="eb-not" placeholder="Wat is het NIET? (negations, één per regel)"></textarea>
    <button class="btn" onclick="geoGenEntity('${escHtml(name)}')">Genereer entity-block</button>
    <pre id="geo-entity-out" class="geo-out"></pre>
  </div>`;
}

async function geoGenEntity(name) {
  const isIt = document.querySelector('#eb-is').value.trim();
  const not = document.querySelector('#eb-not').value.split('\n').map(s => s.trim()).filter(Boolean);
  if (!isIt) return;
  const data = await (await fetch('/api/geo/entity-block', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ site_name: name, what_it_is: isIt, what_it_is_not: not })
  })).json();
  document.querySelector('#geo-entity-out').textContent = data.block || '';
}
