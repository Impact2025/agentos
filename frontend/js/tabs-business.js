// ── Agent OS — tabs: Leads, Opdrachten, Radar
// Onderdeel van de SPA: klassieke scripts, gedeelde globale scope.
// Laadvolgorde staat in index.html — core.js eerst.

// ═══════════════════════════════════════════════════════════════════
//  LEADS — LinkedIn Personen zoeken & B2B Prospecting
// ═══════════════════════════════════════════════════════════════════
async function renderLeadsTab(el) {
  el.innerHTML = '<div class="loading"><div class="spinner"></div><p>Leads laden...</p></div>';
  try {
    var statsResp = await fetch('/api/leads/stats');
    var stats = await statsResp.json();
  } catch(e) { el.innerHTML = '<div class="empty-state">Fout: ' + escHtml(e.message) + '</div>'; return; }

  // Stats
  var total = (stats && stats.total) || 0;
  var enriched = (stats && stats.enriched) || 0;
  var valid = (stats && stats.valid) || 0;
  var contacted = (stats && stats.contacted) || 0;

  var html = '<h3 style="font-size:15px;font-weight:700;margin-bottom:16px">Leads &amp; Prospecting</h3>';

  // ── KPI row ──
  html += '<div class="kpi-grid" style="grid-template-columns:repeat(4,1fr);margin-bottom:16px">' +
    '<div class="kpi-box" style="background:#f0fdf4;border:1px solid #bbf7d0"><div class="kpi-val" style="font-size:22px;color:#16a34a">' + total + '</div><div style="font-size:11px;color:#4ade80;font-weight:500">Totaal leads</div></div>' +
    '<div class="kpi-box" style="background:#eff6ff;border:1px solid #bfdbfe"><div class="kpi-val" style="font-size:22px;color:#2563eb">' + enriched + '</div><div style="font-size:11px;color:#60a5fa;font-weight:500">Verrijkt</div></div>' +
    '<div class="kpi-box" style="background:#fefce8;border:1px solid #fde68a"><div class="kpi-val" style="font-size:22px;color:#ca8a04">' + valid + '</div><div style="font-size:11px;color:#facc15;font-weight:500">Geverifieerd</div></div>' +
    '<div class="kpi-box" style="background:#fef2f2;border:1px solid #fecaca"><div class="kpi-val" style="font-size:22px;color:#dc2626">' + contacted + '</div><div style="font-size:11px;color:#f87171;font-weight:500">Gecontacteerd</div></div>' +
    '</div>';

  // ── LinkedIn People Search ──
  html += '<div class="section-card" style="margin-bottom:16px">' +
    '<h4 style="font-size:13px;font-weight:600;margin-bottom:8px">LinkedIn Personen zoeken</h4>' +
    '<p style="font-size:11px;color:#64748b;margin-bottom:8px">Zoek beslissers/professionals via site:linkedin.com/in. Resultaten worden niet automatisch opgeslagen.</p>' +
    '<div style="display:flex;gap:8px">' +
    '<input id="linkedin-query" type="text" placeholder="Bijv. AI directeur zorginstelling Amsterdam" style="flex:1;padding:8px 10px;border:1px solid #e2e8f0;border-radius:6px;font-size:13px">' +
    '<button onclick="runLinkedinPeopleSearch()" style="padding:8px 16px;background:var(--accent);color:#fff;border:none;border-radius:6px;font-size:12px;font-weight:600;cursor:pointer">Zoeken</button></div>' +
    '<div id="linkedin-results" style="margin-top:10px"></div></div>';

  // ── WeAreImpact Batch Prospecting ──
  if (currentProject === 'WeAreImpact') {
    html += '<div class="section-card" style="margin-bottom:16px">' +
      '<h4 style="font-size:13px;font-weight:600;margin-bottom:8px">Batch Prospecting — WeAreImpact</h4>' +
      '<p style="font-size:11px;color:#64748b;margin-bottom:8px">Doorzoek het web met 15 AI-consultancy queries in zorg/welzijn. Vindt bedrijven, scraped websites, AI-analyse, slaat op in DB + Obsidian.</p>' +
      '<div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">' +
      '<input id="batch-regio" type="text" placeholder="Regio (optioneel)" style="width:140px;padding:8px 10px;border:1px solid #e2e8f0;border-radius:6px;font-size:12px">' +
      '<button onclick="runWeAreImpactBatch()" id="batch-btn" style="padding:8px 16px;background:#059669;color:#fff;border:none;border-radius:6px;font-size:12px;font-weight:600;cursor:pointer">Start batch-run</button>' +
      '</div>' +
      '<div id="batch-progress" style="margin-top:10px;max-height:300px;overflow-y:auto"></div></div>';
  } else {
    html += '<div class="section-card" style="margin-bottom:16px">' +
      '<h4 style="font-size:13px;font-weight:600;margin-bottom:8px">Batch Prospecting</h4>' +
      '<div style="display:flex;gap:8px;flex-wrap:wrap;align-items:end">' +
      '<div><label style="font-size:10px;color:#64748b;display:block;margin-bottom:2px">Template</label>' +
      '<select id="batch-template" style="padding:8px 10px;border:1px solid #e2e8f0;border-radius:6px;font-size:12px">' +
      '<option value="weareimpact_ai">WeAreImpact (AI consultancy)</option>' +
      '<option value="weareimpact_opdrachten">WeAreImpact (AI opdrachten)</option>' +
      '<option value="notarissen_nl">Notarissen NL</option>' +
      '<option value="uitvaart_nl">Uitvaart NL</option>' +
      '<option value="zorg_nl">Zorg NL</option>' +
      '<option value="custom">Eigen queries</option></select></div>' +
      '<div><label style="font-size:10px;color:#64748b;display:block;margin-bottom:2px">Regio</label>' +
      '<input id="batch-regio" type="text" placeholder="Bijv. Noord-Holland" style="width:140px;padding:8px 10px;border:1px solid #e2e8f0;border-radius:6px;font-size:12px"></div>' +
      '<div><label style="font-size:10px;color:#64748b;display:block;margin-bottom:2px">Eigen queries (1 per regel)</label>' +
      '<textarea id="batch-custom-queries" rows="2" placeholder="AI consultancy Den Haag\ninterim projectleider zorg" style="width:200px;padding:6px 8px;border:1px solid #e2e8f0;border-radius:6px;font-size:11px;resize:vertical"></textarea></div>' +
      '<button onclick="runCustomBatch()" id="batch-btn" style="padding:8px 16px;background:#059669;color:#fff;border:none;border-radius:6px;font-size:12px;font-weight:600;cursor:pointer">Start batch</button></div>' +
      '<div id="batch-progress" style="margin-top:10px;max-height:300px;overflow-y:auto"></div></div>';
  }

  // ── Lead lijst ──
  html += '<div class="section-card">' +
    '<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:8px">' +
    '<h4 style="font-size:13px;font-weight:600">Lead overzicht</h4>' +
    '<div style="display:flex;gap:6px;align-items:center">' +
    '<select id="lead-filter-status" onchange="loadLeadList()" style="padding:4px 8px;border:1px solid #e2e8f0;border-radius:4px;font-size:11px">' +
    '<option value="">Alle statussen</option>' +
    '<option value="new">Nieuw</option>' +
    '<option value="enriched">Verrijkt</option>' +
    '<option value="valid">Geverifieerd</option>' +
    '<option value="contacted">Gecontacteerd</option>' +
    '<option value="replied">Reactie</option></select>' +
    '<a href="/api/leads/export" target="_blank" style="padding:4px 10px;background:#f1f5f9;color:#475569;border:1px solid #e2e8f0;border-radius:4px;font-size:10px;text-decoration:none">Export Excel</a></div></div>' +
    '<div id="lead-list" style="max-height:500px;overflow-y:auto">' +
    '<div style="text-align:center;color:#94a3b8;padding:16px;font-size:12px">Laden...</div></div></div>';

  el.innerHTML = html;
  loadLeadList();
}

