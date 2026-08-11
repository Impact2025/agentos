// ── Agent OS — tab: Gauntlet Loop (matcher + parallelle blinde critici)
// Onderdeel van de SPA: klassieke scripts, gedeelde globare scope.
// Laadvolgorde staat in index.html — core.js eerst.
//
// Implementeert het "Gauntlet Loop"-patroon: een Lead Agent splitst de opdracht
// in parallelle deeltaken; elke deeltaak krijgt een eigen builder + BLINDE criticus
// die het product hard meet tegen een echte benchmark. De loop stopt niet vanzelf —
// de mens zet de stop of beoordeelt het eindresultaat als eindjurat.

let _gauntletES = null;        // actieve EventSource voor de live-feed
let _gauntletRuns = {};         // run_id -> laatste bekende status (voor de feed)

// ── Tab-entry ──────────────────────────────────────────────────────────────
async function renderGauntletTab(el) {
  el.innerHTML =
    '<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:12px">' +
      '<h3 style="font-size:15px;font-weight:700">Gauntlet Loop</h3>' +
      '<span style="font-size:11px;color:#64748b">Matched · parallel · blinde critici vs benchmark</span>' +
    '</div>' +
    '<div class="section-card" style="margin-bottom:16px">' +
      '<h4 style="font-size:13px;font-weight:600;margin-bottom:8px">Nieuwe Gauntlet</h4>' +
      '<p style="font-size:12px;color:#64748b;margin-bottom:10px">Eén herhaalbare opdracht + één scherpe benchmark. ' +
        'De matcher splitst hem op in parallelle deeltaken; elke deeltaak krijgt een blinde criticus die het hard meet tegen de benchmark.</p>' +
      '<label style="font-size:12px;font-weight:600;display:block;margin-bottom:4px">Opdracht</label>' +
      '<textarea id="g-objective" rows="2" style="width:100%;padding:8px;border:1px solid #e2e8f0;border-radius:8px;font-size:13px;margin-bottom:10px" placeholder="Bijv. schrijf een wereldklasse landingspagina voor X"></textarea>' +
      '<label style="font-size:12px;font-weight:600;display:block;margin-bottom:4px">Benchmark (waar hard tegenaan gemeten wordt)</label>' +
      '<textarea id="g-benchmark" rows="2" style="width:100%;padding:8px;border:1px solid #e2e8f0;border-radius:8px;font-size:13px;margin-bottom:10px" placeholder="Bijv. de beste landingspagina die je kunt vinden, of een scherpe beschrijving van het gewenste resultaat"></textarea>' +
      '<div style="display:flex;gap:12px;margin-bottom:10px">' +
        '<div><label style="font-size:11px;color:#64748b;display:block">Drempel (0-100)</label>' +
          '<input id="g-threshold" type="number" value="85" min="1" max="100" style="width:90px;padding:6px;border:1px solid #e2e8f0;border-radius:8px;font-size:13px"></div>' +
        '<div><label style="font-size:11px;color:#64748b;display:block">Max rondes / deeltaak</label>' +
          '<input id="g-maxiter" type="number" value="3" min="1" max="10" style="width:90px;padding:6px;border:1px solid #e2e8f0;border-radius:8px;font-size:13px"></div>' +
      '</div>' +
      '<button id="g-start" onclick="startGauntlet()" style="padding:8px 18px;background:#4f46e5;color:#fff;border:none;border-radius:8px;font-size:13px;font-weight:600;cursor:pointer">Start Gauntlet</button>' +
    '</div>' +
    '<div class="section-card">' +
      '<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:8px">' +
        '<h4 style="font-size:13px;font-weight:600">Live feed & runs</h4>' +
        '<button onclick="loadGauntletRuns()" style="font-size:11px;color:#6366f1;background:none;border:none;cursor:pointer">Ververs</button>' +
      '</div>' +
      '<div id="g-feed" style="font-family:monospace;font-size:12px;background:#0f172a;color:#e2e8f0;border-radius:8px;padding:10px;max-height:240px;overflow:auto;white-space:pre-wrap"></div>' +
      '<div id="g-runs" style="margin-top:12px"></div>' +
    '</div>';

  // Live SSE-feed openen (gedeelde event_bus: filtert op gauntlet_* events)
  openGauntletStream();
  loadGauntletRuns();
}

