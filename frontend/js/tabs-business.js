// ── Impact OS — tabs: Leads, Opdrachten, Radar
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
  html += '<div class="kpi-grid" style="margin-bottom:16px">' +
    kpiBox('Totaal leads', total) +
    kpiBox('Verrijkt', enriched) +
    kpiBox('Geverifieerd', valid) +
    kpiBox('Gecontacteerd', contacted) +
    '</div>';

  // ── 1-zin pipeline (video-UX: "type one sentence") ──
  html += '<div class="section-card" style="margin-bottom:16px;border-left:3px solid var(--green)">' +
    '<h4 style="font-size:13px;font-weight:600;margin-bottom:4px">Beschrijf in één zin wie je zoekt</h4>' +
    '<p style="font-size:11px;color:#64748b;margin-bottom:8px">De Hermes Lead Machine: typ wie je wil bereiken. Ze zoekt, verrijkt, scoort, gooit de mismatches weg en haalt voor de beste fits meteen contactgegevens op. Niets gaat uit zonder je goedkeuring.</p>' +
    '<div style="display:flex;gap:8px">' +
    '<input id="describe-sentence" type="text" placeholder="Bijv. SEO agencies die linkbuilding doen" style="flex:1;padding:8px 10px;border:1px solid #e2e8f0;border-radius:6px;font-size:13px">' +
    '<button onclick="runDescribe()" id="describe-btn" class="btn btn-primary" style="background:var(--green)">Vind &amp; verrijk</button></div>' +
    '<div id="describe-progress" style="margin-top:10px;max-height:280px;overflow-y:auto"></div></div>';

  // ── Topleads nu — het antwoord op "wat zijn mijn beste leads", geen tabel ──
  html += '<div class="section-card" style="margin-bottom:16px">' +
    '<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:8px">' +
    '<h4 style="font-size:13px;font-weight:600">Topleads nu</h4>' +
    '<span style="font-size:10px;color:#94a3b8">Score ≥ 70 · nog geen besluit genomen</span></div>' +
    '<div id="top-leads-list">' +
    '<div style="text-align:center;color:#94a3b8;padding:16px;font-size:12px">Laden...</div></div></div>';

  // ── Geavanceerd: losse tools (Quality Gate, LinkedIn, Batch) ──
  html += '<details class="section-card" style="margin-bottom:16px" id="leads-advanced">' +
    '<summary style="font-size:13px;font-weight:600;cursor:pointer">Geavanceerd — losse tools</summary>' +
    '<div style="margin-top:12px">';

  // Quality Gate + funnel
  html += '<div style="padding-bottom:14px;margin-bottom:14px;border-bottom:1px solid #f1f5f9">' +
    '<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:8px">' +
    '<h4 style="font-size:13px;font-weight:600">Quality Gate &amp; Funnel</h4>' +
    '<div style="display:flex;gap:6px">' +
    '<button onclick="runQualityGate(true)" class="btn btn-ghost btn-sm" title="Zie eerst wat er gebeurt">Preview</button>' +
    '<button onclick="runQualityGate(false)" id="qgate-btn" class="btn btn-primary btn-sm" style="background:var(--amber)">Gooi mismatches weg</button></div></div>' +
    '<div id="quality-gate-summary" style="font-size:11px;color:#64748b;margin-bottom:8px">Laad funnel…</div>' +
    '<div id="funnel-viz" style="display:flex;gap:4px;flex-wrap:wrap"></div>' +
    '<div style="margin-top:8px;font-size:10px;color:#94a3b8">Score &lt; 40 → verloren · 40–70 matig · 70–90 scherpe fit (B) · 90+ topfit (A). "Fewer is better."</div>' +
    '</div>';

  // LinkedIn People Search
  html += '<div style="padding-bottom:14px;margin-bottom:14px;border-bottom:1px solid #f1f5f9">' +
    '<h4 style="font-size:13px;font-weight:600;margin-bottom:8px">LinkedIn Personen zoeken</h4>' +
    '<p style="font-size:11px;color:#64748b;margin-bottom:8px">Zoek beslissers/professionals via site:linkedin.com/in. Resultaten worden niet automatisch opgeslagen.</p>' +
    '<div style="display:flex;gap:8px">' +
    '<input id="linkedin-query" type="text" placeholder="Bijv. AI directeur zorginstelling Amsterdam" style="flex:1;padding:8px 10px;border:1px solid #e2e8f0;border-radius:6px;font-size:13px">' +
    '<button onclick="runLinkedinPeopleSearch()" class="btn btn-primary">Zoeken</button></div>' +
    '<div id="linkedin-results" style="margin-top:10px"></div></div>';

  // WeAreImpact Batch Prospecting
  if (currentProject === 'WeAreImpact') {
    html += '<div>' +
      '<h4 style="font-size:13px;font-weight:600;margin-bottom:8px">Batch Prospecting — WeAreImpact</h4>' +
      '<p style="font-size:11px;color:#64748b;margin-bottom:8px">Doorzoek het web met 15 AI-consultancy queries in zorg/welzijn. Vindt bedrijven, scraped websites, AI-analyse, slaat op in DB + Obsidian.</p>' +
      '<div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">' +
      '<input id="batch-regio" type="text" placeholder="Regio (optioneel)" style="width:140px;padding:8px 10px;border:1px solid #e2e8f0;border-radius:6px;font-size:12px">' +
      '<button onclick="runWeAreImpactBatch()" id="batch-btn" class="btn btn-primary" style="background:var(--green)">Start batch-run</button>' +
      '</div>' +
      '<div id="batch-progress" style="margin-top:10px;max-height:300px;overflow-y:auto"></div></div>';
  } else {
    html += '<div>' +
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
      '<button onclick="runCustomBatch()" id="batch-btn" class="btn btn-primary" style="background:var(--green)">Start batch</button></div>' +
      '<div id="batch-progress" style="margin-top:10px;max-height:300px;overflow-y:auto"></div></div>';
  }

  html += '</div></details>';

  // ── Lead lijst ──
  html += '<div class="section-card">' +
    '<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:8px;flex-wrap:wrap;gap:8px">' +
    '<h4 style="font-size:13px;font-weight:600">Lead overzicht</h4>' +
    '<div style="display:flex;gap:6px;align-items:center;flex-wrap:wrap">' +
    '<input id="lead-search" type="text" placeholder="Zoek op bedrijf, contact of e-mail…" oninput="renderLeadRows()" ' +
    'style="padding:4px 8px;border:1px solid #e2e8f0;border-radius:4px;font-size:11px;width:200px">' +
    '<select id="lead-filter-status" onchange="loadLeadList()" style="padding:4px 8px;border:1px solid #e2e8f0;border-radius:4px;font-size:11px">' +
    '<option value="">Alle statussen</option>' +
    '<option value="new">Nieuw</option>' +
    '<option value="enriched">Verrijkt</option>' +
    '<option value="valid">Geverifieerd</option>' +
    '<option value="contacted">Gecontacteerd</option>' +
    '<option value="replied">Reactie</option>' +
    '<option value="outreach_review">Potentiële lead</option></select>' +
    '<a href="/api/leads/export" target="_blank" class="btn btn-ghost btn-sm" style="text-decoration:none">Export Excel</a></div></div>' +
    '<div id="lead-list-count" style="font-size:10px;color:#94a3b8;margin-bottom:6px"></div>' +
    '<div id="lead-list" style="max-height:500px;overflow-y:auto">' +
    '<div style="text-align:center;color:#94a3b8;padding:16px;font-size:12px">Laden...</div></div></div>';

  el.innerHTML = html;
  loadLeadList();
  loadQualityGateSummary();
  loadTopLeads();
}