// ═══════════════════════════════════════════════════════════════════
//  LEADS — Helper functies
// ═══════════════════════════════════════════════════════════════════

function runLinkedinPeopleSearch() {
  var query = document.getElementById('linkedin-query');
  var resultsEl = document.getElementById('linkedin-results');
  if (!query || !resultsEl) return;
  var q = query.value.trim();
  if (!q) { resultsEl.innerHTML = '<div style="color:#ef4444;font-size:11px">Voer een zoekopdracht in</div>'; return; }
  resultsEl.innerHTML = '<div class="loading"><div class="spinner"></div><p>Zoeken op LinkedIn...</p></div>';
  fetch('/api/leads/linkedin-people', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({query: q, max_results: 8})
  }).then(function(r){return r.json();}).then(function(data){
    var results = data.results || [];
    if (!results.length) {
      resultsEl.innerHTML = '<div style="color:#f59e0b;font-size:11px;padding:8px">Geen LinkedIn-profielen gevonden. Probeer een bredere zoekopdracht.</div>';
      return;
    }
    var h = '<table class="data-table"><thead><tr><th>Naam/Titel</th><th>URL</th><th>Context</th></tr></thead><tbody>';
    results.forEach(function(r){
      var title = escHtml(r.title||'').slice(0,80);
      var url = escHtml(r.url||'');
      var snippet = escHtml(r.snippet||'').slice(0,120);
      h += '<tr><td style="font-weight:500">' + title + '</td>' +
        '<td><a href="' + url + '" target="_blank" style="color:#2563eb;font-size:11px" title="' + url + '">Openen ↪</a></td>' +
        '<td style="font-size:10px;color:#64748b">' + snippet + '</td></tr>';
    });
    h += '</tbody></table>';
    resultsEl.innerHTML = h;
  }).catch(function(e){
    resultsEl.innerHTML = '<div style="color:#ef4444;font-size:11px">Fout: ' + escHtml(e.message) + '</div>';
  });
}

function runWeAreImpactBatch() {
  var btn = document.getElementById('batch-btn');
  var progress = document.getElementById('batch-progress');
  if (!btn || !progress) return;
  btn.disabled = true; btn.textContent = 'Bezig...';
  var regio = (document.getElementById('batch-regio') || {}).value || '';
  progress.innerHTML = '<div class="loading"><div class="spinner"></div><p>Batch-run gestart...</p></div>';

  var body = {template: 'weareimpact_ai', max_per_query: 5};
  if (regio) body.regio = regio;

  fetch('/api/leads/batch', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(body)
  }).then(function(r){
    // SSE stream reader
    var reader = r.body.getReader();
    var decoder = new TextDecoder();
    var logs = [];

    function readChunk() {
      reader.read().then(function({done, value}){
        if (done) {
          var total = logs.filter(function(l){return l.type === 'lead_saved';}).length;
          progress.innerHTML = '<div style="font-size:12px;color:#16a34a;padding:8px;background:#f0fdf4;border-radius:6px">✅ Batch klaar — ' + total + ' leads opgeslagen</div>';
          btn.disabled = false; btn.textContent = 'Start batch-run';
          loadLeadList();
          return;
        }
        var chunk = decoder.decode(value, {stream: true});
        var lines = chunk.split('\n');
        for (var i = 0; i < lines.length; i++) {
          var line = lines[i].trim();
          if (!line || !line.startsWith('data: ')) continue;
          try {
            var evt = JSON.parse(line.slice(6));
            logs.push(evt);
            if (evt.type === 'lead_saved') {
              var l = evt.lead || {};
              progress.innerHTML += '<div style="display:flex;align-items:center;gap:6px;padding:4px 6px;font-size:11px;border-bottom:1px solid #f1f5f9">' +
                '<span style="color:#16a34a">✓</span>' +
                '<span style="font-weight:500;flex:1">' + escHtml(l.org_name||'?').slice(0,50) + '</span>' +
                '<span style="color:#64748b;font-size:10px">' + (l.score||'') + '</span>' +
                '<span class="badge badge-' + (l.relevance === 'hoog' ? 'live' : 'draft') + '" style="font-size:9px">' + (l.relevance||'') + '</span></div>';
            } else if (evt.type === 'analyzing') {
              var phase = evt.phase === 'scrapen' ? '🔍 Scrapen: ' : '🧠 AI: ';
              progress.innerHTML += '<div style="color:#64748b;font-size:10px;padding:2px 6px">' + phase + escHtml(evt.org||'').slice(0,40) + '</div>';
            } else if (evt.type === 'error' || evt.type === 'batch_done') {
              // handled by done above
            }
          } catch(e) {}
        }
        readChunk();
      }).catch(function(e){
        progress.innerHTML += '<div style="color:#ef4444;font-size:11px;padding:4px">Fout: ' + escHtml(e.message) + '</div>';
        btn.disabled = false; btn.textContent = 'Start batch-run';
      });
    }
    readChunk();
  }).catch(function(e){
    progress.innerHTML = '<div style="color:#ef4444;font-size:11px">Fout: ' + escHtml(e.message) + '</div>';
    btn.disabled = false; btn.textContent = 'Start batch-run';
  });
}

function runCustomBatch() {
  var btn = document.getElementById('batch-btn');
  var progress = document.getElementById('batch-progress');
  var templateEl = document.getElementById('batch-template');
  var customEl = document.getElementById('batch-custom-queries');
  var regioEl = document.getElementById('batch-regio');
  if (!btn || !progress) return;
  btn.disabled = true; btn.textContent = 'Bezig...';
  progress.innerHTML = '<div class="loading"><div class="spinner"></div><p>Batch gestart...</p></div>';

  var body = {max_per_query: 5};
  var regio = regioEl ? regioEl.value.trim() : '';
  if (regio) body.regio = regio;

  if (templateEl && templateEl.value !== 'custom') {
    body.template = templateEl.value;
  } else if (customEl) {
    body.queries = customEl.value.split('\n').map(function(s){return s.trim();}).filter(Boolean);
    body.template = 'custom';
    if (!body.queries.length) {
      progress.innerHTML = '<div style="color:#ef4444;font-size:11px;padding:8px">Voer minimaal 1 query in</div>';
      btn.disabled = false; btn.textContent = 'Start batch';
      return;
    }
  } else {
    body.template = 'weareimpact_ai';
  }

  fetch('/api/leads/batch', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(body)
  }).then(function(r){
    var reader = r.body.getReader();
    var decoder = new TextDecoder();
    function readChunk() {
      reader.read().then(function({done, value}){
        if (done) {
          var total = progress.querySelectorAll('[data-lead]').length || 0;
          progress.innerHTML += '<div style="font-size:12px;color:#16a34a;padding:8px;background:#f0fdf4;border-radius:6px">✅ Batch klaar</div>';
          btn.disabled = false; btn.textContent = 'Start batch';
          loadLeadList();
          return;
        }
        var chunk = decoder.decode(value, {stream: true});
        var lines = chunk.split('\n');
        for (var i = 0; i < lines.length; i++) {
          var line = lines[i].trim();
          if (!line || !line.startsWith('data: ')) continue;
          try {
            var evt = JSON.parse(line.slice(6));
            if (evt.type === 'lead_saved') {
              var l = evt.lead || {};
              progress.innerHTML += '<div data-lead="1" style="display:flex;align-items:center;gap:6px;padding:4px 6px;font-size:11px;border-bottom:1px solid #f1f5f9">' +
                '<span style="color:#16a34a">✓</span>' +
                '<span style="font-weight:500;flex:1">' + escHtml(l.org_name||'?').slice(0,50) + '</span></div>';
            }
          } catch(e) {}
        }
        readChunk();
      }).catch(function(e){
        progress.innerHTML += '<div style="color:#ef4444">Fout: ' + escHtml(e.message) + '</div>';
        btn.disabled = false;
      });
    }
    readChunk();
  }).catch(function(e){
    progress.innerHTML = '<div style="color:#ef4444;font-size:11px">Fout: ' + escHtml(e.message) + '</div>';
    btn.disabled = false;
  });
}