function openGauntletStream() {
  if (_gauntletES) return;
  try {
    _gauntletES = new EventSource('/api/gauntlet/stream');
    _gauntletES.onmessage = function(ev) {
      try {
        var data = JSON.parse(ev.data);
        appendGauntletFeed(data);
      } catch (e) { /* ignore */ }
    };
    _gauntletES.onerror = function() { /* reconnect gebeurt automatisch */ };
  } catch (e) { /* SSE niet beschikbaar */ }
}

function appendGauntletFeed(data) {
  var feed = document.getElementById('g-feed');
  if (!feed) return;
  var t = (data && data.type) || 'event';
  var line = '';
  if (t === 'gauntlet_start') line = '▶ GAUNTLET gestart — ' + short(data.objective);
  else if (t === 'gauntlet_plan') line = '  plan: ' + (data.subtasks || []).map(function(s){return s.role;}).join(' · ');
  else if (t === 'gauntlet_subtask_start') line = '  • [' + data.role + '] bouwt…';
  else if (t === 'gauntlet_subtask_iteration') {
    var mark = data.passed ? 'PASS' : '—';
    line = '    [' + data.role + '] ronde ' + data.iteration + '/' + data.max_iterations + ' score ' + data.score + '/' + data.threshold + ' ' + mark;
  }
  else if (t === 'gauntlet_subtask_done') line = '  • [' + data.role + '] klaar: ' + data.status + ' (' + data.best_score + ')';
  else if (t === 'gauntlet_done') line = '■ GAUNTLET klaar: ' + data.status + ' — ' + (data.message || '');
  else if (t === 'gauntlet_error') line = '✗ GAUNTLET fout: ' + short(data.error);
  else line = '  · ' + t;
  feed.textContent += line + '\n';
  feed.scrollTop = feed.scrollHeight;
  // Ververs de runs-lijst zodat status / Stop-knop actueel blijven
  if (t === 'gauntlet_done' || t === 'gauntlet_subtask_done') loadGauntletRuns();
}

function short(s, n) { s = s || ''; n = n || 60; return s.length > n ? s.slice(0, n) + '…' : s; }

async function startGauntlet() {
  var objective = (document.getElementById('g-objective') || {}).value || '';
  var benchmark = (document.getElementById('g-benchmark') || {}).value || '';
  var threshold = parseInt((document.getElementById('g-threshold') || {}).value || '85', 10);
  var maxiter = parseInt((document.getElementById('g-maxiter') || {}).value || '3', 10);
  if (!objective.trim() || !benchmark.trim()) {
    alert('Een opdracht én een benchmark zijn verplicht.');
    return;
  }
  var btn = document.getElementById('g-start');
  if (btn) { btn.disabled = true; btn.textContent = 'Start…'; }
  try {
    var resp = await fetch('/api/gauntlet', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ objective: objective, benchmark: benchmark, threshold: threshold, max_iterations: maxiter })
    });
    if (!resp.ok) {
      var err = await resp.json().catch(function(){return {};});
      alert('Fout: ' + (err.detail || resp.status));
      return;
    }
    await resp.json();
    if (window._gauntletFeed) {}
    // Focus de feed
    var feed = document.getElementById('g-feed');
    if (feed) feed.scrollTop = feed.scrollHeight;
  } catch (e) {
    alert('Fout: ' + e.message);
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = 'Start Gauntlet'; }
    loadGauntletRuns();
  }
}