function loadTopLeads() {
  var el = document.getElementById('top-leads-list');
  if (!el) return;
  fetch('/api/leads/top?limit=10').then(function(r){return r.json();}).then(function(leads){
    if (!leads || !leads.length) {
      el.innerHTML = '<div style="text-align:center;color:#94a3b8;padding:16px;font-size:12px">' +
        'Nog geen scherpe fits klaar. Typ hierboven wie je zoekt.</div>';
      return;
    }
    var h = '';
    leads.forEach(function(l){
      var score = l.quality_score || l.score || 0;
      var label = l.quality_label || (score >= 90 ? 'A' : 'B');
      var contact = l.email || l.phone || 'nog geen contactgegevens';
      h += '<div style="display:flex;align-items:center;gap:8px;padding:8px 6px;border-bottom:1px solid #f1f5f9">' +
        '<span class="pill ' + (label==='A'?'pill-ok':'pill-neutral') + '" style="min-width:20px;text-align:center">' + label + '</span>' +
        '<div style="flex:1;min-width:0">' +
        '<div style="font-weight:500;font-size:12px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">' + escHtml(l.org_name||'?') + '</div>' +
        '<div style="font-size:11px;color:#64748b;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">' + escHtml(contact) + '</div></div>' +
        '<span style="font-size:11px;color:#64748b">' + score + '</span>' +
        '<button onclick="startLeadOutreach(\'' + l.id + '\')" class="btn btn-primary btn-sm">Benader</button>' +
        '</div>';
    });
    el.innerHTML = h;
  }).catch(function(e){ el.innerHTML = '<div style="color:#ef4444;font-size:11px">Fout: ' + escHtml(e.message) + '</div>'; });
}

function startLeadOutreach(leadId) {
  fetch('/api/leads/' + leadId + '/outreach', {method: 'POST'})
    .then(function(r){ if (!r.ok) throw new Error('mislukt'); return r.json(); })
    .then(function(){ loadTopLeads(); loadLeadList(); })
    .catch(function(e){ alert('Kon outreach niet starten: ' + e.message); });
}

function loadQualityGateSummary() {
  var sumEl = document.getElementById('quality-gate-summary');
  var vizEl = document.getElementById('funnel-viz');
  if (!sumEl) return;
  fetch('/api/leads/quality-gate/summary').then(function(r){return r.json();}).then(function(d){
    var labels = d.by_fit_label || {};
    var a = labels['A']||0, b = labels['B']||0, c = labels['C']||0, x = labels['D']||0;
    sumEl.innerHTML = 'Fit-verdeling: <b>' + a + '× A</b> · ' + b + '× B · ' + c + '× C · ' + x + '× D (geen fit). ' +
      'Drempels: min ' + d.thresholds.min + ', target ' + d.thresholds.target + '.';
    if (vizEl) {
      var stages = ['new','enriched','valid','outreach_review','contacted','replied','won','lost'];
      var byStatus = d.by_status || {};
      var stageColors = {new:'#64748b',enriched:'#0ea5e9',valid:'#22c55e','outreach_review':'#f59e0b',contacted:'#3b82f6',replied:'#06b6d4',won:'#16a34a',lost:'#ef4444'};
      var h = '';
      stages.forEach(function(s){
        var n = byStatus[s]||0;
        if (!n && s !== 'new') return;
        h += '<span style="display:inline-flex;align-items:center;gap:4px;font-size:10px;background:' + (stageColors[s]||'#ccc') + '22;color:' + (stageColors[s]||'#999') + ';padding:2px 6px;border-radius:10px;border:1px solid ' + (stageColors[s]||'#ccc') + '44">' +
          s + ' <b>' + n + '</b></span>';
      });
      vizEl.innerHTML = h;
    }
  }).catch(function(e){ sumEl.innerHTML = 'Funnel niet geladen: ' + escHtml(e.message); });
}

function runQualityGate(dryRun) {
  var btn = document.getElementById('qgate-btn');
  if (btn) { btn.disabled = true; btn.textContent = 'Bezig…'; }
  var sumEl = document.getElementById('quality-gate-summary');
  if (sumEl) sumEl.innerHTML = 'Quality Gate draait…';
  fetch('/api/leads/quality-gate?dry_run=' + (dryRun ? 'true' : 'false'), {method: 'POST'})
    .then(function(r){return r.json();})
    .then(function(d){
      var msg = (dryRun ? 'PREVIEW — ' : '') + 'Bevorderd: ' + d.promoted.length +
        ' · weggegooid (geen fit): ' + d.discarded.length + ' · behouden: ' + d.kept.length;
      if (sumEl) sumEl.innerHTML = msg;
      loadQualityGateSummary();
      loadLeadList();
      loadTopLeads();
    })
    .catch(function(e){ if (sumEl) sumEl.innerHTML = 'Fout: ' + escHtml(e.message); })
    .finally(function(){ if (btn) { btn.disabled = false; btn.textContent = 'Gooi mismatches weg'; } });
}