function loadLeadList() {
  var listEl = document.getElementById('lead-list');
  if (!listEl) return;
  var filterEl = document.getElementById('lead-filter-status');
  var status = filterEl ? filterEl.value : '';
  var url = '/api/leads?limit=30';
  if (status) url += '&status=' + encodeURIComponent(status);
  listEl.innerHTML = '<div style="text-align:center;color:#94a3b8;padding:16px;font-size:12px">Laden...</div>';
  fetch(url).then(function(r){return r.json();}).then(function(leads){
    if (!leads || !leads.length) {
      listEl.innerHTML = '<div style="text-align:center;color:#94a3b8;padding:20px;font-size:12px">Geen leads gevonden. Start een LinkedIn-zoekopdracht of batch-run hierboven.</div>';
      return;
    }
    var h = '<table class="data-table"><thead><tr><th>Bedrijf</th><th>Plaats</th><th>Contact</th><th>Type</th><th>Score</th><th>Status</th><th>Acties</th></tr></thead><tbody>';
    leads.forEach(function(l){
      var contacts = l.contacts || [];
      var contactName = contacts.length ? escHtml(contacts[0].naam||'').slice(0,25) : '-';
      var statusBadge = '<span class="badge badge-' + (l.status === 'valid' ? 'live' : l.status === 'contacted' ? 'run' : 'draft') + '">' + escHtml(l.status||'') + '</span>';
      var actionsHtml = '';
      if (l.status === 'new' || l.status === 'enriched') {
        actionsHtml += '<button onclick="enrichLead(\'' + l.id + '\')" style="font-size:10px;padding:2px 6px;background:#e0e7ff;color:#4338ca;border:none;border-radius:3px;cursor:pointer;margin-right:3px">Verrijk</button>';
      }
      var hasEmail = (l.email && l.email !== '') || (contacts.length && contacts[0].email);
      if (hasEmail) {
        actionsHtml += '<button onclick="sendOutreachEmail(\'' + l.id + '\')" style="font-size:10px;padding:2px 6px;background:#dcfce7;color:#16a34a;border:none;border-radius:3px;cursor:pointer">Mail</button>';
      }
      h += '<tr><td style="font-weight:500">' + escHtml(l.org_name||'').slice(0,40) + '</td>' +
        '<td style="font-size:11px;color:#64748b">' + escHtml(l.city||'-') + '</td>' +
        '<td style="font-size:11px;color:#64748b">' + contactName + '</td>' +
        '<td style="font-size:10px;color:#94a3b8">' + escHtml(l.lead_type||'-') + '</td>' +
        '<td style="font-size:11px;font-weight:600;color:' + (l.score >= 70 ? '#16a34a' : l.score >= 40 ? '#ca8a04' : '#94a3b8') + '">' + (l.score||'-') + '</td>' +
        '<td>' + statusBadge + '</td>' +
        '<td>' + actionsHtml + '</td></tr>';
    });
    h += '</tbody></table>';
    listEl.innerHTML = h;
  }).catch(function(e){
    listEl.innerHTML = '<div style="color:#ef4444;font-size:11px;padding:8px">Fout: ' + escHtml(e.message) + '</div>';
  });
}

function enrichLead(leadId) {
  fetch('/api/leads/' + encodeURIComponent(leadId) + '/enrich', {method: 'POST'})
    .then(function(r){return r.json();})
    .then(function(updated){
      loadLeadList();
    })
    .catch(function(e){
      alert('Fout: ' + e.message);
    });
}

function sendOutreachEmail(leadId) {
  if (!confirm('Stuur een persoonlijke outreach-mail naar deze lead? (Hermes genereert de tekst)')) return;
  fetch('/api/leads/' + encodeURIComponent(leadId) + '/outreach-send', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({custom_message: ''})
  })
    .then(function(r){return r.json();})
    .then(function(result){
      if (result.status === 'sent') {
        alert('Mail verstuurd naar ' + result.to + '\n\nOnderwerp: ' + result.subject + '\n\n---\n' + result.body);
        loadLeadList();
      } else {
        var body = result.body ? '\n\n---\n' + result.body : '';
        alert('Let op: mail NIET verstuurd. ' + (result.detail || '') + body);
      }
    })
    .catch(function(e){
      alert('Fout: ' + e.message);
    });
}

// ═══════════════════════════════════════════════════════════════════
//  OPDRACHTEN — interim-vacature zoekagent
// ═══════════════════════════════════════════════════════════════════
async function renderOpdrachtenTab(el) {
  el.innerHTML = '<div class="loading"><div class="spinner"></div><p>Opdrachten laden...</p></div>';
  try {
    var statsResp = await fetch('/api/vacancies/stats');
    var stats = await statsResp.json();
  } catch(e) { el.innerHTML = '<div class="empty-state">Fout: ' + escHtml(e.message) + '</div>'; return; }

  var total = (stats && stats.total) || 0;
  var nieuw = (stats && stats.new) || 0;
  var interesting = (stats && stats.interesting) || 0;
  var applied = (stats && stats.applied) || 0;

  var html = '<h3 style="font-size:15px;font-weight:700;margin-bottom:16px">Opdrachten — Interim-vacature zoekagent</h3>';

  html += '<div class="kpi-grid" style="grid-template-columns:repeat(4,1fr);margin-bottom:16px">' +
    '<div class="kpi-box" style="background:#f0fdf4;border:1px solid #bbf7d0"><div class="kpi-val" style="font-size:22px;color:#16a34a">' + total + '</div><div style="font-size:11px;color:#4ade80;font-weight:500">Totaal gevonden</div></div>' +
    '<div class="kpi-box" style="background:#eff6ff;border:1px solid #bfdbfe"><div class="kpi-val" style="font-size:22px;color:#2563eb">' + nieuw + '</div><div style="font-size:11px;color:#60a5fa;font-weight:500">Nieuw</div></div>' +
    '<div class="kpi-box" style="background:#fefce8;border:1px solid #fde68a"><div class="kpi-val" style="font-size:22px;color:#ca8a04">' + interesting + '</div><div style="font-size:11px;color:#facc15;font-weight:500">Interessant</div></div>' +
    '<div class="kpi-box" style="background:#f5f3ff;border:1px solid #ddd6fe"><div class="kpi-val" style="font-size:22px;color:#7c3aed">' + applied + '</div><div style="font-size:11px;color:#a78bfa;font-weight:500">Gesolliciteerd</div></div>' +
    '</div>';

  html += '<div class="section-card" style="margin-bottom:16px">' +
    '<h4 style="font-size:13px;font-weight:600;margin-bottom:8px">Zoek opdrachten</h4>' +
    '<p style="font-size:11px;color:#64748b;margin-bottom:8px">Doorzoekt LinkedIn Jobs, Freelance.nl, Indeed, BMC.nl en een brede webzoekactie per rol. Draait ook automatisch ma/do 07:00. Vacatures ouder dan 3 weken worden automatisch overgeslagen.</p>' +
    '<button id="opdrachten-search-btn" onclick="runVacancySearch()" style="padding:8px 16px;background:var(--accent);color:#fff;border:none;border-radius:6px;font-size:12px;font-weight:600;cursor:pointer">Zoek opdrachten nu</button>' +
    '<div id="opdrachten-progress" style="margin-top:10px;max-height:220px;overflow-y:auto"></div></div>';

  html += '<div class="section-card">' +
    '<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:8px">' +
    '<h4 style="font-size:13px;font-weight:600">Vacature-overzicht</h4>' +
    '<select id="opdrachten-filter-status" onchange="loadVacancyList()" style="padding:4px 8px;border:1px solid #e2e8f0;border-radius:4px;font-size:11px">' +
    '<option value="">Alle statussen</option>' +
    '<option value="new">Nieuw</option>' +
    '<option value="interesting">Interessant</option>' +
    '<option value="rejected">Afgewezen</option>' +
    '<option value="applied">Gesolliciteerd</option></select></div>' +
    '<div id="opdrachten-list" style="max-height:600px;overflow-y:auto">' +
    '<div style="text-align:center;color:#94a3b8;padding:16px;font-size:12px">Laden...</div></div></div>';

  el.innerHTML = html;
  loadVacancyList();
}