async function stopGauntlet(runId) {
  if (!confirm('Gauntlet stoppen? De lussen breken bij de volgende ronde af.')) return;
  try {
    await fetch('/api/gauntlet/' + encodeURIComponent(runId) + '/stop', { method: 'POST' });
    loadGauntletRuns();
  } catch (e) { alert('Fout: ' + e.message); }
}

async function submitGauntletVerdict(runId) {
  var sel = document.getElementById('g-verdict-' + runId);
  var noteEl = document.getElementById('g-verdict-note-' + runId);
  var verdict = sel ? sel.value : '';
  var note = noteEl ? noteEl.value : '';
  if (!verdict) { alert('Kies een oordeel.'); return; }
  try {
    await fetch('/api/gauntlet/' + encodeURIComponent(runId) + '/verdict', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ verdict: verdict, note: note })
    });
    loadGauntletRuns();
  } catch (e) { alert('Fout: ' + e.message); }
}

async function loadGauntletRuns() {
  var box = document.getElementById('g-runs');
  if (!box) return;
  try {
    var resp = await fetch('/api/gauntlet?limit=20');
    var runs = await resp.json();
  } catch (e) { box.innerHTML = '<div class="empty-state">Fout: ' + escHtml(e.message) + '</div>'; return; }

  if (!runs.length) { box.innerHTML = '<div style="color:#64748b;font-size:12px;padding:8px">Nog geen runs.</div>'; return; }

  var html = '<table style="width:100%;border-collapse:collapse;font-size:12px">' +
    '<thead><tr style="text-align:left;color:#64748b;border-bottom:1px solid #e2e8f0">' +
    '<th style="padding:6px">Opdracht</th><th style="padding:6px">Status</th><th style="padding:6px">Deeltaken</th><th style="padding:6px">Jury</th><th style="padding:6px"></th></tr></thead><tbody>';
  runs.forEach(function(r) {
    var statusColor = r.status === 'running' ? '#3b82f6' : (r.status === 'passed' ? '#22c55e' : (r.status === 'stopped_by_user' ? '#f59e0b' : '#64748b'));
    var running = r.status === 'running';
    var verdictCell = r.human_verdict
      ? escHtml(r.human_verdict)
      : (running ? '' :
        '<select id="g-verdict-' + r.id + '" style="padding:3px;border:1px solid #e2e8f0;border-radius:6px;font-size:11px">' +
          '<option value="">— oordeel —</option><option value="goedgekeurd">goedgekeurd</option>' +
          '<option value="aangepast">aangepast</option><option value="afgekeurd">afgekeurd</option></select>' +
        '<br><input id="g-verdict-note-' + r.id + '" placeholder="notitie" style="width:120px;margin-top:3px;padding:3px;border:1px solid #e2e8f0;border-radius:6px;font-size:11px">' +
        '<br><button onclick="submitGauntletVerdict(\'' + r.id + '\')" style="margin-top:3px;padding:3px 8px;background:#4f46e5;color:#fff;border:none;border-radius:6px;font-size:11px;cursor:pointer">Beoordeel</button>');
    html += '<tr style="border-bottom:1px solid #f1f5f9">' +
      '<td style="padding:6px">' + escHtml(short(r.objective, 50)) + '</td>' +
      '<td style="padding:6px;color:' + statusColor + ';font-weight:600">' + escHtml(r.status) + '</td>' +
      '<td style="padding:6px">' + (r.subtask_count || 0) + '</td>' +
      '<td style="padding:6px">' + verdictCell + '</td>' +
      '<td style="padding:6px">' +
        (running ? '<button onclick="stopGauntlet(\'' + r.id + '\')" style="padding:3px 10px;background:#ef4444;color:#fff;border:none;border-radius:6px;font-size:11px;cursor:pointer">Stop</button>' : '') +
      '</td></tr>';
  });
  html += '</tbody></table>';
  box.innerHTML = html;
}
