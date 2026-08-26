// ── Impact OS — Kennis (Knowledge Forge) ────────────────────────────────────
// Leer een document (pad of URL) in een doorzoekbare kennisbank: embeddings-
// retrieval + een gestructureerde brain file (index/glossary/cheat-sheet) die
// naar de Obsidian-vault wordt geschreven. Globaal, niet projectgebonden —
// zelfde reden als Geheugen: het is Vincents eigen vault, geen klantdata.

function renderKennisTab(el) {
  el.innerHTML = '<div class="loading"><div class="spinner"></div><p>Kennisbank laden...</p></div>';
  kennisLoad(el);
}

function kennisLoad(el) {
  fetch('/api/learn/documents').then(function (r) { return r.json(); }).then(function (docs) {
    kennisRender(el, docs || []);
  }).catch(function (e) {
    el.innerHTML = '<div class="empty-state">Kennisbank laden mislukt: ' + escHtml(e.message) + '</div>';
  });
}

function kennisRender(el, docs) {
  var html = '<div class="project-header"><div><h1>Kennis</h1>' +
    '<p class="meta">Documenten leren en doorzoeken — landt als brain file in je Obsidian-vault</p></div></div>';

  html += '<div class="section-card" style="margin-bottom:16px">' +
    '<h3 style="font-size:13px;font-weight:700;color:var(--text);margin-bottom:10px">Document leren</h3>' +
    '<p style="font-size:11px;color:#94a3b8;margin-bottom:10px">Pad (.pdf/.docx/.md/.txt) of een http(s)-URL.</p>' +
    '<div style="display:flex;gap:8px">' +
    '<input id="kennis-source" type="text" placeholder="bijv. C:\\rapporten\\onderzoek.pdf of https://..." ' +
    'style="flex:1;padding:8px 10px;border:1px solid #e2e8f0;border-radius:6px;font-size:12px" />' +
    '<button id="kennis-learn-btn" onclick="kennisLearnSubmit(this)" class="btn btn-primary btn-sm">Leer document</button>' +
    '</div><div id="kennis-learn-result" style="margin-top:8px;font-size:12px"></div>' +
    '</div>';

  html += '<div class="section-card" style="margin-bottom:16px">' +
    '<h3 style="font-size:13px;font-weight:700;color:var(--text);margin-bottom:10px">Vraag stellen</h3>' +
    '<div style="display:flex;gap:8px">' +
    '<input id="kennis-query" type="text" placeholder="Wat wil je weten uit de geleerde documenten?" ' +
    'style="flex:1;padding:8px 10px;border:1px solid #e2e8f0;border-radius:6px;font-size:12px" />' +
    '<button onclick="kennisAskSubmit(this)" class="btn btn-primary btn-sm">Vraag</button>' +
    '</div><div id="kennis-ask-result" style="margin-top:10px"></div>' +
    '</div>';

  html += '<div class="section-card">' +
    '<h3 style="font-size:13px;font-weight:700;color:var(--text);margin-bottom:10px">Geleerde documenten (' + docs.length + ')</h3>';
  if (!docs.length) {
    html += '<p style="color:#64748b;font-size:12px">Nog niets geleerd. Voeg hierboven je eerste document toe.</p>';
  } else {
    html += docs.map(kennisDocRow).join('');
  }
  html += '</div>';

  el.innerHTML = html;
}

function kennisDocRow(d) {
  return '<div style="display:flex;align-items:center;justify-content:space-between;gap:8px;border-top:1px solid #f1f5f9;padding:8px 0">' +
    '<div>' +
    '<p style="margin:0;font-size:13px;font-weight:600;color:var(--text)">' + escHtml(d.title || d.source) + '</p>' +
    '<p style="margin:2px 0 0;font-size:11px;color:#94a3b8">' + escHtml(d.source) + ' · ' + (d.chunk_count || 0) + ' chunks · ' + escHtml(d.provider || '') + ' · ' + escHtml((d.updated_at || '').slice(0, 16).replace('T', ' ')) + '</p>' +
    '</div>' +
    '<button onclick="kennisDeleteDoc(\'' + escHtml(d.id) + '\',this)" class="btn btn-sm btn-danger-outline">Verwijder</button>' +
    '</div>';
}

function kennisLearnSubmit(btn) {
  var input = document.getElementById('kennis-source');
  var source = (input.value || '').trim();
  var resultEl = document.getElementById('kennis-learn-result');
  if (!source) { resultEl.innerHTML = '<span style="color:#c2410c">Vul eerst een pad of URL in.</span>'; return; }
  btn.disabled = true;
  btn.textContent = 'Leren...';
  resultEl.innerHTML = '<span style="color:#94a3b8">Document wordt gelezen en verwerkt — dit kan even duren.</span>';
  post('/api/learn', { source: source }).then(function (r) {
    resultEl.innerHTML = '<span style="color:#16a34a">Geleerd: "' + escHtml(r.title || source) + '" — ' +
      (r.chunks || 0) + ' chunks, ' + (r.glossary_terms || 0) + ' begrippen, ' + (r.cheat_rules || 0) + ' vuistregels.</span>';
    input.value = '';
    kennisLoad(document.getElementById('tab-content'));
  }).catch(function (e) {
    resultEl.innerHTML = '<span style="color:#dc2626">Mislukt: ' + escHtml(e.message) + '</span>';
  }).finally(function () {
    btn.disabled = false;
    btn.textContent = 'Leer document';
  });
}

function kennisAskSubmit(btn) {
  var input = document.getElementById('kennis-query');
  var query = (input.value || '').trim();
  var resultEl = document.getElementById('kennis-ask-result');
  if (!query) { resultEl.innerHTML = '<span style="color:#c2410c;font-size:12px">Vul eerst een vraag in.</span>'; return; }
  btn.disabled = true;
  resultEl.innerHTML = '<span style="color:#94a3b8;font-size:12px">Zoeken...</span>';
  post('/api/learn/ask', { query: query, top_k: 5 }).then(function (r) {
    var html = '';
    if (r.answer_context) {
      html += '<div style="font-size:12px;color:var(--text);white-space:pre-wrap;background:#f8fafc;border:1px solid #e2e8f0;border-radius:6px;padding:10px;margin-bottom:8px">' + escHtml(r.answer_context) + '</div>';
    }
    if (r.sources && r.sources.length) {
      html += '<p style="font-size:11px;color:#94a3b8;margin:0 0 4px">Bronnen: ' + r.sources.map(function (s) { return escHtml(s.title || s.doc_id || s); }).join(', ') + '</p>';
    }
    if (!html) html = '<p style="font-size:12px;color:#94a3b8">Niets relevants gevonden in de geleerde documenten.</p>';
    resultEl.innerHTML = html;
  }).catch(function (e) {
    resultEl.innerHTML = '<span style="color:#dc2626;font-size:12px">Mislukt: ' + escHtml(e.message) + '</span>';
  }).finally(function () {
    btn.disabled = false;
  });
}

function kennisDeleteDoc(docId, btn) {
  if (!confirm('Dit document uit de kennisbank verwijderen?')) return;
  btn.disabled = true;
  fetch('/api/learn/' + encodeURIComponent(docId), { method: 'DELETE' }).then(function (r) {
    if (!r.ok) throw new Error('HTTP ' + r.status);
    return r.json();
  }).then(function () {
    kennisLoad(document.getElementById('tab-content'));
  }).catch(function (e) {
    alert('Verwijderen mislukt: ' + e.message);
    btn.disabled = false;
  });
}