function runVacancySearch() {
  var btn = document.getElementById('opdrachten-search-btn');
  var progress = document.getElementById('opdrachten-progress');
  if (!btn || !progress) return;
  btn.disabled = true; btn.textContent = 'Bezig...';
  progress.innerHTML = '<div class="loading"><div class="spinner"></div><p>Zoekactie gestart...</p></div>';

  fetch('/api/vacancies/search', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({})
  }).then(function(r){
    var reader = r.body.getReader();
    var decoder = new TextDecoder();
    function readChunk() {
      reader.read().then(function({done, value}){
        if (done) {
          progress.innerHTML += '<div style="font-size:12px;color:#16a34a;padding:8px;background:#f0fdf4;border-radius:6px">✅ Zoekactie klaar</div>';
          btn.disabled = false; btn.textContent = 'Zoek opdrachten nu';
          loadVacancyList();
          return;
        }
        var chunk = decoder.decode(value, {stream: true});
        var lines = chunk.split('\n');
        for (var i = 0; i < lines.length; i++) {
          var line = lines[i].trim();
          if (!line || !line.startsWith('data: ')) continue;
          try {
            var evt = JSON.parse(line.slice(6));
            if (evt.type === 'vacancy_saved') {
              var v = evt.vacancy || {};
              progress.innerHTML += '<div data-vacancy="1" style="display:flex;align-items:center;gap:6px;padding:4px 6px;font-size:11px;border-bottom:1px solid #f1f5f9">' +
                '<span style="color:#16a34a">✓</span>' +
                '<span style="font-weight:500;flex:1">' + escHtml(v.title||'?').slice(0,60) + '</span>' +
                '<span style="color:#94a3b8">' + escHtml(v.source||'') + '</span></div>';
            } else if (evt.type === 'query_start') {
              progress.innerHTML += '<div style="font-size:10px;color:#94a3b8;padding:2px 6px">Zoeken: ' + escHtml(evt.role||'') + ' [' + escHtml(evt.source||'') + ']...</div>';
            } else if (evt.type === 'vacancy_skipped_expired') {
              progress.innerHTML += '<div style="display:flex;align-items:center;gap:6px;padding:4px 6px;font-size:11px;border-bottom:1px solid #f1f5f9">' +
                '<span style="color:#f59e0b">⏳</span>' +
                '<span style="flex:1;color:#94a3b8">' + escHtml(evt.title||'?').slice(0,60) + '</span>' +
                '<span style="color:#94a3b8">te oud (' + escHtml(String(evt.posted_days_ago||'')) + 'd)</span></div>';
            }
          } catch(e) {}
        }
        readChunk();
      }).catch(function(e){
        progress.innerHTML += '<div style="color:#ef4444">Fout: ' + escHtml(e.message) + '</div>';
        btn.disabled = false; btn.textContent = 'Zoek opdrachten nu';
      });
    }
    readChunk();
  }).catch(function(e){
    progress.innerHTML = '<div style="color:#ef4444;font-size:11px">Fout: ' + escHtml(e.message) + '</div>';
    btn.disabled = false; btn.textContent = 'Zoek opdrachten nu';
  });
}

function loadVacancyList() {
  var listEl = document.getElementById('opdrachten-list');
  if (!listEl) return;
  var filterEl = document.getElementById('opdrachten-filter-status');
  var status = filterEl ? filterEl.value : '';
  var url = '/api/vacancies';
  if (status) url += '?status=' + encodeURIComponent(status);
  listEl.innerHTML = '<div style="text-align:center;color:#94a3b8;padding:16px;font-size:12px">Laden...</div>';
  fetch(url).then(function(r){return r.json();}).then(function(vacancies){
    if (!vacancies || !vacancies.length) {
      listEl.innerHTML = '<div style="text-align:center;color:#94a3b8;padding:20px;font-size:12px">Geen opdrachten gevonden. Klik op "Zoek opdrachten nu" hierboven.</div>';
      return;
    }
    var h = '<table class="data-table"><thead><tr><th>Titel</th><th>Uren</th><th>Locatie</th><th>Bron</th><th>Geplaatst</th><th>Fit</th><th>Status</th><th>Acties</th></tr></thead><tbody>';
    vacancies.forEach(function(v){
      var statusBadge = '<span class="badge badge-' + (v.status === 'interesting' ? 'live' : v.status === 'applied' ? 'run' : 'draft') + '">' + escHtml(v.status||'') + '</span>';
      var scoreColor = v.fit_score >= 70 ? '#16a34a' : v.fit_score >= 40 ? '#ca8a04' : '#94a3b8';
      var age = (v.posted_days_ago === undefined || v.posted_days_ago === null || v.posted_days_ago < 0) ? 'onbekend' :
        (v.posted_days_ago === 0 ? 'vandaag' : v.posted_days_ago + 'd geleden');
      var actions = '<a href="' + escHtml(v.url||'#') + '" target="_blank" style="font-size:10px;padding:2px 6px;background:#e0e7ff;color:#4338ca;border-radius:3px;text-decoration:none;margin-right:3px">Bekijk</a>';
      if (v.status !== 'interesting') actions += '<button onclick="updateVacancyStatus(\'' + v.id + '\',\'interesting\')" style="font-size:10px;padding:2px 6px;background:#dcfce7;color:#16a34a;border:none;border-radius:3px;cursor:pointer;margin-right:3px">Interessant</button>';
      if (v.status !== 'rejected') actions += '<button onclick="updateVacancyStatus(\'' + v.id + '\',\'rejected\')" style="font-size:10px;padding:2px 6px;background:#fef2f2;color:#dc2626;border:none;border-radius:3px;cursor:pointer;margin-right:3px">Afwijzen</button>';
      if (v.status !== 'applied') actions += '<button onclick="updateVacancyStatus(\'' + v.id + '\',\'applied\')" style="font-size:10px;padding:2px 6px;background:#f5f3ff;color:#7c3aed;border:none;border-radius:3px;cursor:pointer">Gesolliciteerd</button>';
      h += '<tr><td style="font-weight:500" title="' + escHtml(v.fit_rationale||'') + '">' + escHtml(v.title||'').slice(0,60) +
        (v.organization ? '<div style="font-size:10px;color:#94a3b8">' + escHtml(v.organization).slice(0,40) + '</div>' : '') + '</td>' +
        '<td style="font-size:11px;color:#64748b">' + escHtml(v.hours_text||'-') + '</td>' +
        '<td style="font-size:11px;color:#64748b">' + escHtml(v.location||'-') + '</td>' +
        '<td style="font-size:10px;color:#94a3b8">' + escHtml(v.source||'-') + '</td>' +
        '<td style="font-size:10px;color:#94a3b8">' + escHtml(age) + '</td>' +
        '<td style="font-size:11px;font-weight:600;color:' + scoreColor + '">' + (v.fit_score||'-') + '</td>' +
        '<td>' + statusBadge + '</td>' +
        '<td>' + actions + '</td></tr>';
    });
    h += '</tbody></table>';
    listEl.innerHTML = h;
  }).catch(function(e){
    listEl.innerHTML = '<div style="color:#ef4444;font-size:11px;padding:8px">Fout: ' + escHtml(e.message) + '</div>';
  });
}

function updateVacancyStatus(vacancyId, status) {
  fetch('/api/vacancies/' + encodeURIComponent(vacancyId), {
    method: 'PATCH',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({status: status})
  })
    .then(function(r){return r.json();})
    .then(function(){ loadVacancyList(); })
    .catch(function(e){ alert('Fout: ' + e.message); });
}

// ═══════════════════════════════════════════════════════════════════
//  RADAR TAB — Mission Radar / Sky Scanner (concurrenten & trends)
//  Scant elke 4 uur automatisch; hier ook handmatig + acties per signaal
// ═══════════════════════════════════════════════════════════════════
var radarStatusFilter = '';