function runDescribe() {
  var input = document.getElementById('describe-sentence');
  var btn = document.getElementById('describe-btn');
  var progress = document.getElementById('describe-progress');
  if (!input || !progress) return;
  var sentence = input.value.trim();
  if (!sentence) { progress.innerHTML = '<div style="color:#ef4444;font-size:11px">Typ eerst wie je zoekt.</div>'; return; }
  btn.disabled = true; btn.textContent = 'Bezig…';
  progress.innerHTML = '<div class="loading"><div class="spinner"></div><p>Pipeline gestart…</p></div>';
  fetch('/api/leads/describe', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({sentence: sentence, max_results: 5, run_quality_gate: true, run_waterfall: true})
  }).then(function(r){
    var reader = r.body.getReader();
    var decoder = new TextDecoder();
    function readChunk() {
      reader.read().then(function(res){
        if (res.done) { loadLeadList(); loadQualityGateSummary(); loadTopLeads(); return; }
        var chunk = decoder.decode(res.value, {stream: true});
        var lines = chunk.split('\n');
        for (var i = 0; i < lines.length; i++) {
          var line = lines[i].trim();
          if (!line || !line.startsWith('data: ')) continue;
          try {
            var evt = JSON.parse(line.slice(6));
            if (evt.type === 'describe') {
              progress.innerHTML += '<div style="font-size:11px;color:#2563eb;padding:3px 6px">Zoekquery: <b>' + escHtml(evt.query) + '</b></div>';
            } else if (evt.type === 'lead_saved') {
              var l = evt.lead || {};
              var fit = l.quality_label || (l.score >= 70 ? 'B' : l.score >= 40 ? 'C' : 'D');
              progress.innerHTML += '<div style="display:flex;align-items:center;gap:6px;padding:4px 6px;font-size:11px;border-bottom:1px solid #f1f5f9">' +
                '<span style="font-weight:500;flex:1">' + escHtml(l.org_name||'?').slice(0,50) + '</span>' +
                '<span style="color:#64748b">' + (l.score||'') + '</span>' +
                '<span class="pill ' + (fit==='A'?'pill-ok':fit==='D'?'pill-danger':'pill-neutral') + '">' + fit + '</span></div>';
            } else if (evt.type === 'quality_gate') {
              progress.innerHTML += '<div style="font-size:11px;color:var(--amber);padding:3px 6px">Quality Gate: ' + evt.promoted + ' bevorderd, ' + evt.discarded + ' weggegooid, ' + evt.kept + ' behouden.</div>';
            } else if (evt.type === 'waterfall_enriched') {
              var wl = evt.lead || {};
              progress.innerHTML += '<div style="font-size:11px;color:#0ea5e9;padding:3px 6px">Contactgegevens gevonden: <b>' + escHtml(wl.org_name||'?') + '</b></div>';
            } else if (evt.type === 'done') {
              progress.innerHTML += '<div style="font-size:12px;color:var(--ok-fg);padding:8px;background:var(--ok-bg);border-radius:6px">' + escHtml(evt.message||'Klaar') + '</div>';
            }
          } catch(e) {}
        }
        readChunk();
      }).catch(function(e){
        progress.innerHTML += '<div style="color:#ef4444;font-size:11px">Fout: ' + escHtml(e.message) + '</div>';
        btn.disabled = false; btn.textContent = 'Vind & verrijk';
      });
    }
    readChunk();
  }).catch(function(e){
    progress.innerHTML = '<div style="color:#ef4444;font-size:11px">Fout: ' + escHtml(e.message) + '</div>';
    btn.disabled = false; btn.textContent = 'Vind & verrijk';
  });
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
          progress.innerHTML = '<div style="font-size:12px;color:var(--ok-fg);padding:8px;background:var(--ok-bg);border-radius:6px">Batch klaar — ' + total + ' leads opgeslagen</div>';
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
                '<span style="font-weight:500;flex:1">' + escHtml(l.org_name||'?').slice(0,50) + '</span>' +
                '<span style="color:#64748b;font-size:10px">' + (l.score||'') + '</span>' +
                '<span class="pill ' + (l.relevance === 'hoog' ? 'pill-ok' : 'pill-neutral') + '">' + escHtml(l.relevance||'') + '</span></div>';
            } else if (evt.type === 'analyzing') {
              var phase = evt.phase === 'scrapen' ? 'Scrapen: ' : 'AI: ';
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
          progress.innerHTML += '<div style="font-size:12px;color:var(--ok-fg);padding:8px;background:var(--ok-bg);border-radius:6px">Batch klaar</div>';
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

var _leadListCache = [];

// Relatieve tijd t.o.v. nu, voor "laatst benaderd"-context onder de status.
function _leadRelTime(iso) {
  if (!iso) return '';
  var then = new Date(iso).getTime();
  if (isNaN(then)) return '';
  var diffMin = Math.round((Date.now() - then) / 60000);
  if (diffMin < 1) return 'zojuist';
  if (diffMin < 60) return diffMin + ' min geleden';
  var diffH = Math.round(diffMin / 60);
  if (diffH < 24) return diffH + ' u geleden';
  var diffD = Math.round(diffH / 24);
  return diffD + ' d geleden';
}

function loadLeadList() {
  var listEl = document.getElementById('lead-list');
  if (!listEl) return;
  var filterEl = document.getElementById('lead-filter-status');
  var status = filterEl ? filterEl.value : '';
  var url = '/api/leads';
  if (status) url += '?status=' + encodeURIComponent(status);
  listEl.innerHTML = '<div style="text-align:center;color:#94a3b8;padding:16px;font-size:12px">Laden...</div>';
  fetch(url).then(function(r){return r.json();}).then(function(leads){
    _leadListCache = leads || [];
    renderLeadRows();
  }).catch(function(e){
    listEl.innerHTML = '<div style="color:#ef4444;font-size:11px;padding:8px">Fout: ' + escHtml(e.message) + '</div>';
  });
}

// Rendert _leadListCache, gefilterd op het zoekveld — géén nieuwe fetch nodig,
// dus zoeken werkt ook meteen na een statuswijziging zonder de hele lijst
// opnieuw op te halen. Dit is ook de plek waar een oude lead (ver terug in
// created_at-volgorde) terug te vinden is zonder te scrollen: typ een deel
// van de bedrijfs- of contactnaam.
function renderLeadRows() {
  var listEl = document.getElementById('lead-list');
  var countEl = document.getElementById('lead-list-count');
  if (!listEl) return;
  var searchEl = document.getElementById('lead-search');
  var term = searchEl ? searchEl.value.trim().toLowerCase() : '';
  var leads = _leadListCache;
  if (term) {
    leads = leads.filter(function(l){
      var contacts = l.contacts || [];
      var haystack = [
        l.org_name||'', l.city||'', l.email||'',
        contacts.length ? (contacts[0].naam||'') : '',
        contacts.length ? (contacts[0].email||'') : '',
      ].join(' ').toLowerCase();
      return haystack.indexOf(term) !== -1;
    });
  }
  if (countEl) {
    countEl.textContent = term
      ? leads.length + ' van ' + _leadListCache.length + ' leads gevonden voor "' + term + '"'
      : (_leadListCache.length ? _leadListCache.length + ' leads' : '');
  }
  if (!leads.length) {
    listEl.innerHTML = '<div style="text-align:center;color:#94a3b8;padding:20px;font-size:12px">' +
      (term ? 'Geen leads gevonden voor "' + escHtml(term) + '".' :
        'Geen leads gevonden. Start een LinkedIn-zoekopdracht of batch-run hierboven.') + '</div>';
    return;
  }
  var h = '<table class="data-table"><thead><tr><th>Bedrijf</th><th>Plaats</th><th>Contact</th><th>Type</th><th>Score</th><th>Status</th><th>Acties</th></tr></thead><tbody>';
  leads.forEach(function(l){
    var contacts = l.contacts || [];
    var contactName = contacts.length ? escHtml(contacts[0].naam||'').slice(0,25) : '-';
    var statusMap = {
      'new': {label: 'Nieuw', cls: 'pill-neutral'},
      'enriched': {label: 'Verrijkt', cls: 'pill-neutral'},
      'valid': {label: 'Geverifieerd', cls: 'pill-ok'},
      'contacted': {label: 'Gecontacteerd', cls: 'pill-info'},
      'replied': {label: 'Reactie', cls: 'pill-info'},
      'outreach_review': {label: 'Potentiële lead', cls: 'pill-warn'},
      'won': {label: 'Gewonnen', cls: 'pill-ok'},
      'lost': {label: 'Verloren', cls: 'pill-danger'}
    };
    var _s = statusMap[l.status] || {label: l.status||'', cls: 'pill-neutral'};
    var statusBadge = '<span class="pill ' + _s.cls + '">' + escHtml(_s.label) + '</span>';
    // Laat zien wanneer en welke funnel-stap het laatst gestempeld is, zodat
    // een statuswijziging na een klik zichtbaar blijft zonder in de database
    // te hoeven kijken — dit was exact het gat dat "waar is deze lead gebleven"
    // veroorzaakte (22 aug 2026).
    var lastStamp = '';
    if (l.replied_at) lastStamp = 'Reactie ' + _leadRelTime(l.replied_at);
    else if (l.contacted_at) lastStamp = 'Benaderd ' + _leadRelTime(l.contacted_at);
    if (lastStamp) statusBadge += '<div style="font-size:9px;color:#94a3b8;margin-top:2px">' + escHtml(lastStamp) + '</div>';
    // Opvolgconcept: los van de funnel-status (blijft bv. 'contacted'), dus
    // een eigen badge i.p.v. te leunen op statusMap (26 aug 2026 — dit stond
    // voorheen alleen in het Actiecentrum, followup_review had geen eigen
    // scherm en verdween daar nu zonder vervanging als dat niet hier kwam).
    if (l.followup_draft) statusBadge += '<div style="margin-top:2px"><span class="pill pill-warn" style="font-size:9px">Opvolging klaar</span></div>';
    var actionsHtml = '';
    if (l.status === 'new' || l.status === 'enriched') {
      actionsHtml += '<button onclick="enrichLead(\'' + l.id + '\')" class="btn btn-ghost btn-sm" style="margin-right:3px">Verrijk</button>';
    }
    var hasEmail = (l.email && l.email !== '') || (contacts.length && contacts[0].email);
    if (hasEmail) {
      actionsHtml += '<button onclick="sendOutreachEmail(\'' + l.id + '\')" class="btn btn-primary btn-sm" style="background:var(--green)">Mail</button>';
    }
    // Iris' onderzoeksverslag (o.a. Impact Calculator-leads): apart van het
    // outreach-concept hieronder, want dat gaat over de mailtekst, dit gaat
    // over wie de lead is (23 aug 2026: dit stond nergens in de UI, alleen
    // in de mail en de database).
    var summaryHtml = '';
    if (l.summary) {
      var summaryId = 'lead-summary-' + l.id;
      actionsHtml += '<button onclick="toggleLeadDetail(\'' + summaryId + '\')" class="btn btn-ghost btn-sm" style="margin-right:3px">Verslag</button>';
      summaryHtml = '<tr id="' + summaryId + '" style="display:none"><td colspan="7" style="background:#f8fafc;padding:10px 12px">' +
        '<div style="font-size:10px;font-weight:600;color:#64748b;margin-bottom:4px;text-transform:uppercase;letter-spacing:.03em">Iris\' verslag</div>' +
        '<pre style="white-space:pre-wrap;font-size:11px;line-height:1.5;background:#fff;border:1px solid #e2e8f0;border-radius:6px;padding:8px;margin:0;font-family:inherit">' + escHtml(l.summary) + '</pre>' +
        '</td></tr>';
    }
    // Potentiële lead (outreach_review): concept klaar — uitklapbare details + verzendklik.
    var detailsHtml = '';
    if (l.status === 'outreach_review') {
      var detailId = 'lead-detail-' + l.id;
      var subject = l.outreach_subject || '';
      var draft = l.outreach_draft || '';
      var targetEmail = l.email || (contacts.length ? contacts[0].email : '') || '';
      actionsHtml += '<button onclick="toggleLeadDetail(\'' + detailId + '\')" class="btn btn-ghost btn-sm" style="margin-right:3px">Meer details</button>';
      actionsHtml += '<button onclick="approveLeadOutreach(\'' + l.id + '\')" class="btn btn-primary btn-sm" style="background:var(--green);margin-right:3px">Verstuur</button>';
      actionsHtml += '<button onclick="dismissLeadOutreach(\'' + l.id + '\')" class="btn btn-danger-outline btn-sm">Wijs af</button>';
      detailsHtml = '<tr id="' + detailId + '" style="display:none"><td colspan="7" style="background:#fffbeb;padding:10px 12px">' +
        (subject ? '<div style="font-size:12px;font-weight:600;color:#92400e;margin-bottom:4px">Onderwerp: ' + escHtml(subject) + '</div>' : '') +
        (targetEmail ? '<div style="font-size:11px;color:#64748b;margin-bottom:6px">Aan: ' + escHtml(targetEmail) + '</div>' : '') +
        '<pre style="white-space:pre-wrap;font-size:11px;line-height:1.5;background:#fff;border:1px solid #fde68a;border-radius:6px;padding:8px;margin:0">' + escHtml(draft) + '</pre>' +
        '</td></tr>';
    }
    // Opvolgconcept (leads die na outreach stil bleven): eigen blok, want een
    // lead kan tegelijk 'outreach_review' zijn afgerond (status verder) én een
    // opvolgconcept hebben liggen — deze twee zijn onafhankelijk van elkaar.
    var followupDetailsHtml = '';
    if (l.followup_draft) {
      var fuDetailId = 'lead-followup-' + l.id;
      var fuSubject = l.followup_subject || '';
      var fuDraft = l.followup_draft || '';
      actionsHtml += '<button onclick="toggleLeadDetail(\'' + fuDetailId + '\')" class="btn btn-ghost btn-sm" style="margin-right:3px">Opvolging bekijken</button>';
      actionsHtml += '<button onclick="approveLeadFollowup(\'' + l.id + '\')" class="btn btn-primary btn-sm" style="background:var(--green);margin-right:3px">Verstuur opvolging</button>';
      actionsHtml += '<button onclick="skipLeadFollowup(\'' + l.id + '\')" class="btn btn-ghost btn-sm">Sla over</button>';
      followupDetailsHtml = '<tr id="' + fuDetailId + '" style="display:none"><td colspan="7" style="background:#fffbeb;padding:10px 12px">' +
        (fuSubject ? '<div style="font-size:12px;font-weight:600;color:#92400e;margin-bottom:4px">Onderwerp: ' + escHtml(fuSubject) + '</div>' : '') +
        '<pre style="white-space:pre-wrap;font-size:11px;line-height:1.5;background:#fff;border:1px solid #fde68a;border-radius:6px;padding:8px;margin:0">' + escHtml(fuDraft) + '</pre>' +
        '</td></tr>';
    }
    h += '<tr><td style="font-weight:500">' + escHtml(l.org_name||'').slice(0,40) + '</td>' +
      '<td style="font-size:11px;color:#64748b">' + escHtml(l.city||'-') + '</td>' +
      '<td style="font-size:11px;color:#64748b">' + contactName + '</td>' +
      '<td style="font-size:10px;color:#94a3b8">' + escHtml(l.lead_type||'-') + '</td>' +
      '<td style="font-size:11px;font-weight:600;color:' + (l.score >= 70 ? 'var(--green)' : l.score >= 40 ? 'var(--amber)' : 'var(--text-muted)') + '">' + (l.score||'-') + '</td>' +
      '<td>' + statusBadge + '</td>' +
      '<td>' + actionsHtml + '</td></tr>' +
      summaryHtml + detailsHtml + followupDetailsHtml;
  });
  h += '</tbody></table>';
  listEl.innerHTML = h;
}

function enrichLead(leadId) {
  fetch('/api/leads/' + encodeURIComponent(leadId) + '/enrich', {method: 'POST'})
    .then(function(r){return r.json();})
    .then(function(updated){
      loadLeadList();
    })
    .catch(function(e){
      showToast('Fout: ' + e.message, 'error');
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
        showToast('Mail verstuurd naar ' + result.to + ' — ' + result.subject, 'ok');
        loadLeadList();
      } else {
        showToast('Mail NIET verstuurd: ' + (result.detail || 'onbekende fout'), 'error');
      }
    })
    .catch(function(e){
      showToast('Fout: ' + e.message, 'error');
    });
}

