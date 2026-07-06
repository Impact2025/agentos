// ── Agent OS — kern: constanten, state, helpers, routing
// Onderdeel van de SPA: klassieke scripts, gedeelde globale scope.
// Laadvolgorde staat in index.html — core.js eerst.

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