async function renderRadarTab(el) {
  el.innerHTML = '<div class="loading"><div class="spinner"></div><p>Radar laden...</p></div>';
  var stats, watchlist;
  try {
    var [statsResp, watchResp] = await Promise.all([
      fetch('/api/radar/stats?project=' + encodeURIComponent(currentProject)),
      fetch('/api/radar/watch-list?project=' + encodeURIComponent(currentProject)),
    ]);
    stats = await statsResp.json(); watchlist = await watchResp.json();
  } catch(e) { el.innerHTML = '<div class="empty-state">Fout: ' + escHtml(e.message) + '</div>'; return; }

  var html = '<h3 style="font-size:15px;font-weight:700;margin-bottom:4px">✦ Mission Radar — Sky Scanner</h3>' +
    '<p style="font-size:11px;color:#64748b;margin-bottom:16px">Monitort concurrenten, keywords en RSS-feeds. Draait automatisch elke 4 uur; gevonden trends krijgen een Signal Score + AI-invalshoek en topsignalen landen direct als markdown in je Obsidian-vault (10_Projects/_trends/).</p>';

  html += '<div class="kpi-grid" style="grid-template-columns:repeat(4,1fr);margin-bottom:16px">' +
    '<div class="kpi-box" style="background:#eff6ff;border:1px solid #bfdbfe"><div class="kpi-val" style="font-size:22px;color:#2563eb">' + (stats.new||0) + '</div><div style="font-size:11px;color:#60a5fa;font-weight:500">Nieuwe signalen</div></div>' +
    '<div class="kpi-box" style="background:#f0fdf4;border:1px solid #bbf7d0"><div class="kpi-val" style="font-size:22px;color:#16a34a">' + (stats.converted||0) + '</div><div style="font-size:11px;color:#4ade80;font-weight:500">AEO-aanvallen gestart</div></div>' +
    '<div class="kpi-box" style="background:#fefce8;border:1px solid #fde68a"><div class="kpi-val" style="font-size:22px;color:#ca8a04">' + Math.round(stats.top_score||0) + '</div><div style="font-size:11px;color:#facc15;font-weight:500">Hoogste score</div></div>' +
    '<div class="kpi-box" style="background:#f5f3ff;border:1px solid #ddd6fe"><div class="kpi-val" style="font-size:22px;color:#7c3aed">' + (stats.watch_count||0) + '</div><div style="font-size:11px;color:#a78bfa;font-weight:500">Watchlist-items</div></div>' +
    '</div>';

  // ── Star map (score = hoogte, versheid = rechts) ──
  html += '<div id="radar-skymap"></div>';

  // ── Watchlist ──
  html += '<div class="section-card" style="margin-bottom:16px">' +
    '<h4 style="font-size:13px;font-weight:600;margin-bottom:8px">Watchlist</h4>' +
    '<div style="display:flex;gap:6px;flex-wrap:wrap;margin-bottom:10px">' +
    '<select id="radar-watch-type" style="padding:6px 8px;border:1px solid #e2e8f0;border-radius:6px;font-size:11px;background:#fff"><option value="keyword">Keyword</option><option value="competitor">Concurrent (domein)</option><option value="rss">RSS-feed</option></select>' +
    '<input id="radar-watch-value" placeholder="bv. ai in de zorg — of concurrent.nl" style="flex:1;min-width:200px;padding:6px 10px;border:1px solid #e2e8f0;border-radius:6px;font-size:12px">' +
    '<input id="radar-watch-label" placeholder="label (optioneel)" style="width:140px;padding:6px 10px;border:1px solid #e2e8f0;border-radius:6px;font-size:12px">' +
    '<button onclick="addRadarWatch(this)" style="padding:6px 16px;background:#4f46e5;color:#fff;border:none;border-radius:6px;font-size:12px;font-weight:600;cursor:pointer">+ Toevoegen</button></div>' +
    '<div id="radar-watchlist">' + renderRadarWatchlist(watchlist) + '</div>' +
    '<div style="display:flex;align-items:center;gap:8px;margin-top:10px;padding-top:10px;border-top:1px solid #f1f5f9">' +
    '<button id="radar-scan-btn" onclick="runRadarScan()" style="padding:8px 16px;background:var(--accent);color:#fff;border:none;border-radius:6px;font-size:12px;font-weight:600;cursor:pointer">✦ Scan nu de hemel</button>' +
    '<span style="font-size:10px;color:#94a3b8">Draait ook automatisch elke 4 uur</span>' +
    '<span style="font-size:10px;padding:2px 8px;border-radius:6px;font-weight:600;background:#ecfdf5;color:#16a34a">🤖 Auto-AEO aan</span></div>' +
    '<div id="radar-progress" style="margin-top:8px;max-height:200px;overflow-y:auto"></div></div>';

  // ── Signalen ──
  html += '<div class="section-card">' +
    '<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:8px">' +
    '<h4 style="font-size:13px;font-weight:600">Trending signalen</h4>' +
    '<select id="radar-filter-status" onchange="radarStatusFilter=this.value;loadRadarSignals()" style="padding:4px 8px;border:1px solid #e2e8f0;border-radius:4px;font-size:11px">' +
    '<option value=""' + (radarStatusFilter===''?' selected':'') + '>Alle statussen</option>' +
    '<option value="new"' + (radarStatusFilter==='new'?' selected':'') + '>Nieuw</option>' +
    '<option value="targeted"' + (radarStatusFilter==='targeted'?' selected':'') + '>Getarget</option>' +
    '<option value="converted"' + (radarStatusFilter==='converted'?' selected':'') + '>Geconverteerd</option>' +
    '<option value="dismissed" ' + (radarStatusFilter==='dismissed'?' selected':'') + '>Genegeerd</option>' +
    '<option value="growth" ' + (radarStatusFilter==='growth'?' selected':'') + '>📈 Growth (GSC)</option></select></div>' +
    '<div id="radar-signals"><div style="text-align:center;color:#94a3b8;padding:16px;font-size:12px">Laden...</div></div></div>';

  el.innerHTML = html;
  loadRadarSignals();
}

function renderRadarWatchlist(items) {
  if (!items || !items.length) return '<p style="font-size:11px;color:#94a3b8;padding:4px 0">Nog geen watch-items. Voeg een concurrent-domein, keyword of RSS-feed toe — daarna heeft de scan iets om te monitoren.</p>';
  var typeLabels = { keyword: 'Keyword', competitor: 'Concurrent', rss: 'RSS' };
  var typeColors = { keyword: '#dbeafe;color:#1d4ed8', competitor: '#fee2e2;color:#b91c1c', rss: '#fef3c7;color:#92400e' };
  return items.map(function(w){
    return '<div style="display:flex;align-items:center;gap:8px;padding:5px 6px;font-size:12px;border-bottom:1px solid #f8fafc' + (w.active?'':';opacity:.45') + '">' +
      '<span style="font-size:10px;padding:2px 8px;border-radius:6px;font-weight:600;background:' + (typeColors[w.type]||'#f1f5f9;color:#475569') + '">' + (typeLabels[w.type]||w.type) + '</span>' +
      '<span style="font-weight:500;flex:1">' + escHtml(w.label||w.value) + (w.label && w.label!==w.value ? ' <span style="color:#94a3b8;font-weight:400">(' + escHtml(w.value) + ')</span>' : '') + '</span>' +
      (w.last_scanned_at ? '<span style="font-size:10px;color:#94a3b8">gescand ' + escHtml(w.last_scanned_at.slice(0,16).replace('T',' ')) + '</span>' : '<span style="font-size:10px;color:#cbd5e1">nog niet gescand</span>') +
      '<button onclick="toggleRadarWatch(\'' + w.id + '\',' + (w.active?'false':'true') + ')" style="font-size:10px;padding:2px 8px;background:#f1f5f9;color:#475569;border:none;border-radius:3px;cursor:pointer">' + (w.active?'Pauzeer':'Activeer') + '</button>' +
      '<button onclick="deleteRadarWatch(\'' + w.id + '\')" style="font-size:10px;padding:2px 8px;background:#fef2f2;color:#dc2626;border:none;border-radius:3px;cursor:pointer">✕</button></div>';
  }).join('');
}