// ── Potentiële lead (outreach_review) — details uitklappen + verzendklik ──
function toggleLeadDetail(detailId) {
  var row = document.getElementById(detailId);
  if (row) row.style.display = (row.style.display === 'none' ? 'table-row' : 'none');
}

function approveLeadOutreach(leadId) {
  if (!confirm('Deze outreach-mail wordt ECHT verstuurd. Doorgaan?')) return;
  fetch('/api/leads/' + encodeURIComponent(leadId) + '/outreach-approve', {method: 'POST'})
    .then(function(r){return r.json();})
    .then(function(result){
      if (result.status === 'sent') {
        showToast('Verstuurd naar ' + result.to + ' — ' + result.subject, 'ok');
      } else {
        showToast('Versturen mislukt: ' + (result.detail || 'onbekend'), 'error');
      }
      loadLeadList();
    })
    .catch(function(e){ showToast('Fout: ' + e.message, 'error'); });
}

function dismissLeadOutreach(leadId) {
  if (!confirm('Concept afwijzen? De lead gaat naar "verloren".')) return;
  fetch('/api/leads/' + encodeURIComponent(leadId) + '/outreach-dismiss', {method: 'POST'})
    .then(function(){ showToast('Concept afgewezen — lead staat op verloren.', 'info'); loadLeadList(); })
    .catch(function(e){ showToast('Fout: ' + e.message, 'error'); });
}

