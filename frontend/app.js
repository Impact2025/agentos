// ── Agent OS — Pro SEO Dashboard ──────────────────────────────────
const PROJECTS = ['WeAreImpact', 'Pootgelukkig', 'BewaardVoorJou', 'Kappersassistent', 'DatingAssistent', 'Finance Expert', 'Bijeen', 'Brickme', 'Vrijwilligersmatch', 'Skillkaart', 'Steentjeapp', 'Zorgblik'];
const COLORS = { WeAreImpact: ['from-indigo-500 to-indigo-600','indigo'], Pootgelukkig: ['from-emerald-500 to-emerald-600','emerald'], BewaardVoorJou: ['from-amber-500 to-amber-600','amber'], Kappersassistent: ['from-violet-500 to-violet-600','violet'], DatingAssistent: ['from-red-500 to-red-600','red'], 'Finance Expert': ['from-rose-500 to-rose-600','rose'], Bijeen: ['from-cyan-500 to-cyan-600','cyan'], Brickme: ['from-orange-500 to-orange-600','orange'], Vrijwilligersmatch: ['from-teal-500 to-teal-600','teal'], Skillkaart: ['from-pink-500 to-pink-600','pink'], Steentjeapp: ['from-sky-500 to-sky-600','sky'], Zorgblik: ['from-lime-500 to-lime-600','lime'] };
const DESCS = { WeAreImpact: 'AI en innovatie voor zorg, welzijn en gemeenten', Pootgelukkig: 'Adoptieplatform voor asieldieren', BewaardVoorJou: 'Digitaal levensboek voor 65-plussers', Kappersassistent: 'Project kappersbranche (in opstart)', DatingAssistent: 'AI dating coach & datingadvies', 'Finance Expert': 'Financiele rapportage en analyse', Bijeen: 'Sociale verbinding & bijeenkomsten', Brickme: 'Bouw & constructie', Vrijwilligersmatch: 'Vrijwilligers matching platform', Skillkaart: 'Vaardigheden & competenties', Steentjeapp: 'Mobiele app Steentjebijsteentje', Zorgblik: 'Zorginnovatie & inzicht' };
const TABS = ['Dashboard', 'Content', 'Kansen', 'Optimalisatie', 'Wachtrij', 'Concurrentie', 'Radar', 'Keywords', 'Doelen', 'Geheugen', 'Leads', 'Opdrachten', 'Technisch', 'Activiteit', 'Instellingen'];
const TAB_ICONS = { Dashboard: 'D', Content: 'C', Kansen: 'K', Optimalisatie: '↗', Wachtrij: 'Q', Concurrentie: 'R', Radar: '✦', Keywords: 'W', Doelen: 'G', Geheugen: 'I', Leads: 'L', Opdrachten: 'O', Technisch: 'T', Activiteit: 'A', Instellingen: 'S' };

let currentProject = null, currentTab = 'Dashboard', weSuggestions = [], oppStatusFilter = null, scanningInProgress = false, chartInstances = {};
let _agentStatusTimer = null;

// ── Recent Activity Logs (Vercel-stijl) ────────────────────────────
function loadActivityLogs(targetElId) {
  targetElId = targetElId || 'activity-log-panel';
  var el = document.getElementById(targetElId);
  if (!el) return;
  el.innerHTML = '<div style="color:#64748b;text-align:center;padding:8px">Laden...</div>';
  fetch('/api/action-center/feed?limit=30').then(function(r){return r.json();}).then(function(logs){
    if (!logs || !logs.length) {
      el.innerHTML = '<div style="color:#64748b;text-align:center;padding:16px">Nog geen activiteit</div>';
      return;
    }
    var html = '';
    var countEl = document.getElementById('agent-log-count');
    if (countEl) countEl.textContent = logs.length + ' events';
    logs.forEach(function(l){
      var time = (l.created_at||'').slice(11,19);
      var icon = '○';
      var color = '#64748b';
      if (l.status === 'error') { icon = '✗'; color = '#ef4444'; }
      else if (l.action === 'task_done' || l.action === 'goal_done' || l.action === 'phase_done' || l.action === 'live') { icon = '✓'; color = '#22c55e'; }
      else if (l.action === 'task_failed' || l.action === 'goal_error' || (l.action||'').indexOf('fout') >= 0) { icon = '✗'; color = '#ef4444'; }
      else if (l.action === 'task_retry') { icon = '↻'; color = '#f59e0b'; }
      else if (l.action === 'task_start' || l.action === 'goal_start' || l.action === 'phase_start') { icon = '▶'; color = '#60a5fa'; }
      else if (l.action === 'goal_retry') { icon = '⟳'; color = '#a78bfa'; }
      var project = (l.project||'').replace('goal:','').split(':')[0];
      var detail = escHtml(l.detail||'').slice(0,120);
      // Uitkomst-kaart: artefact-link ("waar staat het") + volgende stap ("wat moet ik doen")
      var artifact = (l.artifact||'').trim();
      if (artifact) {
        var artLabel = artifact.indexOf('http') === 0 ? 'bekijk resultaat' : escHtml(artifact.split(/[\\\/]/).pop());
        var href = artifact.indexOf('http') === 0 ? artifact : 'obsidian://open?path=' + encodeURIComponent(artifact);
        detail += ' <a href="' + escHtml(href) + '" target="_blank" style="color:#38bdf8;text-decoration:underline">→ ' + artLabel + '</a>';
      }
      if ((l.next_step||'').trim()) detail += ' <span style="color:#fbbf24">✋ ' + escHtml(l.next_step) + '</span>';
      html += '<div style="display:flex;align-items:flex-start;gap:6px;padding:4px 6px;border-bottom:1px solid #1e293b;line-height:1.5">' +
        '<span style="color:' + color + ';flex-shrink:0;width:14px;text-align:center">' + icon + '</span>' +
        '<span style="color:#64748b;flex-shrink:0;width:50px">' + time + '</span>' +
        '<span style="color:#38bdf8;flex-shrink:0;width:80px;font-size:10px">' + escHtml(project) + '</span>' +
        '<span style="color:#94a3b8;word-break:break-word">' + detail + '</span></div>';
    });
    el.innerHTML = html;
  }).catch(function(e){
    el.innerHTML = '<div style="color:#ef4444;text-align:center;padding:8px">Fout: ' + escHtml(e.message) + '</div>';
  });
}

// ── Project Activity Logs (per project, in dashboard tab) ──────────
function loadProjectActivityLogs() {
  if (!currentProject) return;
  var el = document.getElementById('project-activity-panel');
  if (!el) return;
  el.innerHTML = '<div style="color:#64748b;text-align:center;padding:8px">Laden...</div>';
  fetch('/api/projects/' + encodeURIComponent(currentProject) + '/activity?limit=20').then(function(r){return r.json();}).then(function(logs){
    if (!logs || !logs.length) {
      el.innerHTML = '<div style="color:#64748b;text-align:center;padding:16px">Nog geen activiteit voor dit project</div>';
      return;
    }
    var html = '';
    logs.forEach(function(l){
      var time = (l.created_at||'').slice(11,19);
      var icon = '○';
      var color = '#64748b';
      if (l.action === 'task_done' || l.action === 'goal_done' || l.action === 'phase_done') { icon = '✓'; color = '#22c55e'; }
      else if (l.action === 'task_failed' || l.action === 'goal_error') { icon = '✗'; color = '#ef4444'; }
      else if (l.action === 'task_retry') { icon = '↻'; color = '#f59e0b'; }
      else if (l.action === 'task_start' || l.action === 'goal_start' || l.action === 'phase_start') { icon = '▶'; color = '#60a5fa'; }
      else if (l.action === 'goal_retry') { icon = '⟳'; color = '#a78bfa'; }
      var detail = escHtml(l.detail||'').slice(0,120);
      html += '<div style="display:flex;align-items:flex-start;gap:6px;padding:3px 6px;border-bottom:1px solid #1e293b;line-height:1.5">' +
        '<span style="color:' + color + ';flex-shrink:0;width:14px;text-align:center">' + icon + '</span>' +
        '<span style="color:#64748b;flex-shrink:0;width:50px">' + time + '</span>' +
        '<span style="color:#94a3b8;word-break:break-word">' + detail + '</span></div>';
    });
    el.innerHTML = html;
  }).catch(function(e){
    el.innerHTML = '<div style="color:#ef4444;text-align:center;padding:8px">Fout: ' + escHtml(e.message) + '</div>';
  });
}

// ── Status indicator (pollt elke 15s) ─────────────────────────────
function startAgentStatusPoll() {
  stopAgentStatusPoll();
  pollAgentStatus();
  _agentStatusTimer = setInterval(pollAgentStatus, 15000);
}
function stopAgentStatusPoll() {
  if (_agentStatusTimer) { clearInterval(_agentStatusTimer); _agentStatusTimer = null; }
}
function pollAgentStatus() {
  fetch('/api/goals?limit=1').then(function(r){return r.json();}).then(function(goals){
    var hasRunning = false, hasFailed = false, hasReady = false;
    if (goals && goals.length) {
      goals.forEach(function(g){
        if (g.status === 'running') hasRunning = true;
        if (g.status === 'failed') hasFailed = true;
        if (g.status === 'ready' || g.status === 'draft') hasReady = true;
      });
    }
    // Also check control room for running count
    fetch('/api/strategist/control-room').then(function(r2){return r2.json();}).then(function(data){
      var gs = data.goals_summary || {};
      var running = (gs.running || 0);
      var failed = (gs.failed || 0);
      var total = (gs.total || 0);
      updateStatusIndicator(running > 0, failed > 0, total > 0, failed);
    }).catch(function(){});
  }).catch(function(){});
}
function updateStatusIndicator(busy, hasError, hasGoals, failedCount) {
  var el = document.getElementById('agent-status-indicator');
  if (!el) return;
  if (busy) {
    el.innerHTML = '<span style="display:inline-flex;align-items:center;gap:4px;padding:3px 8px;border-radius:12px;background:#dcfce7;color:#166534;font-size:10px;font-weight:600">' +
      '<span style="width:6px;height:6px;border-radius:50%;background:#16a34a;animation:pulse 1.5s infinite"></span> Bezig</span>';
  } else if (hasError) {
    el.innerHTML = '<span style="display:inline-flex;align-items:center;gap:4px;padding:3px 8px;border-radius:12px;background:#fef2f2;color:#dc2626;font-size:10px;font-weight:600">' +
      '<span style="width:6px;height:6px;border-radius:50%;background:#ef4444"></span> Mislukt (' + (failedCount||'?') + ')</span>';
    var resEl = document.getElementById('resolve-failed-btn-container');
    if (resEl) resEl.innerHTML = '<button onclick="resolveAllFailed()" style="padding:3px 10px;background:#ef4444;color:#fff;border:none;border-radius:6px;font-size:11px;cursor:pointer;font-weight:600">\u{1F9E0} Oplossen</button>';
  } else if (hasGoals) {
    el.innerHTML = '<span style="display:inline-flex;align-items:center;gap:4px;padding:3px 8px;border-radius:12px;background:#f0fdf4;color:#16a34a;font-size:10px;font-weight:600">' +
      '<span style="width:6px;height:6px;border-radius:50%;background:#22c55e"></span> Idle</span>';
    var resEl = document.getElementById('resolve-failed-btn-container');
    if (resEl) resEl.innerHTML = '';
  } else {
    el.innerHTML = '<span style="display:inline-flex;align-items:center;gap:4px;padding:3px 8px;border-radius:12px;background:#f1f5f9;color:#94a3b8;font-size:10px;font-weight:600">' +
      '<span style="width:6px;height:6px;border-radius:50%;background:#94a3b8"></span> Standby</span>';
    var resEl = document.getElementById('resolve-failed-btn-container');
    if (resEl) resEl.innerHTML = '';
  }
}

// ── Resolve all failed goals ────────────────────────────────────────
function resolveAllFailed() {
  if (!confirm('Alle mislukte doelen resetten en opnieuw proberen met AI?')) return;
  var btn = document.querySelector('[onclick="resolveAllFailed()"]');
  if (btn) { btn.disabled = true; btn.textContent = 'Bezig...'; }
  fetch('/api/goals?limit=50').then(function(r){return r.json();}).then(function(goals){
    var failed = goals.filter(function(g){return g.status==='failed';});
    if (!failed.length) { alert('Geen mislukte doelen meer.'); pollAgentStatus(); return; }
    var done = 0;
    failed.forEach(function(g){
      fetch('/api/goals/retry-failed', {
        method:'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({goal_id: g.id}),
      }).then(function(r){
        done++;
        if (done >= failed.length) {
          setTimeout(function(){
            pollAgentStatus();
            loadActivityLogs();
            if (btn) { btn.disabled = false; btn.innerHTML = '\u{1F9E0} Oplossen'; }
          }, 500);
        }
      }).catch(function(){
        done++;
        if (done >= failed.length) {
          setTimeout(function(){
            pollAgentStatus();
            loadActivityLogs();
            if (btn) { btn.disabled = false; btn.innerHTML = '\u{1F9E0} Oplossen'; }
          }, 500);
        }
      });
    });
  }).catch(function(e){
    alert('Fout: ' + e.message);
    if (btn) { btn.disabled = false; btn.innerHTML = '\u{1F9E0} Oplossen'; }
  });
}

function escHtml(s) { if (!s) return ''; var d = document.createElement('div'); d.textContent = s; return d.innerHTML; }
function kpiBox(label, val, change, sub) {
  var extra = '';
  if (change !== undefined && change !== '') { var cls = change >= 0 ? 'pos' : 'neg'; extra = '<p class="change ' + cls + '">' + (change >= 0 ? '+' : '') + change + '</p>'; }
  else if (sub) { extra = '<p style="font-size:11px;color:#94a3b8;margin-top:1px">' + sub + '</p>'; }
  return '<div class="kpi-card"><p class="label">' + label + '</p><p class="value">' + val + '</p>' + extra + '</div>';
}
function togglePrint() { window.print(); }