async function addRadarWatch(btn) {
  var type = document.getElementById('radar-watch-type').value;
  var value = document.getElementById('radar-watch-value').value.trim();
  var label = document.getElementById('radar-watch-label').value.trim();
  if (!value) { alert('Vul een keyword, domein of feed-url in'); return; }
  btn.disabled = true;
  try {
    var r = await fetch('/api/radar/watch-list', { method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({project: currentProject, label: label, type: type, value: value}) });
    if (!r.ok) { var err = await r.json(); alert('Fout: ' + (err.detail||r.status)); return; }
    var items = await (await fetch('/api/radar/watch-list?project=' + encodeURIComponent(currentProject))).json();
    document.getElementById('radar-watchlist').innerHTML = renderRadarWatchlist(items);
    document.getElementById('radar-watch-value').value = ''; document.getElementById('radar-watch-label').value = '';
  } catch(e) { alert('Fout: ' + e.message); }
  finally { btn.disabled = false; }
}

async function toggleRadarWatch(id, active) {
  try {
    await fetch('/api/radar/watch-list/' + id, { method:'PATCH', headers:{'Content-Type':'application/json'}, body: JSON.stringify({active: active === true || active === 'true'}) });
    var items = await (await fetch('/api/radar/watch-list?project=' + encodeURIComponent(currentProject))).json();
    document.getElementById('radar-watchlist').innerHTML = renderRadarWatchlist(items);
  } catch(e) { alert('Fout: ' + e.message); }
}

async function deleteRadarWatch(id) {
  if (!confirm('Watch-item verwijderen? Bestaande signalen blijven staan.')) return;
  try {
    await fetch('/api/radar/watch-list/' + id, { method:'DELETE' });
    var items = await (await fetch('/api/radar/watch-list?project=' + encodeURIComponent(currentProject))).json();
    document.getElementById('radar-watchlist').innerHTML = renderRadarWatchlist(items);
  } catch(e) { alert('Fout: ' + e.message); }
}

function runRadarScan() {
  var btn = document.getElementById('radar-scan-btn');
  var progress = document.getElementById('radar-progress');
  if (!btn || !progress) return;
  btn.disabled = true; btn.textContent = 'Scannen...';
  progress.innerHTML = '<div class="loading"><div class="spinner"></div><p>Sky-scan gestart...</p></div>';

  fetch('/api/radar/scan', { method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({project: currentProject}) }).then(function(r){
    var reader = r.body.getReader();
    var decoder = new TextDecoder();
    var buffer = '';
    function readChunk() {
      reader.read().then(function(res){
        if (res.done) {
          progress.innerHTML += '<div style="font-size:12px;color:#16a34a;padding:8px;background:#f0fdf4;border-radius:6px">✅ Scan klaar</div>';
          btn.disabled = false; btn.textContent = '✦ Scan nu de hemel';
          loadRadarSignals();
          return;
        }
        buffer += decoder.decode(res.value, {stream: true});
        var lines = buffer.split('\n');
        buffer = lines.pop();
        for (var i = 0; i < lines.length; i++) {
          var line = lines[i].trim();
          if (!line || !line.startsWith('data: ')) continue;
          try {
            var evt = JSON.parse(line.slice(6));
            if (evt.type === 'watch_start') {
              progress.innerHTML += '<div style="font-size:10px;color:#94a3b8;padding:2px 6px">Scannen: ' + escHtml(evt.label||'') + ' [' + escHtml(evt.watch_type||'') + ']...</div>';
            } else if (evt.type === 'analyzing') {
              progress.innerHTML += '<div style="font-size:10px;color:#7c3aed;padding:2px 6px">🧠 AI-invalshoek: ' + escHtml((evt.title||'').slice(0,70)) + '</div>';
            } else if (evt.type === 'watch_done') {
              progress.innerHTML += '<div style="display:flex;gap:6px;padding:4px 6px;font-size:11px;border-bottom:1px solid #f1f5f9"><span style="color:#16a34a">✓</span><span style="flex:1;font-weight:500">' + escHtml(evt.label||'') + '</span><span style="color:#94a3b8">' + (evt.found||0) + ' nieuw · ' + (evt.skipped||0) + ' bekend' + (evt.top_score?' · top '+Math.round(evt.top_score):'') + '</span></div>';
            } else if (evt.type === 'watch_error') {
              progress.innerHTML += '<div style="font-size:11px;color:#ef4444;padding:2px 6px">⚠ ' + escHtml(evt.label||'') + ': ' + escHtml(evt.error||'') + '</div>';
            } else if (evt.type === 'scan_done' && evt.note) {
              progress.innerHTML += '<div style="font-size:11px;color:#92400e;padding:6px;background:#fffbeb;border-radius:6px">' + escHtml(evt.note) + '</div>';
            } else if (evt.type === 'auto_aeo' && evt.count) {
              progress.innerHTML += '<div style="font-size:11px;color:#16a34a;padding:6px;background:#ecfdf5;border-radius:6px">🤖 Auto-AEO: ' + evt.count + ' signaal(len) zelfstandig aangevallen — concepten rollen via de Conveyor naar de Wachtrij.</div>';
            }
            progress.scrollTop = progress.scrollHeight;
          } catch(e) {}
        }
        readChunk();
      }).catch(function(e){
        progress.innerHTML += '<div style="color:#ef4444;font-size:11px">Fout: ' + escHtml(e.message) + '</div>';
        btn.disabled = false; btn.textContent = '✦ Scan nu de hemel';
      });
    }
    readChunk();
  }).catch(function(e){
    progress.innerHTML = '<div style="color:#ef4444;font-size:11px">Fout: ' + escHtml(e.message) + '</div>';
    btn.disabled = false; btn.textContent = '✦ Scan nu de hemel';
  });
}

function renderRadarSkyMap(signals) {
  var vis = signals.filter(function(s){ return s.status !== 'dismissed'; }).slice(0, 60);
  if (vis.length < 2) return '';
  var dots = vis.map(function(s, i){
    var days = (s.published_days_ago === null || s.published_days_ago < 0) ? 7 : Math.min(s.published_days_ago, 14);
    var x = 4 + (1 - days / 14) * 92;                 // vers = rechts
    var y = 8 + (1 - (s.signal_score||0) / 100) * 76; // hoge score = boven
    var size = 5 + (s.signal_score||0) / 14;
    var color = s.status === 'converted' ? '#4ade80' : s.status === 'targeted' ? '#facc15' : '#93c5fd';
    return '<div onclick="var c=document.getElementById(\'radar-sig-' + i + '\');if(c){c.scrollIntoView({behavior:\'smooth\',block:\'center\'});c.style.outline=\'2px solid #4f46e5\';setTimeout(function(){c.style.outline=\'\';},1500);}" title="' + escHtml((s.title||'').slice(0,90)) + ' — score ' + Math.round(s.signal_score||0) + '" ' +
      'style="position:absolute;left:' + x.toFixed(1) + '%;top:' + y.toFixed(1) + '%;width:' + size.toFixed(0) + 'px;height:' + size.toFixed(0) + 'px;border-radius:50%;background:' + color + ';box-shadow:0 0 ' + (size*1.5).toFixed(0) + 'px ' + color + ';cursor:pointer;transform:translate(-50%,-50%)"></div>';
  }).join('');
  return '<div class="section-card" style="margin-bottom:16px;padding:0;overflow:hidden">' +
    '<div style="position:relative;height:150px;background:linear-gradient(180deg,#0f172a,#1e293b)">' + dots +
    '<span style="position:absolute;left:8px;top:6px;font-size:9px;color:#64748b">↑ hogere score</span>' +
    '<span style="position:absolute;right:8px;bottom:4px;font-size:9px;color:#64748b">verser →</span>' +
    '<span style="position:absolute;left:8px;bottom:4px;font-size:9px;color:#64748b"><span style="color:#93c5fd">●</span> nieuw &nbsp;<span style="color:#facc15">●</span> getarget &nbsp;<span style="color:#4ade80">●</span> geconverteerd</span>' +
    '</div></div>';
}