function approveLeadFollowup(leadId) {
  if (!confirm('Deze opvolgmail wordt ECHT verstuurd. Doorgaan?')) return;
  fetch('/api/leads/' + encodeURIComponent(leadId) + '/followup-approve', {method: 'POST'})
    .then(function(r){return r.json();})
    .then(function(result){
      if (result.status === 'sent') {
        showToast('Opvolging verstuurd naar ' + result.to + ' — ' + result.subject, 'ok');
      } else {
        showToast('Versturen mislukt: ' + (result.detail || 'onbekend'), 'error');
      }
      loadLeadList();
    })
    .catch(function(e){ showToast('Fout: ' + e.message, 'error'); });
}

function skipLeadFollowup(leadId) {
  fetch('/api/leads/' + encodeURIComponent(leadId) + '/followup-dismiss', {method: 'POST'})
    .then(function(){ showToast('Opvolging overgeslagen.', 'info'); loadLeadList(); })
    .catch(function(e){ showToast('Fout: ' + e.message, 'error'); });
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

  html += '<div class="kpi-grid" style="margin-bottom:16px">' +
    kpiBox('Totaal gevonden', total) +
    kpiBox('Nieuw', nieuw) +
    kpiBox('Interessant', interesting) +
    kpiBox('Gesolliciteerd', applied) +
    '</div>';

  html += '<div class="section-card" style="margin-bottom:16px">' +
    '<h4 style="font-size:13px;font-weight:600;margin-bottom:8px">Zoek opdrachten</h4>' +
    '<p style="font-size:11px;color:#64748b;margin-bottom:8px">Doorzoekt LinkedIn Jobs, Freelance.nl, Indeed, BMC.nl en een brede webzoekactie per rol. Draait ook automatisch ma/do 07:00. Vacatures ouder dan 3 weken worden automatisch overgeslagen.</p>' +
    '<button id="opdrachten-search-btn" onclick="runVacancySearch()" class="btn btn-primary">Zoek opdrachten nu</button>' +
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
          progress.innerHTML += '<div style="font-size:12px;color:var(--ok-fg);padding:8px;background:var(--ok-bg);border-radius:6px">Zoekactie klaar</div>';
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
                '<span style="font-weight:500;flex:1">' + escHtml(v.title||'?').slice(0,60) + '</span>' +
                '<span style="color:#94a3b8">' + escHtml(v.source||'') + '</span></div>';
            } else if (evt.type === 'query_start') {
              progress.innerHTML += '<div style="font-size:10px;color:#94a3b8;padding:2px 6px">Zoeken: ' + escHtml(evt.role||'') + ' [' + escHtml(evt.source||'') + ']...</div>';
            } else if (evt.type === 'vacancy_skipped_expired') {
              progress.innerHTML += '<div style="display:flex;align-items:center;gap:6px;padding:4px 6px;font-size:11px;border-bottom:1px solid #f1f5f9">' +
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
      var statusBadge = '<span class="pill ' + (v.status === 'interesting' ? 'pill-ok' : v.status === 'applied' ? 'pill-info' : 'pill-neutral') + '">' + escHtml(v.status||'') + '</span>';
      var scoreColor = v.fit_score >= 70 ? 'var(--green)' : v.fit_score >= 40 ? 'var(--amber)' : 'var(--text-muted)';
      var age = (v.posted_days_ago === undefined || v.posted_days_ago === null || v.posted_days_ago < 0) ? 'onbekend' :
        (v.posted_days_ago === 0 ? 'vandaag' : v.posted_days_ago + 'd geleden');
      var actions = '<a href="' + escHtml(v.url||'#') + '" target="_blank" class="btn btn-ghost btn-sm" style="text-decoration:none;margin-right:3px">Bekijk</a>';
      if (v.status !== 'interesting') actions += '<button onclick="updateVacancyStatus(\'' + v.id + '\',\'interesting\')" class="btn btn-primary btn-sm" style="background:var(--green);margin-right:3px">Interessant</button>';
      if (v.status !== 'rejected') actions += '<button onclick="updateVacancyStatus(\'' + v.id + '\',\'rejected\')" class="btn btn-danger-outline btn-sm" style="margin-right:3px">Afwijzen</button>';
      if (v.status !== 'applied') actions += '<button onclick="updateVacancyStatus(\'' + v.id + '\',\'applied\')" class="btn btn-ghost btn-sm">Gesolliciteerd</button>';
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

  var html = '<h3 style="font-size:15px;font-weight:700;margin-bottom:4px">Mission Radar — Sky Scanner</h3>' +
    '<p style="font-size:11px;color:#64748b;margin-bottom:16px">Monitort concurrenten, keywords en RSS-feeds. Draait automatisch elke 4 uur; gevonden trends krijgen een Signal Score + AI-invalshoek en topsignalen landen direct als markdown in je Obsidian-vault (10_Projects/_trends/).</p>';

  html += '<div class="kpi-grid" style="margin-bottom:16px">' +
    kpiBox('Nieuwe signalen', stats.new||0) +
    kpiBox('AEO-aanvallen gestart', stats.converted||0) +
    kpiBox('Hoogste score', Math.round(stats.top_score||0)) +
    kpiBox('Watchlist-items', stats.watch_count||0) +
    '</div>';

  // ── Star map (score = hoogte, versheid = rechts) ──
  html += '<div id="radar-skymap"></div>';

  // ── Astros Momentum-paneel (echte trend over meerdere scans) ──
  html += '<div class="section-card" style="margin-bottom:16px">' +
    '<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:8px">' +
    '<h4 style="font-size:13px;font-weight:600">🚀 Astros Momentum — wat stijgt er nu écht</h4>' +
    '<button onclick="loadAstrosMomentum(this)" class="btn btn-ghost btn-sm">↻ Ververs</button></div>' +
    '<div id="astros-momentum" style="font-size:12px;color:#94a3b8">Laden...</div></div>';

  // ── Watchlist ──
  html += '<div class="section-card" style="margin-bottom:16px">' +
    '<h4 style="font-size:13px;font-weight:600;margin-bottom:8px">Watchlist</h4>' +
    '<div style="display:flex;gap:6px;flex-wrap:wrap;margin-bottom:10px">' +
    '<select id="radar-watch-type" style="padding:6px 8px;border:1px solid #e2e8f0;border-radius:6px;font-size:11px;background:#fff"><option value="keyword">Keyword</option><option value="competitor">Concurrent (domein)</option><option value="brand_mention">Merkvermelding (PR)</option><option value="rss">RSS-feed</option><option value="youtube">YouTube-creator (kanaal/@[at]handle)</option><option value="reddit">Reddit (r/sub of u/user)</option></select>' +
    '<input id="radar-watch-value" placeholder="bv. ai in de zorg — of concurrent.nl" style="flex:1;min-width:200px;padding:6px 10px;border:1px solid #e2e8f0;border-radius:6px;font-size:12px">' +
    '<input id="radar-watch-label" placeholder="label (optioneel)" style="width:140px;padding:6px 10px;border:1px solid #e2e8f0;border-radius:6px;font-size:12px">' +
    '<button onclick="addRadarWatch(this)" class="btn btn-primary btn-sm">+ Toevoegen</button></div>' +
    '<div id="radar-watchlist">' + renderRadarWatchlist(watchlist) + '</div>' +
    '<div style="display:flex;align-items:center;gap:8px;margin-top:10px;padding-top:10px;border-top:1px solid #f1f5f9">' +
    '<button id="radar-scan-btn" onclick="runRadarScan()" class="btn btn-primary">Scan nu de hemel</button>' +
    '<span style="font-size:10px;color:#94a3b8">Draait ook automatisch elke 4 uur</span>' +
    '<span class="pill pill-ok">Auto-AEO aan</span></div>' +
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
    '<option value="growth" ' + (radarStatusFilter==='growth'?' selected':'') + '>Growth (GSC)</option></select></div>' +
    '<div id="radar-signals"><div style="text-align:center;color:#94a3b8;padding:16px;font-size:12px">Laden...</div></div></div>';

  el.innerHTML = html;
  loadRadarSignals();
  loadAstrosMomentum();
}