// ── Markdown renderer (safe: escHtml eerst, dan patterns → HTML) ──
function mdToHtml(text) {
  if (!text) return '';
  var t = escHtml(text);
  var lines = t.split('\n');
  var html = '';
  var inTable = false, inList = false, inOl = false;
  for (var i = 0; i < lines.length; i++) {
    var line = lines[i];
    if (inTable && !line.match(/^\|/)) { html += '</tbody></table>'; inTable = false; }
    if (inList && !line.match(/^- /)) { html += '</ul>'; inList = false; }
    if (inOl && !line.match(/^\d+\. /)) { html += '</ol>'; inOl = false; }
    if (line.trim() === '') { html += '<br>'; continue; }
    if (line.match(/^### /)) { html += '<h4 style="font-size:13px;font-weight:700;margin:12px 0 6px;color:#1e293b">' + inlineMd(line.slice(4)) + '</h4>'; continue; }
    if (line.match(/^## /)) { html += '<h3 style="font-size:14px;font-weight:700;margin:16px 0 8px;color:#0f172a">' + inlineMd(line.slice(3)) + '</h3>'; continue; }
    if (line.match(/^# /)) { html += '<h2 style="font-size:15px;font-weight:700;margin:16px 0 8px;color:#0f172a">' + inlineMd(line.slice(2)) + '</h2>'; continue; }
    if (line.match(/^\|/)) {
      if (line.match(/^\|[-:\s]+\|/)) continue;
      if (!inTable) { inTable = true; html += '<table class="data-table" style="margin:8px 0;font-size:11px"><tbody>'; }
      var cells = line.split('|').filter(function(c){return c.trim();});
      html += '<tr>';
      for (var c = 0; c < cells.length; c++) { html += '<td style="padding:4px 8px;border:1px solid #e2e8f0">' + inlineMd(cells[c].trim()) + '</td>'; }
      html += '</tr>';
      continue;
    }
    if (line.match(/^- /)) {
      if (!inList) { html += '<ul style="padding-left:16px;margin:6px 0;font-size:12px;line-height:1.6">'; inList = true; }
      html += '<li style="color:#475569">' + inlineMd(line.slice(2)) + '</li>';
      continue;
    }
    if (line.match(/^\d+\. /)) {
      if (!inOl) { html += '<ol style="padding-left:16px;margin:6px 0;font-size:12px;line-height:1.6">'; inOl = true; }
      html += '<li style="color:#475569">' + inlineMd(line.replace(/^\d+\. /, '')) + '</li>';
      continue;
    }
    html += '<p style="margin:4px 0;font-size:12px;line-height:1.6;color:#475569">' + inlineMd(line) + '</p>';
  }
  if (inTable) html += '</tbody></table>';
  if (inList) html += '</ul>';
  if (inOl) html += '</ol>';
  return html;
}
function inlineMd(text) {
  text = text.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
  text = text.replace(/`([^`]+)`/g, '<code style="font-size:11px;padding:1px 4px;background:#f1f5f9;border-radius:3px">$1</code>');
  return text;
}

function route() {
  var main = document.getElementById('main-content');
  if (!main) return;
  stopDashboardBannerPoll();
  if (!currentProject) renderHome(main);
  else if (currentTab === 'Chat') renderChat(main);
  else renderProjectView(main);
}
function selectProject(name) { currentProject = name; currentTab = 'Dashboard'; weSuggestions = []; history.pushState(null, '', '#project=' + encodeURIComponent(name)); route(); }
function goHome() { currentProject = null; currentTab = 'Dashboard'; weSuggestions = []; history.pushState(null, '', '#'); route(); }
function switchView(view) { if (view === 'home') { goHome(); return; } if (view === 'chat') { currentTab = 'Chat'; route(); return; } currentTab = view; route(); }
window.addEventListener('popstate', route);

function renderHome(main) {
  main.innerHTML = '<div class="loading"><div class="spinner"></div><p>Control Room laden...</p></div>';
  fetch('/api/strategist/control-room').then(function(r){return r.json();}).then(function(data){
    if (data.error) { main.innerHTML = '<div class="empty-state">Fout: ' + escHtml(data.error) + '</div>'; return; }
    var html = '<div class="homescreen"><h2>Agent OS</h2><div style="display:flex;align-items:center;gap:8px;margin-bottom:16px"><span id="agent-status-indicator">' +
      '<span style="display:inline-flex;align-items:center;gap:4px;padding:3px 8px;border-radius:12px;background:#f1f5f9;color:#94a3b8;font-size:10px;font-weight:600">' +
      '<span style="width:6px;height:6px;border-radius:50%;background:#94a3b8"></span> Laden...</span></span>' +
      '<span style="font-size:11px;color:#94a3b8" id="agent-log-count"></span>' +
      '<span id="resolve-failed-btn-container"></span>' +
      '<p style="font-size:12px;color:#64748b">Control Room &mdash; overzicht van alle projecten en systemen</p></div>';

    // ── Actiecentrum: alles wat op een menselijke beslissing wacht ──
    html += '<div id="action-center-panel"><div style="color:#64748b;font-size:12px;padding:8px 0">Inbox laden...</div></div>';

    // ── Recent Activity logs (Vercel-style) ──
    html += '<div class="section-card" style="margin-bottom:16px"><div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:8px">' +
      '<h4 style="font-size:13px;font-weight:700">\u{1F4DC} Recente activiteit</h4>' +
      '<button onclick="loadActivityLogs()" style="padding:3px 10px;background:#f1f5f9;color:#475569;border:1px solid #e2e8f0;border-radius:4px;font-size:10px;cursor:pointer">Ververs</button></div>' +
      '<div id="activity-log-panel" style="background:#0f172a;border-radius:8px;padding:8px;font-family:monospace;font-size:11px;max-height:300px;overflow-y:auto">' +
      '<div style="color:#64748b;text-align:center;padding:16px">Laden...</div></div></div>';

    // ── System Health bar ──
    var sys = data.system || {};
    var obs = sys.obsidian || {};
    html += '<div class="kpi-grid" style="grid-template-columns:repeat(4,1fr);margin-bottom:16px">' +
      kpiBox('Hermes', sys.hermes_configured ? sys.hermes_backend : '❌ Uit', '', sys.hermes_model || '') +
      kpiBox('Obsidian', obs.configured ? 'Actief' : 'Uit', '', obs.total_notes + ' notities') +
      kpiBox('OMI', obs.omi_configured ? 'Actief' : 'Uit') +
      kpiBox('Doelen', data.goals_summary ? data.goals_summary.total : 0, '', (data.goals_summary ? data.goals_summary.running : 0) + ' actief') +
    '</div>';

    // ── Systeemgezondheid ──
    html += '<div id="system-health-panel"></div>';

    // ── Goals summary mini-bar ──
    var gs = data.goals_summary || {};
    html += '<div class="kpi-grid" style="grid-template-columns:repeat(5,1fr);margin-bottom:16px">' +
      kpiBox('Totaal doelen', gs.total||0) +
      kpiBox('Actief', gs.running||0, '', '') +
      kpiBox('Gereed', gs.completed||0, '', '') +
      kpiBox('Mislukt', gs.failed||0, '', '') +
      kpiBox('Gepauzeerd', gs.paused||0, '', '') +
    '</div>';

    // ── Project cards ──
    html += '<div class="grid-2" style="margin-bottom:20px">';
    (data.projects||[]).forEach(function(p){
      var goals = p.goals || [];
      var rGoals = goals.filter(function(g){return g.status==='running'||g.status==='ready';});
      var runningBadge = rGoals.length > 0 ? '<span class="badge badge-draft" style="margin-left:6px;font-size:10px">' + rGoals.length + ' bezig</span>' : '';
      var gscBadge = p.gsc_configured ? '<span style="color:#16a34a;font-size:10px">✓ GSC</span>' : '<span style="color:#94a3b8;font-size:10px">✗ GSC</span>';
      var oppNew = (p.opportunities||{}).new || 0;
      var oppBadge = oppNew > 0 ? '<span class="badge badge-zzp" style="margin-left:6px;font-size:10px">' + oppNew + ' kansen</span>' : '';

      html += '<div class="project-card" onclick="selectProject(\'' + p.name.replace(/'/g,"\\'") + '\')" style="cursor:pointer;padding:16px;border:1px solid var(--card-border);border-radius:10px;background:var(--card-bg)">' +
        '<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:8px">' +
        '<p class="pc-name" style="font-size:14px;font-weight:600;color:var(--text)">' + escHtml(p.name) + runningBadge + oppBadge + '</p>' +
        gscBadge +
        '</div>' +
        '<p style="font-size:11px;color:#64748b;margin-bottom:6px">' + escHtml(p.description) + '</p>' +
        '<div style="display:flex;gap:16px;font-size:11px;color:#94a3b8">' +
        '<span>' + (p.content_count||0) + ' bestanden</span>' +
        '<span>' + goals.length + ' doelen</span>' +
        '<span>' + ((p.opportunities||{}).total ?? 0) + ' kansen</span>' +
        '</div>' +
        (goals.length > 0 ? '<div style="margin-top:8px;font-size:10px;color:#64748b;border-top:1px solid #f1f5f9;padding-top:6px">' +
          goals.slice(0,3).map(function(g){return '<div>' + (g.status==='running'||g.status==='ready'?'▶ ':'○ ') + escHtml(g.title) + ' <span style="color:#94a3b8">(' + g.status + ')</span></div>';}).join('') +
          (goals.length>3?'<div style="color:#94a3b8">+ ' + (goals.length-3) + ' meer</div>':'') +
        '</div>' : '') +
        '</div>';
    });
    html += '</div>';

    // ── Strategist analyse knop ──
    html += '<div class="section-card" style="text-align:center;background:linear-gradient(135deg,#eef2ff,#f8fafc);border:1px solid #e0e7ff">' +
      '<h4 style="font-size:14px;font-weight:700;color:var(--accent);margin-bottom:4px">Strategist Agent</h4>' +
      '<p style="font-size:12px;color:#64748b;margin-bottom:12px">AI-manager die alle projecten, doelen en kansen analyseert en prioriteiten stelt</p>' +
      '<button onclick="runStrategistAnalysis()" id="strat-btn" style="padding:10px 24px;background:var(--accent);color:#fff;border:none;border-radius:8px;font-size:13px;font-weight:600;cursor:pointer">\u{1F9E0} Analyseer &amp; prioriteer</button>' +
      '</div>';

    // ── Strategist result container ──
    html += '<div id="strategist-result"></div>';

    html += '</div>';
    main.innerHTML = html;
    loadActionCenter();
    loadActivityLogs();
    loadSystemHealth();
    startAgentStatusPoll();
  }).catch(function(e){ main.innerHTML = '<div class="empty-state">Fout: ' + escHtml(e.message) + '</div>'; });
}

// ── Actiecentrum — één inbox met alles wat op jou wacht ────────────
var _acKindMeta = {
  goal_draft:    { icon: '\u{1F4CB}', color: '#f59e0b', label: 'Plan wacht op akkoord' },
  goal_ready:    { icon: '▶',    color: '#3b82f6', label: 'Klaar om te starten' },
  goal_failed:   { icon: '✗',    color: '#ef4444', label: 'Vastgelopen doel' },
  content_review:{ icon: '\u{1F4F0}', color: '#8b5cf6', label: 'Content ter review' },
  task_approval: { icon: '✓',    color: '#0ea5e9', label: 'Taak wacht op goedkeuring' },
  vacancies:     { icon: '\u{1F4BC}', color: '#10b981', label: 'Opdracht-kansen' },
  leads:         { icon: '\u{1F465}', color: '#10b981', label: 'Nieuwe leads' },
  error:         { icon: '⚠',    color: '#ef4444', label: 'Fout' }
};

function loadActionCenter() {
  var el = document.getElementById('action-center-panel');
  if (!el) return;
  fetch('/api/action-center').then(function(r){return r.json();}).then(function(data){
    if (!el) return;
    var items = data.items || [];
    if (!items.length) {
      el.innerHTML = '<div style="background:#f0fdf4;border:1px solid #86efac;border-radius:10px;padding:14px 16px;margin-bottom:16px;font-size:13px;color:#166534">' +
        '✅ <b>Inbox leeg</b> — niets wacht op jou. De agents draaien op schema.</div>';
      return;
    }
    var html = '<div class="section-card" style="margin-bottom:16px;border:2px solid #6366f1;background:linear-gradient(135deg,#eef2ff,#fff)">' +
      '<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:10px">' +
      '<h3 style="font-size:15px;font-weight:800;color:#312e81">\u{1F4E5} Vandaag — wacht op jou (' + items.length + ')</h3>' +
      '<span style="font-size:11px;color:#64748b">' + (data.counts.errors ? data.counts.errors + ' fout(en) · ' : '') + 'klik = klaar</span></div>';
    items.forEach(function(it, idx){
      var meta = _acKindMeta[it.kind] || { icon: '•', color: '#64748b', label: it.kind };
      var when = it.created_at ? '<span style="color:#94a3b8;font-size:10px;flex-shrink:0">' + escHtml(String(it.created_at).slice(0,10)) + '</span>' : '';
      html += '<div id="ac-item-' + idx + '" style="display:flex;align-items:flex-start;gap:10px;padding:10px 12px;margin-bottom:6px;background:#fff;border:1px solid #e2e8f0;border-left:3px solid ' + meta.color + ';border-radius:8px">' +
        '<span style="font-size:15px;flex-shrink:0">' + meta.icon + '</span>' +
        '<div style="flex:1;min-width:0">' +
        '<div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">' +
        '<span style="font-size:10px;font-weight:700;text-transform:uppercase;color:' + meta.color + '">' + meta.label + '</span>' +
        '<span style="font-size:10px;color:#64748b;background:#f1f5f9;padding:1px 6px;border-radius:4px">' + escHtml(it.project || '') + '</span>' + when + '</div>' +
        '<p style="font-size:13px;font-weight:600;color:#1e293b;margin:2px 0">' + escHtml(it.title) + '</p>' +
        '<p style="font-size:11px;color:#64748b;margin-bottom:6px">' + escHtml(it.summary || '') + '</p>' +
        '<div style="display:flex;gap:6px;flex-wrap:wrap">' +
        it.actions.map(function(a){
          var style = a.danger
            ? 'background:#fff;color:#dc2626;border:1px solid #fecaca'
            : (a.type === 'open_tab' || a.type === 'dismiss')
              ? 'background:#f8fafc;color:#475569;border:1px solid #e2e8f0'
              : 'background:#4f46e5;color:#fff;border:none';
          return '<button onclick=\'acAction(this, ' + JSON.stringify(a).replace(/'/g, '&#39;') + ', ' + JSON.stringify(it.project || '') + ')\' ' +
            'style="padding:4px 12px;border-radius:6px;font-size:11px;font-weight:600;cursor:pointer;' + style + '">' + escHtml(a.label) + '</button>';
        }).join('') +
        '</div></div></div>';
    });
    html += '</div>';
    el.innerHTML = html;
  }).catch(function(e){
    el.innerHTML = '<div style="background:#fef2f2;border:1px solid #fecaca;border-radius:8px;padding:10px;margin-bottom:16px;font-size:12px;color:#991b1b">Actiecentrum laden mislukt: ' + escHtml(e.message) + '</div>';
  });
}

function acAction(btn, action, project) {
  var type = action.type;
  if (type === 'open_tab') {
    var proj = PROJECTS.indexOf(project) >= 0 ? project : (currentProject || 'WeAreImpact');
    currentProject = proj; currentTab = action.tab; weSuggestions = [];
    history.pushState(null, '', '#project=' + encodeURIComponent(proj));
    route();
    return;
  }
  if (btn) { btn.disabled = true; btn.textContent = 'Bezig...'; }
  var done = function(){ loadActionCenter(); loadActivityLogs(); };
  var fail = function(e){ if (btn) { btn.disabled = false; btn.textContent = 'Mislukt — opnieuw'; } console.error('[Actiecentrum]', e); };
  var post = function(url, body){ return fetch(url, { method:'POST', headers:{'Content-Type':'application/json'}, body: body ? JSON.stringify(body) : undefined }).then(function(r){ if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); }); };

  if (type === 'goal_confirm_start') {
    post('/api/goals/confirm', { goal_id: action.id })
      .then(function(){ return post('/api/goals/start', { goal_id: action.id }); })
      .then(done).catch(fail);
  } else if (type === 'goal_start') {
    post('/api/goals/start', { goal_id: action.id }).then(done).catch(fail);
  } else if (type === 'goal_retry') {
    post('/api/goals/retry-failed', { goal_id: action.id }).then(done).catch(fail);
  } else if (type === 'goal_delete') {
    if (!confirm('Doel definitief verwijderen?')) { if (btn) { btn.disabled = false; btn.textContent = action.label; } return; }
    fetch('/api/goals/' + encodeURIComponent(action.id), { method:'DELETE' }).then(done).catch(fail);
  } else if (type === 'content_approve') {
    if (!confirm('Publiceren naar website + social. Doorgaan?')) { if (btn) { btn.disabled = false; btn.textContent = action.label; } return; }
    post('/api/content-queue/' + encodeURIComponent(action.id) + '/approve').then(done).catch(fail);
  } else if (type === 'content_reject') {
    post('/api/content-queue/' + encodeURIComponent(action.id) + '/reject').then(done).catch(fail);
  } else if (type === 'task_approve') {
    fetch('/api/tasks/' + encodeURIComponent(action.id) + '/status', { method:'PATCH', headers:{'Content-Type':'application/json'}, body: JSON.stringify({status:'done'}) })
      .then(function(r){ if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); }).then(done).catch(fail);
  } else if (type === 'dismiss') {
    post('/api/action-center/dismiss', { kind: action.dismiss_kind, ref_id: String(action.id) }).then(done).catch(fail);
  } else {
    fail(new Error('Onbekende actie: ' + type));
  }
}

// ── Systeemgezondheid (herbruikbaar: Control Room + per-project Dashboard) ──
function loadSystemHealth(elId, btnId) {
  elId = elId || 'system-health-panel';
  btnId = btnId || 'autoheal-btn';
  var el = document.getElementById(elId);
  if (!el) return;
  fetch('/api/strategist/health').then(function(r){return r.json();}).then(function(h){
    if (!el || h.error) return;
    if (h.ok) {
      el.innerHTML = '<div style="background:#dcfce7;border:1px solid #86efac;border-radius:8px;padding:10px 14px;margin-bottom:16px;font-size:12px;color:#166534;display:flex;align-items:center;gap:8px">' +
        '✅ Systeem gezond' +
        (h.last_autoheal && h.last_autoheal.time ? '<span style="color:#15803d;font-size:10px;margin-left:auto">laatste check: ' + h.last_autoheal.time.slice(11,16) + '</span>' : '') +
        '</div>';
      return;
    }
    var html = '<div style="background:#fef2f2;border:1px solid #fecaca;border-radius:8px;padding:12px 14px;margin-bottom:16px">' +
      '<p style="font-weight:600;font-size:13px;color:#991b1b;margin-bottom:8px">⚠️ Aandachtspunten</p>';

    (h.stalled_goals||[]).forEach(function(g){
      html += '<div style="display:flex;align-items:center;justify-content:space-between;gap:8px;padding:6px 8px;margin-bottom:4px;background:#fff;border:1px solid #fecaca;border-radius:6px">' +
        '<span style="font-size:11px;color:#7f1d1d"><b>' + escHtml(g.project||'') + '</b> — ‘' + escHtml(g.title) + '’ draait niet (verweesd na herstart)</span>' +
        '<button onclick="runAutoheal(\'' + elId + '\',\'' + btnId + '\')" id="' + btnId + '" style="padding:4px 10px;background:#dc2626;color:#fff;border:none;border-radius:4px;font-size:10px;font-weight:600;cursor:pointer;flex-shrink:0">Direct oplossen door agent</button>' +
        '</div>';
    });

    (h.failed_goals||[]).forEach(function(g){
      html += '<div style="display:flex;align-items:center;justify-content:space-between;gap:8px;padding:6px 8px;margin-bottom:4px;background:#fff;border:1px solid #fecaca;border-radius:6px">' +
        '<span style="font-size:11px;color:#7f1d1d"><b>' + escHtml(g.project||'') + '</b> — ‘' + escHtml(g.title) + '’ is mislukt</span>' +
        '<button onclick="retryFailedGoal(\'' + g.goal_id + '\')" style="padding:4px 10px;background:#dc2626;color:#fff;border:none;border-radius:4px;font-size:10px;font-weight:600;cursor:pointer;flex-shrink:0">Direct oplossen door agent</button>' +
        '</div>';
    });

    (h.failed_jobs||[]).forEach(function(j){
      var lr = j.last_run || {};
      var missed = lr.status === 'missed';
      html += '<p style="font-size:11px;color:' + (missed ? '#92400e' : '#991b1b') + ';margin-top:4px">- scheduler-taak <b>' + escHtml(j.label) + '</b> ' +
        (missed ? 'is gemist' : 'is mislukt') + ': ' + escHtml(lr.error || 'geen details — zie agentos.err') + '</p>';
    });

    if (!h.stalled_goals.length && !h.failed_goals.length && !h.failed_jobs.length) {
      (h.issues||[]).forEach(function(issue){
        html += '<p style="font-size:11px;color:#991b1b;margin-top:2px">- ' + escHtml(issue) + '</p>';
      });
    }

    html += '</div>';
    el.innerHTML = html;
  }).catch(function(){});
}

function runAutoheal(elId, btnId) {
  var btn = document.getElementById(btnId || 'autoheal-btn');
  if (btn) { btn.disabled = true; btn.textContent = 'Bezig...'; }
  fetch('/api/strategist/autoheal', { method:'POST' }).then(function(r){return r.json();}).then(function(){
    loadSystemHealth(elId, btnId);
  }).catch(function(){}).finally(function(){ if (btn) btn.disabled = false; });
}

// ── Strategist Agent (AI-analyse) ──
var strategistAnalysis = null;
function runStrategistAnalysis() {
  var btn = document.getElementById('strat-btn');
  var resultEl = document.getElementById('strategist-result');
  if (!btn || !resultEl) return;
  btn.disabled = true; btn.textContent = 'Bezig met analyseren...';
  resultEl.innerHTML = '<div class="loading" style="padding:20px"><div class="spinner"></div><p>Hermes analyseert alle projecten, doelen en kansen...</p></div>';
  fetch('/api/strategist/analyse', { method:'POST' }).then(function(r){return r.json();}).then(function(data){
    if (data.error) { resultEl.innerHTML = '<div class="empty-state">Fout: ' + escHtml(data.error) + '</div>'; return; }
    var analysis = data.analysis || '(geen analyse)';
    globalStrategistAnalysis = analysis;
    var html = '<div class="section-card" style="margin-top:16px">' +
      '<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:12px">' +
      '<h4 style="font-size:14px;font-weight:700">\u{1F9E0} Strategist — prioriteiten</h4>' +
      '<span style="font-size:10px;color:#94a3b8">' + (data.timestamp||'').slice(0,16) + '</span>' +
      '</div>' +
      '<div class="strategist-analyse-content">' +
      mdToHtml(analysis) +
      '</div>' +
      '<div style="margin-top:16px;padding-top:12px;border-top:1px solid #e2e8f0;text-align:center">' +
      '<button onclick="runStrategistExecute()" id="strat-exec-btn" style="padding:10px 24px;background:#059669;color:#fff;border:none;border-radius:8px;font-size:13px;font-weight:600;cursor:pointer">\u{2699}\u{FE0F} Zal ik de agents vragen dit uit te voeren?</button>' +
      '<div id="strategist-exec-result" style="margin-top:12px"></div>' +
      '</div></div>';
    resultEl.innerHTML = html;
    btn.textContent = '\u{1F9E0} Opnieuw analyseren';
  }).catch(function(e){
    resultEl.innerHTML = '<div class="empty-state">Fout: ' + escHtml(e.message) + '</div>';
  }).finally(function(){ btn.disabled = false; });
}

var globalStrategistAnalysis = null;
function runStrategistExecute() {
  if (!globalStrategistAnalysis) { alert('Geen analyse om uit te voeren'); return; }
  var execBtn = document.getElementById('strat-exec-btn');
  var execResult = document.getElementById('strategist-exec-result');
  if (!execBtn || !execResult) return;
  execBtn.disabled = true; execBtn.textContent = 'Bezig met uitvoeren...';
  execResult.innerHTML = '<div class="loading"><div class="spinner"></div><p>Hermes vertaalt prioriteiten naar concrete acties...</p></div>';
  fetch('/api/strategist/execute', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({analysis:globalStrategistAnalysis}) })
    .then(function(r){return r.json();}).then(function(data){
      if (data.error) { execResult.innerHTML = '<div class="empty-state" style="color:#ef4444">Fout: ' + escHtml(data.error) + '</div>'; return; }
      var actions = data.actions || [];
      var created = data.created_goals || [];
      var heal = data.autoheal || {};
      var healed = (heal.deleted||[]).length > 0 || (heal.resumed||[]).length > 0;
      var html = '';
      if (healed) {
        html += '<div style="background:#e0f2fe;border:1px solid #7dd3fc;border-radius:8px;padding:12px;margin-bottom:12px;text-align:left">' +
          '<p style="font-weight:600;font-size:13px;color:#075985;margin-bottom:4px">\u{1F527} Zelf-reparatie uitgevoerd</p>';
        (heal.deleted||[]).forEach(function(d){
          html += '<p style="font-size:11px;color:#075985;margin-top:2px">- verwijderd: ' + escHtml(d.title) + ' <span style="color:#0369a1">(' + escHtml(d.project) + ' — ' + escHtml(d.reason) + ')</span></p>';
        });
        (heal.resumed||[]).forEach(function(r){
          html += '<p style="font-size:11px;color:#075985;margin-top:2px">- hervat: ' + escHtml(r.title) + ' <span style="color:#0369a1">(' + escHtml(r.project) + ')</span></p>';
        });
        html += '</div>';
      }
      if (created.length > 0) {
        html += '<div style="background:#dcfce7;border:1px solid #86efac;border-radius:8px;padding:12px;margin-bottom:12px;text-align:left">' +
          '<p style="font-weight:600;font-size:13px;color:#166534;margin-bottom:4px">\u2705 Uitgevoerd!</p>' +
          '<p style="font-size:11px;color:#166534">' + created.length + ' doelen aangemaakt (' + (data.created_tasks||0) + ' taken)</p>';
        for (var g = 0; g < created.length; g++) {
          html += '<p style="font-size:11px;color:#166534;margin-top:4px">- ' + escHtml(created[g].title) + ' <span style="color:#15803d">(' + created[g].project + ')</span></p>';
        }
        html += '</div>';
      } else if (actions.length > 0) {
        html += '<div style="background:#fef3c7;border:1px solid #fde68a;border-radius:8px;padding:12px;margin-bottom:12px;text-align:left">' +
          '<p style="font-weight:600;font-size:13px;color:#92400e;margin-bottom:4px">\u{1F4CB} Geanalyseerde acties</p>';
        for (var a = 0; a < actions.length; a++) {
          var item = actions[a];
          var prioColor = item.priority === 'kritiek' ? '#ef4444' : item.priority === 'belangrijk' ? '#d97706' : '#64748b';
          html += '<div style="font-size:11px;padding:6px 8px;margin:4px 0;background:#fff;border-radius:4px;border:1px solid #f1f5f9">' +
            '<span style="display:inline-block;padding:1px 6px;border-radius:4px;background:' + prioColor + ';color:#fff;font-size:10px;margin-right:6px;text-transform:uppercase">' + escHtml(item.priority||'') + '</span>' +
            escHtml(item.action||'') +
            (item.target_project ? ' <span style="color:#64748b">(' + escHtml(item.target_project) + ')</span>' : '') +
            '</div>';
        }
        html += '</div>';
      }
      html += '<button onclick="window.location.reload()" style="padding:8px 20px;background:#4f46e5;color:#fff;border:none;border-radius:6px;font-size:12px;cursor:pointer">\u{1F504} Vernieuw dashboard</button>';
      execResult.innerHTML = html;
      execBtn.textContent = '\u{2699}\u{FE0F} Opnieuw uitvoeren';
    }).catch(function(e){
      execResult.innerHTML = '<div class="empty-state" style="color:#ef4444">Fout: ' + escHtml(e.message) + '</div>';
    }).finally(function(){ execBtn.disabled = false; });
}

function renderSidebar() {
  return '<div class="sidebar"><div class="sidebar-logo"><img src="logo.png" alt="AO" onerror="this.style.display=\'none\'"><span>Agent OS</span></div><nav class="sidebar-nav">' +
    (currentProject ? TABS.map(function(t) { return '<button class="' + (t===currentTab?' active':'') + '" onclick="switchView(\''+t+'\')"><span class="icon">' + (TAB_ICONS[t]||'') + '</span>' + t + '</button>'; }).join('') : '') +
    '</nav><div class="sidebar-footer">' + (currentProject ? '<button onclick="switchView(\'chat\')"><span class="icon">o</span>Chat</button>' : '') +
    '<button onclick="goHome()"><span class="icon"><-</span>Projecten</button></div></div>';
}
function renderHeader() {
  if (!currentProject) return '';
  return '<div class="project-header"><div><h1>' + escHtml(currentProject) + ' <span id="agent-status-indicator" style="margin-left:6px;vertical-align:middle"></span></h1><p class="meta">' + escHtml(currentTab) + ' &middot; ' + escHtml(DESCS[currentProject]||'') + '</p></div>' +
    '<div class="actions">' + (currentTab !== 'Dashboard' ? '<button onclick="switchView(\'Dashboard\')">Dashboard</button>' : '') +
    '<button onclick="switchView(\'chat\')">Chat</button><button onclick="togglePrint()" class="no-print">Export</button></div></div>';
}

function renderProjectView(main) {
  main.innerHTML = renderSidebar() + '<div class="main-content">' + renderHeader() + '<div class="tab-content" id="tab-content"><div class="loading"><div class="spinner"></div><p>' + currentTab + ' laden...</p></div></div></div>';
  startAgentStatusPoll();
  loadCurrentTab();
}
async function loadCurrentTab() {
  var el = document.getElementById('tab-content'); if (!el) return;
  try {
    if (currentTab === 'Dashboard') await renderDashboardTab(el);
    else if (currentTab === 'Content') await renderContentTab(el);
    else if (currentTab === 'Kansen') await renderKansenTab(el);
    else if (currentTab === 'Optimalisatie') await renderOptimalisatieTab(el);
    else if (currentTab === 'Wachtrij') await renderWachtrijTab(el);
    else if (currentTab === 'Concurrentie') await renderConcurrentieTab(el);
    else if (currentTab === 'Radar') await renderRadarTab(el);
    else if (currentTab === 'Keywords') await renderKeywordsTab(el);
    else if (currentTab === 'Doelen') await renderDoelenTab(el);
    else if (currentTab === 'Geheugen') await renderGeheugenTab(el);
    else if (currentTab === 'Leads') await renderLeadsTab(el);
    else if (currentTab === 'Opdrachten') await renderOpdrachtenTab(el);
    else if (currentTab === 'Technisch') await renderTechTab(el);
    else if (currentTab === 'Activiteit') await renderActiviteitTab(el);
    else if (currentTab === 'Instellingen') await renderInstellingenTab(el);
    else el.innerHTML = '<div class="empty-state">Tab niet gevonden</div>';
  } catch(e) { el.innerHTML = '<div class="empty-state">Fout: ' + escHtml(e.message) + '</div>'; }
}

// ═══════════════════════════════════════════════════════════════════
//  DASHBOARD TAB — Wereldklasse dashboard met status, advies, alerts
// ═══════════════════════════════════════════════════════════════════
function renderAdviceBanner(advice) {
  if (!advice || !advice.banner) return '';
  var b = advice.banner;
  var bgColor, icon, borderColor, actionAttr = '';
  if (b.type === 'running') { bgColor = '#f0fdf4'; icon = '▶️'; borderColor = '#16a34a'; }
  else if (b.type === 'failed') { bgColor = '#fef2f2'; icon = '❌'; borderColor = '#ef4444'; actionAttr = ' onclick="retryFailedGoal(\'' + b.action.replace('retry_goal:','') + '\')" style="cursor:pointer"'; }
  else { bgColor = '#f8fafc'; icon = '○'; borderColor = '#64748b'; }
  return '<div class="dash-status-banner" style="background:' + bgColor + ';border-left:4px solid ' + borderColor + ';border-radius:8px;padding:10px 14px;margin-bottom:12px;display:flex;align-items:center;gap:10px"' + actionAttr + '>' +
    '<span style="font-size:16px">' + icon + '</span>' +
    '<span style="font-size:13px;font-weight:600;color:#1e293b;flex:1">' + escHtml(b.text) + '</span>' +
    (advice.running_goal ? '<div style="display:flex;align-items:center;gap:6px"><div style="width:80px;height:6px;background:#e2e8f0;border-radius:3px;overflow:hidden"><div style="height:100%;width:' + advice.running_goal.percent + '%;background:#16a34a;border-radius:3px;transition:width .5s"></div></div><span style="font-size:11px;color:#64748b">' + advice.running_goal.progress + '</span></div>' : '') +
    '</div>';
}

// ── Houdt de statusbanner op de Dashboard-tab live (pollt elke 8s zolang je op deze tab/project blijft) ──
let _dashBannerTimer = null;
let _dashPollTick = 0;
function startDashboardBannerPoll(project) {
  stopDashboardBannerPoll();
  _dashPollTick = 0;
  _dashBannerTimer = setInterval(function() {
    if (currentProject !== project || currentTab !== 'Dashboard') { stopDashboardBannerPoll(); return; }
    fetch('/api/projects/' + encodeURIComponent(project) + '/advice?days=28').then(function(r){return r.json();}).then(function(advice) {
      if (currentProject !== project || currentTab !== 'Dashboard') return;
      var c = document.getElementById('dash-banner-container');
      if (c) c.innerHTML = renderAdviceBanner(advice);
    }).catch(function(){});
    // Systeemgezondheid elke ~24s mee-verversen, zodat een opgelost probleem
    // (bv. gemiste job vóór een herstart) niet als spookmelding blijft staan.
    _dashPollTick++;
    if (_dashPollTick % 3 === 0) loadSystemHealth('system-health-panel-proj', 'autoheal-btn-proj');
  }, 8000);
}
function stopDashboardBannerPoll() { if (_dashBannerTimer) { clearInterval(_dashBannerTimer); _dashBannerTimer = null; } }

async function renderDashboardTab(el) {
  if (currentProject === 'Finance Expert') { renderFinanceExpert(el); return; }
  el.innerHTML = '<div class="loading"><div class="spinner"></div><p>Dashboard laden...</p></div>';
  try {
    var [gscResp, trendResp, adviceResp, goalsResp] = await Promise.all([
      fetch('/api/projects/' + encodeURIComponent(currentProject) + '/dashboard?days=28'),
      fetch('/api/projects/' + encodeURIComponent(currentProject) + '/trends?days=28'),
      fetch('/api/projects/' + encodeURIComponent(currentProject) + '/advice?days=28'),
      fetch('/api/goals?project=' + encodeURIComponent(currentProject) + '&limit=5'),
    ]);
    var gsc = await gscResp.json(), trend = await trendResp.json();
    var advice = await adviceResp.json(), goals = await goalsResp.json();
  } catch(e) { el.innerHTML = '<div class="empty-state">Fout: ' + escHtml(e.message) + '</div>'; return; }

  // ── BUILD HTML ──────────────────────────────────────────────
  var html = '';

  // ── 0. SYSTEEMGEZONDHEID ──
  html += '<div id="system-health-panel-proj"></div>';

  // ── 1. STATUS BANNER (live, herbouwd door startDashboardBannerPoll) ──
  html += '<div id="dash-banner-container">' + renderAdviceBanner(advice) + '</div>';

  // ── 2. ALERTS (hele card klikbaar - tekst + knop) ──
  if (advice && advice.alerts && advice.alerts.length) {
    advice.alerts.forEach(function(a) {
      var bg = a.type === 'danger' ? '#fef2f2' : a.type === 'warning' ? '#fffbeb' : a.type === 'opportunity' ? '#f0fdf4' : '#f8fafc';
      var border = a.type === 'danger' ? '#fecaca' : a.type === 'warning' ? '#fde68a' : a.type === 'opportunity' ? '#bbf7d0' : '#e2e8f0';
      var hasAction = !!a.action;
      var onClickAttr = hasAction ? ' onclick="handleAdviceAction(this,' + "'" + escHtml(a.action) + "'" + ')" style="cursor:pointer"' : '';
      var actionBtn = hasAction ? '<button onclick="event.stopPropagation();handleAdviceAction(this,' + "'" + escHtml(a.action) + "'" + ')" style="padding:4px 14px;background:#059669;color:#fff;border:none;border-radius:6px;font-size:11px;font-weight:600;cursor:pointer;flex-shrink:0;white-space:nowrap">' + escHtml(a.action_label || 'Oplossen') + '</button>' : '';
      html += '<div class="dash-alert" ' + onClickAttr + ' style="display:flex;align-items:center;gap:8px;padding:8px 12px;margin-bottom:6px;background:' + bg + ';border:1px solid ' + border + ';border-radius:6px;transition:all .15s">' +
        '<span style="font-size:14px;flex-shrink:0">' + (a.icon||'') + '</span>' +
        '<span style="font-size:12px;color:#475569;line-height:1.5;flex:1">' + escHtml(a.text) + '</span>' + actionBtn + '</div>';
    });
  }

  // ── 3. NEXT STEP  // ── 3. NEXT STEP + QUICK ACTIONS ──
  if (advice && advice.next_step) {
    html += '<div class="section-card" style="margin-bottom:16px;background:linear-gradient(135deg,#eef2ff,#f8fafc);border:1px solid #e0e7ff">' +
      '<div style="display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:8px">' +
      '<div style="display:flex;align-items:center;gap:8px;flex:1;min-width:200px"><span style="font-size:18px;flex-shrink:0">🎯</span><span style="font-size:13px;font-weight:600;color:#1e293b;flex-shrink:0">Beste volgende stap:</span><span style="font-size:12px;color:#475569">' + escHtml(advice.next_step) + '</span></div>' +
      '<div style="display:flex;gap:6px;flex-wrap:wrap;align-items:center">' +
      (advice.next_step_action ? '<button onclick="handleAdviceAction(this,\'' + escHtml(advice.next_step_action) + '\')" style="padding:7px 18px;background:#059669;color:#fff;border:none;border-radius:6px;font-size:12px;font-weight:600;cursor:pointer">Nu uitvoeren</button>' : '') +
      (advice.quick_actions && advice.quick_actions.length ? advice.quick_actions.map(function(qa) {
        var isPrimary = qa.primary ? ';background:#4f46e5;color:#fff;border:none' : ';background:#fff;color:#475569;border:1px solid #e2e8f0';
        return '<button onclick="handleAdviceAction(this,\'' + escHtml(qa.action) + '\')" style="padding:6px 14px;border-radius:6px;font-size:11px;font-weight:600;cursor:pointer;transition:all .15s' + isPrimary + '">' + escHtml(qa.label) + '</button>';
      }).join('') : '') +
      '</div></div></div>';
  }

  // ── 4. CHARTS (alleen als trends data heeft) ──
  // Elke meetreeks zijn eigen as: klikken en impressies verschillen 10-40x in
  // schaal — samen op één grafiek met dubbele y-as misleidt het oog.
  if (trend && trend.daily && trend.daily.length > 0) {
    html += '<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:12px;margin-bottom:16px">' +
      '<div class="section-card"><h3>Klikken (28 dagen)</h3><div style="position:relative;height:180px"><canvas id="dash-chart-clicks"></canvas></div></div>' +
      '<div class="section-card"><h3>Impressies (28 dagen)</h3><div style="position:relative;height:180px"><canvas id="dash-chart-impressions"></canvas></div></div>' +
      '<div class="section-card"><h3>Gemiddelde positie (28 dagen)</h3><div style="position:relative;height:180px"><canvas id="dash-chart-position"></canvas></div></div>' +
      '</div>';
  }

  // ── 5. KPI GRID ──
  if (gsc.error || !gsc.summary) {
    html += '<div class="empty-state"><p style="font-size:15px;font-weight:600;color:#475569;margin-bottom:4px">Nog geen data</p><p style="color:#94a3b8">' + escHtml(gsc.error||'Geen GSC-data') + '</p></div>';
  } else {
    var s = gsc.summary;
    html += '<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:12px"><h3 style="font-size:15px;font-weight:700">Prestatieoverzicht</h3>' +
      '<div class="actions no-print" style="display:flex;gap:6px"><button onclick="switchView(\'Concurrentie\')" style="padding:5px 12px;background:#f1f5f9;color:#475569;border:1px solid #e2e8f0;border-radius:6px;font-size:11px;cursor:pointer">Trends & Analyse</button><button onclick="togglePrint()" style="padding:5px 12px;background:#f1f5f9;color:#475569;border:1px solid #e2e8f0;border-radius:6px;font-size:11px;cursor:pointer">Export PDF</button></div></div>' +
      '<div class="kpi-grid">' +
      kpiBox('Geïndexeerd', s.indexed_pages, s.indexed_pages_change,'') + kpiBox('Klikken', s.total_clicks, s.total_clicks_change,'') +
      kpiBox('CTR', s.avg_ctr + '%', '', s.total_impressions + ' impressies') +
      // avg_position_change = vorige - huidige: positief = verbeterd (groen), negatief = verslechterd (rood)
      kpiBox('Positie', s.avg_position, (s.avg_position_change !== undefined ? s.avg_position_change.toFixed(1) : ''), '') +
      '</div>' +
      '<div class="grid-2">' +
      '<div class="section-card"><h3>Top pagina\'s</h3>' + tbl(gsc.top_pages||[], ['pagina','page'], ['Klikken','clicks'], ['Impressies','impressions'], ['CTR','ctr'], ['Positie','position']) + '</div>' +
      '<div class="section-card"><h3>Top zoekwoorden</h3>' + tbl(gsc.top_queries||[], ['zoekwoord','query'], ['Klikken','clicks'], ['Impressies','impressions'], ['CTR','ctr'], ['Positie','position']) + '</div></div>';
    if (gsc.page_comparison && gsc.page_comparison.length) html += '<div class="section-card"><h3>Week-op-week</h3>' + tbl(gsc.page_comparison.slice(0,10), ['pagina','page'], ['Klikken (deze)','clicks_current'], ['Verschil','clicks_change'], ['Positie','position_current'], ['Pos. verschil','position_change']) + '</div>';
  }

  // ── 6. DOELEN op dashboard ──
  if (goals && goals.length) {
    html += '<div class="section-card" style="margin-bottom:16px">' +
      '<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:8px"><h3>🎯 Doelen (' + goals.length + ')</h3><button onclick="switchView(\'Doelen\')" style="padding:4px 10px;background:#f1f5f9;color:#475569;border:1px solid #e2e8f0;border-radius:4px;font-size:10px;cursor:pointer">Beheer</button></div>' +
      goals.slice(0,3).map(function(g) {
        var statusColor = {draft:'#f1f5f9',ready:'#dbeafe',running:'#fef3c7',paused:'#f1f5f9',completed:'#dcfce7',failed:'#fecaca'}[g.status]||'#f1f5f9';
        var total = g.task_count || 1;
        var done = g.completed_tasks || 0;
        var pct = total > 0 ? Math.round(done/total*100) : 0;
        return '<div style="display:flex;align-items:center;gap:8px;padding:6px 0;border-bottom:1px solid #f1f5f9;font-size:12px">' +
          '<span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:' + (g.status==='running'?'#f59e0b':g.status==='completed'?'#16a34a':g.status==='failed'?'#ef4444':'#94a3b8') + '"></span>' +
          '<span style="flex:1;color:#1e293b">' + escHtml(g.title) + '</span>' +
          '<span style="padding:1px 6px;border-radius:4px;font-size:9px;background:' + statusColor + ';font-weight:600;color:' + (g.status==='running'?'#92400e':'#475569') + '">' + g.status + '</span>' +
          (g.status==='running' ? '<span style="font-size:10px;color:#64748b">' + done + '/' + total + '</span>' : '') +
          '</div>';
      }).join('') +
      '</div>';
  }

  // ── 7. ACTIVITY ──
    html += '<div class="section-card"><div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:8px">' +
      '<h3 style="font-size:13px;font-weight:700">📜 Activiteit</h3>' +
      '<button onclick="loadProjectActivityLogs()" style="padding:2px 8px;background:#f1f5f9;color:#475569;border:1px solid #e2e8f0;border-radius:4px;font-size:10px;cursor:pointer">Ververs</button></div>' +
      '<div id="project-activity-panel" style="background:#0f172a;border-radius:8px;padding:8px;font-family:monospace;font-size:11px;max-height:200px;overflow-y:auto">' +
      '<div style="color:#64748b;text-align:center;padding:12px">Laden...</div></div></div>';

    el.innerHTML = html;

  // ── RENDER CHARTS ──
  if (trend && trend.daily && trend.daily.length) {
    renderSeriesChart('dash-chart-clicks', trend.daily, 'clicks', 'Klikken', '#4f46e5');
    renderSeriesChart('dash-chart-impressions', trend.daily, 'impressions', 'Impressies', '#d97706');
    renderPositionChart('dash-chart-position', trend.daily, trend.prev_period || []);
  }

  // ── LOAD ACTIVITY ──
  loadProjectActivityLogs();
  loadSystemHealth('system-health-panel-proj', 'autoheal-btn-proj');
  startDashboardBannerPoll(currentProject);
}

// ── Handle advice quick actions ──
function handleAdviceAction(btn, action) {
  if (action.startsWith('fix_alert:')) {
    var objective = action.slice('fix_alert:'.length);
    solveAlert(objective, btn);
    return;
  }
  if (action === 'write_all_kansen') { writeAllNewKansen(); return; }
  if (action === 'run_scan') { switchViewThen('Kansen', runDemandScan); return; }
  if (action === 'generate_suggestions') { switchViewThen('Content', generateSuggestions); return; }
  if (action === 'new_goal') { switchViewThen('Doelen', showNewGoalForm); return; }
  if (action.startsWith('retry_goal:')) { retryFailedGoal(action.split(':')[1]); return; }
  if (action.startsWith('open_tab:')) { switchView(action.split(':')[1]); return; }
  if (action.startsWith('write_article:')) {
    var keyword = action.split(':').slice(1).join(':');
    writeArticleForKeyword(keyword, btn);
    return;
  }
  console.warn('Onbekende action:', action);
}

// ── Start een write-and-publish job en poll de voortgang, met live update op de knop die de actie startte ──
async function runArticlePipeline(payload, btn) {
  var origLabel = btn ? btn.textContent : null;
  var origWidth = btn ? btn.offsetWidth : null;
  if (btn) { btn.disabled = true; if (origWidth) btn.style.minWidth = origWidth + 'px'; btn.textContent = 'Starten...'; }
  try {
    var startResp = await fetch('/api/projects/' + encodeURIComponent(currentProject) + '/write-and-publish', {
      method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify(payload)
    });
    var start = await startResp.json();
    if (!start.job_id) return {success: false, detail: start.detail || 'Kon job niet starten'};

    while (true) {
      await new Promise(function(r){ setTimeout(r, 1500); });
      var st = await (await fetch('/api/projects/' + encodeURIComponent(currentProject) + '/write-and-publish/' + start.job_id)).json();
      if (btn) btn.textContent = (st.percent || 0) + '% — ' + (st.phase || 'bezig...');
      if (st.status === 'done') return st.result;
      if (st.status === 'error') return {success: false, detail: st.error || 'onbekende fout'};
    }
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = origLabel; btn.style.minWidth = ''; }
  }
}

// ── Navigeer naar een tab en voer daarna een actie uit (geeft de tab tijd om te renderen) ──
function switchViewThen(view, callback) {
  switchView(view);
  setTimeout(callback, 350);
}

// ── Maak een doel aan en start de agent er direct op, zonder tussenscherm ──
async function createAndStartGoal(title, objective, project, btn) {
  var origLabel = btn ? btn.textContent : null;
  var origWidth = btn ? btn.offsetWidth : null;
  if (btn) { btn.disabled = true; if (origWidth) btn.style.minWidth = origWidth + 'px'; btn.style.opacity = '0.7'; btn.textContent = 'Plan maken...'; }
  try {
    var planResp = await fetch('/api/goals/plan', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({title: title, objective: objective, project: project || currentProject || 'Dashboard'}),
    });
    var plan = await planResp.json();
    if (!plan || !plan.goal_id) { alert('❌ Kon geen plan genereren: ' + (plan.detail || plan.error || 'onbekende fout')); return; }
    if (btn) btn.textContent = 'Starten...';
    await fetch('/api/goals/confirm', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({goal_id: plan.goal_id}) });
    await fetch('/api/goals/start', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({goal_id: plan.goal_id}) });
    if (btn) btn.textContent = '✅ Gestart';
    alert('✅ Agent gestart: "' + title + '"');
    loadCurrentTab();
  } catch(e) { alert('❌ Fout: ' + e.message); }
  finally { if (btn) { btn.disabled = false; btn.style.opacity = ''; btn.textContent = origLabel; btn.style.minWidth = ''; } }
}

function startGoalFromAction(opts, btn) {
  if (!confirm('Agent laten starten met: "' + opts.title + '"?')) return;
  createAndStartGoal(opts.title, opts.objective || opts.title, opts.project, btn);
}

function solveAlert(objective, btn) {
  var title = 'Actiepunt: ' + objective.slice(0, 60);
  if (!confirm('Agent dit laten oplossen?\n\n"' + objective.slice(0, 160) + (objective.length > 160 ? '...' : '') + '"')) return;
  createAndStartGoal(title, objective, currentProject, btn);
}

// ── Bouw een consistent statusbericht na de schrijf/optimalisatie-pipeline ──
function formatSeoResultMsg(result) {
  var msg = 'SEO-score: ' + result.seo_score + '/10';
  if (result.optimization_rounds) msg += ' (na ' + result.optimization_rounds + ' optimalisatieronde' + (result.optimization_rounds>1?'n':'') + ')';
  msg += result.world_class ? '\n🏆 Wereldklasse-niveau bereikt' : '\n⚠️ Nog niet op wereldklasse-niveau (streefwaarde 8.5/10)';
  if (!result.world_class && result.seo_review && result.seo_review.verbeterpunten && result.seo_review.verbeterpunten.length) {
    msg += '\nResterende verbeterpunten: ' + result.seo_review.verbeterpunten.slice(0,3).join('; ');
  }
  return msg;
}
// ── Write article directly from keyword (via suggestions flow) ──
async function writeArticleForKeyword(keyword, btn) {
  if (!confirm('Schrijf een artikel voor "' + keyword + '"?')) return;
  var title = keyword.charAt(0).toUpperCase() + keyword.slice(1);
  try {
    var result = await runArticlePipeline({title: title, rationale: 'SEO-kans uit GSC: veel impressies maar 0 klikken', keyword: keyword}, btn);
    if (result.success) {
      alert('✅ Artikel geschreven!\nTitel: ' + result.title + ' (' + result.word_count + ' woorden)\n' + formatSeoResultMsg(result));
      loadCurrentTab();
    } else {
      alert('❌ Mislukt: ' + (result.detail || 'onbekend'));
    }
  } catch(e) { alert('❌ Fout: ' + e.message); }
}

function tbl(items, labelA, ...cols) {
  if (!items||!items.length) return '<p style="color:#94a3b8;font-size:12px;text-align:center;padding:16px">Geen data</p>';
  return '<table class="data-table"><thead><tr><th>' + labelA[0] + '</th>' + cols.map(function(c){return '<th class="num">'+c[0]+'</th>';}).join('') + '</tr></thead><tbody>' +
    items.map(function(it){
      var cell = '<td class="url-cell">' + escHtml(it[labelA[1]]||it.page||it.query||'-') + '</td>';
      var vals = cols.map(function(c){
        var v = it[c[1]]; if (v===undefined||v===null) return '<td class="num" style="color:#94a3b8">-</td>';
        if (c[1]==='ctr') return '<td class="num">' + (typeof v==='number'?v.toFixed(1):v) + '%</td>';
        if (c[1]==='position'||c[1]==='position_current') return '<td class="num" style="'+(v<=5?'color:#16a34a':v<=15?'color:#d97706':'')+'">'+(typeof v==='number'?v.toFixed(1):v)+'</td>';
        if (c[1]==='clicks_change'||c[1]==='position_change') { var cls=v>0?'color:#16a34a':v<0?'color:#ef4444':'color:#94a3b8'; return '<td class="num" style="'+cls+'">'+(v>0?'+':'')+(typeof v==='number'?v.toFixed(1):v)+'</td>'; }
        return '<td class="num">'+v+'</td>';
      }).join('');
      return '<tr>' + cell + vals + '</tr>';
    }).join('') + '</tbody></table>';
}

// ═══════════════════════════════════════════════════════════════════
//  CONTENT TAB
// ═══════════════════════════════════════════════════════════════════
async function renderContentTab(el) {
  if (currentProject === 'Finance Expert') { el.innerHTML = '<div class="empty-state">Geen content</div>'; return; }
  el.innerHTML = '<div class="loading"><div class="spinner"></div><p>Content laden...</p></div>';
  try { var d = await (await fetch('/api/projects/' + encodeURIComponent(currentProject) + '/content')).json(); }
  catch(e) { el.innerHTML = '<div class="empty-state">Fout: ' + escHtml(e.message) + '</div>'; return; }
  var html = '<div class="grid-2">';
  var gsc = d.gsc_pages||[];
  html += '<div class="section-card"><h3>Live pagina\'s (' + gsc.length + ')</h3>' + (gsc.length ? '<table class="data-table"><thead><tr><th>Pagina</th><th class="num">Clicks</th><th class="num">Positie</th></tr></thead><tbody>' + gsc.slice(0,20).map(function(p){return '<tr><td class="url-cell"><span class="badge badge-live">live</span> ' + escHtml(p.title) + '</td><td class="num">' + p.clicks + '</td><td class="num">' + (typeof p.position==='number'?p.position.toFixed(1):p.position) + '</td></tr>';}).join('') + '</tbody></table>' + (gsc.length>20?'<p style="font-size:11px;color:#94a3b8;margin-top:6px">+ nog ' + (gsc.length-20) + '</p>':''):'<p style="color:#94a3b8;font-size:12px;padding:16px;text-align:center">Geen GSC-data</p>') + '</div>';
  var cf=d.content_files||[], le=d.log_entries||[], zf=d.zzp_opdrachten||[];
  function fileRow(kind, badgeClass, badgeLabel, f) {
    return '<div onclick="openContentFile(\''+kind+'\',\''+encodeURIComponent(f.name)+'\')" style="font-size:12px;padding:3px 0;cursor:pointer;color:#2563eb" onmouseover="this.style.textDecoration=\'underline\'" onmouseout="this.style.textDecoration=\'none\'"><span class="badge '+badgeClass+'" style="color:inherit;text-decoration:none">'+badgeLabel+'</span> '+escHtml(f.name.replace(/-/g,' '))+'</div>';
  }
  html += '<div class="section-card"><h3>Lokale bestanden (' + (cf.length+le.length+zf.length) + ')</h3>' + (cf.length?'<p style="font-size:11px;color:#64748b;margin-bottom:4px">Concepten (klik om te lezen)</p>'+cf.map(function(f){return fileRow('content','badge-draft','concept',f);}).join(''):'') + (le.length?'<p style="font-size:11px;color:#64748b;margin:8px 0 4px">Logboek</p>'+le.map(function(f){return fileRow('log','badge-log','log',f);}).join(''):'') + (zf.length?'<p style="font-size:11px;color:#64748b;margin:8px 0 4px">ZZP</p>'+zf.map(function(f){return fileRow('zzp','badge-zzp','zzp',f);}).join(''):'') + (!cf.length&&!le.length&&!zf.length?'<p style="color:#94a3b8;font-size:12px;padding:16px;text-align:center">Geen lokale bestanden</p>':'') + '</div></div>';
  html += '<div class="section-card"><h3>Blog suggesties</h3><div id="sug-container">' + (weSuggestions.length ? renderSuggestions() : '<p style="color:#94a3b8;font-size:12px;text-align:center;padding:12px">Klik op "Genereer suggesties" voor AI-blog-onderwerpen.</p>') + '</div><button onclick="generateSuggestions()" id="sug-btn" style="margin-top:10px;padding:6px 16px;background:#4f46e5;color:#fff;border:none;border-radius:6px;font-size:12px;cursor:pointer">Genereer suggesties</button></div>';
  el.innerHTML = html;
}
async function openContentFile(kind, encodedName) {
  var name = decodeURIComponent(encodedName);
  var overlay = document.createElement('div');
  overlay.id = 'file-modal-overlay';
  overlay.style.cssText = 'position:fixed;inset:0;background:rgba(15,23,42,.5);display:flex;align-items:center;justify-content:center;z-index:1000;padding:24px';
  var footer = kind === 'content' ? (
    '<div style="display:flex;gap:8px;flex-wrap:wrap;padding:10px 16px;border-top:1px solid #e2e8f0;background:#f8fafc">' +
    '<button onclick="analyzeContentFile(\''+kind+'\',\''+encodedName+'\')" style="padding:6px 14px;background:#4f46e5;color:#fff;border:none;border-radius:6px;font-size:11px;cursor:pointer;font-weight:600">🔍 Analyseer als SEO-expert</button>' +
    '<button onclick="generateSocialCopyForFile(\''+kind+'\',\''+encodedName+'\')" style="padding:6px 14px;background:#0891b2;color:#fff;border:none;border-radius:6px;font-size:11px;cursor:pointer;font-weight:600">📣 Maak social media teksten</button>' +
    '</div>'
  ) : '';
  overlay.innerHTML = '<div style="background:#fff;border-radius:10px;max-width:800px;width:100%;max-height:85vh;display:flex;flex-direction:column;overflow:hidden">' +
    '<div style="display:flex;justify-content:space-between;align-items:center;padding:12px 16px;border-bottom:1px solid #e2e8f0"><h3 style="font-size:14px;font-weight:700">'+escHtml(name.replace(/-/g,' '))+'</h3><button onclick="closeContentFile()" style="background:none;border:none;font-size:18px;cursor:pointer;color:#64748b;line-height:1">✕</button></div>' +
    '<div style="overflow:auto;flex:1"><div id="file-modal-body" style="padding:16px;font-size:13px;line-height:1.6">Laden...</div><div id="file-modal-results" style="padding:0 16px 16px"></div></div>' +
    footer + '</div>';
  overlay.addEventListener('click', function(e){ if (e.target === overlay) closeContentFile(); });
  document.body.appendChild(overlay);
  try {
    var res = await fetch('/api/projects/' + encodeURIComponent(currentProject) + '/content-file?kind=' + encodeURIComponent(kind) + '&file=' + encodeURIComponent(name));
    var data = await res.json();
    var body = document.getElementById('file-modal-body');
    if (!res.ok) { body.innerHTML = '<p style="color:#dc2626">'+escHtml(data.detail||'Kon bestand niet laden')+'</p>'; return; }
    if (data.extension === '.html') {
      body.innerHTML = data.content.replace(/^---[\s\S]*?---\n*/, '');
    } else {
      body.innerHTML = '<pre style="white-space:pre-wrap;font-family:inherit">'+escHtml(data.content)+'</pre>';
    }
  } catch(e) {
    var body = document.getElementById('file-modal-body'); if (body) body.innerHTML = '<p style="color:#dc2626">Fout: '+escHtml(e.message)+'</p>';
  }
}
function closeContentFile() {
  var overlay = document.getElementById('file-modal-overlay');
  if (overlay) overlay.remove();
}
async function analyzeContentFile(kind, encodedName) {
  var results = document.getElementById('file-modal-results'); if (!results) return;
  results.innerHTML = '<div style="margin-top:10px;padding:10px;background:#f1f5f9;border-radius:8px;font-size:12px;color:#64748b">SEO-expert analyseert...</div>';
  try {
    var res = await fetch('/api/projects/' + encodeURIComponent(currentProject) + '/content-file/analyze?kind=' + encodeURIComponent(kind) + '&file=' + encodeURIComponent(decodeURIComponent(encodedName)), {method:'POST'});
    var data = await res.json();
    if (!res.ok) { results.innerHTML = '<div style="margin-top:10px;color:#dc2626;font-size:12px">'+escHtml(data.detail||'Analyse mislukt')+'</div>'; return; }
    var color = data.score>=85?'#16a34a':(data.score>=60?'#d97706':'#dc2626');
    results.innerHTML = '<div style="margin-top:10px;padding:12px;border:1px solid #e2e8f0;border-radius:8px">' +
      '<div style="display:flex;align-items:center;justify-content:space-between;gap:8px;margin-bottom:6px">' +
      '<span style="font-size:13px;font-weight:700;color:'+color+'">Score: '+data.score+'/100'+(data.score>=85?' 🏆':'')+'</span>' +
      (data.score<85?'<button onclick="applyContentFileFeedback(this,\''+kind+'\',\''+encodedName+'\')" style="padding:5px 12px;background:#4f46e5;color:#fff;border:none;border-radius:6px;font-size:11px;cursor:pointer;font-weight:600">✓ Pas toe</button>':'') +
      '</div>' +
      '<div style="font-size:12px;color:#334155;white-space:pre-wrap">'+escHtml(data.feedback||'(geen feedback)')+'</div></div>';
  } catch(e) { results.innerHTML = '<div style="margin-top:10px;color:#dc2626;font-size:12px">Fout: '+escHtml(e.message)+'</div>'; }
}
async function applyContentFileFeedback(btn, kind, encodedName) {
  var results = document.getElementById('file-modal-results');
  var origLabel = btn.textContent;
  btn.disabled = true; btn.textContent = 'Toepassen (kan ~30-60s duren)...';
  try {
    var res = await fetch('/api/projects/' + encodeURIComponent(currentProject) + '/content-file/optimize?kind=' + encodeURIComponent(kind) + '&file=' + encodeURIComponent(decodeURIComponent(encodedName)), {method:'POST'});
    var data = await res.json();
    if (!res.ok) { alert('Toepassen mislukt: ' + (data.detail||'onbekende fout')); btn.disabled = false; btn.textContent = origLabel; return; }
    // Ververs zowel het artikel als de score met de nieuwe, opgeslagen versie
    var bodyEl = document.getElementById('file-modal-body');
    if (bodyEl) bodyEl.innerHTML = data.extension === '.html' ? data.content.replace(/^---[\s\S]*?---\n*/, '') : '<pre style="white-space:pre-wrap;font-family:inherit">'+escHtml(data.content)+'</pre>';
    var color = data.score>=85?'#16a34a':(data.score>=60?'#d97706':'#dc2626');
    if (results) results.innerHTML = '<div style="margin-top:10px;padding:12px;border:1px solid #e2e8f0;border-radius:8px">' +
      '<div style="display:flex;align-items:center;justify-content:space-between;gap:8px;margin-bottom:6px">' +
      '<span style="font-size:13px;font-weight:700;color:'+color+'">Nieuwe score: '+data.score+'/100'+(data.score>=85?' 🏆':'')+' (na '+data.rounds+' ronde'+(data.rounds!==1?'n':'')+')</span>' +
      (data.score<85?'<button onclick="applyContentFileFeedback(this,\''+kind+'\',\''+encodedName+'\')" style="padding:5px 12px;background:#4f46e5;color:#fff;border:none;border-radius:6px;font-size:11px;cursor:pointer;font-weight:600">✓ Nogmaals toepassen</button>':'') +
      '</div><div style="font-size:12px;color:#334155;white-space:pre-wrap">'+escHtml(data.feedback||'(geen feedback)')+'</div></div>';
  } catch(e) { alert('Fout: ' + e.message); btn.disabled = false; btn.textContent = origLabel; }
}
async function generateSocialCopyForFile(kind, encodedName) {
  var results = document.getElementById('file-modal-results'); if (!results) return;
  results.innerHTML = '<div style="margin-top:10px;padding:10px;background:#f1f5f9;border-radius:8px;font-size:12px;color:#64748b">Social teksten genereren...</div>';
  try {
    var res = await fetch('/api/projects/' + encodeURIComponent(currentProject) + '/content-file/social-copy?kind=' + encodeURIComponent(kind) + '&file=' + encodeURIComponent(decodeURIComponent(encodedName)), {method:'POST'});
    var data = await res.json();
    if (!res.ok) { results.innerHTML = '<div style="margin-top:10px;color:#dc2626;font-size:12px">'+escHtml(data.detail||'Genereren mislukt')+'</div>'; return; }
    window._fileSocialCopy = data.social_copy || {};
    var platforms = [['linkedin','LinkedIn'],['facebook','Facebook'],['instagram','Instagram'],['twitter','X / Twitter']];
    results.innerHTML = '<div style="margin-top:10px;display:flex;flex-direction:column;gap:8px">' + platforms.map(function(p){
      var text = window._fileSocialCopy[p[0]] || '(geen tekst)';
      return '<div style="border:1px solid #e2e8f0;border-radius:8px;padding:10px"><div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px"><span style="font-size:11px;font-weight:700;color:#475569">'+p[1]+'</span><button onclick="copySocialText(this,\''+p[0]+'\')" style="padding:2px 8px;background:#fff;border:1px solid #cbd5e1;border-radius:4px;font-size:10px;cursor:pointer">Kopieer</button></div><div style="font-size:12px;color:#334155;white-space:pre-wrap">'+escHtml(text)+'</div></div>';
    }).join('') + '</div>';
  } catch(e) { results.innerHTML = '<div style="margin-top:10px;color:#dc2626;font-size:12px">Fout: '+escHtml(e.message)+'</div>'; }
}
function copySocialText(btn, platform) {
  var text = (window._fileSocialCopy || {})[platform] || '';
  navigator.clipboard.writeText(text).then(function(){
    var orig = btn.textContent; btn.textContent = 'Gekopieerd ✓';
    setTimeout(function(){ btn.textContent = orig; }, 1500);
  });
}
function renderSuggestions() {
  if (!weSuggestions.length) return '<p style="color:#94a3b8;font-size:12px;text-align:center;padding:12px">Geen suggesties</p>';
  return weSuggestions.map(function(sug,i){return '<div style="border:1px solid #e2e8f0;border-radius:6px;padding:10px;margin-bottom:6px"><div style="display:flex;justify-content:space-between;align-items:flex-start"><div><p style="font-weight:600;font-size:13px">' + escHtml(sug.title) + '</p><p style="font-size:11px;color:#64748b;margin-top:2px">' + escHtml(sug.rationale) + '</p><div style="display:flex;gap:6px;margin-top:4px"><span style="font-size:10px;padding:1px 6px;background:#f1f5f9;border-radius:4px;color:#475569">' + escHtml(sug.keyword) + '</span><span style="font-size:10px;padding:1px 6px;background:#f1f5f9;border-radius:4px;color:#475569">' + escHtml(sug.estimated_hours||'?') + '</span></div></div><button onclick="publishSuggestion(this,' + i + ')" style="padding:4px 12px;background:#4f46e5;color:#fff;border:none;border-radius:4px;font-size:11px;cursor:pointer">Schrijf &amp; publiceer</button></div></div>';}).join('');
}
async function generateSuggestions() {
  var btn = document.getElementById('sug-btn'); if (!btn) return;
  btn.disabled = true; btn.textContent = 'Genereren...';
  try {
    var data = await (await fetch('/api/projects/' + encodeURIComponent(currentProject) + '/suggest-blogs?days=28', {method:'POST'})).json();
    weSuggestions = data.suggestions || [];
    var cont = document.getElementById('sug-container'); if (cont) cont.innerHTML = renderSuggestions();
  } catch(e) { alert('Fout: ' + e.message); }
  btn.disabled = false; btn.textContent = 'Genereer suggesties';
}
async function publishSuggestion(btn, index) {
  var sug = weSuggestions[index]; if (!sug) return;
  if (!confirm('Schrijf artikel: "' + sug.title + '"?')) return;
  try {
    var result = await runArticlePipeline({title:sug.title, rationale:sug.rationale, keyword:sug.keyword}, btn);
    if (result.success) {
      weSuggestions.splice(index,1); var cont = document.getElementById('sug-container'); if (cont) cont.innerHTML = renderSuggestions();
      var msg = 'Artikel opgeslagen als ' + result.local_path;
      if (result.word_count) msg += ' (' + result.word_count + ' woorden)';
      msg += '\n' + formatSeoResultMsg(result);
      if (result.ping_results) { var pings=[]; for(var k in result.ping_results) pings.push(k+': '+(result.ping_results[k]===200?'OK':result.ping_results[k])); if(pings.length) msg+='\nPings: '+pings.join(', '); }
      alert(msg);
    } else alert('Mislukt: ' + (result.detail||'onbekend'));
  } catch(e) { alert('Fout: ' + e.message); }
}

// ═══════════════════════════════════════════════════════════════════
//  KANSEN TAB
// ═══════════════════════════════════════════════════════════════════
function renderOppStepper(status) {
  if (status === 'dismissed') {
    return '<div style="display:flex;align-items:center;gap:6px;margin:8px 0;padding:4px 8px;background:#f8fafc;border-radius:6px;font-size:11px;color:#94a3b8">⊘ Genegeerd — niet in behandeling</div>';
  }
  var steps = [['new','Nieuw'],['in_progress','In behandeling'],['published','Gepubliceerd']];
  var idx = 0;
  steps.forEach(function(s,i){ if (s[0]===status) idx=i; });
  var html = '<div style="display:flex;align-items:flex-start;margin:10px 0 8px">';
  steps.forEach(function(s,i){
    var done = i < idx, current = i === idx;
    var circleBg = done ? '#4f46e5' : '#fff';
    var circleBorder = (done||current) ? '#4f46e5' : '#e2e8f0';
    var inner = done ? '<span style="color:#fff;font-size:9px;line-height:1">✓</span>' : (current ? '<span style="width:6px;height:6px;border-radius:50%;background:#4f46e5;display:block"></span>' : '');
    html += '<div style="display:flex;flex-direction:column;align-items:center;gap:4px;min-width:64px">' +
      '<div style="width:16px;height:16px;border-radius:50%;background:'+circleBg+';border:2px solid '+circleBorder+';display:flex;align-items:center;justify-content:center">'+inner+'</div>' +
      '<span style="font-size:10px;font-weight:'+(current?'700':'500')+';color:'+(current?'#1e293b':(done?'#475569':'#94a3b8'))+'">'+s[1]+'</span>' +
      '</div>';
    if (i < steps.length-1) html += '<div style="flex:1;height:2px;background:'+(i<idx?'#4f46e5':'#e2e8f0')+';margin:7px 2px 0"></div>';
  });
  html += '</div>';
  return html;
}
async function renderKansenTab(el) {
  if (scanningInProgress) { el.innerHTML = '<div class="loading"><div class="spinner"></div><p>Scan bezig... GSC-data ophalen + AI-analyse (20-60 sec).</p></div>'; return; }
  el.innerHTML = '<div class="loading"><div class="spinner"></div><p>Kansen laden...</p></div>';
  try { var data = await (await fetch('/api/projects/' + encodeURIComponent(currentProject) + '/kansen' + (oppStatusFilter?'?status='+oppStatusFilter:''))).json(); }
  catch(e) { el.innerHTML = '<div class="empty-state">Fout: ' + escHtml(e.message) + '</div>'; return; }
  if (data.error) { el.innerHTML = '<div class="empty-state">' + escHtml(data.error) + '</div>'; return; }
  var kansen = data.kansen || [];
  window._kansenData = kansen;
  var newCount = kansen.filter(function(o){return o.status==='new';}).length;
  var html = '<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:14px;flex-wrap:wrap;gap:8px"><div><h3 style="font-size:15px;font-weight:700">Striking distance kansen (' + kansen.length + ')</h3>' + (newCount>0?'<p style="font-size:11px;color:#64748b;margin-top:2px">' + newCount + ' nieuwe kansen</p>':'') + '</div><div style="display:flex;gap:6px;flex-wrap:wrap">' +
    '<select id="kansen-filter" onchange="oppStatusFilter=this.value;renderKansenTab(document.getElementById(\'tab-content\'))" style="padding:4px 8px;border:1px solid #e2e8f0;border-radius:6px;font-size:11px;background:#fff">' +
    '<option value="">Alle</option><option value="new">Nieuw (' + kansen.filter(function(o){return o.status==='new';}).length + ')</option><option value="in_progress">In behandeling</option><option value="published">Gepubliceerd</option><option value="dismissed">Genegeerd</option></select>' +
    (newCount>=2?'<button onclick="writeAllNewKansen(this)" style="padding:4px 12px;background:#059669;color:#fff;border:none;border-radius:6px;font-size:11px;cursor:pointer">Schrijf alle ' + newCount + '</button>':'') +
    '<button onclick="runDemandScan()" style="padding:4px 12px;background:#4f46e5;color:#fff;border:none;border-radius:6px;font-size:11px;cursor:pointer">Scan uitvoeren</button></div></div>';
  if (!kansen.length) { el.innerHTML = html + '<div class="empty-state"><p style="font-size:14px;font-weight:600;color:#475569;margin-bottom:4px">Nog geen kansen</p><p style="color:#94a3b8">Voer een scan uit</p></div>'; return; }
  kansen.forEach(function(opp, idx) {
    var sc = ({new:'#dbeafe',in_progress:'#fef3c7',published:'#dcfce7',dismissed:'#f1f5f9'})[opp.status]||'#f1f5f9';
    var st = ({new:'Nieuw',in_progress:'In behandeling',published:'Gepubliceerd',dismissed:'Genegeerd'})[opp.status]||opp.status;
    var score = typeof opp.opportunity_score==='number'?opp.opportunity_score.toFixed(0):opp.opportunity_score;
    var pos = typeof opp.position==='number'?opp.position:10;
    var GOAL_POS = 3;
    var posPct = function(p){ return Math.max(0, Math.min(100, ((20-p)/19)*100)); };
    var curPct = posPct(pos), goalPct = posPct(GOAL_POS), atGoal = pos <= GOAL_POS;
    var barColor = atGoal ? '#16a34a' : (pos<=10?'#4f46e5':'#d97706');
    html += '<div class="opp-card" style="'+(opp.status==='new'?'border-left:3px solid #4f46e5;':'')+'"><div class="opp-header"><div style="flex:1"><div style="display:flex;align-items:center;gap:8px;margin-bottom:4px"><p class="opp-query">'+escHtml(opp.query)+'</p><span style="font-size:10px;padding:2px 8px;border-radius:6px;background:'+sc+';font-weight:600">'+st+'</span><span style="font-size:10px;padding:2px 8px;border-radius:6px;background:'+(opp.action==='re-optimaliseren'?'#fef3c7':'#dbeafe')+'">'+(opp.action==='re-optimaliseren'?'Heroptimaliseren':'Nieuwe content')+'</span></div>' +
    '<div class="opp-meta"><span style="color:#16a34a;font-weight:600">'+opp.clicks+' clicks</span><span>'+opp.impressions+' impressies</span><span>Pos. '+pos.toFixed(1)+'</span><span style="font-weight:600">Score '+score+'</span></div></div></div>' +
    (opp.angle?'<div class="opp-angle" style="margin-top:6px">'+escHtml(opp.angle)+'</div>':'') + (opp.rationale?'<div class="opp-rationale" style="margin-top:4px">'+escHtml(opp.rationale)+'</div>':'') +
    renderOppStepper(opp.status) +
    '<div style="margin:2px 0 8px">' +
      '<div style="display:flex;align-items:flex-end;margin-bottom:1px">' +
        '<div style="width:50px"></div>' +
        '<div style="flex:1;position:relative;height:13px">' +
          '<span style="position:absolute;left:'+goalPct+'%;transform:translateX(-50%);font-size:9px;font-weight:700;color:#059669;white-space:nowrap">▼ doel: top 3</span>' +
        '</div>' +
        '<div style="width:64px"></div>' +
      '</div>' +
      '<div style="display:flex;align-items:center;gap:8px">' +
        '<span style="font-size:10px;color:#94a3b8;width:50px">Pos. '+pos.toFixed(1)+'</span>' +
        '<div style="flex:1;position:relative;height:6px;background:#e2e8f0;border-radius:3px">' +
          '<div style="position:absolute;top:0;left:0;height:100%;width:'+curPct+'%;background:'+barColor+';border-radius:3px;transition:width .3s"></div>' +
          '<div style="position:absolute;top:-2px;left:'+goalPct+'%;width:2px;height:10px;background:#059669;transform:translateX(-1px)"></div>' +
        '</div>' +
        '<span style="font-size:10px;width:64px;text-align:right;color:'+(atGoal?'#16a34a':'#94a3b8')+';font-weight:'+(atGoal?'700':'400')+'">'+(atGoal?'✓ doel bereikt':'positie 1 →')+'</span>' +
      '</div>' +
    '</div>' +
    '<div class="opp-actions" style="display:flex;gap:6px;flex-wrap:wrap">' +
    ((opp.status==='new'||opp.status==='in_progress')?'<button onclick="writeArticleFromOpp(this,'+idx+')" style="padding:5px 14px;background:#059669;color:#fff;border:none;border-radius:6px;font-size:11px;cursor:pointer;font-weight:600">Schrijf artikel</button>':'') +
    (opp.status==='new'?'<button onclick="updateOppStatus(\''+opp.id+'\',\'in_progress\')" style="padding:5px 12px;background:#fff;color:#92400e;border:1.5px solid #f59e0b;border-radius:6px;font-size:10px;cursor:pointer;font-weight:600">→ Pak aan</button>':'') +
    (opp.status==='in_progress'?'<button onclick="updateOppStatus(\''+opp.id+'\',\'published\')" style="padding:5px 12px;background:#fff;color:#166534;border:1.5px solid #16a34a;border-radius:6px;font-size:10px;cursor:pointer;font-weight:600">✓ Markeer gepubliceerd</button>':'') +
    (opp.status!=='dismissed'?'<button onclick="updateOppStatus(\''+opp.id+'\',\'dismissed\')" style="padding:5px 12px;background:#fff;color:#475569;border:1.5px solid #cbd5e1;border-radius:6px;font-size:10px;cursor:pointer;font-weight:600">✕ Negeren</button>':'') +
    (opp.status==='dismissed'?'<button onclick="updateOppStatus(\''+opp.id+'\',\'new\')" style="padding:5px 12px;background:#fff;color:#1e40af;border:1.5px solid #3b82f6;border-radius:6px;font-size:10px;cursor:pointer;font-weight:600">↺ Heropen</button>':'') +
    '</div></div>';
  });
  el.innerHTML = html;
}
async function writeArticleFromOpp(btn, idx) {
  var opp = window._kansenData && window._kansenData[idx]; if (!opp) { alert('Kans niet gevonden'); return; }
  if (!confirm('Schrijf artikel voor kans: "'+opp.query+'"?')) return;
  try {
    var result = await runArticlePipeline({title: opp.angle||opp.query, rationale: opp.rationale||'SEO-kans', keyword: opp.query}, btn);
    if (result.success) {
      await fetch('/api/demand/opportunities/'+opp.id, { method:'PATCH', headers:{'Content-Type':'application/json'}, body: JSON.stringify({status:'in_progress'}) });
      alert('Artikel opgeslagen: '+result.local_path+'\n'+formatSeoResultMsg(result));
      renderKansenTab(document.getElementById('tab-content'));
    } else alert('Mislukt: '+(result.detail||'onbekend'));
  } catch(e) { alert('Fout: '+e.message); }
}
async function writeAllNewKansen(btn) {
  if (!confirm('Schrijf artikelen voor ALLE nieuwe kansen? Dit kan even duren.')) return;
  var data = await (await fetch('/api/projects/' + encodeURIComponent(currentProject) + '/kansen')).json();
  var newKansen = (data.kansen||[]).filter(function(o){return o.status==='new';});
  if (!newKansen.length) { alert('Geen nieuwe kansen'); return; }
  if (btn) btn.disabled = true;
  var written = 0;
  for (var i=0; i<newKansen.length; i++) {
    var opp = newKansen[i];
    var prefix = 'Artikel ' + (i+1) + '/' + newKansen.length + ': ';
    var fakeBtn = btn ? {
      style: {},
      set textContent(v) { btn.textContent = prefix + v; },
      get textContent() { return btn.textContent; }
    } : null;
    try {
      var wr = await runArticlePipeline({title: opp.angle||opp.query, rationale: opp.rationale||'SEO-kans', keyword: opp.query}, fakeBtn);
      if (wr.success) { await fetch('/api/demand/opportunities/'+opp.id, { method:'PATCH', headers:{'Content-Type':'application/json'}, body: JSON.stringify({status:'in_progress'}) }); written++; }
    } catch(e) {}
  }
  alert(written+'/'+newKansen.length+' artikelen geschreven.'); renderKansenTab(document.getElementById('tab-content'));
}
async function updateOppStatus(oppId, status) {
  try { await fetch('/api/demand/opportunities/'+oppId, { method:'PATCH', headers:{'Content-Type':'application/json'}, body: JSON.stringify({status:status}) }); renderKansenTab(document.getElementById('tab-content')); }
  catch(e) { alert('Fout: '+e.message); }
}
async function runDemandScan() {
  try {
    var sites = await (await fetch('/api/sites')).json();
    var norm = function(s){return s.name.toLowerCase().replace(/ /g,'').replace(/-/g,'');};
    var target = norm({name:currentProject});
    var site = sites.find(function(s){return norm(s) === target;});
    if (!site) { alert('Site niet gevonden voor project: ' + currentProject); return; }
    scanningInProgress = true; var el = document.getElementById('tab-content'); if (el) renderKansenTab(el);
    await fetch('/api/demand/scan', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({site_id:site.id, days:90}) });
    var attempts = 0;
    var poll = setInterval(async function() {
      attempts++;
      try {
        var cd = await (await fetch('/api/projects/' + encodeURIComponent(currentProject) + '/kansen')).json();
        if ((cd.kansen && cd.kansen.length > 0) || attempts >= 12) { clearInterval(poll); scanningInProgress = false; renderKansenTab(document.getElementById('tab-content')); }
        if (scanningInProgress && el) el.innerHTML = '<div class="loading"><div class="spinner"></div><p>Scan bezig... ' + (attempts*5) + 's</p></div>';
      } catch(e) { clearInterval(poll); scanningInProgress = false; renderKansenTab(document.getElementById('tab-content')); }
    }, 5000);
  } catch(e) { alert('Fout: '+e.message); scanningInProgress = false; }
}

// ═══════════════════════════════════════════════════════════════════
//  WACHTRIJ TAB — auto-gegenereerde blog + social-copy, wacht op goedkeuring
//  (2x/week scheduler zet hier concepten klaar; NOOIT automatisch gepost)
// ═══════════════════════════════════════════════════════════════════
var wachtrijStatusFilter = 'pending_review';
var wachtrijPlatformLabels = { linkedin: 'LinkedIn', facebook: 'Facebook', instagram: 'Instagram', twitter: 'X / Twitter' };

async function renderWachtrijTab(el) {
  el.innerHTML = '<div class="loading"><div class="spinner"></div><p>Wachtrij laden...</p></div>';
  var jobs;
  try { jobs = await (await fetch('/api/projects/' + encodeURIComponent(currentProject) + '/content-queue' + (wachtrijStatusFilter ? '?status=' + wachtrijStatusFilter : ''))).json(); }
  catch(e) { el.innerHTML = '<div class="empty-state">Fout: ' + escHtml(e.message) + '</div>'; return; }
  window._wachtrijJobs = jobs || [];

  var html = '<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:14px;flex-wrap:wrap;gap:8px">' +
    '<div><h3 style="font-size:15px;font-weight:700">Content-wachtrij</h3>' +
    '<p style="font-size:11px;color:#64748b;margin-top:2px">2x/week (di + vr) zet de scheduler hier automatisch een concept klaar. Niets gaat live zonder jouw goedkeuring.</p></div>' +
    '<div style="display:flex;gap:6px;flex-wrap:wrap">' +
    '<select id="wachtrij-filter" onchange="wachtrijStatusFilter=this.value;renderWachtrijTab(document.getElementById(\'tab-content\'))" style="padding:4px 8px;border:1px solid #e2e8f0;border-radius:6px;font-size:11px;background:#fff">' +
    '<option value="pending_review"' + (wachtrijStatusFilter==='pending_review'?' selected':'') + '>Te reviewen</option>' +
    '<option value="published"' + (wachtrijStatusFilter==='published'?' selected':'') + '>Gepubliceerd</option>' +
    '<option value="rejected"' + (wachtrijStatusFilter==='rejected'?' selected':'') + '>Afgewezen</option>' +
    '<option value=""' + (wachtrijStatusFilter===''?' selected':'') + '>Alle</option></select>' +
    '<button onclick="runWachtrijNow(this)" style="padding:4px 12px;background:#4f46e5;color:#fff;border:none;border-radius:6px;font-size:11px;cursor:pointer">Genereer nu</button></div></div>';

  if (!jobs || !jobs.length) {
    el.innerHTML = html + '<div class="empty-state"><p style="font-size:14px;font-weight:600;color:#475569;margin-bottom:4px">Niets te reviewen</p><p style="color:#94a3b8">Wacht op de volgende scheduler-run (di/vr 09:00) of klik "Genereer nu"</p></div>';
    return;
  }

  jobs.forEach(function(job, idx) {
    var sc = ({pending_review:'#dbeafe',published:'#dcfce7',rejected:'#fee2e2'})[job.status]||'#f1f5f9';
    var st = ({pending_review:'Te reviewen',published:'Gepubliceerd',rejected:'Afgewezen'})[job.status]||job.status;
    var score = typeof job.seo_score==='number'?job.seo_score.toFixed(0):job.seo_score;
    html += '<div class="opp-card"><div class="opp-header"><div style="flex:1"><div style="display:flex;align-items:center;gap:8px;margin-bottom:4px">' +
      '<p class="opp-query">' + escHtml(job.title) + '</p>' +
      '<span style="font-size:10px;padding:2px 8px;border-radius:6px;background:' + sc + ';font-weight:600">' + st + '</span></div>' +
      '<div class="opp-meta"><span>Zoekwoord: ' + escHtml(job.keyword||'-') + '</span><span style="font-weight:600">SEO-score ' + score + '/100</span></div></div></div>' +
      '<details style="margin-top:8px"><summary style="cursor:pointer;font-size:11px;color:#4f46e5;font-weight:600">Blog-voorbeeld</summary>' +
      '<div class="prose-dark" style="margin-top:6px;padding:10px;background:#f8fafc;border-radius:6px;max-height:260px;overflow:auto;font-size:12px">' + (job.blog_html||'') + '</div></details>' +
      '<div style="margin-top:8px;display:flex;flex-wrap:wrap;gap:8px">' +
      Object.keys(wachtrijPlatformLabels).map(function(p) {
        var copy = (job.social_copy||{})[p];
        if (!copy) return '';
        return '<details style="flex:1;min-width:200px;background:#f8fafc;border-radius:6px;padding:8px"><summary style="cursor:pointer;font-size:11px;font-weight:600;color:#475569">' + wachtrijPlatformLabels[p] + '</summary>' +
          '<div style="white-space:pre-wrap;font-size:11px;color:#334155;margin-top:6px">' + escHtml(copy) + '</div></details>';
      }).join('') + '</div>' +
      (job.image_path ? '<img src="data:image/png;base64,' + job.image_path + '" style="margin-top:8px;max-width:180px;border-radius:6px;border:1px solid #e2e8f0" />' : '') +
      (job.status==='pending_review' ? '<div class="opp-actions" style="margin-top:10px;display:flex;gap:6px;flex-wrap:wrap">' +
        '<button onclick="approveWachtrijJob(this,\'' + job.id + '\')" style="padding:6px 16px;background:#059669;color:#fff;border:none;border-radius:6px;font-size:11px;cursor:pointer;font-weight:600">Goedkeuren &amp; publiceren</button>' +
        '<button onclick="regenerateWachtrijJob(this,\'' + job.id + '\')" style="padding:6px 12px;background:#fef3c7;color:#92400e;border:1px solid #fde68a;border-radius:6px;font-size:11px;cursor:pointer">Opnieuw genereren</button>' +
        '<button onclick="rejectWachtrijJob(this,\'' + job.id + '\')" style="padding:6px 12px;background:#f1f5f9;color:#475569;border:1px solid #e2e8f0;border-radius:6px;font-size:11px;cursor:pointer">Afwijzen</button></div>' : '') +
      (job.status==='published' && job.publish_result ? '<div style="margin-top:8px;font-size:11px;color:#64748b">' + renderPublishResult(job.publish_result) + '</div>' : '') +
      '</div>';
  });
  el.innerHTML = html;
}

function renderPublishResult(pr) {
  var parts = [];
  if (pr.netlify && pr.netlify.url) parts.push('Netlify: <a href="' + pr.netlify.url + '" target="_blank">' + pr.netlify.url + '</a>');
  if (pr.gsc && pr.gsc.status) parts.push('GSC: ' + pr.gsc.status);
  if (pr.bing && pr.bing.status_code) parts.push('Bing: ' + pr.bing.status_code);
  if (pr.social) {
    Object.keys(pr.social).forEach(function(p) {
      var r = pr.social[p];
      parts.push((wachtrijPlatformLabels[p]||p) + ': ' + (r.success ? 'gepost' : 'mislukt (' + escHtml((r.error||'').slice(0,80)) + ')'));
    });
  }
  return parts.join(' · ');
}

async function runWachtrijNow(btn) {
  if (btn) { btn.disabled = true; btn.textContent = 'Bezig...'; }
  try {
    var resp = await (await fetch('/api/projects/' + encodeURIComponent(currentProject) + '/content-queue/run-now', { method: 'POST' })).json();
    if (resp.success) { wachtrijStatusFilter = 'pending_review'; renderWachtrijTab(document.getElementById('tab-content')); }
    else alert(resp.detail || 'Geen nieuwe kansen — voer eerst een Demand Engine-scan uit.');
  } catch(e) { alert('Fout: ' + e.message); }
  if (btn) { btn.disabled = false; btn.textContent = 'Genereer nu'; }
}

async function approveWachtrijJob(btn, jobId) {
  if (!confirm('Publiceren + posten naar alle geconfigureerde platformen. Doorgaan?')) return;
  if (btn) { btn.disabled = true; btn.textContent = 'Publiceren...'; }
  try {
    var resp = await fetch('/api/projects/' + encodeURIComponent(currentProject) + '/content-queue/' + jobId + '/approve', { method: 'POST' });
    var data = await resp.json();
    if (!resp.ok) { alert('Mislukt: ' + (data.detail || 'onbekende fout')); if (btn) btn.disabled = false; return; }
    renderWachtrijTab(document.getElementById('tab-content'));
  } catch(e) { alert('Fout: ' + e.message); if (btn) btn.disabled = false; }
}

async function rejectWachtrijJob(btn, jobId) {
  if (!confirm('Dit concept afwijzen?')) return;
  try {
    await fetch('/api/projects/' + encodeURIComponent(currentProject) + '/content-queue/' + jobId + '/reject', { method: 'POST' });
    renderWachtrijTab(document.getElementById('tab-content'));
  } catch(e) { alert('Fout: ' + e.message); }
}

async function regenerateWachtrijJob(btn, jobId) {
  if (btn) { btn.disabled = true; btn.textContent = 'Herschrijven...'; }
  try {
    var resp = await fetch('/api/projects/' + encodeURIComponent(currentProject) + '/content-queue/' + jobId + '/regenerate', { method: 'POST' });
    if (!resp.ok) { var data = await resp.json(); alert('Mislukt: ' + (data.detail || 'onbekende fout')); if (btn) btn.disabled = false; return; }
    renderWachtrijTab(document.getElementById('tab-content'));
  } catch(e) { alert('Fout: ' + e.message); if (btn) btn.disabled = false; }
}

// ═══════════════════════════════════════════════════════════════════
//  CONCURRENTIE TAB (Trends + PageSpeed)
// ═══════════════════════════════════════════════════════════════════
async function renderConcurrentieTab(el) {
  el.innerHTML = '<div class="loading"><div class="spinner"></div><p>Trends laden...</p></div>';
  try {
    var [trendResp, speedResp, gapResp] = await Promise.all([
      fetch('/api/projects/' + encodeURIComponent(currentProject) + '/trends?days=28'),
      fetch('/api/projects/' + encodeURIComponent(currentProject) + '/pagespeed?strategy=mobile'),
      fetch('/api/projects/' + encodeURIComponent(currentProject) + '/keyword-gaps?days=28'),
    ]);
    var trends = await trendResp.json(), speed = await speedResp.json(), gaps = await gapResp.json();
  } catch(e) { el.innerHTML = '<div class="empty-state">Fout: ' + escHtml(e.message) + '</div>'; return; }

  var html = '<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:16px"><h3 style="font-size:15px;font-weight:700">Concurrentie &amp; Analyse</h3></div>';

  // ── Trend grafieken ──
  html += '<div class="grid-2">';
  html += '<div class="section-card"><h3>Klikken (28 dagen)</h3><div style="position:relative;height:180px"><canvas id="chart-clicks"></canvas></div></div>';
  html += '<div class="section-card"><h3>Impressies (28 dagen)</h3><div style="position:relative;height:180px"><canvas id="chart-impressions"></canvas></div></div>';
  html += '<div class="section-card"><h3>Gemiddelde positie (28 dagen)</h3><div style="position:relative;height:180px"><canvas id="chart-position"></canvas></div></div>';
  html += '</div>';

  // ── PageSpeed Scores ──
  if (speed && speed.scores) {
    html += '<div class="section-card"><h3>Core Web Vitals (homepage - mobile)</h3><div class="kpi-grid" style="grid-template-columns:repeat(4,1fr)">';
    var scoreLabels = {performance:'Performance',accessibility:'Toegankelijkheid',seo:'SEO',best_practices:'Best Practices'};
    for (var sk in speed.scores) {
      var sc = speed.scores[sk];
      var color = sc >= 90 ? '#16a34a' : sc >= 50 ? '#d97706' : '#ef4444';
      html += '<div class="kpi-card"><p class="label">' + (scoreLabels[sk]||sk) + '</p><p class="value" style="color:' + color + '">' + (sc !== null ? sc + '' : '-') + '</p></div>';
    }
    html += '</div>';
    // Core Web Vitals metrics
    html += '<table class="data-table" style="margin-top:8px"><thead><tr><th>Metric</th><th class="num">Waarde</th><th>Doel</th></tr></thead><tbody>';
    var metricInfo = {lcp:['LCP (laadtijd)', '≤2.5s'], fcp:['FCP', '≤1.8s'], tbt:['TBT', '≤200ms'], cls:['CLS', '≤0.1'], si:['Speed Index', '≤3.4s']};
    for (var mk in speed.metrics) {
      var mi = metricInfo[mk] || [mk, '-'];
      html += '<tr><td>' + mi[0] + '</td><td class="num">' + speed.metrics[mk] + '</td><td class="num" style="color:#94a3b8">' + mi[1] + '</td></tr>';
    }
    html += '</tbody></table>';
    if (currentTab === 'Concurrentie') { // only show desktop toggle on this tab
      html += '<div style="margin-top:8px"><button onclick="loadDesktopSpeed()" style="padding:4px 12px;background:#4f46e5;color:#fff;border:none;border-radius:6px;font-size:11px;cursor:pointer">Desktop test</button></div>';
    }
    html += '</div>';
  }

  // ── Keyword Gaps ──
  if (gaps && gaps.gaps) {
    html += '<div class="section-card"><h3>Kansen: hoge impressies, lage CTR</h3>' +
      (gaps.gaps.length ? '<table class="data-table"><thead><tr><th>Zoekwoord</th><th class="num">Impressies</th><th class="num">CTR</th><th class="num">Positie</th></tr></thead><tbody>' +
        gaps.gaps.slice(0,10).map(function(q){return '<tr><td class="url-cell">'+escHtml(q.query)+'</td><td class="num">'+q.impressions+'</td><td class="num" style="color:#ef4444">'+q.ctr+'%</td><td class="num">'+(typeof q.position==='number'?q.position.toFixed(1):q.position)+'</td></tr>';}).join('') +
        '</tbody></table>' : '<p style="color:#94a3b8;font-size:12px;text-align:center;padding:12px">Geen gaps gevonden</p>') +
      '</div>';
  }

  el.innerHTML = html;

  // ── Render Charts ──
  if (trends && trends.daily && trends.daily.length) {
    renderSeriesChart('chart-clicks', trends.daily, 'clicks', 'Klikken', '#4f46e5');
    renderSeriesChart('chart-impressions', trends.daily, 'impressions', 'Impressies', '#d97706');
    renderPositionChart('chart-position', trends.daily, trends.prev_period || []);
  }
  window.loadDesktopSpeed = function() {
    var btn = event.target; btn.disabled = true; btn.textContent = 'Laden...';
    fetch('/api/projects/' + encodeURIComponent(currentProject) + '/pagespeed?strategy=desktop').then(function(r){return r.json();}).then(function(data){
      var html = '<div class="section-card"><h3>Desktop scores</h3><div class="kpi-grid" style="grid-template-columns:repeat(4,1fr)">';
      for (var sk in data.scores) {
        var sc = data.scores[sk];
        var color = sc >= 90 ? '#16a34a' : sc >= 50 ? '#d97706' : '#ef4444';
        html += '<div class="kpi-card"><p class="label">'+(scoreLabels[sk]||sk)+'</p><p class="value" style="color:'+color+'">'+(sc!==null?sc:'-')+'</p></div>';
      }
      html += '</div></div>';
      var tc = document.getElementById('tab-content');
      if (tc) tc.innerHTML += html;
      else el.innerHTML += html;
    }).catch(function(){alert('Fout bij laden desktop speed');}).finally(function(){btn.disabled=false;btn.textContent='Desktop test';});
  };
}

// Eén meetreeks per grafiek — de kaarttitel benoemt de reeks, dus geen legenda nodig.
function renderSeriesChart(canvasId, daily, field, label, color) {
  var canvas = document.getElementById(canvasId); if (!canvas) return;
  if (chartInstances[canvasId]) { chartInstances[canvasId].destroy(); delete chartInstances[canvasId]; }
  var ctx = canvas.getContext('2d');
  chartInstances[canvasId] = new Chart(ctx, {
    type: 'line', data: {
      labels: daily.map(function(d){return d.date.slice(5);}),
      datasets: [{ label: label, data: daily.map(function(d){return d[field];}), borderColor: color, backgroundColor: color + '1a', fill: true, tension: .3, pointRadius: 2 }]
    },
    options: { animation: false, responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false } },
      scales: { y: { beginAtZero: true, grid: { color: '#f1f5f9' }, ticks: { font: { size: 9 }, precision: 0 } }, x: { grid: { display: false }, ticks: { font: { size: 9 }, maxTicksLimit: 14 } } }
    }
  });
}
function renderPositionChart(canvasId, daily, prev) {
  var canvas = document.getElementById(canvasId); if (!canvas) return;
  if (chartInstances[canvasId]) { chartInstances[canvasId].destroy(); delete chartInstances[canvasId]; }
  var ctx = canvas.getContext('2d');
  // Reverse position axis (lower = better)
  chartInstances[canvasId] = new Chart(ctx, {
    type: 'line', data: {
      labels: daily.map(function(d){return d.date.slice(5);}),
      datasets: [{ label: 'Positie (lager = beter)', data: daily.map(function(d){return d.position;}), borderColor: '#16a34a', backgroundColor: 'rgba(22,163,74,.08)', fill: true, tension: .3, pointRadius: 2,
        segment: { borderColor: function(ctx){return ctx.p0.parsed.y < ctx.p1.parsed.y ? '#ef4444' : '#16a34a';} }
      }]
    },
    options: { animation: false, responsive: true, maintainAspectRatio: false, plugins: { legend: { position: 'top', labels: { boxWidth: 12, padding: 8, font: { size: 10 } } } },
      scales: { y: { reverse: true, grid: { color: '#f1f5f9' }, ticks: { font: { size: 9 } } }, x: { grid: { display: false }, ticks: { font: { size: 9 }, maxTicksLimit: 14 } } }
    }
  });
}

// ═══════════════════════════════════════════════════════════════════
//  KEYWORDS TAB
// ═══════════════════════════════════════════════════════════════════
async function renderKeywordsTab(el) {
  el.innerHTML = '<div class="loading"><div class="spinner"></div><p>Keyword data laden...</p></div>';
  try {
    var gapResp = await fetch('/api/projects/' + encodeURIComponent(currentProject) + '/keyword-gaps?days=28');
    var gaps = await gapResp.json();
  } catch(e) { el.innerHTML = '<div class="empty-state">Fout: ' + escHtml(e.message) + '</div>'; return; }
  if (gaps.error) { el.innerHTML = '<div class="empty-state">' + escHtml(gaps.error) + '</div>'; return; }

  var html = '<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:16px"><h3 style="font-size:15px;font-weight:700">Keyword Research</h3></div>' +
    '<div class="kpi-grid" style="grid-template-columns:repeat(4,1fr)">' +
    kpiBox('Totaal queries', gaps.total_queries||0, '', '') +
    kpiBox('Totaal klikken', (gaps.categories&&gaps.categories.clicks)||0, '', '') +
    kpiBox('Gem. CTR', (gaps.categories&&gaps.categories.avg_ctr)+'%'||'', '', '') +
    kpiBox('Gem. positie', (gaps.categories&&gaps.categories.avg_position)||'', '', '') +
    '</div>';

  // Best performers
  if (gaps.best_performers && gaps.best_performers.length) {
    html += '<div class="section-card"><h3>Best presterend</h3>' +
      '<table class="data-table"><thead><tr><th>Zoekwoord</th><th class="num">Clicks</th><th class="num">Impressies</th><th class="num">CTR</th><th class="num">Positie</th></tr></thead><tbody>' +
      gaps.best_performers.map(function(q){return '<tr><td class="url-cell">'+escHtml(q.query)+'</td><td class="num" style="color:#16a34a;font-weight:600">'+q.clicks+'</td><td class="num">'+q.impressions+'</td><td class="num">'+q.ctr+'%</td><td class="num">'+(typeof q.position==='number'?q.position.toFixed(1):q.position)+'</td></tr>';}).join('') +
      '</tbody></table></div>';
  }

  // Gaps (hoge impressies, lage CTR)
  if (gaps.gaps && gaps.gaps.length) {
    html += '<div class="section-card"><h3>Kansen: hoge impressies, lage CTR (' + gaps.gaps.length + ')</h3>' +
      '<table class="data-table"><thead><tr><th>Zoekwoord</th><th class="num">Impressies</th><th class="num">Clicks</th><th class="num" style="color:#ef4444">CTR</th><th class="num">Positie</th></tr></thead><tbody>' +
      gaps.gaps.map(function(q){return '<tr><td class="url-cell">'+escHtml(q.query)+'</td><td class="num">'+q.impressions+'</td><td class="num">'+q.clicks+'</td><td class="num" style="color:#ef4444;font-weight:600">'+q.ctr+'%</td><td class="num">'+(typeof q.position==='number'?q.position.toFixed(1):q.position)+'</td></tr>';}).join('') +
      '</tbody></table></div>';
  }

  // Striking distance
  if (gaps.striking_distance && gaps.striking_distance.length) {
    html += '<div class="section-card"><h3>Striking distance (pos 4-20, veel impressies)</h3>' +
      '<table class="data-table"><thead><tr><th>Zoekwoord</th><th class="num">Impressies</th><th class="num">Clicks</th><th class="num">CTR</th><th class="num">Positie</th></tr></thead><tbody>' +
      gaps.striking_distance.map(function(q){return '<tr><td class="url-cell">'+escHtml(q.query)+'</td><td class="num">'+q.impressions+'</td><td class="num">'+(q.clicks||0)+'</td><td class="num">'+q.ctr+'%</td><td class="num">'+(typeof q.position==='number'?q.position.toFixed(1):q.position)+'</td></tr>';}).join('') +
      '</tbody></table></div>';
  }

  el.innerHTML = html;
}

// ═══════════════════════════════════════════════════════════════════
//  TECHNISCH TAB
// ═══════════════════════════════════════════════════════════════════
async function renderTechTab(el) {
  el.innerHTML = '<div class="loading"><div class="spinner"></div><p>Technische data laden...</p></div>';
  try { var data = await (await fetch('/api/projects/' + encodeURIComponent(currentProject) + '/tech-seo')).json(); }
  catch(e) { el.innerHTML = '<div class="empty-state">Fout: ' + escHtml(e.message) + '</div>'; return; }
  if (data.error) { el.innerHTML = '<div class="empty-state">' + escHtml(data.error) + '</div>'; return; }
  var ic = data.index_coverage || {};
  var html = '<div class="kpi-grid">' +
    kpiBox('Geindexeerd (7d)', ic.total||'?', ic.change||0, '') +
    kpiBox('Kennisbank', (ic.by_type && ic.by_type.kennisbank)||0, '', '') +
    kpiBox('Blogs', (ic.by_type && ic.by_type.blog)||0, '', '') +
    kpiBox('Overig', (ic.by_type && ic.by_type.overig)||0, '', '') + '</div>' +
    '<div class="grid-2">' +
    '<div class="section-card"><h3>Indexverdeling</h3>' + '<table class="data-table"><thead><tr><th>Type</th><th class="num">Aantal</th></tr></thead><tbody>' +
    (ic.by_type ? Object.entries(ic.by_type).map(function(e){return '<tr><td>' + e[0].charAt(0).toUpperCase()+e[0].slice(1) + '</td><td class="num">' + e[1] + '</td></tr>';}).join('') : '<tr><td colspan="2" style="color:#94a3b8;text-align:center">Geen data</td></tr>') +
    '</tbody></table></div>' +
    '<div class="section-card"><h3>Sitemap</h3>' + (data.sitemap && data.sitemap.url ? '<p style="font-size:13px;margin-bottom:8px">Sitemap URL:</p><p style="font-size:12px;color:#4f46e5;word-break:break-all">' + escHtml(data.sitemap.url) + '</p><button onclick="submitSitemap()" style="margin-top:10px;padding:6px 16px;background:#4f46e5;color:#fff;border:none;border-radius:6px;font-size:12px;cursor:pointer">Indienen bij Google</button>' : '<p style="color:#94a3b8;font-size:12px">Geen sitemap URL</p>') + '</div></div>';
  if (data.top_queries_28d && data.top_queries_28d.length) {
    html += '<div class="section-card"><h3>Top zoekwoorden (28d)</h3>' + tbl(data.top_queries_28d.slice(0,15), ['zoekwoord','query'], ['Clicks','clicks'], ['Impressies','impressions'], ['Positie','position']) + '</div>';
  }
  el.innerHTML = html;
}
async function submitSitemap() {
  try {
    var sites = await (await fetch('/api/sites')).json();
    var site = sites.find(function(s){return s.name.toLowerCase()===currentProject.toLowerCase();});
    if (!site||!site.base_url) { alert('Geen site URL'); return; }
    var url = site.base_url.replace(/\/+$/,'')+'/sitemap.xml';
    var data = await (await fetch('/api/demand/submit-sitemap', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({site_url:site.base_url, sitemap_url:url}) })).json();
    alert(data.status==='ingediend' ? 'Sitemap ingediend!' : 'Fout: '+(data.detail||'onbekend'));
  } catch(e) { alert('Fout: '+e.message); }
}

// ═══════════════════════════════════════════════════════════════════
//  ACTIVITEIT TAB
// ═══════════════════════════════════════════════════════════════════
async function renderActiviteitTab(el) {
  el.innerHTML = '<div class="loading"><div class="spinner"></div><p>Activiteit laden...</p></div>';
  try { var items = await (await fetch('/api/projects/' + encodeURIComponent(currentProject) + '/activity?limit=50')).json(); }
  catch(e) { el.innerHTML = '<div class="empty-state">Fout: ' + escHtml(e.message) + '</div>'; return; }
  if (!items||!items.length) { el.innerHTML = '<div class="empty-state"><p style="color:#94a3b8;font-size:13px">Nog geen activiteit</p></div>'; return; }
  el.innerHTML = '<div class="section-card"><h3>Recente activiteit ('+items.length+')</h3>' +
    '<table class="data-table"><thead><tr><th>Tijd</th><th>Actie</th><th>Detail</th></tr></thead><tbody>' +
    items.map(function(a){return '<tr><td style="color:#94a3b8;white-space:nowrap">'+(a.created_at?a.created_at.slice(11,16):'')+'</td><td><span class="badge '+(a.action==='publicatie'?'badge-live':a.action==='suggestie'?'badge-draft':'badge-log')+'">' + escHtml(a.action) + '</span></td><td>' + escHtml(a.detail) + '</td></tr>';}).join('') +
    '</tbody></table></div>';
}

// ═══════════════════════════════════════════════════════════════════
//  DOELEN TAB — Goal Mode
// ═══════════════════════════════════════════════════════════════════
let goalPlanResult = null;
let goalCurrentId = null;

async function renderDoelenTab(el) {
  el.innerHTML = '<div class="loading"><div class="spinner"></div><p>Doelen laden...</p></div>';
  try {
    var resp = await fetch('/api/goals?limit=10');
    var goals = await resp.json();
  } catch(e) { el.innerHTML = '<div class="empty-state">Fout: ' + escHtml(e.message) + '</div>'; return; }

  var html = '<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:16px"><h3 style="font-size:15px;font-weight:700">Goal Mode</h3>' +
    '<button onclick="showNewGoalForm()" style="padding:6px 16px;background:#4f46e5;color:#fff;border:none;border-radius:6px;font-size:12px;cursor:pointer">+ Nieuw doel</button></div>';

  if (goalCurrentId) {
    // Toon detail van actieve goal
    html += '<div id="goal-detail"></div>';
  }

  // Lijst goals
  if (goals && goals.length) {
    html += '<div class="section-card"><h3>Recente doelen (' + goals.length + ')</h3>' +
      '<table class="data-table"><thead><tr><th>Titel</th><th>Status</th><th>Voortgang</th><th>Gemaakt</th></tr></thead><tbody>' +
      goals.map(function(g) {
        var statusColors = {draft:'#f1f5f9',ready:'#dbeafe',running:'#fef3c7',paused:'#f1f5f9',completed:'#dcfce7',partial:'#fed7aa',failed:'#fecaca'};
        var sc = statusColors[g.status]||'#f1f5f9';
        var total = g.phase_count || 1;
        var done = g.completed_tasks || 0;
        var pct = total > 0 ? Math.round(done/total*100) : 0;
        return '<tr style="cursor:pointer" onclick="loadGoalDetail(\'' + g.id + '\')"><td><span class="badge" style="background:' + sc + '">' + escHtml(g.status) + '</span> ' + escHtml(g.title) + (g.status==='failed'?' <button onclick="event.stopPropagation();retryFailedGoal(\'' + g.id + '\')" style="padding:2px 8px;background:#ef4444;color:#fff;border:none;border-radius:4px;font-size:10px;cursor:pointer">\u2728 Los het op met AI</button>':'') + '</td>' +
          '<td>' + escHtml(g.status) + '</td><td><div style="display:flex;align-items:center;gap:6px"><div style="flex:1;height:4px;background:#e2e8f0;border-radius:2px;overflow:hidden"><div style="height:100%;width:' + pct + '%;background:' + (g.status==='completed'?'#16a34a':g.status==='failed'?'#ef4444':'#4f46e5') + ';border-radius:2px"></div></div><span style="font-size:10px;color:#64748b">' + done + '/' + total + '</span></div></td>' +
          '<td style="font-size:11px;color:#94a3b8">' + (g.created_at ? g.created_at.slice(0,10) : '') + '</td></tr>';
      }).join('') +
      '</tbody></table></div>';
  } else {
    html += '<div class="section-card"><div class="empty-state"><p style="font-size:14px;font-weight:600;color:#475569;margin-bottom:4px">Nog geen doelen</p>' +
      '<p style="color:#94a3b8">Stel een langetermijndoel in. Hermes splitst het op in taken en voert ze autonoom uit.</p></div></div>';
  }

  el.innerHTML = html;
}

function showNewGoalForm() {
  var el = document.getElementById('tab-content'); if (!el) return;
  el.innerHTML = '<div class="section-card" style="max-width:600px;margin:0 auto"><h3 style="margin-bottom:16px">Nieuw langetermijndoel</h3>' +
    '<div style="margin-bottom:12px"><label style="font-size:12px;color:#64748b;display:block;margin-bottom:4px">Titel</label>' +
    '<input id="goal-title" style="width:100%;padding:8px 10px;border:1px solid #e2e8f0;border-radius:6px;font-size:13px" placeholder="Bijv. Lanceer marketingcampagne SaaS"></div>' +
    '<div style="margin-bottom:12px"><label style="font-size:12px;color:#64748b;display:block;margin-bottom:4px">Doelstelling (uitgebreid)</label>' +
    '<textarea id="goal-objective" rows="4" style="width:100%;padding:8px 10px;border:1px solid #e2e8f0;border-radius:6px;font-size:13px;resize:vertical" placeholder="Beschrijf het overkoepelende doel. Bijv.: Lanceer een marketingcampagne voor een SaaS-product dat zorginstellingen helpt met digitaal vrijwilligersmanagement. Doel: 50 leads in 30 dagen."></textarea></div>' +
    '<button onclick="generateGoalPlan()" style="padding:8px 20px;background:#4f46e5;color:#fff;border:none;border-radius:6px;font-size:13px;cursor:pointer">Plan genereren (AI decompositie)</button></div>';
}

async function generateGoalPlan() {
  var title = document.getElementById('goal-title'); if (!title) return;
  var objective = document.getElementById('goal-objective'); if (!objective) return;
  if (!title.value.trim() || !objective.value.trim()) { alert('Vul zowel titel als doelstelling in.'); return; }

  var btn = document.querySelector('button[onclick*=\"generateGoalPlan\"]'); 
  if (btn) { btn.disabled = true; btn.textContent = 'Bezig met decompositie...'; }
  try {
    var resp = await fetch('/api/goals/plan', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({title: title.value.trim(), objective: objective.value.trim(), project: currentProject||'WeAreImpact'}),
    });
    goalPlanResult = await resp.json();
    showGoalPlan();
  } catch(e) { alert('Fout: ' + e.message); }
  if (btn) { btn.disabled = false; btn.textContent = 'Plan genereren (AI decompositie)'; }
}

function showGoalPlan() {
  var el = document.getElementById('tab-content'); if (!el) return;
  var plan = goalPlanResult.plan;
  if (!plan || !plan.phases) { alert('Geen plan ontvangen'); return; }

  var html = '<div class="section-card"><div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:12px">' +
    '<div><h3 style="font-size:15px;font-weight:700">' + escHtml(goalPlanResult.title) + '</h3>' +
    '<p style="font-size:12px;color:#64748b;margin-top:2px">' + escHtml(plan.plan_summary||'') + '</p></div>' +
    '<button onclick="confirmAndStartGoal()" style="padding:8px 20px;background:#059669;color:#fff;border:none;border-radius:6px;font-size:13px;cursor:pointer;font-weight:600">Plan goedkeuren &amp; starten</button></div>' +
    '<p style="font-size:11px;color:#94a3b8;margin-bottom:12px">Geschatte duur: ' + escHtml(plan.estimated_duration||'onbekend') + '</p>';

  plan.phases.forEach(function(phase, pidx) {
    html += '<div style="border:1px solid #e2e8f0;border-radius:8px;margin-bottom:8px;overflow:hidden">' +
      '<div style="background:#f8fafc;padding:8px 12px;font-weight:600;font-size:13px;border-bottom:1px solid #e2e8f0">Fase ' + (pidx+1) + ': ' + escHtml(phase.title) + '</div>' +
      (phase.description ? '<div style="padding:6px 12px;font-size:11px;color:#64748b;border-bottom:1px solid #e2e8f0">' + escHtml(phase.description) + '</div>' : '') +
      '<div style="padding:6px 12px">';
    (phase.tasks||[]).forEach(function(task, tidx) {
      html += '<div style="display:flex;align-items:center;gap:8px;padding:4px 0;font-size:12px">' +
        '<span style="width:18px;height:18px;border-radius:4px;background:#dbeafe;color:#1e40af;display:flex;align-items:center;justify-content:center;font-size:10px;font-weight:600;flex-shrink:0">' + (tidx+1) + '</span>' +
        '<span style="flex:1">' + escHtml(task.title) + '</span>' +
        '<span style="font-size:10px;padding:1px 6px;border-radius:4px;background:#f1f5f9;color:#475569">' + (task.skill||'?') + '</span>' +
        (task.dependencies && task.dependencies.length ? '<span style="font-size:10px;color:#94a3b8">na ' + task.dependencies.map(function(d){return '#'+d;}).join(', ') + '</span>' : '') +
        '</div>';
    });
    html += '</div></div>';
  });

  html += '</div>';
  el.innerHTML = html;
}

async function confirmAndStartGoal() {
  if (!goalPlanResult) return;
  try {
    var confirmResp = await fetch('/api/goals/confirm', {
      method: 'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({goal_id: goalPlanResult.goal_id}),
    });
    var confirmData = await confirmResp.json();
    var startResp = await fetch('/api/goals/start', {
      method: 'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({goal_id: goalPlanResult.goal_id}),
    });
    var startData = await startResp.json();
    goalCurrentId = goalPlanResult.goal_id;
    alert('Doel gestart! ' + confirmData.phase_count + ' fasen, ' + confirmData.task_count + ' taken.');
    renderDoelenTab(document.getElementById('tab-content'));
  } catch(e) { alert('Fout: ' + e.message); }
}

async function loadGoalDetail(goalId) {
  try {
    var resp = await fetch('/api/goals/' + goalId);
    var goal = await resp.json();
    goalCurrentId = goalId;
    renderGoalDetail(goal);
  } catch(e) { alert('Fout: ' + e.message); }
}

function renderGoalDetail(goal) {
  var el = document.getElementById('goal-detail'); if (!el) return;
  var total = goal.task_count || 1;
  var done = goal.completed_tasks || 0;
  var failed = goal.failed_tasks || 0;
  var pct = total > 0 ? Math.round(done/total*100) : 0;

  var html = '<div class="section-card" style="border-left:3px solid ' + (goal.status==='running'?'#4f46e5':goal.status==='completed'?'#16a34a':goal.status==='failed'?'#ef4444':'#e2e8f0') + '">' +
    '<div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:8px">' +
    '<div><h4 style="font-size:14px;font-weight:700">' + escHtml(goal.title) + '</h4>' +
    '<p style="font-size:11px;color:#64748b">' + escHtml(goal.objective) + '</p></div>' +
    '<div style="display:flex;gap:4px">' +
    (goal.status === 'running' ? '<button onclick="pauseGoal()" style="padding:4px 10px;background:#fef3c7;color:#92400e;border:1px solid #fde68a;border-radius:6px;font-size:10px;cursor:pointer">Pauzeer</button>' : '') +
    (goal.status === 'paused' ? '<button onclick="resumeGoal()" style="padding:4px 10px;background:#dbeafe;color:#1e40af;border:1px solid #bfdbfe;border-radius:6px;font-size:10px;cursor:pointer">Hervat</button>' : '') +
    (goal.status === 'failed' ? '<button onclick="retryFailedGoal(\'' + goal.id + '\')" style="padding:4px 10px;background:#ef4444;color:#fff;border:1px solid #fca5a5;border-radius:6px;font-size:10px;cursor:pointer">\u2728 Los het op met AI</button>' : '') +
    '<button onclick="goalCurrentId=null;renderDoelenTab(document.getElementById(\'tab-content\'))" style="padding:4px 10px;background:#f1f5f9;color:#475569;border:1px solid #e2e8f0;border-radius:6px;font-size:10px;cursor:pointer">Terug</button>' +
    '</div></div>' +
    '<div style="display:flex;align-items:center;gap:8px;margin-bottom:12px">' +
    '<span class="badge" style="background:' + ({draft:'#f1f5f9',ready:'#dbeafe',running:'#fef3c7',paused:'#f1f5f9',completed:'#dcfce7',partial:'#fed7aa',failed:'#fecaca'}[goal.status]||'#f1f5f9') + '">' + escHtml(goal.status) + '</span>' +
    '<div style="flex:1;height:6px;background:#e2e8f0;border-radius:3px;overflow:hidden"><div style="height:100%;width:' + pct + '%;background:' + (goal.status==='completed'?'#16a34a':goal.status==='failed'?'#ef4444':'#4f46e5') + ';border-radius:3px;transition:width .5s"></div></div>' +
    '<span style="font-size:11px;color:#64748b;white-space:nowrap">' + done + '/' + total + ' taken</span>' +
    (failed>0?'<span style="font-size:11px;color:#ef4444;white-space:nowrap">' + failed + ' mislukt</span>':'') +
    '</div>';

  (goal.phases||[]).forEach(function(phase) {
    var phaseColors = {pending:'#f1f5f9',running:'#fef3c7',completed:'#dcfce7',failed:'#fecaca',skipped:'#f1f5f9'};
    html += '<div style="border:1px solid #e2e8f0;border-radius:8px;margin-bottom:6px;overflow:hidden">' +
      '<div style="background:#f8fafc;padding:6px 12px;font-weight:600;font-size:12px;border-bottom:1px solid #e2e8f0;display:flex;align-items:center;gap:6px">' +
      '<span class="badge" style="background:' + (phaseColors[phase.status]||'#f1f5f9') + '">' + escHtml(phase.status) + '</span>' +
      escHtml(phase.title) + '</div>';

    (phase.tasks||[]).forEach(function(task) {
      var taskColors = {pending:'#f1f5f9',ready:'#dbeafe',running:'#fef3c7',completed:'#dcfce7',failed:'#fecaca',skipped:'#f1f5f9'};
      var taskIcons = {pending:'o',ready:'&rarr;',running:'&bull;',completed:'&check;',failed:'x',skipped:'-'};
      html += '<div style="display:flex;align-items:center;gap:6px;padding:5px 12px;border-bottom:1px solid #f1f5f9;font-size:12px">' +
        '<span style="width:16px;height:16px;border-radius:3px;background:' + (taskColors[task.status]||'#f1f5f9') + ';display:flex;align-items:center;justify-content:center;font-size:9px;font-weight:600;flex-shrink:0">' + (taskIcons[task.status]||'?') + '</span>' +
        '<span style="flex:1">' + escHtml(task.title) + '</span>' +
        '<span style="font-size:10px;padding:1px 6px;border-radius:4px;background:#f1f5f9;color:#475569">' + escHtml(task.skill||'') + '</span>' +
        (task.duration_ms ? '<span style="font-size:10px;color:#94a3b8">' + (task.duration_ms > 1000 ? (task.duration_ms/1000).toFixed(0)+'s' : task.duration_ms+'ms') + '</span>' : '') +
        (task.status==='running'?'<span class="spinner" style="width:10px;height:10px;border-width:1.5px"></span>':'') +
        '</div>';
    });
    html += '</div>';
  });

  html += '</div>';
  el.innerHTML = html;
}

async function pauseGoal() {
  if (!goalCurrentId) return;
  try {
    await fetch('/api/goals/pause', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({goal_id: goalCurrentId}) });
    loadGoalDetail(goalCurrentId);
  } catch(e) { alert('Fout: ' + e.message); }
}
async function resumeGoal() {
  if (!goalCurrentId) return;
  try {
    await fetch('/api/goals/resume', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({goal_id: goalCurrentId}) });
    loadGoalDetail(goalCurrentId);
  } catch(e) { alert('Fout: ' + e.message); }
}

// ── Retry failed goal ("Los het op met AI") ──
async function retryFailedGoal(goalId) {
  if (!goalId) return;
  var btn = event && event.target || document.querySelector('[onclick*="' + goalId + '"]');
  if (btn) { btn.disabled = true; btn.textContent = 'Bezig...'; }
  try {
    var resp = await fetch('/api/goals/retry-failed', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({goal_id: goalId}),
    });
    var data = await resp.json();
    if (data.error) { alert('Fout: ' + data.error); return; }
    alert('✅ Doel herstart! Hermes probeert het opnieuw.');
    // Refresh current view
    if (goalCurrentId === goalId) {
      loadGoalDetail(goalId);
    } else {
      renderDoelenTab(document.getElementById('tab-content'));
    }
    pollAgentStatus();
  } catch(e) { alert('Fout: ' + e.message); }
  if (btn) { btn.disabled = false; btn.textContent = '✨ Los het op met AI'; }
}

// ═══════════════════════════════════════════════════════════════════
//  GEHEUGEN — Infinite Context Engine tab
// ═══════════════════════════════════════════════════════════════════
async function renderGeheugenTab(el) {
  var sub = window._memSubtab || 'galaxy';
  var btn = function(id, label) {
    var active = sub === id;
    return '<button onclick="switchMemSubtab(\'' + id + '\')" style="padding:6px 14px;border-radius:8px;font-size:12px;font-weight:600;border:1px solid ' + (active ? '#6366f1' : '#e2e8f0') + ';background:' + (active ? '#eef2ff' : '#fff') + ';color:' + (active ? '#4338ca' : '#64748b') + ';cursor:pointer">' + label + '</button>';
  };
  el.innerHTML = '<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:12px">' +
    '<h3 style="font-size:15px;font-weight:700">Geheugen</h3>' +
    '<div style="display:flex;gap:6px">' + btn('galaxy', '\u{2726} Galaxy') + btn('overzicht', 'Overzicht') + '</div></div>' +
    '<div id="mem-sub-content"></div>';
  var subEl = document.getElementById('mem-sub-content');
  if (sub === 'galaxy') await renderMemoryGalaxy(subEl);
  else await renderGeheugenOverzicht(subEl);
}
function switchMemSubtab(id) {
  window._memSubtab = id;
  var el = document.getElementById('tab-content');
  if (el) renderGeheugenTab(el);
}

async function renderGeheugenOverzicht(el) {
  el.innerHTML = '<div class="loading"><div class="spinner"></div><p>Geheugen laden...</p></div>';
  try {
    var resp = await fetch('/api/infinite-context/status');
    var data = await resp.json();
  } catch(e) { el.innerHTML = '<div class="empty-state">Fout: ' + escHtml(e.message) + '</div>'; return; }

  var html = '<h3 style="font-size:15px;font-weight:700;margin-bottom:16px">Infinite Context Engine — Oneindige Geheugenlus</h3>';

  if (!data.configured && !data.omi_configured) {
    html += '<div class="empty-state" style="margin-bottom:16px">' +
      '<p style="margin-bottom:8px">De Infinite Context Engine is niet aangesloten.</p>' +
      '<p style="font-size:12px;color:#64748b">Stel OBSIDIAN_VAULT_PATH of OMI_API_KEY in .env in om de Oneindige Loop te starten.</p></div>';
    el.innerHTML = html;
    return;
  }

  // ── Statuskaarten ──
  html += '<div class="kpi-grid">' +
    kpiBox('Obsidian vault', data.configured ? 'Actief' : 'Uit', '', data.vault_path || '') +
    kpiBox('OMI', data.omi_configured ? 'Actief' : 'Uit') +
    kpiBox('Notities', data.note_count || 0, '', 'totaal in vault') +
    kpiBox('Vandaag', data.today_session_count || 0, '', 'agent-sessies') +
  '</div>';

  // ── The Loop uitleg ──
  html += '<div class="section-card" style="margin-bottom:16px">' +
    '<h4 style="font-size:13px;font-weight:600;margin-bottom:8px">De Oneindige Loop (The Loop)</h4>' +
    '<div style="font-size:12px;line-height:1.7;color:#475569">' +
    '<div style="display:flex;gap:12px;align-items:center;justify-content:center;margin:8px 0;padding:12px;background:#f8fafc;border-radius:8px;font-size:11px">' +
    '<span style="background:#dbeafe;padding:6px 12px;border-radius:6px;font-weight:600">\u{1F4D6} READ</span>' +
    '<span style="color:#94a3b8">\u{27A1}</span>' +
    '<span style="background:#dcfce7;padding:6px 12px;border-radius:6px;font-weight:600">\u{2699} ACT</span>' +
    '<span style="color:#94a3b8">\u{27A1}</span>' +
    '<span style="background:#fef3c7;padding:6px 12px;border-radius:6px;font-weight:600">\u{1F4DD} WRITE</span>' +
    '<span style="color:#94a3b8">\u{27A1}</span>' +
    '<span style="background:#f1f5f9;padding:6px 12px;border-radius:6px;font-style:italic;color:#64748b">elke dag slimmer</span>' +
    '</div>' +
    '<ul style="padding-left:16px;margin:0">' +
    '<li><strong>READ</strong> \u{2192} Laadt context uit Obsidian + OMI v\u00f3\u00f3r elke agent-run</li>' +
    '<li><strong>ACT</strong> \u{2192} Agent voert taak uit met rijke context in system prompt</li>' +
    '<li><strong>WRITE</strong> \u{2192} Resultaten terug naar Obsidian (dagboek, taken, doelen) + OMI</li>' +
    '</ul></div></div>';

  // ── AgentOS folder statistieken ──
  if (data.folders && data.folders.length) {
    html += '<div class="section-card" style="margin-bottom:16px"><h4 style="font-size:13px;font-weight:600;margin-bottom:8px">AgentOS in Obsidian</h4>' +
      '<div class="kpi-grid">';
    data.folders.forEach(function(f) {
      html += kpiBox(f.folder.split('/').pop(), f.count, '', f.count > 0 ? f.recent_files.map(function(x){return x.name;}).join(', ').substring(0,40) + (f.count>5 ? '...' : '') : 'leeg');
    });
    html += '</div></div>';
  }

  // ── Dagboek vandaag ──
  if (data.daily_log_preview) {
    html += '<div class="section-card" style="margin-bottom:16px"><h4 style="font-size:13px;font-weight:600;margin-bottom:8px">Dagboek vandaag</h4>' +
      '<pre style="font-size:11px;line-height:1.5;color:#475569;white-space:pre-wrap;max-height:400px;overflow-y:auto;padding:8px;background:#f8fafc;border-radius:6px">' +
      escHtml(data.daily_log_preview) +
      '</pre></div>';
  }

  // ── OMI status ──
  if (data.omi_configured) {
    html += '<div class="section-card"><h4 style="font-size:13px;font-weight:600;margin-bottom:8px">OMI (Open Memory Interface)</h4>' +
      '<p style="font-size:12px;color:#64748b">OMI is actief. Memories en conversaties worden automatisch meegestuurd context voor alle agent-runs. Resultaten worden teruggeschreven als OMI-memories.</p></div>';
  } else {
    html += '<div class="section-card"><h4 style="font-size:13px;font-weight:600;margin-bottom:8px">OMI (Open Memory Interface)</h4>' +
      '<p style="font-size:12px;color:#94a3b8">OMI is niet geconfigureerd. Stel OMI_API_KEY in .env in om real-time gesprekscontext uit OMI te gebruiken.</p></div>';
  }

  el.innerHTML = html;
}

// ═══════════════════════════════════════════════════════════════════
//  OPTIMALISATIE — SEO Optimizer: interne links, CTR-kansen, refresh
//  Verzilvert rankings die de site al heeft (GSC-data + live site)
// ═══════════════════════════════════════════════════════════════════
function optShortUrl(u) {
  try { var p = new URL(u); return p.pathname === '/' ? p.hostname : p.pathname; } catch (e) { return u; }
}

async function renderOptimalisatieTab(el) {
  el.innerHTML = '<div class="loading"><div class="spinner"></div><p>Optimalisatiekansen laden...</p></div>';
  var data;
  try {
    var resp = await fetch('/api/seo-optimizer/' + encodeURIComponent(currentProject) + '/suggestions');
    if (!resp.ok) { var err = await resp.json().catch(function(){return {};}); throw new Error(err.detail || resp.status); }
    data = await resp.json();
  } catch (e) { el.innerHTML = '<div class="empty-state">Fout: ' + escHtml(e.message) + '</div>'; return; }

  var sugs = data.suggestions || [];
  var byType = { internal_link: [], ctr: [], refresh: [] };
  sugs.forEach(function(s) { if (byType[s.type]) byType[s.type].push(s); });
  var missedClicks = byType.ctr.reduce(function(a, s) { return a + (s.data.missed_clicks_per_period || 0); }, 0);

  var html = '<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:12px">' +
    '<div><h3 style="font-size:15px;font-weight:700">SEO Optimizer</h3>' +
    '<p style="font-size:11px;color:#64748b;margin-top:2px">Verzilvert rankings die je al hebt: interne links, CTR en content-refresh. Scant automatisch elke maandag 07:45.</p></div>' +
    '<button id="opt-scan-btn" onclick="runOptimizerScan(this)" style="padding:8px 18px;background:#4f46e5;color:#fff;border:none;border-radius:8px;font-size:12px;font-weight:600;cursor:pointer">Scan nu</button></div>';

  html += '<div class="kpi-grid" style="margin-bottom:16px">' +
    kpiBox('Interne linkkansen', byType.internal_link.length, '', 'ontbrekende links') +
    kpiBox('CTR-kansen', byType.ctr.length, '', '~' + Math.round(missedClicks) + ' gemiste klikken/28d') +
    kpiBox('Refresh-kandidaten', byType.refresh.length, '', 'wegzakkende pagina’s') +
    '</div>';

  if (!sugs.length) {
    html += '<div class="empty-state"><p style="font-size:14px;font-weight:600;color:#475569;margin-bottom:6px">Nog geen openstaande kansen</p>' +
      '<p style="color:#94a3b8;font-size:12px">Klik op “Scan nu” om de site en Search Console-data te analyseren.</p></div>';
    el.innerHTML = html;
    return;
  }

  var actBtns = function(s) {
    return '<button onclick="optSuggestionAction(\'' + s.id + '\',\'done\',this)" title="Gedaan" style="padding:4px 10px;background:#f0fdf4;color:#16a34a;border:1px solid #bbf7d0;border-radius:6px;font-size:11px;cursor:pointer">✓ Gedaan</button>' +
      '<button onclick="optSuggestionAction(\'' + s.id + '\',\'dismissed\',this)" title="Verwerpen" style="padding:4px 10px;background:#fff;color:#94a3b8;border:1px solid #e2e8f0;border-radius:6px;font-size:11px;cursor:pointer">✕</button>';
  };

  // ── CTR-kansen (grootste directe winst) ──
  if (byType.ctr.length) {
    html += '<div class="section-card" style="margin-bottom:16px"><h3 style="margin-bottom:4px">\u{1F3AF} CTR-kansen — je ranking is er al, verzilver hem</h3>' +
      '<p style="font-size:11px;color:#94a3b8;margin-bottom:10px">Pagina’s die veel minder klikken krijgen dan normaal voor hun positie. Betere title/meta = directe traffic zonder linkbuilding.</p>';
    byType.ctr.forEach(function(s) {
      var d = s.data;
      html += '<div id="opt-' + s.id + '" style="padding:10px;border:1px solid #f1f5f9;border-radius:8px;margin-bottom:8px">' +
        '<div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">' +
        '<a href="' + escHtml(s.page) + '" target="_blank" style="font-size:12px;font-weight:600;color:#1e293b;text-decoration:none;flex:1;min-width:200px">' + escHtml(optShortUrl(s.page)) + '</a>' +
        '<span style="font-size:11px;color:#d97706;font-weight:600">CTR ' + d.ctr + '% → benchmark ' + d.expected_ctr + '%</span>' +
        '<span style="font-size:11px;color:#64748b">pos ' + d.position + ' · ' + d.impressions + ' imp · ~' + d.missed_clicks_per_period + ' gemiste klikken</span>' +
        '<button onclick="optGenerateVariants(\'' + s.id + '\',this)" style="padding:4px 12px;background:#4f46e5;color:#fff;border:none;border-radius:6px;font-size:11px;font-weight:600;cursor:pointer">✍ Title/meta-varianten</button>' +
        actBtns(s) + '</div>' +
        (s.query ? '<div style="font-size:11px;color:#94a3b8;margin-top:4px">zoekwoord: ' + escHtml(s.query) + '</div>' : '') +
        '<div id="opt-variants-' + s.id + '">' + (d.variants ? renderOptVariants(d) : '') + '</div>' +
        '</div>';
    });
    html += '</div>';
  }

  // ── Interne links ──
  if (byType.internal_link.length) {
    html += '<div class="section-card" style="margin-bottom:16px"><h3 style="margin-bottom:4px">\u{1F517} Ontbrekende interne links</h3>' +
      '<p style="font-size:11px;color:#94a3b8;margin-bottom:10px">De ankertekst staat al letterlijk op de bronpagina — alleen de link ontbreekt nog. Plaats de link in je CMS en markeer als gedaan.</p>';
    byType.internal_link.forEach(function(s) {
      var d = s.data;
      html += '<div id="opt-' + s.id + '" style="padding:10px;border:1px solid #f1f5f9;border-radius:8px;margin-bottom:8px">' +
        '<div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">' +
        '<span style="font-size:12px;color:#1e293b;flex:1;min-width:220px"><a href="' + escHtml(d.from) + '" target="_blank" style="color:#4f46e5;text-decoration:none">' + escHtml(optShortUrl(d.from)) + '</a>' +
        ' <span style="color:#94a3b8">→</span> <a href="' + escHtml(d.to) + '" target="_blank" style="color:#16a34a;text-decoration:none">' + escHtml(optShortUrl(d.to)) + '</a></span>' +
        '<span style="font-size:11px;color:#64748b">' + (d.target_impressions || 0) + ' imp/28d</span>' + actBtns(s) + '</div>' +
        '<div style="font-size:11px;color:#475569;margin-top:6px;background:#f8fafc;padding:6px 8px;border-radius:6px">…' + escHtml((d.context || '').replace(d.anchor, '')).slice(0, 60) + '<strong style="background:#fef3c7;padding:0 3px;border-radius:3px">' + escHtml(d.anchor) + '</strong>…</div>' +
        '</div>';
    });
    html += '</div>';
  }

  // ── Refresh-kandidaten ──
  if (byType.refresh.length) {
    html += '<div class="section-card" style="margin-bottom:16px"><h3 style="margin-bottom:4px">♻️ Content-refresh — wegzakkende pagina’s</h3>' +
      '<p style="font-size:11px;color:#94a3b8;margin-bottom:10px">De agent haalt de pagina + huidige top-resultaten op, verrijkt het artikel en zet het in de Wachtrij ter review.</p>';
    byType.refresh.forEach(function(s) {
      var d = s.data;
      html += '<div id="opt-' + s.id + '" style="padding:10px;border:1px solid #f1f5f9;border-radius:8px;margin-bottom:8px">' +
        '<div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">' +
        '<a href="' + escHtml(s.page) + '" target="_blank" style="font-size:12px;font-weight:600;color:#1e293b;text-decoration:none;flex:1;min-width:200px">' + escHtml(optShortUrl(s.page)) + '</a>' +
        '<span style="font-size:11px;color:#ef4444;font-weight:600">' + escHtml(s.title) + '</span>' +
        '<button onclick="optRefresh(\'' + s.id + '\',this)" style="padding:4px 12px;background:#059669;color:#fff;border:none;border-radius:6px;font-size:11px;font-weight:600;cursor:pointer">♻ Ververs → Wachtrij</button>' +
        actBtns(s) + '</div>' +
        (s.query ? '<div style="font-size:11px;color:#94a3b8;margin-top:4px">zoekwoord: ' + escHtml(s.query) + '</div>' : '') +
        '</div>';
    });
    html += '</div>';
  }

  el.innerHTML = html;
}

function renderOptVariants(d) {
  if (!d.variants || !d.variants.length) return '';
  var html = '<div style="margin-top:8px;border-top:1px dashed #e2e8f0;padding-top:8px">' +
    (d.current_title ? '<div style="font-size:11px;color:#94a3b8;margin-bottom:6px">Nu: <em>' + escHtml(d.current_title) + '</em></div>' : '');
  d.variants.forEach(function(v, i) {
    html += '<div style="padding:6px 8px;background:#f8fafc;border-radius:6px;margin-bottom:5px">' +
      '<div style="font-size:12px;font-weight:600;color:#1e293b">' + (i + 1) + '. ' + escHtml(v.title || '') + '</div>' +
      '<div style="font-size:11px;color:#475569;margin-top:2px">' + escHtml(v.meta || '') + '</div>' +
      (v.waarom ? '<div style="font-size:10px;color:#94a3b8;margin-top:2px;font-style:italic">' + escHtml(v.waarom) + '</div>' : '') +
      '</div>';
  });
  return html + '</div>';
}

async function runOptimizerScan(btn) {
  btn.disabled = true; var orig = btn.textContent; btn.textContent = 'Scannen... (±30s)';
  try {
    var resp = await fetch('/api/seo-optimizer/' + encodeURIComponent(currentProject) + '/scan', { method: 'POST' });
    var res = await resp.json();
    if (!resp.ok) throw new Error(res.detail || 'Scan mislukt');
    var c = res.counts || {};
    btn.textContent = '✓ ' + ((c.internal_link || 0) + (c.ctr || 0) + (c.refresh || 0)) + ' nieuwe kansen';
    setTimeout(function() { var el = document.getElementById('tab-content'); if (el && currentTab === 'Optimalisatie') renderOptimalisatieTab(el); }, 900);
  } catch (e) {
    btn.textContent = 'Fout: ' + e.message.slice(0, 40);
    setTimeout(function() { btn.disabled = false; btn.textContent = orig; }, 3500);
  }
}

async function optSuggestionAction(sid, status, btn) {
  btn.disabled = true;
  try {
    var resp = await fetch('/api/seo-optimizer/suggestions/' + sid, {
      method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ status: status })
    });
    if (!resp.ok) throw new Error('mislukt');
    var card = document.getElementById('opt-' + sid);
    if (card) { card.style.opacity = '0.35'; card.style.pointerEvents = 'none'; }
  } catch (e) { btn.disabled = false; }
}

async function optGenerateVariants(sid, btn) {
  btn.disabled = true; var orig = btn.textContent; btn.textContent = 'Schrijven...';
  try {
    var resp = await fetch('/api/seo-optimizer/suggestions/' + sid + '/ctr-variants', { method: 'POST' });
    var res = await resp.json();
    if (!resp.ok) throw new Error(res.detail || 'mislukt');
    var box = document.getElementById('opt-variants-' + sid);
    if (box) box.innerHTML = renderOptVariants({ variants: res.variants });
    btn.textContent = '✓ 3 varianten';
  } catch (e) {
    btn.textContent = 'Fout';
    setTimeout(function() { btn.disabled = false; btn.textContent = orig; }, 3000);
  }
}

async function optRefresh(sid, btn) {
  btn.disabled = true; var orig = btn.textContent; btn.textContent = 'Agent schrijft... (±1 min)';
  try {
    var resp = await fetch('/api/seo-optimizer/suggestions/' + sid + '/refresh', { method: 'POST' });
    var res = await resp.json();
    if (!resp.ok) throw new Error(res.detail || 'mislukt');
    btn.textContent = '✓ In Wachtrij';
    btn.onclick = function() { switchView('Wachtrij'); };
    btn.style.background = '#16a34a';
  } catch (e) {
    btn.textContent = 'Fout: ' + (e.message || '').slice(0, 30);
    setTimeout(function() { btn.disabled = false; btn.textContent = orig; }, 4000);
  }
}

// ═══════════════════════════════════════════════════════════════════
//  MEMORY GALAXY — 3D sterrenkaart van de Obsidian vault
//  Elke notitie = een ster · wikilinks = verbindingen
//  Slepen = draaien · scrollen = zoomen · klik = detail · dubbelklik = pauze
// ═══════════════════════════════════════════════════════════════════

// Gevalideerd categorisch palet voor donkere achtergrond (#0a0e1c)
var GALAXY_PALETTE = ['#3987e5', '#199e70', '#c98500', '#008300', '#9085e9', '#e66767', '#d55181', '#d95926'];
var GALAXY_OTHER_COLOR = '#8b93a7';
var GALAXY_MAX_GROUPS = 8;

var _galaxy = null; // actieve galaxy-state (één tegelijk)

async function renderMemoryGalaxy(el) {
  if (_galaxy && _galaxy.raf) { cancelAnimationFrame(_galaxy.raf); _galaxy = null; }
  el.innerHTML = '<div class="loading"><div class="spinner"></div><p>Sterrenkaart laden...</p></div>';

  var data;
  try {
    var resp = await fetch('/api/infinite-context/graph');
    data = await resp.json();
  } catch (e) { el.innerHTML = '<div class="empty-state">Fout: ' + escHtml(e.message) + '</div>'; return; }

  if (!data.nodes || !data.nodes.length) {
    el.innerHTML = '<div class="empty-state"><p style="margin-bottom:8px">Geen notities gevonden.</p>' +
      '<p style="font-size:12px;color:#64748b">Stel OBSIDIAN_VAULT_PATH in .env in om je vault als sterrenkaart te zien.</p></div>';
    return;
  }

  // ── Groepen → kleuren (top-groepen krijgen een eigen kleur, rest = Overig)
  var groupColor = {};
  var legendGroups = [];
  var groupCounts = {};
  data.nodes.forEach(function(n) { groupCounts[n.group] = (groupCounts[n.group] || 0) + 1; });
  (data.groups || []).forEach(function(g, i) {
    if (i < GALAXY_MAX_GROUPS) { groupColor[g] = GALAXY_PALETTE[i]; legendGroups.push({ name: g, color: GALAXY_PALETTE[i], count: groupCounts[g] || 0 }); }
    else { groupColor[g] = GALAXY_OTHER_COLOR; }
  });
  var otherCount = data.nodes.filter(function(n) { return groupColor[n.group] === GALAXY_OTHER_COLOR; }).length;
  if (otherCount) legendGroups.push({ name: 'Overig', color: GALAXY_OTHER_COLOR, count: otherCount });

  // ── HTML skelet: canvas + overlays
  el.innerHTML =
    '<div id="galaxy-card" style="position:relative;background:#0a0e1c;border-radius:14px;overflow:hidden;height:calc(100vh - 210px);min-height:520px;box-shadow:0 4px 24px rgba(2,6,23,.35)">' +
      '<canvas id="galaxy-canvas" style="position:absolute;inset:0;width:100%;height:100%;display:block;cursor:grab"></canvas>' +
      // Titelblok
      '<div style="position:absolute;top:16px;left:18px;pointer-events:none;user-select:none">' +
        '<div style="font-size:11px;font-weight:700;letter-spacing:2px;color:#cbd5e1">\u{2726} MEMORY GALAXY</div>' +
        '<div style="font-size:11px;color:#64748b;margin-top:3px">' + data.note_count + ' sterren \u{B7} ' + data.link_count + ' links</div>' +
        '<div style="font-size:10px;color:#475569;margin-top:2px">sleep om te draaien \u{B7} scroll om te zoomen \u{B7} klik een ster \u{B7} dubbelklik pauzeert de vlucht</div>' +
        '<div style="font-size:10px;color:#475569">\u{2726} feller &amp; witter = recenter bijgewerkt</div>' +
      '</div>' +
      // Zoekveld
      '<div style="position:absolute;top:14px;right:16px;width:260px">' +
        '<input id="galaxy-search" type="text" placeholder="Zoek in ' + data.note_count + ' notities..." autocomplete="off" ' +
          'style="width:100%;padding:8px 12px;border-radius:8px;border:1px solid #1e293b;background:rgba(15,23,42,.85);color:#e2e8f0;font-size:12px;outline:none" />' +
        '<div id="galaxy-search-results" style="display:none;margin-top:6px;max-height:300px;overflow-y:auto;background:rgba(15,23,42,.95);border:1px solid #1e293b;border-radius:8px"></div>' +
      '</div>' +
      // Legenda (direct gelabeld — kleur draagt nooit alleen betekenis)
      '<div id="galaxy-legend" style="position:absolute;left:18px;bottom:14px;display:flex;flex-wrap:wrap;gap:6px;max-width:55%">' +
        legendGroups.map(function(g) {
          return '<button class="galaxy-legend-chip" data-group="' + escHtml(g.name) + '" onclick="galaxyToggleGroup(this)" ' +
            'style="display:flex;align-items:center;gap:5px;padding:3px 9px;border-radius:99px;border:1px solid #1e293b;background:rgba(15,23,42,.7);color:#94a3b8;font-size:10px;cursor:pointer">' +
            '<span style="width:7px;height:7px;border-radius:99px;background:' + g.color + ';display:inline-block"></span>' +
            escHtml(g.name) + ' <span style="color:#475569">' + g.count + '</span></button>';
        }).join('') +
      '</div>' +
      // Besturing
      '<div style="position:absolute;right:16px;bottom:14px;display:flex;gap:6px">' +
        '<button id="galaxy-flight-btn" onclick="galaxyToggleFlight()" title="Vlucht pauzeren/hervatten" style="padding:6px 12px;border-radius:8px;border:1px solid #1e293b;background:rgba(15,23,42,.85);color:#94a3b8;font-size:11px;cursor:pointer">\u{23F8} Pauze</button>' +
        '<button onclick="galaxyResetView()" title="Beeld terugzetten" style="padding:6px 12px;border-radius:8px;border:1px solid #1e293b;background:rgba(15,23,42,.85);color:#94a3b8;font-size:11px;cursor:pointer">\u{27F2} Reset</button>' +
      '</div>' +
      // Tooltip + detailpaneel
      '<div id="galaxy-tooltip" style="display:none;position:absolute;pointer-events:none;background:rgba(15,23,42,.95);border:1px solid #334155;border-radius:6px;padding:5px 10px;font-size:11px;color:#e2e8f0;z-index:5;max-width:260px"></div>' +
      '<div id="galaxy-detail" style="display:none;position:absolute;top:60px;right:16px;bottom:52px;width:300px;background:rgba(10,14,28,.95);border:1px solid #1e293b;border-radius:12px;padding:14px;overflow-y:auto;z-index:6"></div>' +
    '</div>';

  // ── State opbouwen
  var canvas = document.getElementById('galaxy-canvas');
  var g = {
    nodes: data.nodes, links: data.links, groupColor: groupColor,
    canvas: canvas, ctx: canvas.getContext('2d'),
    yaw: 0.4, pitch: 0.18, zoom: 1, autoRotate: true,
    targetYaw: null, targetPitch: null, targetZoom: null,
    dragging: false, lastX: 0, lastY: 0, velYaw: 0, velPitch: 0,
    hover: -1, selected: -1, dimGroups: {}, searchHits: null,
    simIter: 0, simMax: 220, raf: null, sprites: {}, bgStars: []
  };
  _galaxy = g;

  // Startposities: elke groep krijgt een clusterkern op een bol, sterren eromheen
  var R = 300;
  var centers = {};
  var gi = 0, gTotal = (data.groups || []).length || 1;
  (data.groups || []).forEach(function(grp) {
    var phi = Math.acos(1 - 2 * (gi + 0.5) / gTotal);
    var theta = Math.PI * (1 + Math.sqrt(5)) * gi; // fibonacci-verdeling
    centers[grp] = [R * 0.55 * Math.sin(phi) * Math.cos(theta), R * 0.55 * Math.sin(phi) * Math.sin(theta), R * 0.55 * Math.cos(phi)];
    gi++;
  });
  g.centers = centers;
  g.nodes.forEach(function(n) {
    var c = centers[n.group] || [0, 0, 0];
    n.x = c[0] + (Math.random() - 0.5) * 150;
    n.y = c[1] + (Math.random() - 0.5) * 150;
    n.z = c[2] + (Math.random() - 0.5) * 150;
    n.vx = 0; n.vy = 0; n.vz = 0;
    // Helderheid: recent bijgewerkt = fel wit, oud = gedimd
    n.bright = Math.max(0.35, Math.min(1, 1.15 - (n.days || 0) / 90));
    n.r = 1.6 + Math.sqrt(n.deg || 0) * 1.1; // straal ∝ aantal verbindingen
  });

  // Achtergrondsterretjes (puur decoratief, statisch)
  for (var b = 0; b < 130; b++) g.bgStars.push([Math.random(), Math.random(), Math.random() * 0.8 + 0.2]);

  // Labels voor de belangrijkste knopen (hoogste degree)
  var byDeg = g.nodes.map(function(n, i) { return [n.deg, i]; }).sort(function(a, b2) { return b2[0] - a[0]; });
  g.labeled = {};
  for (var li = 0; li < Math.min(14, byDeg.length); li++) if (byDeg[li][0] > 0) g.labeled[byDeg[li][1]] = true;

  galaxyBindEvents(g);
  galaxyLoop(g);
}

// ── Fysica: force-directed layout in 3D (draait de eerste seconden warm)
function galaxySimStep(g) {
  var nodes = g.nodes, links = g.links, n = nodes.length;
  var i, j, a, b2, dx, dy, dz, d2, d, f;
  // Afstoting (O(n²), maar n≈500 dus prima)
  for (i = 0; i < n; i++) {
    a = nodes[i];
    for (j = i + 1; j < n; j++) {
      b2 = nodes[j];
      dx = a.x - b2.x; dy = a.y - b2.y; dz = a.z - b2.z;
      d2 = dx * dx + dy * dy + dz * dz + 0.01;
      if (d2 > 22500) continue; // >150 eenheden: verwaarloosbaar
      f = 260 / d2;
      dx *= f; dy *= f; dz *= f;
      a.vx += dx; a.vy += dy; a.vz += dz;
      b2.vx -= dx; b2.vy -= dy; b2.vz -= dz;
    }
  }
  // Veren langs links
  for (i = 0; i < links.length; i++) {
    a = nodes[links[i][0]]; b2 = nodes[links[i][1]];
    dx = b2.x - a.x; dy = b2.y - a.y; dz = b2.z - a.z;
    d = Math.sqrt(dx * dx + dy * dy + dz * dz) + 0.01;
    f = (d - 55) * 0.012 / d;
    dx *= f; dy *= f; dz *= f;
    a.vx += dx; a.vy += dy; a.vz += dz;
    b2.vx -= dx; b2.vy -= dy; b2.vz -= dz;
  }
  // Zwaartekracht naar clusterkern + integratie
  for (i = 0; i < n; i++) {
    a = nodes[i];
    var c = g.centers[a.group];
    if (c) { a.vx += (c[0] - a.x) * 0.004; a.vy += (c[1] - a.y) * 0.004; a.vz += (c[2] - a.z) * 0.004; }
    a.vx *= 0.82; a.vy *= 0.82; a.vz *= 0.82;
    a.x += a.vx; a.y += a.vy; a.z += a.vz;
  }
}

// ── Glow-sprite per kleur (gecachet — veel sneller dan gradients per frame)
function galaxySprite(g, color) {
  if (g.sprites[color]) return g.sprites[color];
  var s = document.createElement('canvas'); s.width = 64; s.height = 64;
  var c = s.getContext('2d');
  var grad = c.createRadialGradient(32, 32, 0, 32, 32, 32);
  grad.addColorStop(0, '#ffffff');
  grad.addColorStop(0.25, color);
  grad.addColorStop(1, 'rgba(0,0,0,0)');
  c.fillStyle = grad; c.fillRect(0, 0, 64, 64);
  g.sprites[color] = s;
  return s;
}

function galaxyProject(g, n, w, h) {
  var cy = Math.cos(g.yaw), sy = Math.sin(g.yaw), cp = Math.cos(g.pitch), sp = Math.sin(g.pitch);
  var x1 = n.x * cy + n.z * sy, z1 = -n.x * sy + n.z * cy;
  var y1 = n.y * cp - z1 * sp, z2 = n.y * sp + z1 * cp;
  var scale = 900 / (900 + z2);
  return [w / 2 + x1 * scale * g.zoom, h / 2 + y1 * scale * g.zoom, scale, z2];
}

function galaxyLoop(g) {
  if (!g.canvas.isConnected || _galaxy !== g) { g.raf = null; return; }
  var canvas = g.canvas, ctx = g.ctx;
  var dpr = window.devicePixelRatio || 1;
  var w = canvas.clientWidth, h = canvas.clientHeight;
  if (canvas.width !== w * dpr || canvas.height !== h * dpr) { canvas.width = w * dpr; canvas.height = h * dpr; }
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

  // Fysica warmdraaien (6 stappen per frame tot de layout stabiel is)
  if (g.simIter < g.simMax) { for (var s = 0; s < 6; s++) galaxySimStep(g); g.simIter += 6; }

  // Camera: trage vlucht + soepel naar doel vliegen + inertie
  if (g.targetYaw !== null) {
    g.yaw += (g.targetYaw - g.yaw) * 0.08; g.pitch += (g.targetPitch - g.pitch) * 0.08;
    if (g.targetZoom !== null) g.zoom += (g.targetZoom - g.zoom) * 0.08;
    if (Math.abs(g.targetYaw - g.yaw) < 0.002) { g.targetYaw = null; g.targetPitch = null; g.targetZoom = null; }
  } else if (g.autoRotate && !g.dragging) {
    g.yaw += 0.0016;
  }
  if (!g.dragging && g.targetYaw === null) { g.yaw += g.velYaw; g.pitch += g.velPitch; g.velYaw *= 0.92; g.velPitch *= 0.92; }
  g.pitch = Math.max(-1.4, Math.min(1.4, g.pitch));

  // ── Achtergrond
  ctx.fillStyle = '#0a0e1c'; ctx.fillRect(0, 0, w, h);
  var bg = ctx.createRadialGradient(w / 2, h / 2, 0, w / 2, h / 2, Math.max(w, h) * 0.7);
  bg.addColorStop(0, 'rgba(49,46,129,0.16)'); bg.addColorStop(1, 'rgba(0,0,0,0)');
  ctx.fillStyle = bg; ctx.fillRect(0, 0, w, h);
  ctx.fillStyle = 'rgba(148,163,184,0.35)';
  for (var bs = 0; bs < g.bgStars.length; bs++) {
    var st = g.bgStars[bs];
    ctx.fillRect(st[0] * w, st[1] * h, st[2], st[2]);
  }

  // ── Projecteer alle sterren
  var proj = new Array(g.nodes.length);
  for (var i = 0; i < g.nodes.length; i++) proj[i] = galaxyProject(g, g.nodes[i], w, h);

  var isDimmed = function(idx) {
    var n = g.nodes[idx];
    if (g.dimGroups[n.group]) return true;
    if (g.searchHits && !g.searchHits[idx]) return true;
    return false;
  };

  // ── Links (diepte bepaalt zichtbaarheid)
  ctx.lineWidth = 0.5;
  for (var l = 0; l < g.links.length; l++) {
    var a = g.links[l][0], b = g.links[l][1];
    var pa = proj[a], pb = proj[b];
    var dim = isDimmed(a) || isDimmed(b);
    var sel = g.selected === a || g.selected === b;
    var alpha = sel ? 0.5 : (dim ? 0.02 : 0.09 * Math.min(pa[2], pb[2]));
    ctx.strokeStyle = sel ? 'rgba(165,180,252,' + alpha + ')' : 'rgba(148,163,184,' + alpha + ')';
    ctx.beginPath(); ctx.moveTo(pa[0], pa[1]); ctx.lineTo(pb[0], pb[1]); ctx.stroke();
  }

  // ── Sterren (glow-sprites, additief gemengd)
  ctx.globalCompositeOperation = 'lighter';
  for (var k = 0; k < g.nodes.length; k++) {
    var n = g.nodes[k], p = proj[k];
    if (p[0] < -30 || p[0] > w + 30 || p[1] < -30 || p[1] > h + 30) continue;
    var color = g.groupColor[n.group] || GALAXY_OTHER_COLOR;
    var size = (n.r * 3.2 + 3) * p[2] * Math.sqrt(g.zoom);
    var alpha2 = n.bright * p[2];
    if (isDimmed(k)) alpha2 *= 0.08;
    if (g.searchHits && g.searchHits[k]) { alpha2 = Math.min(1, alpha2 * 1.6); size *= 1.25; }
    if (k === g.hover || k === g.selected) { alpha2 = 1; size *= 1.35; }
    ctx.globalAlpha = Math.min(1, alpha2);
    ctx.drawImage(galaxySprite(g, color), p[0] - size / 2, p[1] - size / 2, size, size);
  }
  ctx.globalCompositeOperation = 'source-over';
  ctx.globalAlpha = 1;

  // ── Selectiering + labels van belangrijkste knopen
  if (g.selected >= 0) {
    var ps = proj[g.selected];
    ctx.strokeStyle = 'rgba(199,210,254,0.9)'; ctx.lineWidth = 1.2;
    ctx.beginPath(); ctx.arc(ps[0], ps[1], 11 * Math.sqrt(g.zoom), 0, Math.PI * 2); ctx.stroke();
  }
  ctx.font = '10px Inter, sans-serif'; ctx.textAlign = 'left';
  for (var lb in g.labeled) {
    var lidx = +lb;
    if (isDimmed(lidx)) continue;
    var pl = proj[lidx];
    if (pl[3] > 150 || pl[0] < 0 || pl[0] > w) continue; // alleen labels vooraan
    ctx.fillStyle = 'rgba(203,213,225,' + (0.55 * pl[2]) + ')';
    ctx.fillText(g.nodes[lidx].name.slice(0, 26), pl[0] + 8, pl[1] + 3);
  }

  g.proj = proj;
  g.raf = requestAnimationFrame(function() { galaxyLoop(g); });
}

// ── Interactie ──────────────────────────────────────────────────────
function galaxyNodeAt(g, mx, my) {
  if (!g.proj) return -1;
  var best = -1, bestD = 144; // 12px zoekradius
  for (var i = 0; i < g.proj.length; i++) {
    var p = g.proj[i];
    var dx = p[0] - mx, dy = p[1] - my;
    var d = dx * dx + dy * dy;
    if (d < bestD) { bestD = d; best = i; }
  }
  return best;
}

function galaxyBindEvents(g) {
  var canvas = g.canvas;
  var moved = false;
  canvas.addEventListener('mousedown', function(e) {
    g.dragging = true; moved = false; g.lastX = e.offsetX; g.lastY = e.offsetY; canvas.style.cursor = 'grabbing';
  });
  canvas.addEventListener('mousemove', function(e) {
    if (g.dragging) {
      var dx = e.offsetX - g.lastX, dy = e.offsetY - g.lastY;
      if (Math.abs(dx) + Math.abs(dy) > 2) moved = true;
      g.yaw += dx * 0.005; g.pitch += dy * 0.005;
      g.velYaw = dx * 0.0012; g.velPitch = dy * 0.0012;
      g.targetYaw = null; g.targetPitch = null; g.targetZoom = null;
      g.lastX = e.offsetX; g.lastY = e.offsetY;
      return;
    }
    var idx = galaxyNodeAt(g, e.offsetX, e.offsetY);
    g.hover = idx;
    canvas.style.cursor = idx >= 0 ? 'pointer' : 'grab';
    var tip = document.getElementById('galaxy-tooltip');
    if (!tip) return;
    if (idx >= 0) {
      var n = g.nodes[idx];
      tip.innerHTML = '<strong>' + escHtml(n.name) + '</strong><br><span style="color:#94a3b8">' + escHtml(n.group) + ' \u{B7} ' + n.deg + ' links \u{B7} ' + Math.round(n.days) + 'd geleden</span>';
      tip.style.display = 'block';
      tip.style.left = Math.min(e.offsetX + 14, canvas.clientWidth - 270) + 'px';
      tip.style.top = (e.offsetY + 14) + 'px';
    } else tip.style.display = 'none';
  });
  window.addEventListener('mouseup', function() { if (g.dragging) { g.dragging = false; if (g.canvas.isConnected) g.canvas.style.cursor = 'grab'; } });
  canvas.addEventListener('click', function(e) {
    if (moved) return;
    var idx = galaxyNodeAt(g, e.offsetX, e.offsetY);
    if (idx >= 0) galaxySelect(idx);
    else { g.selected = -1; var d = document.getElementById('galaxy-detail'); if (d) d.style.display = 'none'; }
  });
  canvas.addEventListener('dblclick', function() { galaxyToggleFlight(); });
  canvas.addEventListener('wheel', function(e) {
    e.preventDefault();
    g.zoom = Math.max(0.3, Math.min(5, g.zoom * (e.deltaY > 0 ? 0.92 : 1.09)));
  }, { passive: false });

  // Zoeken: direct op naam, na een korte pauze ook op inhoud (backend)
  var input = document.getElementById('galaxy-search');
  var resBox = document.getElementById('galaxy-search-results');
  var debounce = null;
  input.addEventListener('input', function() {
    var q = input.value.trim().toLowerCase();
    if (debounce) clearTimeout(debounce);
    if (!q) { g.searchHits = null; resBox.style.display = 'none'; return; }
    // Naam-matches: highlight in de galaxy + lijst
    var hits = {}, list = [];
    g.nodes.forEach(function(n, i) { if (n.name.toLowerCase().indexOf(q) >= 0) { hits[i] = true; list.push(i); } });
    g.searchHits = hits;
    var html = list.slice(0, 12).map(function(i) {
      var n = g.nodes[i];
      return '<div onclick="galaxySelect(' + i + ')" style="padding:7px 10px;font-size:11px;color:#e2e8f0;cursor:pointer;border-bottom:1px solid #1e293b">\u{2726} ' + escHtml(n.name) +
        ' <span style="color:#64748b">\u{B7} ' + escHtml(n.group) + '</span></div>';
    }).join('');
    resBox.innerHTML = html || '<div style="padding:8px 10px;font-size:11px;color:#64748b">Geen naam-matches \u{2014} inhoud doorzoeken...</div>';
    resBox.style.display = 'block';
    // Inhoudelijke zoekresultaten erbij (debounced)
    debounce = setTimeout(function() {
      fetch('/api/infinite-context/search?q=' + encodeURIComponent(input.value.trim())).then(function(r) { return r.json(); }).then(function(sr) {
        if (input.value.trim().toLowerCase() !== q || !sr.results || !sr.results.length) return;
        var extra = '<div style="padding:5px 10px;font-size:9px;letter-spacing:1px;color:#64748b;border-bottom:1px solid #1e293b">OP INHOUD</div>';
        sr.results.slice(0, 8).forEach(function(r2) {
          var i = g.nodes.findIndex(function(n) { return n.id === (r2.path || '').replace(/\\/g, '/'); });
          if (i >= 0) g.searchHits[i] = true;
          extra += '<div onclick="' + (i >= 0 ? 'galaxySelect(' + i + ')' : '') + '" style="padding:7px 10px;font-size:11px;color:#cbd5e1;cursor:pointer;border-bottom:1px solid #1e293b">' +
            escHtml(r2.file || '') + '<div style="color:#64748b;font-size:10px;margin-top:2px">' + escHtml((r2.snippet || '').slice(0, 90)) + '...</div></div>';
        });
        resBox.innerHTML = (html || '') + extra;
      }).catch(function() {});
    }, 350);
  });
}

function galaxySelect(idx) {
  var g = _galaxy; if (!g) return;
  g.selected = idx;
  var n = g.nodes[idx];
  var box = document.getElementById('galaxy-search-results');
  if (box) box.style.display = 'none';
  // Vlieg ernaartoe: draai de camera zó dat de ster in het midden vooraan komt
  // (yaw zodanig dat x'=0 en de ster vóór de camera staat, pitch zodat y'=0)
  var rxz = Math.sqrt(n.x * n.x + n.z * n.z) || 0.01;
  var ty = Math.atan2(-n.x, n.z) + Math.PI;
  // Kies de draairichting met de kortste weg vanaf de huidige yaw
  while (ty - g.yaw > Math.PI) ty -= 2 * Math.PI;
  while (ty - g.yaw < -Math.PI) ty += 2 * Math.PI;
  g.targetYaw = ty;
  g.targetPitch = Math.max(-1.2, Math.min(1.2, Math.atan2(-n.y, rxz)));
  g.targetZoom = Math.max(g.zoom, 1.6);
  // Pauzeer de vlucht zodat de ster in beeld blijft (hervatten kan met ▶)
  if (g.autoRotate) galaxyToggleFlight();

  var d = document.getElementById('galaxy-detail');
  if (!d) return;
  var color = g.groupColor[n.group] || GALAXY_OTHER_COLOR;
  d.style.display = 'block';
  d.innerHTML = '<div style="display:flex;justify-content:space-between;align-items:flex-start;gap:8px">' +
    '<div style="font-size:13px;font-weight:700;color:#f1f5f9;line-height:1.4">' + escHtml(n.name) + '</div>' +
    '<button onclick="document.getElementById(\'galaxy-detail\').style.display=\'none\';if(_galaxy)_galaxy.selected=-1" style="background:none;border:none;color:#64748b;font-size:15px;cursor:pointer;line-height:1">\u{2715}</button></div>' +
    '<div style="display:flex;align-items:center;gap:6px;margin:7px 0"><span style="width:8px;height:8px;border-radius:99px;background:' + color + '"></span>' +
    '<span style="font-size:10px;color:#94a3b8">' + escHtml(n.group) + ' \u{B7} ' + n.deg + ' links \u{B7} ' + Math.round(n.days) + ' dagen geleden</span></div>' +
    '<div id="galaxy-note-body" style="font-size:11px;color:#94a3b8">Laden...</div>';

  fetch('/api/infinite-context/note?path=' + encodeURIComponent(n.id)).then(function(r) { return r.json(); }).then(function(note) {
    var body = document.getElementById('galaxy-note-body');
    if (!body || !_galaxy || _galaxy.selected !== idx) return;
    var linkChip = function(name) {
      var i = _galaxy.nodes.findIndex(function(x) { return x.name === name; });
      return '<span ' + (i >= 0 ? 'onclick="galaxySelect(' + i + ')" style="cursor:pointer;color:#a5b4fc;"' : 'style="color:#64748b"') +
        ' class="galaxy-link-chip">[[' + escHtml(name) + ']]</span>';
    };
    var html = '';
    if (note.backlinks && note.backlinks.length) html += '<div style="margin-bottom:8px"><div style="font-size:9px;letter-spacing:1px;color:#64748b;margin-bottom:4px">VERBONDEN MET</div><div style="display:flex;flex-wrap:wrap;gap:4px;font-size:10px">' + note.backlinks.map(linkChip).join(' ') + '</div></div>';
    html += '<div style="font-size:9px;letter-spacing:1px;color:#64748b;margin-bottom:4px">INHOUD \u{B7} ' + escHtml(note.modified || '') + '</div>' +
      '<pre style="white-space:pre-wrap;font-size:11px;line-height:1.6;color:#cbd5e1;font-family:Inter,sans-serif;margin:0">' + escHtml((note.content || '').slice(0, 2500)) + (note.truncated || (note.content || '').length > 2500 ? '\n\u{2026}' : '') + '</pre>';
    body.innerHTML = html;
  }).catch(function() {
    var body = document.getElementById('galaxy-note-body');
    if (body) body.innerHTML = '<span style="color:#ef4444">Kon notitie niet laden</span>';
  });
}

function galaxyToggleFlight() {
  var g = _galaxy; if (!g) return;
  g.autoRotate = !g.autoRotate;
  var btn = document.getElementById('galaxy-flight-btn');
  if (btn) btn.innerHTML = g.autoRotate ? '\u{23F8} Pauze' : '\u{25B6} Vlucht';
}
function galaxyResetView() {
  var g = _galaxy; if (!g) return;
  g.targetYaw = 0.4; g.targetPitch = 0.18; g.targetZoom = 1;
  g.selected = -1; g.searchHits = null; g.dimGroups = {};
  var d = document.getElementById('galaxy-detail'); if (d) d.style.display = 'none';
  var s = document.getElementById('galaxy-search'); if (s) s.value = '';
  var r = document.getElementById('galaxy-search-results'); if (r) r.style.display = 'none';
  document.querySelectorAll('.galaxy-legend-chip').forEach(function(c) { c.style.opacity = '1'; });
}
function galaxyToggleGroup(chip) {
  var g = _galaxy; if (!g) return;
  var grp = chip.getAttribute('data-group');
  // 'Overig' = alle groepen zonder eigen kleur
  var targets = grp === 'Overig'
    ? Object.keys(g.nodes.reduce(function(acc, n) { if ((g.groupColor[n.group] || GALAXY_OTHER_COLOR) === GALAXY_OTHER_COLOR) acc[n.group] = 1; return acc; }, {}))
    : [grp];
  var nowDimmed = !g.dimGroups[targets[0]];
  targets.forEach(function(t) { if (nowDimmed) g.dimGroups[t] = true; else delete g.dimGroups[t]; });
  chip.style.opacity = nowDimmed ? '0.35' : '1';
}

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
    '<span style="font-size:10px;color:#94a3b8">Draait ook automatisch elke 4 uur</span></div>' +
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
    '<option value="dismissed"' + (radarStatusFilter==='dismissed'?' selected':'') + '>Genegeerd</option></select></div>' +
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
  var url = '/api/radar/sky?project=' + encodeURIComponent(currentProject) + (radarStatusFilter ? '&status=' + radarStatusFilter : '');
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
        '<span style="font-size:10px;padding:2px 8px;border-radius:6px;font-weight:600;background:' + sb[1] + '">' + sb[0] + '</span></div>' +
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
    alert('🚀 ' + data.tasks.length + ' ta' + (data.tasks.length===1?'ak':'ken') + ' aangemaakt in de Conveyor:\n\n' + data.tasks.map(function(t){ return '• ' + t.title + ' (' + t.agent + ')'; }).join('\n') + '\n\nWorkspace: ' + data.workspace);
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
//  INSTELLINGEN — MCP Manager & Agent Profielen
// ═══════════════════════════════════════════════════════════════════
async function renderInstellingenTab(el) {
  el.innerHTML = '<div class="loading"><div class="spinner"></div><p>Instellingen laden...</p></div>';
  try {
    var [profilesResp, goalsResp] = await Promise.all([
      fetch('/api/agents'),
      fetch('/api/goals?limit=1'),
    ]);
    var profiles = await profilesResp.json();
    var skillsEndp = await fetch('/api/strategist/control-room');
  } catch(e) { el.innerHTML = '<div class="empty-state">Fout: ' + escHtml(e.message) + '</div>'; return; }

  var html = '<h3 style="font-size:15px;font-weight:700;margin-bottom:16px">Instellingen &amp; Beheer</h3>';

  html += await renderSitePublishSettings();

  // ── Agent Profielen tabel ──
  html += '<div class="section-card"><h4 style="font-size:13px;font-weight:600;margin-bottom:8px">Agent Profielen (' + (profiles||[]).length + ')</h4>' +
    '<table class="data-table"><thead><tr><th>Naam</th><th>Model</th><th>MCP Servers</th><th>Aangemaakt</th></tr></thead><tbody>';
  (profiles||[]).forEach(function(p){
    var mcpStr = (p.mcp_servers||[]).join(', ') || '-';
    var created = (p.created_at||'').slice(0,10);
    html += '<tr><td><span style="font-weight:600">' + escHtml(p.name) + '</span></td>' +
      '<td style="font-size:11px;color:#64748b">' + escHtml(p.model||'-') + '</td>' +
      '<td style="font-size:11px;color:#64748b">' + escHtml(mcpStr) + '</td>' +
      '<td style="font-size:11px;color:#94a3b8">' + escHtml(created) + '</td></tr>';
  });
  html += '</tbody></table></div>';

  // ── Skills overzicht ──
  var skills = [
    ['research', 'SEO Specialist', 'Onderzoek'],
    ['content-writer', 'Content Writer', 'Schrijven'],
    ['content-editor', 'Content Editor', 'Eindredactie'],
    ['content-judge', 'Content Judge', 'Beoordeling'],
    ['seo', 'SEO Specialist', 'SEO'],
    ['video-builder', 'Video Creator', 'Video script'],
    ['video-director', 'Video Director', 'Video regie'],
    ['outreach', 'Outreach Agent', 'Lead generatie'],
    ['publisher', 'Content Writer', 'Publiceren'],
    ['analyst', 'SEO Specialist', 'Analyse'],
    ['designer', 'Content Writer', 'Design'],
  ];
  html += '<div class="section-card" style="margin-bottom:16px"><h4 style="font-size:13px;font-weight:600;margin-bottom:8px">Skills &#8594; Profiel mapping</h4>' +
    '<table class="data-table"><thead><tr><th>Skill</th><th>Profiel</th><th>Type</th></tr></thead><tbody>';
  skills.forEach(function(s){
    html += '<tr><td><code style="font-size:11px;padding:1px 5px;background:#f1f5f9;border-radius:3px">' + escHtml(s[0]) + '</code></td>' +
      '<td><span class="badge badge-draft">' + escHtml(s[1]) + '</span></td>' +
      '<td style="color:#64748b;font-size:11px">' + escHtml(s[2]) + '</td></tr>';
  });
  html += '</tbody></table></div>';

  // ── Systeeminfo ──
  html += '<div class="section-card"><h4 style="font-size:13px;font-weight:600;margin-bottom:8px">API Overzicht</h4>' +
    '<table class="data-table"><thead><tr><th>Endpoint</th><th>Method</th><th>Omschrijving</th></tr></thead><tbody>' +
    '<tr><td><code>/api/agents</code></td><td>GET</td><td>Alle profielen</td></tr>' +
    '<tr><td><code>/api/agents</code></td><td>POST</td><td>Nieuw profiel aanmaken</td></tr>' +
    '<tr><td><code>/api/agents/{id}</code></td><td>PATCH</td><td>Profiel bijwerken</td></tr>' +
    '<tr><td><code>/api/agents/{id}</code></td><td>DELETE</td><td>Profiel verwijderen</td></tr>' +
    '<tr><td><code>/api/goals</code></td><td>GET</td><td>Alle doelen</td></tr>' +
    '<tr><td><code>/api/strategist/control-room</code></td><td>GET</td><td>Control Room status</td></tr>' +
    '<tr><td><code>/api/strategist/analyse</code></td><td>POST</td><td>Strategist AI-analyse</td></tr>' +
    '<tr><td><code>/api/infinite-context/status</code></td><td>GET</td><td>ICE status</td></tr>' +
    '</tbody></table></div>';

  el.innerHTML = html;
}

// ── Publicatie- & social-instellingen voor de site achter dit project ──
function _siteField(label, name, value, opts) {
  opts = opts || {};
  var isSecret = !!opts.secret;
  var placeholder = isSecret ? (opts.set ? '•••••••• (ingesteld — laat leeg om te behouden)' : 'niet ingesteld') : (opts.placeholder || '');
  var type = isSecret ? 'password' : (opts.type || 'text');
  return '<label style="display:block;margin-bottom:8px"><span style="display:block;font-size:11px;color:#64748b;margin-bottom:2px">' + label + '</span>' +
    '<input type="' + type + '" data-site-field="' + name + '" value="' + (isSecret ? '' : escHtml(value||'')) + '" placeholder="' + escHtml(placeholder) + '" ' +
    'style="width:100%;padding:6px 8px;border:1px solid #e2e8f0;border-radius:6px;font-size:12px;box-sizing:border-box" /></label>';
}

async function renderSitePublishSettings() {
  var site;
  try {
    var sites = await (await fetch('/api/sites')).json();
    var norm = function(s){return (s||'').toLowerCase().replace(/ /g,'').replace(/-/g,'').replace(/_/g,'');};
    site = (sites||[]).find(function(s){return norm(s.name) === norm(currentProject);});
  } catch(e) { return ''; }
  if (!site) {
    return '<div class="section-card" style="margin-bottom:16px"><h4 style="font-size:13px;font-weight:600;margin-bottom:8px">Publicatie &amp; Social</h4>' +
      '<p style="font-size:12px;color:#94a3b8">Geen site gevonden voor dit project — maak er eerst één aan via <code>POST /api/sites</code> (zie /docs).</p></div>';
  }
  window._settingsSite = site;
  return '<div class="section-card" style="margin-bottom:16px">' +
    '<h4 style="font-size:13px;font-weight:600;margin-bottom:4px">Publicatie &amp; Social — ' + escHtml(site.name) + '</h4>' +
    '<p style="font-size:11px;color:#94a3b8;margin-bottom:10px">Tokens worden nooit teruggestuurd naar de browser — laat een veld leeg om de bestaande waarde te behouden.</p>' +
    '<label style="display:flex;align-items:center;gap:6px;margin-bottom:12px;font-size:12px;font-weight:600;color:#334155">' +
    '<input type="checkbox" id="site-auto-content" ' + (site.auto_content_enabled ? 'checked' : '') + ' /> ' +
    '2x/week auto-content aan (schrijft di+vr een concept, wacht op jouw goedkeuring in de Wachtrij-tab)</label>' +
    '<div style="display:grid;grid-template-columns:1fr 1fr;gap:0 16px">' +
    _siteField('Basis-URL (live site)', 'base_url', site.base_url) +
    _siteField('Search Console property', 'gsc_property', site.gsc_property) +
    _siteField('Netlify site-ID', 'publish_api_url', site.publish_api_url) +
    _siteField('Netlify token', 'publish_api_key', '', {secret:true, set:site.publish_api_key_set}) +
    _siteField('LinkedIn token', 'linkedin_token', '', {secret:true, set:site.linkedin_token_set}) +
    _siteField('LinkedIn user URN/ID', 'linkedin_user_urn', site.linkedin_user_urn) +
    _siteField('Facebook page-ID', 'facebook_page_id', site.facebook_page_id) +
    _siteField('Facebook page-token', 'facebook_page_token', '', {secret:true, set:site.facebook_page_token_set}) +
    _siteField('Instagram business-ID', 'instagram_business_id', site.instagram_business_id) +
    _siteField('X API key', 'twitter_api_key', '', {secret:true, set:site.twitter_api_key_set}) +
    _siteField('X API secret', 'twitter_api_secret', '', {secret:true, set:site.twitter_api_secret_set}) +
    _siteField('X access token', 'twitter_access_token', '', {secret:true, set:site.twitter_access_token_set}) +
    _siteField('X access secret', 'twitter_access_secret', '', {secret:true, set:site.twitter_access_secret_set}) +
    '</div>' +
    '<button onclick="saveSitePublishSettings(this)" style="margin-top:8px;padding:6px 16px;background:#4f46e5;color:#fff;border:none;border-radius:6px;font-size:11px;cursor:pointer">Opslaan</button>' +
    '<span id="site-settings-status" style="margin-left:10px;font-size:11px;color:#059669"></span>' +
    '</div>';
}

async function saveSitePublishSettings(btn) {
  var site = window._settingsSite; if (!site) return;
  var body = { auto_content_enabled: !!document.getElementById('site-auto-content').checked };
  document.querySelectorAll('[data-site-field]').forEach(function(input) {
    var v = input.value;
    if (v !== '') body[input.getAttribute('data-site-field')] = v;
  });
  if (btn) { btn.disabled = true; btn.textContent = 'Opslaan...'; }
  try {
    var resp = await fetch('/api/sites/' + site.id, { method: 'PATCH', headers: {'Content-Type':'application/json'}, body: JSON.stringify(body) });
    if (!resp.ok) { var d = await resp.json(); alert('Mislukt: ' + (d.detail || 'onbekende fout')); }
    else { var statusEl = document.getElementById('site-settings-status'); if (statusEl) statusEl.textContent = 'Opgeslagen ✓'; renderInstellingenTab(document.getElementById('tab-content')); }
  } catch(e) { alert('Fout: ' + e.message); }
  if (btn) { btn.disabled = false; btn.textContent = 'Opslaan'; }
}

// ═══════════════════════════════════════════════════════════════════
//  CHAT — Werkende chat met streaming
// ═══════════════════════════════════════════════════════════════════
var _chatSessionId = null;

async function ensureChatSession() {
  if (_chatSessionId) return _chatSessionId;
  try {
    var resp = await fetch('/api/sessions', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({name: currentProject + ' chat', agent: 'claude'}),
    });
    var data = await resp.json();
    _chatSessionId = data.id;
    return _chatSessionId;
  } catch(e) {
    // Fallback: create via sessions endpoint
    try {
      var resp2 = await fetch('/api/sessions');
      var existing = await resp2.json();
      if (existing && existing.length) {
        _chatSessionId = existing[0].id;
        return _chatSessionId;
      }
    } catch(e2) {}
    return null;
  }
}

function renderChat(main) {
  main.innerHTML = renderSidebar() + '<div class="main-content"><div class="project-header"><div><h1>Chat — ' + escHtml(currentProject||'Agent OS') + '</h1></div><div class="actions"><button onclick="goHome()">Projecten</button></div></div><div class="chat-container"><div id="chat-messages" class="chat-messages"><div class="chat-msg assistant">Hallo! Ik ben je AI-assistent voor ' + escHtml(currentProject||'Agent OS') + '. Waar kan ik je mee helpen?</div></div><div class="chat-input"><input id="chat-input" placeholder="Typ je bericht..." onkeydown="if(event.key===\'Enter\')sendChat()"><button onclick="sendChat()">Verstuur</button></div></div></div>';
  ensureChatSession();
}

async function sendChat() {
  var input = document.getElementById('chat-input');
  var msg = input.value.trim();
  if (!msg) return;
  input.value = '';
  var container = document.getElementById('chat-messages');
  container.innerHTML += '<div class="chat-msg user">' + escHtml(msg) + '</div><div class="chat-msg assistant" id="chat-pending"><em>Hermes denkt...</em></div>';
  container.scrollTop = container.scrollHeight;

  var sid = _chatSessionId;
  if (!sid) {
    sid = await ensureChatSession();
  }
  if (!sid) {
    document.getElementById('chat-pending').outerHTML = '<div class="chat-msg assistant" style="color:#ef4444">❌ Kon geen chatsessie starten. Start eerst een sessie via Instellingen.</div>';
    return;
  }

  // Use the streaming chat endpoint
  try {
    var resp = await fetch('/api/chat/stream', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({session_id: sid, message: msg, agent: 'claude', use_obsidian: true}),
    });
    if (!resp.ok) {
      var errText = await resp.text();
      document.getElementById('chat-pending').outerHTML = '<div class="chat-msg assistant" style="color:#ef4444">❌ Fout: ' + escHtml(errText.slice(0,200)) + '</div>';
      return;
    }

    var pending = document.getElementById('chat-pending');
    if (!pending) return;
    pending.outerHTML = '<div class="chat-msg assistant" id="chat-streaming"><em>Antwoord ontvangen...</em></div>';
    var streamingEl = document.getElementById('chat-streaming');
    if (!streamingEl) return;

    // Read the stream
    var reader = resp.body.getReader();
    var decoder = new TextDecoder();
    var fullText = '';
    streamingEl.innerHTML = '';

    while (true) {
      var {done, value} = await reader.read();
      if (done) break;
      var chunk = decoder.decode(value, {stream: true});
      var lines = chunk.split('\n');
      for (var li = 0; li < lines.length; li++) {
        var line = lines[li].trim();
        if (!line || line === ':' || line.startsWith(':keepalive')) continue;
        if (line === '[DONE]' || line === 'data: [DONE]') {
          streamingEl.innerHTML = fullText ? mdToHtmlSimple(fullText) : '(geen antwoord)';
          break;
        }
        if (line.startsWith('data: ')) {
          try {
            var evt = JSON.parse(line.slice(6));
            if (evt.type === 'text' || evt.type === 'thought') {
              fullText += evt.text || '';
              streamingEl.innerHTML = mdToHtmlSimple(fullText);
              container.scrollTop = container.scrollHeight;
            } else if (evt.type === 'error') {
              streamingEl.innerHTML += '<div style="color:#ef4444;margin-top:8px">❌ Fout: ' + escHtml(evt.message||'') + '</div>';
            } else if (evt.type === 'tool_start') {
              streamingEl.innerHTML += '<div style="color:#64748b;font-size:11px;margin:4px 0">🔧 Gebruik: ' + escHtml(evt.name||'') + '...</div>';
            } else if (evt.type === 'tool_result') {
              streamingEl.innerHTML += '<div style="color:#94a3b8;font-size:10px;margin:2px 0">✓ ' + escHtml(evt.name||'') + ' klaar</div>';
            }
          } catch(e) {
            // Non-JSON SSE line, skip
          }
        }
      }
    }
    streamingEl.id = ''; // Remove id after done
  } catch(e) {
    var p = document.getElementById('chat-pending') || document.getElementById('chat-streaming');
    if (p) p.outerHTML = '<div class="chat-msg assistant" style="color:#ef4444">❌ Fout: ' + escHtml(e.message) + '</div>';
  }
}

// Simple markdown renderer for chat (no tables needed)
function mdToHtmlSimple(text) {
  if (!text) return '';
  var t = escHtml(text);
  // Code blocks
  t = t.replace(/```(\w*)\n([\s\S]*?)```/g, function(m, lang, code) {
    return '<pre style="background:#1e293b;color:#e2e8f0;padding:10px;border-radius:6px;overflow-x:auto;font-size:11px;line-height:1.5;margin:8px 0"><code>' + code + '</code></pre>';
  });
  // Inline code
  t = t.replace(/`([^`]+)`/g, '<code style="background:#f1f5f9;padding:1px 4px;border-radius:3px;font-size:11px">$1</code>');
  // Bold
  t = t.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
  // Italic
  t = t.replace(/\*(.+?)\*/g, '<em>$1</em>');
  // Line breaks
  t = t.replace(/\n/g, '<br>');
  return t;
}

// ═══════════════════════════════════════════════════════════════════
//  FINANCE EXPERT (non-GSC)
// ═══════════════════════════════════════════════════════════════════
function renderFinanceExpert(el) {
  el.innerHTML = '<div class="agent-card"><div class="agent-icon" style="background:linear-gradient(135deg,#f43f5e,#e11d48)">F</div><h2>Finance Expert Agent</h2><p class="desc">Financiele analyse, rapportage en inzicht.</p><div class="cap-grid">' +
    '<div class="cap-item"><div class="num" style="background:#f43f5e">1</div><div><p>Dagelijks financieel rapport</p><p class="sub">Automatisch om 09:00.</p></div></div>' +
    '<div class="cap-item"><div class="num" style="background:#f43f5e">2</div><div><p>Wekelijkse trendanalyse</p><p class="sub">Inzicht in patronen en budget-bewaking.</p></div></div>' +
    '<div class="cap-item"><div class="num" style="background:#f43f5e">3</div><div><p>Ad-hoc analyses</p><p class="sub">Stel vragen over specifieke periodes.</p></div></div></div>' +
    '<div class="tips"><h3>Tips</h3><ul><li>Vraag naar de dagelijkse financiele samenvatting voor een snel overzicht van je omzet en uitgaven.</li><li>Laat een wekelijks rapport genereren met trends en afwijkingen in je financien.</li><li>Gebruik "vergelijken met vorige maand" om seizoenspatronen te ontdekken.</li></ul></div>' +
    '<button onclick="switchView(\'chat\')" style="padding:10px 28px;background:#f43f5e;color:#fff;border:none;border-radius:8px;font-size:14px;cursor:pointer">Start chat</button></div>';
}

// ═══════════════════════════════════════════════════════════════════
//  INIT
// ═══════════════════════════════════════════════════════════════════
document.addEventListener('DOMContentLoaded', function() {
  var m = location.hash.match(/project=([^&]+)/);
  if (m) currentProject = decodeURIComponent(m[1]);
  var t = location.hash.match(/tab=([^&]+)/);
  if (t && TABS.indexOf(decodeURIComponent(t[1])) >= 0) currentTab = decodeURIComponent(t[1]);
  route();
});