function loadRadarSignals() {
  var listEl = document.getElementById('radar-signals');
  if (!listEl) return;
  var url = '/api/radar/sky?project=' + encodeURIComponent(currentProject);
  if (radarStatusFilter === 'growth') {
    url += '&source=gsc-growth';
  } else if (radarStatusFilter) {
    url += '&status=' + radarStatusFilter;
  }
  listEl.innerHTML = '<div style="text-align:center;color:#94a3b8;padding:16px;font-size:12px">Laden...</div>';
  fetch(url).then(function(r){return r.json();}).then(function(signals){
    window._radarSignals = signals || [];
    var mapEl = document.getElementById('radar-skymap');
    if (mapEl) mapEl.innerHTML = renderRadarSkyMap(window._radarSignals);
    if (!signals || !signals.length) {
      listEl.innerHTML = '<div style="text-align:center;color:#94a3b8;padding:20px;font-size:12px">Nog geen signalen' + (radarStatusFilter?' met deze status':'') + '. Vul de watchlist en klik op "Scan nu de hemel".</div>';
      return;
    }
    var statusBadge = { new: ['Nieuw','#dbeafe;color:#1d4ed8'], targeted: ['Getarget','#fef3c7;color:#92400e'], converted: ['Geconverteerd','#dcfce7;color:#166534'], dismissed: ['Genegeerd','#f1f5f9;color:#64748b'] };
    var h = '';
    signals.forEach(function(s, idx){
      var score = Math.round(s.signal_score||0);
      var scoreColor = score >= 70 ? '#16a34a' : score >= 45 ? '#ca8a04' : '#94a3b8';
      var sb = statusBadge[s.status] || [s.status, '#f1f5f9;color:#64748b'];
      var age = (s.published_days_ago === null || s.published_days_ago < 0) ? '' : (s.published_days_ago === 0 ? 'vandaag' : s.published_days_ago + 'd geleden');
      var titles = s.ai_titles || [];
      h += '<div id="radar-sig-' + idx + '" class="opp-card" style="' + (s.status==='new'?'border-left:3px solid #4f46e5;':'') + 'transition:outline .3s">' +
        '<div style="display:flex;align-items:flex-start;gap:10px">' +
        '<div style="min-width:44px;text-align:center"><div style="font-size:20px;font-weight:700;color:' + scoreColor + '">' + score + '</div><div style="font-size:9px;color:#94a3b8">score</div></div>' +
        '<div style="flex:1">' +
        '<div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:2px">' +
        '<a href="' + escHtml(s.url||'#') + '" target="_blank" style="font-weight:600;font-size:13px;color:#1e293b;text-decoration:none">' + escHtml((s.title||'').slice(0,90)) + ' ↗</a>' +
        '<span style="font-size:10px;padding:2px 8px;border-radius:6px;font-weight:600;background:' + sb[1] + '">' + sb[0] + '</span>' +
        (s.source==='gsc-growth' ? '<span style="font-size:10px;padding:2px 8px;border-radius:6px;font-weight:600;background:#dcfce7;color:#166534">📈 Growth</span>' : '') +
        '</div>' +
        '<div style="font-size:10px;color:#94a3b8">' + escHtml(s.source||'') + (age?' · '+age:'') + ' · keyword: ' + escHtml(s.keyword||'') + (s.obsidian_path ? ' · 📝 in vault' : '') + '</div>' +
        (s.ai_hook ? '<div style="margin-top:6px;font-size:12px;font-weight:600;color:#4338ca">💡 ' + escHtml(s.ai_hook) + '</div>' : '') +
        (s.ai_angle ? '<div style="margin-top:3px;font-size:11px;color:#475569">' + escHtml(s.ai_angle) + '</div>' : '') +
        (titles.length ? '<div style="margin-top:5px">' + titles.map(function(t){ return '<div style="font-size:11px;color:#64748b;padding:1px 0">→ ' + escHtml(t) + '</div>'; }).join('') + '</div>' : '') +
        '</div></div>' +
        '<div style="display:flex;gap:6px;flex-wrap:wrap;margin-top:8px">' +
        ((s.status==='new'||s.status==='targeted') ?
          '<button onclick="radarAeoAttack(\'' + s.id + '\',null,this)" style="padding:5px 14px;background:#4f46e5;color:#fff;border:none;border-radius:6px;font-size:11px;cursor:pointer;font-weight:600">🚀 AEO-aanval (blog+video+Reddit)</button>' +
          '<button onclick="radarWriteArticle(' + idx + ',this)" style="padding:5px 12px;background:#059669;color:#fff;border:none;border-radius:6px;font-size:11px;cursor:pointer;font-weight:600">Schrijf SEO-artikel</button>' +
          '<button onclick="radarAeoAttack(\'' + s.id + '\',[\'video\'],this)" style="padding:5px 12px;background:#fff;color:#7c3aed;border:1.5px solid #a78bfa;border-radius:6px;font-size:10px;cursor:pointer;font-weight:600">🎬 Alleen videoscript</button>' : '') +
        (s.status==='converted' ?
          '<button onclick="radarQueueListicle(\'' + s.id + '\',null,this)" style="padding:5px 14px;background:#059669;color:#fff;border:none;border-radius:6px;font-size:11px;cursor:pointer;font-weight:600">📬 Listicle → wachtrij</button>' : '') +
        '<button onclick="radarNotebookLM(\'' + s.id + '\',this)" style="padding:5px 12px;background:#fff;color:#0369a1;border:1.5px solid #7dd3fc;border-radius:6px;font-size:10px;cursor:pointer;font-weight:600">📓 NotebookLM-pakket</button>' +
        '<button onclick="radarInfographic(\'' + s.id + '\',this)" style="padding:5px 12px;background:#fff;color:#be185d;border:1.5px solid #f9a8d4;border-radius:6px;font-size:10px;cursor:pointer;font-weight:600">🖼️ Infographic</button>' +
        (!s.obsidian_path ? '<button onclick="radarToObsidian(\'' + s.id + '\',this)" style="padding:5px 12px;background:#fff;color:#475569;border:1.5px solid #cbd5e1;border-radius:6px;font-size:10px;cursor:pointer;font-weight:600">→ Vault</button>' : '') +
        (s.status!=='dismissed' ? '<button onclick="radarUpdateStatus(\'' + s.id + '\',\'dismissed\')" style="padding:5px 12px;background:#fff;color:#475569;border:1.5px solid #cbd5e1;border-radius:6px;font-size:10px;cursor:pointer;font-weight:600">✕ Negeren</button>'
          : '<button onclick="radarUpdateStatus(\'' + s.id + '\',\'new\')" style="padding:5px 12px;background:#fff;color:#1e40af;border:1.5px solid #3b82f6;border-radius:6px;font-size:10px;cursor:pointer;font-weight:600">↺ Heropen</button>') +
        '</div></div>';
    });
    listEl.innerHTML = h;
  }).catch(function(e){
    listEl.innerHTML = '<div style="color:#ef4444;font-size:11px;padding:8px">Fout: ' + escHtml(e.message) + '</div>';
  });
}

function radarUpdateStatus(id, status) {
  fetch('/api/radar/signals/' + id, { method:'PATCH', headers:{'Content-Type':'application/json'}, body: JSON.stringify({status: status}) })
    .then(function(){ loadRadarSignals(); })
    .catch(function(e){ alert('Fout: ' + e.message); });
}