function loadAstrosMomentum(btn) {
  var box = document.getElementById('astros-momentum');
  if (!box) return;
  if (btn) { btn.disabled = true; }
  fetch('/api/radar/momentum?project=' + encodeURIComponent(currentProject) + '&limit=12')
    .then(function(r){ return r.json(); }).then(function(rows){
      if (!rows || !rows.length) {
        box.innerHTML = '<span style="color:#94a3b8">Nog geen momentum-data. Na 2+ scans van dezelfde signalen verschijnt hier wat er écht stijgt (exploding / rising / cooling).</span>';
        return;
      }
      var trendInfo = {
        exploding: ['EXPLODING', 'pill-danger'],
        rising: ['RISING', 'pill-warn'],
        steady: ['STEADY', 'pill-neutral'],
        cooling: ['COOLING', 'pill-info'],
        new: ['NIEUW', 'pill-info']
      };
      box.innerHTML = rows.map(function(m){
        var t = trendInfo[m.trend] || ['—', 'pill-neutral'];
        var mi = Math.round(m.momentum_index || 0);
        var title = (m.title || '(geen titel)');
        return '<div style="display:flex;align-items:center;gap:8px;padding:5px 4px;font-size:12px;border-bottom:1px solid #f8fafc">' +
          '<span class="pill ' + t[1] + '" style="min-width:74px;text-align:center">' + t[0] + '</span>' +
          '<span style="font-weight:600;flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">' +
            '<a href="' + escHtml(m.url || '#') + '" target="_blank" style="color:#1e293b;text-decoration:none">' + escHtml(title) + '</a></span>' +
          '<span style="font-size:11px;color:#64748b">momentum ' + mi + '</span>' +
          '</div>';
      }).join('');
    }).catch(function(e){
      box.innerHTML = '<span style="color:#ef4444">Fout: ' + escHtml(e.message) + '</span>';
    }).finally(function(){
      if (btn) { btn.disabled = false; }
    });
}

function renderRadarWatchlist(items) {
  if (!items || !items.length) return '<p style="font-size:11px;color:#94a3b8;padding:4px 0">Nog geen watch-items. Voeg een concurrent-domein, keyword of RSS-feed toe — daarna heeft de scan iets om te monitoren.</p>';
  var typeLabels = { keyword: 'Keyword', competitor: 'Concurrent', rss: 'RSS' };
  var typePills = { keyword: 'pill-info', competitor: 'pill-danger', rss: 'pill-warn' };
  return items.map(function(w){
    return '<div style="display:flex;align-items:center;gap:8px;padding:5px 6px;font-size:12px;border-bottom:1px solid #f8fafc' + (w.active?'':';opacity:.45') + '">' +
      '<span class="pill ' + (typePills[w.type]||'pill-neutral') + '">' + escHtml(typeLabels[w.type]||w.type) + '</span>' +
      '<span style="font-weight:500;flex:1">' + escHtml(w.label||w.value) + (w.label && w.label!==w.value ? ' <span style="color:#94a3b8;font-weight:400">(' + escHtml(w.value) + ')</span>' : '') + '</span>' +
      (w.last_scanned_at ? '<span style="font-size:10px;color:#94a3b8">gescand ' + escHtml(w.last_scanned_at.slice(0,16).replace('T',' ')) + '</span>' : '<span style="font-size:10px;color:#cbd5e1">nog niet gescand</span>') +
      '<button onclick="toggleRadarWatch(\'' + w.id + '\',' + (w.active?'false':'true') + ')" class="btn btn-ghost btn-sm">' + (w.active?'Pauzeer':'Activeer') + '</button>' +
      '<button onclick="deleteRadarWatch(\'' + w.id + '\')" class="btn btn-danger-outline btn-sm">Verwijder</button></div>';
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
          progress.innerHTML += '<div style="font-size:12px;color:var(--ok-fg);padding:8px;background:var(--ok-bg);border-radius:6px">Scan klaar</div>';
          btn.disabled = false; btn.textContent = 'Scan nu de hemel';
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
              progress.innerHTML += '<div style="font-size:10px;color:#7c3aed;padding:2px 6px">AI-invalshoek: ' + escHtml((evt.title||'').slice(0,70)) + '</div>';
            } else if (evt.type === 'watch_done') {
              progress.innerHTML += '<div style="display:flex;gap:6px;padding:4px 6px;font-size:11px;border-bottom:1px solid #f1f5f9"><span style="flex:1;font-weight:500">' + escHtml(evt.label||'') + '</span><span style="color:#94a3b8">' + (evt.found||0) + ' nieuw · ' + (evt.skipped||0) + ' bekend' + (evt.top_score?' · top '+Math.round(evt.top_score):'') + '</span></div>';
            } else if (evt.type === 'watch_error') {
              progress.innerHTML += '<div style="font-size:11px;color:var(--danger-fg);padding:2px 6px">' + escHtml(evt.label||'') + ': ' + escHtml(evt.error||'') + '</div>';
            } else if (evt.type === 'scan_done' && evt.note) {
              progress.innerHTML += '<div style="font-size:11px;color:var(--warn-fg);padding:6px;background:var(--warn-bg);border-radius:6px">' + escHtml(evt.note) + '</div>';
            } else if (evt.type === 'auto_aeo' && evt.count) {
              progress.innerHTML += '<div style="font-size:11px;color:var(--ok-fg);padding:6px;background:var(--ok-bg);border-radius:6px">Auto-AEO: ' + evt.count + ' signaal(len) zelfstandig aangevallen — concepten rollen via de Conveyor naar de Wachtrij.</div>';
            }
            progress.scrollTop = progress.scrollHeight;
          } catch(e) {}
        }
        readChunk();
      }).catch(function(e){
        progress.innerHTML += '<div style="color:#ef4444;font-size:11px">Fout: ' + escHtml(e.message) + '</div>';
        btn.disabled = false; btn.textContent = 'Scan nu de hemel';
      });
    }
    readChunk();
  }).catch(function(e){
    progress.innerHTML = '<div style="color:#ef4444;font-size:11px">Fout: ' + escHtml(e.message) + '</div>';
    btn.disabled = false; btn.textContent = 'Scan nu de hemel';
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
    var color = s.status === 'converted' ? 'var(--green)' : s.status === 'targeted' ? 'var(--amber)' : '#93c5fd';
    return '<div onclick="var c=document.getElementById(\'radar-sig-' + i + '\');if(c){c.scrollIntoView({behavior:\'smooth\',block:\'center\'});c.style.outline=\'2px solid #4f46e5\';setTimeout(function(){c.style.outline=\'\';},1500);}" title="' + escHtml((s.title||'').slice(0,90)) + ' — score ' + Math.round(s.signal_score||0) + '" ' +
      'style="position:absolute;left:' + x.toFixed(1) + '%;top:' + y.toFixed(1) + '%;width:' + size.toFixed(0) + 'px;height:' + size.toFixed(0) + 'px;border-radius:50%;background:' + color + ';box-shadow:0 0 ' + (size*1.5).toFixed(0) + 'px ' + color + ';cursor:pointer;transform:translate(-50%,-50%)"></div>';
  }).join('');
  return '<div class="section-card" style="margin-bottom:16px;padding:0;overflow:hidden">' +
    '<div style="position:relative;height:150px;background:linear-gradient(180deg,#0f172a,#1e293b)">' + dots +
    '<span style="position:absolute;left:8px;top:6px;font-size:9px;color:#64748b">↑ hogere score</span>' +
    '<span style="position:absolute;right:8px;bottom:4px;font-size:9px;color:#64748b">verser →</span>' +
    '<span style="position:absolute;left:8px;bottom:4px;font-size:9px;color:#64748b"><span style="color:#93c5fd">●</span> nieuw &nbsp;<span style="color:var(--amber)">●</span> getarget &nbsp;<span style="color:var(--green)">●</span> geconverteerd</span>' +
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
    var statusBadge = { new: ['Nieuw','pill-info'], targeted: ['Getarget','pill-warn'], converted: ['Geconverteerd','pill-ok'], dismissed: ['Genegeerd','pill-neutral'] };
    var h = '';
    signals.forEach(function(s, idx){
      var score = Math.round(s.signal_score||0);
      var scoreColor = score >= 70 ? 'var(--green)' : score >= 45 ? 'var(--amber)' : 'var(--text-muted)';
      var sb = statusBadge[s.status] || [s.status, 'pill-neutral'];
      var age = (s.published_days_ago === null || s.published_days_ago < 0) ? '' : (s.published_days_ago === 0 ? 'vandaag' : s.published_days_ago + 'd geleden');
      var titles = s.ai_titles || [];
      h += '<div id="radar-sig-' + idx + '" class="opp-card" style="' + (s.status==='new'?'border-left:3px solid #4f46e5;':'') + 'transition:outline .3s">' +
        '<div style="display:flex;align-items:flex-start;gap:10px">' +
        '<div style="min-width:44px;text-align:center"><div style="font-size:20px;font-weight:700;color:' + scoreColor + '">' + score + '</div><div style="font-size:9px;color:#94a3b8">score</div></div>' +
        '<div style="flex:1">' +
        '<div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:2px">' +
        '<a href="' + escHtml(s.url||'#') + '" target="_blank" style="font-weight:600;font-size:13px;color:#1e293b;text-decoration:none">' + escHtml((s.title||'').slice(0,90)) + '</a>' +
        '<span class="pill ' + sb[1] + '">' + sb[0] + '</span>' +
        (s.source==='gsc-growth' ? '<span class="pill pill-ok">Growth</span>' : '') +
        '</div>' +
        '<div style="font-size:10px;color:#94a3b8">' + escHtml(s.source||'') + (age?' · '+age:'') + ' · keyword: ' + escHtml(s.keyword||'') + (s.obsidian_path ? ' · in vault' : '') + '</div>' +
        (s.ai_hook ? '<div style="margin-top:6px;font-size:12px;font-weight:600;color:#4338ca">' + escHtml(s.ai_hook) + '</div>' : '') +
        (s.ai_angle ? '<div style="margin-top:3px;font-size:11px;color:#475569">' + escHtml(s.ai_angle) + '</div>' : '') +
        (titles.length ? '<div style="margin-top:5px">' + titles.map(function(t){ return '<div style="font-size:11px;color:#64748b;padding:1px 0">' + escHtml(t) + '</div>'; }).join('') + '</div>' : '') +
        '</div></div>' +
        '<div style="display:flex;gap:6px;flex-wrap:wrap;margin-top:8px">' +
        ((s.status==='new'||s.status==='targeted') ?
          '<button onclick="radarAeoAttack(\'' + s.id + '\',null,this)" class="btn btn-primary btn-sm">AEO-aanval (blog+video+Reddit)</button>' +
          '<button onclick="radarWriteArticle(' + idx + ',this)" class="btn btn-primary btn-sm" style="background:var(--green)">Schrijf SEO-artikel</button>' +
          '<button onclick="radarAeoAttack(\'' + s.id + '\',[\'video\'],this)" class="btn btn-ghost btn-sm">Alleen videoscript</button>' : '') +
        (s.status==='converted' ?
          '<button onclick="radarQueueListicle(\'' + s.id + '\',null,this)" class="btn btn-primary btn-sm" style="background:var(--green)">Listicle → wachtrij</button>' : '') +
        '<button onclick="radarNotebookLM(\'' + s.id + '\',this)" class="btn btn-ghost btn-sm">NotebookLM-pakket</button>' +
        '<button onclick="radarInfographic(\'' + s.id + '\',this)" class="btn btn-ghost btn-sm">Infographic</button>' +
        (!s.obsidian_path ? '<button onclick="radarToObsidian(\'' + s.id + '\',this)" class="btn btn-ghost btn-sm">→ Vault</button>' : '') +
        (s.status!=='dismissed' ? '<button onclick="radarUpdateStatus(\'' + s.id + '\',\'dismissed\')" class="btn btn-ghost btn-sm">Negeren</button>'
          : '<button onclick="radarUpdateStatus(\'' + s.id + '\',\'new\')" class="btn btn-ghost btn-sm">Heropen</button>') +
        '</div>' +
        (s.status==='converted' ? '<div id="radar-progress-' + escHtml(s.id) + '" class="radar-progress" style="margin-top:8px;font-size:11px;color:#94a3b8">Voortgang laden...</div>' : '') +
        '</div>';
    });
    listEl.innerHTML = h;
    refreshRadarProgress();
  }).catch(function(e){
    listEl.innerHTML = '<div style="color:#ef4444;font-size:11px;padding:8px">Fout: ' + escHtml(e.message) + '</div>';
  });
}

// Voortgang van AEO-conveyor-taken: zonder dit is een AEO-aanval voor de
// gebruiker een zwart gat tussen de bevestigingspop-up en het moment dat er
// (soms minuten later) iets in de Wachtrij verschijnt. Ververst elke 5s zolang
// er nog iets loopt; stopt vanzelf zodra alles klaar is of de tab herlaadt.
var _radarProgressTimer = null;

var RADAR_TASK_STATUS_LABEL = {
  todo: ['wacht', '#94a3b8'],
  ready: ['staat klaar', '#94a3b8'],
  running: ['bezig...', '#d97706'],
  done: ['klaar', '#16a34a'],
  awaiting_approval: ['klaar', '#16a34a'],
  needs_work: ['kwaliteit onvoldoende', '#dc2626'],
  error: ['mislukt', '#dc2626'],
};

var RADAR_TERMINAL_STATUSES = ['done', 'awaiting_approval', 'needs_work', 'error'];
var RADAR_CHANNEL_LABEL = { listicle: 'Listicle', video: 'Video/TikTok', reddit: 'Reddit' };

function renderRadarProgressPills(data) {
  if (!data || !data.tasks || !data.tasks.length) return '';
  var allDone = true;
  var pills = data.tasks.map(function(t){
    // Een taak die crasht valt terug naar 'todo' (niet 'ready') zodat de
    // conveyor niet in een faal-lus komt — zie conveyor.py:_execute_task.
    // Zonder deze check ziet zo'n vastgelopen taak er identiek uit als
    // "nog niet begonnen" en blijft de storing onzichtbaar.
    var stuck = (t.status === 'todo' || t.status === 'ready') && !!t.error;
    var effStatus = stuck ? 'error' : t.status;
    var meta = RADAR_TASK_STATUS_LABEL[effStatus] || [effStatus, '#94a3b8'];
    var label = RADAR_CHANNEL_LABEL[t.channel] || t.channel || t.title;
    var icon = effStatus === 'done' || effStatus === 'awaiting_approval' ? '✓'
      : effStatus === 'running' ? '●'
      : (effStatus === 'error' || effStatus === 'needs_work') ? '✕' : '○';
    if (RADAR_TERMINAL_STATUSES.indexOf(effStatus) === -1) allDone = false;
    var title = stuck ? ' title="' + escHtml(t.error) + '"' : '';
    return '<span' + title + ' style="display:inline-flex;align-items:center;gap:4px;color:' + meta[1] + '">' + icon + ' ' + escHtml(label) + ' — ' + meta[0] + '</span>';
  });
  return { html: pills.join('<span style="color:#cbd5e1;margin:0 6px">→</span>'), done: allDone };
}