function showRadarModal(title, bodyHtml) {
  var overlay = document.getElementById('radar-modal-overlay');
  if (!overlay) {
    overlay = document.createElement('div');
    overlay.id = 'radar-modal-overlay';
    overlay.style.cssText = 'position:fixed;inset:0;background:rgba(15,23,42,.45);display:flex;align-items:center;justify-content:center;z-index:9999';
    overlay.onclick = function(e){ if (e.target === overlay) overlay.remove(); };
    document.body.appendChild(overlay);
  }
  overlay.innerHTML =
    '<div style="background:#fff;border-radius:12px;max-width:440px;width:90%;padding:20px;box-shadow:0 20px 60px rgba(0,0,0,.25)">' +
      '<div style="display:flex;align-items:center;gap:8px;margin-bottom:12px"><span style="font-size:20px">🚀</span>' +
      '<h3 style="margin:0;font-size:15px;color:#1e293b">' + escHtml(title) + '</h3></div>' +
      bodyHtml +
      '<div style="margin-top:16px;text-align:right"><button onclick="document.getElementById(\'radar-modal-overlay\').remove()" ' +
      'style="padding:8px 20px;background:var(--accent);color:#fff;border:none;border-radius:6px;font-size:13px;font-weight:600;cursor:pointer">OK</button></div>' +
    '</div>';
}

async function radarAeoAttack(id, channels, btn) {
  var isFull = !channels;
  if (!confirm(isFull
    ? 'AEO Domination Journey starten?\n\nDit maakt 3 gekoppelde taken aan in de Conveyor:\n1. ~1000-woord listicle (SEO Copywriter)\n2. YouTube/TikTok-script op dezelfde tekst (Video Director)\n3. Reddit-discussiepost-concept (Social Media Copywriter)\n\nAlles zijn concepten — er wordt niets automatisch gepubliceerd.'
    : 'Alleen een videoscript-taak aanmaken in de Conveyor?')) return;
  if (btn) { btn.disabled = true; btn.textContent = 'Taken aanmaken...'; }
  try {
    var r = await fetch('/api/radar/signals/' + id + '/aeo-attack', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({channels: channels}) });
    var data = await r.json();
    if (!r.ok) { alert('Fout: ' + (data.detail||r.status)); return; }
    showRadarModal(data.tasks.length + ' ta' + (data.tasks.length===1?'ak':'ken') + ' aangemaakt in de Conveyor',
      '<div style="font-size:12px;color:#374151;line-height:1.6">' +
      data.tasks.map(function(t){ return '• <b>' + escHtml(t.title) + '</b> <span style="color:#7c3aed">(' + escHtml(t.agent) + ')</span>'; }).join('<br>') +
      '</div><div style="margin-top:8px;font-size:10px;color:#94a3b8">Workspace: ' + escHtml(data.workspace) + '</div>');
    loadRadarSignals();
  } catch(e) { alert('Fout: ' + e.message); }
  finally { if (btn) btn.disabled = false; }
}

async function radarWriteArticle(idx, btn) {
  var s = window._radarSignals && window._radarSignals[idx];
  if (!s) { alert('Signaal niet gevonden'); return; }
  var title = (s.ai_titles && s.ai_titles[0]) || s.ai_angle || s.title;
  if (!confirm('Direct een SEO-artikel schrijven en klaarzetten?\n\n"' + title + '"')) return;
  try {
    var result = await runArticlePipeline({ title: title, rationale: s.ai_hook || 'Trending bij concurrentie (Mission Radar, score ' + Math.round(s.signal_score||0) + ')', keyword: s.keyword || '' }, btn);
    if (result.success) {
      await fetch('/api/radar/signals/' + s.id, { method:'PATCH', headers:{'Content-Type':'application/json'}, body: JSON.stringify({status:'targeted'}) });
      alert('Artikel opgeslagen: ' + result.local_path + '\n' + formatSeoResultMsg(result));
      loadRadarSignals();
    } else alert('Mislukt: ' + (result.detail||'onbekend'));
  } catch(e) { alert('Fout: ' + e.message); }
}

async function radarNotebookLM(id, btn) {
  if (!confirm('NotebookLM-bronpakket genereren?\n\nJe krijgt: brondocument, podcast-dialoogscript (audio overview), infographic-outline en shorts-script — opgeslagen in de vault, klaar om in NotebookLM of een TTS-workflow te plakken.')) return;
  var orig = btn ? btn.textContent : '';
  if (btn) { btn.disabled = true; btn.textContent = 'Genereren... (~30s)'; }
  try {
    var r = await fetch('/api/radar/signals/' + id + '/notebooklm', { method:'POST' });
    var data = await r.json();
    if (!r.ok) { alert('Fout: ' + (data.detail||r.status)); return; }
    alert('📓 NotebookLM-pakket klaar: "' + data.title + '"' + (data.obsidian_path ? '\n\nOpgeslagen in vault: ' + data.obsidian_path : '\n\n(Let op: vault niet geconfigureerd — pakket alleen in deze melding.)'));
  } catch(e) { alert('Fout: ' + e.message); }
  finally { if (btn) { btn.disabled = false; btn.textContent = orig; } }
}

async function radarQueueListicle(id, siteId, btn) {
  if (!siteId && !confirm('Listicle in de publicatie-wachtrij zetten?\n\nDe afgeronde AEO-listicle (Conveyor) wordt omgezet naar een blog-concept met SEO-review, social copy en afbeelding — klaar voor jouw goedkeuring in de Wachtrij-tab.\n\nEr wordt niets automatisch gepubliceerd.')) return;
  var orig = btn ? btn.textContent : '';
  if (btn) { btn.disabled = true; btn.textContent = 'In wachtrij zetten... (~1 min)'; }
  try {
    var r = await fetch('/api/radar/signals/' + id + '/queue-listicle', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({site_id: siteId || null}) });
    var data = await r.json();
    if (r.status === 422 && !siteId) {
      // Meerdere sites mogelijk — laat de gebruiker kiezen en probeer direct opnieuw.
      var sites = await (await fetch('/api/sites')).json();
      if (!sites.length) { alert('Geen sites geconfigureerd — voeg eerst een site toe in de SEO-tab.'); return; }
      var keuze = prompt('Voor welke site?\n\n' + sites.map(function(s,i){ return (i+1) + '. ' + s.name; }).join('\n') + '\n\nTyp het nummer:');
      var pick = sites[parseInt(keuze, 10) - 1];
      if (!pick) return;
      r = await fetch('/api/radar/signals/' + id + '/queue-listicle', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({site_id: pick.id}) });
      data = await r.json();
    }
    if (!r.ok) { alert('Fout: ' + (data.detail||r.status)); return; }
    alert('📬 Listicle staat in de wachtrij voor "' + data.site + '" — beoordeel en publiceer \'m via de Wachtrij-tab.');
  } catch(e) { alert('Fout: ' + e.message); }
  finally { if (btn) { btn.disabled = false; btn.textContent = orig; } }
}

async function radarInfographic(id, btn) {
  if (!confirm('Infographic (PNG, 1080x1350) genereren?\n\nDe AI maakt 5-7 blokken over dit onderwerp en rendert een on-brand afbeelding — direct als download én opgeslagen in de vault.')) return;
  var orig = btn ? btn.textContent : '';
  if (btn) { btn.disabled = true; btn.textContent = 'Renderen... (~20s)'; }
  try {
    var r = await fetch('/api/radar/signals/' + id + '/infographic', { method:'POST' });
    var data = await r.json();
    if (!r.ok) { alert('Fout: ' + (data.detail||r.status)); return; }
    var a = document.createElement('a');
    a.href = 'data:image/png;base64,' + data.png_base64;
    a.download = data.filename || 'infographic.png';
    document.body.appendChild(a); a.click(); a.remove();
    alert('🖼️ Infographic "' + data.title + '" (' + data.blocks + ' blokken) gedownload' + (data.vault_path ? '\nOok in vault: ' + data.vault_path : '') + '.');
  } catch(e) { alert('Fout: ' + e.message); }
  finally { if (btn) { btn.disabled = false; btn.textContent = orig; } }
}

async function radarToObsidian(id, btn) {
  if (btn) btn.disabled = true;
  try {
    var r = await fetch('/api/radar/signals/' + id + '/obsidian', { method:'POST' });
    var data = await r.json();
    if (!r.ok) { alert('Fout: ' + (data.detail||r.status)); return; }
    loadRadarSignals();
  } catch(e) { alert('Fout: ' + e.message); }
  finally { if (btn) btn.disabled = false; }
}