function refreshRadarProgress() {
  if (_radarProgressTimer) { clearTimeout(_radarProgressTimer); _radarProgressTimer = null; }
  var els = document.querySelectorAll('.radar-progress');
  if (!els.length) return;
  var pending = false;
  var fetches = Array.prototype.map.call(els, function(el){
    var id = el.id.replace('radar-progress-', '');
    return fetch('/api/radar/signals/' + id + '/aeo-progress').then(function(r){ return r.ok ? r.json() : null; })
      .then(function(data){
        var el2 = document.getElementById('radar-progress-' + id);
        if (!el2) return;
        var out = renderRadarProgressPills(data);
        if (!out) { el2.textContent = ''; return; }
        el2.innerHTML = out.html;
        if (!out.done) pending = true;
      })
      .catch(function(){});
  });
  Promise.all(fetches).then(function(){
    if (pending) _radarProgressTimer = setTimeout(refreshRadarProgress, 5000);
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
      '<h3 style="margin:0 0 12px;font-size:15px;color:#1e293b">' + escHtml(title) + '</h3>' +
      bodyHtml +
      '<div style="margin-top:16px;text-align:right"><button onclick="document.getElementById(\'radar-modal-overlay\').remove()" ' +
      'class="btn btn-primary">OK</button></div>' +
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


// ═══════════════════════════════════════════════════════════════════
//  LINKS TAB — linkbuilding per project (funnel · concepten · live links)
//  Data komt uit /api/linkbuilding/*, gescoped op de site van dit project.
//  Versturen kan ALLEEN via de approve-endpoint (review-gate).
// ═══════════════════════════════════════════════════════════════════
async function renderLinksTab(el) {
  el.innerHTML = '<div class="loading"><div class="spinner"></div><p>Linkbuilding laden...</p></div>';
  var site;
  try {
    var sites = await (await fetch('/api/sites')).json();
    var norm = function(n){ return (n||'').toLowerCase().replace(/ /g,'').replace(/-/g,''); };
    site = sites.find(function(s){ return norm(s.name) === norm(currentProject); });
  } catch(e) { el.innerHTML = '<div class="empty-state">Fout: ' + escHtml(e.message) + '</div>'; return; }
  if (!site) {
    el.innerHTML = '<div class="empty-state"><p style="font-size:14px;font-weight:600;color:#475569;margin-bottom:4px">Geen site gekoppeld</p>' +
      '<p style="color:#94a3b8">Linkbuilding werkt per site — koppel dit project aan een site (Instellingen &rarr; Sites).</p></div>';
    return;
  }
  var q = '?site_id=' + encodeURIComponent(site.id);
  try {
    var res = await Promise.all([
      fetch('/api/linkbuilding/funnel' + q).then(function(r){return r.json();}),
      fetch('/api/linkbuilding/prospects' + q).then(function(r){return r.json();}),
      fetch('/api/linkbuilding/placements' + q + '&status=live').then(function(r){return r.json();})
    ]);
  } catch(e) { el.innerHTML = '<div class="empty-state">Fout: ' + escHtml(e.message) + '</div>'; return; }
  var f = res[0] || {}, prospects = res[1] || [], live = res[2] || [];

  var html = '<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:14px;flex-wrap:wrap;gap:8px">' +
    '<div><h3 style="font-size:15px;font-weight:700">Linkbuilding &mdash; ' + escHtml(site.name) + '</h3>' +
    '<p style="font-size:11px;color:#64748b;margin-top:2px">De agent zoekt en schrijft; versturen blijft jouw klik.</p></div>' +
    '<div style="display:flex;gap:6px;flex-wrap:wrap">' +
    '<button onclick="runLinkbuildingProspecting(this, \'' + site.id + '\')" class="btn btn-ghost btn-sm">Zoek kansen</button>' +
    '<button onclick="runLinkbuildingBatch(this, \'' + site.id + '\')" class="btn btn-ghost btn-sm">Maak concepten (review)</button>' +
    '<button onclick="renderLinksTab(document.getElementById(\'tab-content\'))" class="btn btn-ghost btn-sm">Ververs</button>' +
    '</div></div>';

  // Concepten die op de verzendklik wachten — dezelfde gate als het Actiecentrum.
  var review = prospects.filter(function(p){ return p.status === 'outreach_review'; });
  if (review.length) {
    html += '<div class="section-card" style="margin-bottom:14px;background:var(--warn-bg);border-color:var(--warn-border)">' +
      '<h4 style="font-size:13px;font-weight:700;color:var(--warn-fg);margin-bottom:8px">Wacht op je verzendklik (' + review.length + ')</h4>';
    review.forEach(function(p){
      html += '<div style="border-top:1px solid #fef3c7;padding:8px 0;font-size:12px">' +
        '<div style="display:flex;align-items:center;justify-content:space-between;gap:8px;flex-wrap:wrap">' +
        '<div style="flex:1;min-width:220px"><span style="font-weight:600">' + escHtml(p.domain) + '</span>' +
        ' <span style="color:#94a3b8">&rarr; ' + escHtml(p.contact_email||'') + '</span>' +
        '<div style="color:#475569;margin-top:2px">\u{2018}' + escHtml(p.outreach_subject||'') + '\u{2019}</div></div>' +
        '<div style="display:flex;gap:6px">' +
        '<button onclick="lbApprove(this, \'' + p.id + '\')" class="btn btn-primary btn-sm" style="background:var(--green)">Verstuur</button>' +
        '<button onclick="lbDismiss(this, \'' + p.id + '\')" class="btn btn-danger-outline btn-sm">Wijs af</button>' +
        '</div></div>' +
        '<details style="margin-top:4px"><summary style="cursor:pointer;font-size:11px;color:#64748b">Lees het concept</summary>' +
        '<pre style="white-space:pre-wrap;font-size:11px;background:#fff;border:1px solid #fef3c7;border-radius:6px;padding:8px;margin-top:4px">' + escHtml(p.outreach_draft||'') + '</pre></details>' +
        '</div>';
    });
    html += '</div>';
  }

  html += buildLinkbuildingHtml(f, prospects, live);
  el.innerHTML = html;
}

function lbApprove(btn, id) {
  if (!confirm('Deze link-outreach-mail wordt ECHT verstuurd. Doorgaan?')) return;
  if (btn) { btn.disabled = true; btn.textContent = 'Versturen...'; }
  post('/api/linkbuilding/' + encodeURIComponent(id) + '/outreach-approve').then(function(){
    renderLinksTab(document.getElementById('tab-content'));
  }).catch(function(e){
    if (btn) { btn.disabled = false; btn.textContent = 'Verstuur'; }
    alert('Versturen mislukt: ' + e.message);
  });
}

function lbDismiss(btn, id) {
  if (!confirm('Concept afwijzen? De linkkans gaat naar verloren.')) return;
  if (btn) btn.disabled = true;
  post('/api/linkbuilding/' + encodeURIComponent(id) + '/outreach-dismiss').then(function(){
    renderLinksTab(document.getElementById('tab-content'));
  }).catch(function(e){
    if (btn) btn.disabled = false;
    alert('Afwijzen mislukt: ' + e.message);
  });
}
