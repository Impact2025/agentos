// ── Agent OS — kern: constanten, state, helpers, routing
// Onderdeel van de SPA: klassieke scripts, gedeelde globale scope.
// Laadvolgorde staat in index.html — core.js eerst.

// ── Agent OS — Pro SEO Dashboard ──────────────────────────────────
const PROJECTS = ['WeAreImpact', 'IctusGo', 'Pootgelukkig', 'BewaardVoorJou', 'Kappersassistent', 'DatingAssistent', 'Finance Expert', 'Bijeen', 'Brickme', 'Vrijwilligersmatch', 'Skillkaart', 'Steentjeapp', 'Zorgblik', 'Teambuildingmetimpact', 'Daar'];
const COLORS = { WeAreImpact: ['from-indigo-500 to-indigo-600','indigo'], Pootgelukkig: ['from-emerald-500 to-emerald-600','emerald'], BewaardVoorJou: ['from-amber-500 to-amber-600','amber'], Kappersassistent: ['from-violet-500 to-violet-600','violet'], DatingAssistent: ['from-red-500 to-red-600','red'], 'Finance Expert': ['from-rose-500 to-rose-600','rose'], Bijeen: ['from-cyan-500 to-cyan-600','cyan'], Brickme: ['from-orange-500 to-orange-600','orange'], Vrijwilligersmatch: ['from-teal-500 to-teal-600','teal'], Skillkaart: ['from-pink-500 to-pink-600','pink'], Steentjeapp: ['from-sky-500 to-sky-600','sky'], Zorgblik: ['from-lime-500 to-lime-600','lime'], Teambuildingmetimpact: ['from-amber-500 to-amber-600','amber'], IctusGo: ['from-cyan-700 to-emerald-600','emerald'] };
const DESCS = { WeAreImpact: 'AI en innovatie voor zorg, welzijn en gemeenten', Pootgelukkig: 'Adoptieplatform voor asieldieren', BewaardVoorJou: 'Digitaal levensboek voor 65-plussers', Kappersassistent: 'Project kappersbranche (in opstart)', DatingAssistent: 'AI dating coach & datingadvies', 'Finance Expert': 'Financiele rapportage en analyse', Bijeen: 'Sociale verbinding & bijeenkomsten', Brickme: 'Bouw & constructie', Vrijwilligersmatch: 'Vrijwilligers matching platform', Skillkaart: 'Vaardigheden & competenties', Steentjeapp: 'Mobiele app Steentjebijsteentje', Zorgblik: 'Zorginnovatie & inzicht', Teambuildingmetimpact: 'Bedrijfsvrijwilligerswerk, impact days & LEGO Serious Play', IctusGo: 'GPS teambuilding met sociale impact (Hoofddorp/Schiphol)' };
const TABS = ['Dashboard', 'Postvak', 'Kansen', 'Optimalisatie', 'Wachtrij', 'Concurrentie', 'Radar', 'Doelen', 'Geheugen', 'Leads', 'Links', 'Opdrachten', 'Technisch', 'Activiteit', 'Social Creatie', 'Omni', 'Gauntlet', 'Helpdesk', 'Instellingen'];
// Menu-iconen. Bewust één monochrome familie (geometrische vormen + pijlen) en
// géén emoji: emoji krijgen op Windows een eigen kleur en een eigen optische
// maat, waardoor een menu van zeventien regels zeventien verschillende hoogtes
// krijgt. Tot 10 aug 2026 stonden hier losse hoofdletters ('D', 'K', 'Q') —
// leesbaar als afkorting náást het woord dat er al stond, dus ruis; en
// 'Social Creatie' en 'Instellingen' deelden allebei de 'S'.
const TAB_ICONS = { Dashboard: '▦', Postvak: '✉︎', Kansen: '◎', Optimalisatie: '↗', Wachtrij: '◷', Concurrentie: '⧉', Radar: '✦', Doelen: '◉', Geheugen: '❖', Leads: '⊕', Links: '⇄', Opdrachten: '▤', Technisch: '◫', Activiteit: '⟳', 'Social Creatie': '◐', Omni: '⬡', Gauntlet: '⛓', Helpdesk: '↩', Instellingen: '⚙︎' };
// Welk backend-domein een tab nodig heeft (zie shared/config.py:domain_enabled).
// Geen entry = kerntab, altijd zichtbaar. Op de hoofdinstallatie is
// window.__enabledDomains altijd null (geen whitelist) dus verandert hier niets.
// Postvak hoort bij 'outlook_legacy' (Graph/OAuth), niet bij 'mail' — dat laatste
// is het generieke POP3-mailboxen-domein achter de Helpdesk-tab, een ander
// systeem met een andere auth-vorm (zie domains/outlook/service.py-docstring).
const TAB_DOMAIN = { Postvak: 'outlook_legacy', Kansen: 'seo', Optimalisatie: 'seo', Wachtrij: 'publish', Concurrentie: 'seo', Radar: 'radar', Doelen: 'goal', Leads: 'prospecting', Links: 'linkbuilding', Opdrachten: 'vacancies', Technisch: 'seo', 'Social Creatie': 'social', Omni: 'seo', Gauntlet: 'pipeline', Helpdesk: 'mail' };
// Eén check voor "hoort dit domein bij deze instance" — gebruikt door de
// tab-filter én door de Control Room-secties die verwijzen naar routes die op
// een beperkte instance niet gemonteerd zijn (Linkbuilding, Strategist/Doelen).
function domainOn(d) {
  return !window.__enabledDomains || window.__enabledDomains.indexOf(d) >= 0;
}
function visibleTabs() {
  return TABS.filter(function(t) { return !TAB_DOMAIN[t] || domainOn(TAB_DOMAIN[t]); });
}

let currentProject = null, currentTab = 'Dashboard', weSuggestions = [], oppStatusFilter = 'open', scanningInProgress = false, chartInstances = {};
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
  // Eén call naar de geconsolideerde healthcheck geeft backend-gezondheid,
  // tokengebruik en actieve-status in één keer — geen 2 aparte fetches meer.
  fetch('/api/healthcheck').then(function(r){return r.json();}).catch(function(){return null;}).then(function(h){
    if (!h) return;
    var b = h.backend || {};
    var active = b.active || '?';
    var llm = h.llm || {};
    var t = llm.today || {};
    var pct = t.budget_pct != null ? t.budget_pct : 0;
    var color, label, dot;
    if (h.status === 'degraded') { color = '#dc2626'; dot = '#ef4444'; label = 'Degraded'; }
    else if (h.status === 'warning') { color = '#d97706'; dot = '#f59e0b'; label = 'Let op'; }
    else { color = '#16a34a'; dot = '#22c55e'; label = 'Gezond'; }

    // Noem de backend die het wérk doet, niet de probe die toevallig antwoordde.
    // Tot 4 aug 2026 stond hier 'local·Ollama' zodra de lokale tier leefde —
    // óók als al het denkwerk via de OpenModel-gateway liep. Het badge meldde
    // dan een motor die niet draaide, náást een 'Degraded' zonder reden, en
    // suggereerde zo een LLM-storing waar de agenda-sync het probleem was.
    var extra = active;
    if (b.local && b.local.live === false && active === 'local') extra = active + ' (DOOD)';
    var reden = h.reden || '';

    // Op een telefoon is de projectkop verborgen (de mobiele balk vervangt hem),
    // dus zou de gezondheidsstatus daar helemaal wegvallen. Eén stip met een
    // tooltip is genoeg om te zien dát er iets mis is — de volle regel staat op
    // de Control Room.
    var mob = document.getElementById('agent-status-indicator-mobile');
    if (mob) mob.innerHTML = '<span title="' + escHtml((h.summary || label)) + '" style="display:block;width:9px;height:9px;border-radius:50%;background:' + dot + '"></span>';

    var el = document.getElementById('agent-status-indicator');
    if (!el) return;
    el.innerHTML = '<span style="display:inline-flex;align-items:center;gap:4px;padding:3px 8px;border-radius:12px;background:'
      + (h.status==='degraded' ? '#fef2f2' : h.status==='warning' ? '#fffbeb' : '#f0fdf4')
      + ';color:' + color + ';font-size:10px;font-weight:600" title="'
      + escHtml(h.summary || '') + '">'
      + '<span style="width:6px;height:6px;border-radius:50%;background:' + dot
      + (h.status==='ok' ? ';animation:pulse 1.5s infinite' : '')
      + '"></span> ' + label
      + (reden ? ' · ' + escHtml(reden) : '')
      + ' · ' + escHtml(extra)
      + (pct != null ? ' · ' + pct + '% tokens' : '') + '</span>';

    // Mislukte doelen tonen nog steeds de Oplossen-knop (bestaand gedrag).
    var failed = (h.active_work && h.active_work.goals) ? h.active_work.goals.filter(function(g){return g.status==='failed';}).length : 0;
    var resEl = document.getElementById('resolve-failed-btn-container');
    if (resEl) resEl.innerHTML = failed ? '<button onclick="resolveAllFailed()" style="padding:3px 10px;background:#ef4444;color:#fff;border:none;border-radius:6px;font-size:11px;cursor:pointer;font-weight:600">\u{1F9E0} Oplossen</button>' : '';
  });
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

// POST met JSON-body. De `detail` uit een FastAPI-HTTPException wordt de
// foutmelding, zodat de gebruiker leest wat er stuk is ("Websearch niet
// beschikbaar: quota op") in plaats van een kaal "HTTP 502".
function post(url, body) {
  // Een al-gestringificeerde body gaat ongemoeid door: sommige aanroepers geven
  // JSON.stringify(...) mee, en die nog eens stringificeren levert de server een
  // string in plaats van een object op.
  var payload = (body === undefined || body === null) ? undefined
    : (typeof body === 'string' ? body : JSON.stringify(body));
  return fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: payload,
  }).then(function (r) {
    return r.json().catch(function () { return null; }).then(function (data) {
      if (!r.ok) throw new Error((data && data.detail) ? data.detail : 'HTTP ' + r.status);
      return data;
    });
  });
}
// Alle social-platformen die de pipeline kent. Wordt alléén meegestuurd als de
// mens social expliciet aanvinkt — social is nooit de standaard.
var ALL_SOCIAL_CHANNELS = ['linkedin', 'facebook', 'instagram', 'twitter'];

// ── Confirm-modal met keuze-opties (vervangt de kale browser-confirm) ──
// opts: { title, body, buttons: [{label, value, primary, danger}] }
// Resolves met de `value` van de gekozen knop, of null bij annuleren/sluiten.
function showChoiceModal(opts) {
  return new Promise(function (resolve) {
    var overlay = document.createElement('div');
    overlay.style.cssText = 'position:fixed;inset:0;background:rgba(15,23,42,.45);display:flex;align-items:center;justify-content:center;z-index:9999;padding:16px;backdrop-filter:blur(2px)';
    var box = document.createElement('div');
    box.style.cssText = 'background:#fff;border-radius:14px;max-width:440px;width:100%;box-shadow:0 20px 50px rgba(0,0,0,.25);overflow:hidden;font-family:inherit';
    var html = '';
    if (opts.title) html += '<div style="padding:18px 20px 0;font-size:15px;font-weight:700;color:#0f172a">' + escHtml(opts.title) + '</div>';
    if (opts.body) html += '<div style="padding:10px 20px 4px;font-size:13px;line-height:1.55;color:#475569;white-space:pre-wrap">' + (opts.bodyHtml || escHtml(opts.body)) + '</div>';
    html += '<div style="padding:18px 20px 20px;display:flex;flex-direction:column;gap:8px">';
    (opts.buttons || []).forEach(function (b) {
      var bg = b.primary ? '#2563eb' : (b.danger ? '#dc2626' : '#f1f5f9');
      var fg = b.primary || b.danger ? '#fff' : '#475569';
      var bd = b.primary || b.danger ? 'none' : '1px solid #e2e8f0';
      html += '<button data-value="' + escHtml(String(b.value)) + '" style="padding:11px 16px;background:' + bg + ';color:' + fg + ';border:' + bd + ';border-radius:8px;font-size:13px;font-weight:600;cursor:pointer;text-align:left">' + escHtml(b.label) + '</button>';
    });
    html += '</div>';
    box.innerHTML = html;
    overlay.appendChild(box);
    document.body.appendChild(overlay);
    function close(val) { if (overlay.parentNode) overlay.parentNode.removeChild(overlay); resolve(val); }
    overlay.addEventListener('click', function (e) { if (e.target === overlay) close(null); });
    Array.prototype.forEach.call(box.querySelectorAll('button[data-value]'), function (b) {
      b.addEventListener('click', function () { close(b.getAttribute('data-value')); });
    });
  });
}

// `foot` is de recente reeks onder de periode-delta. Die twee kunnen elkaar
// tegenspreken — een 28-daags gemiddelde kan verbeteren terwijl de laatste week
// wegzakt — en dan is alleen de delta tonen misleidend: het dashboard meldt
// vooruitgang midden in een terugval (WeAreImpact, 2 aug 2026).
function kpiBox(label, val, change, sub, foot, footTone) {
  var extra = '';
  if (change !== undefined && change !== '') { var cls = change >= 0 ? 'pos' : 'neg'; extra = '<p class="change ' + cls + '">' + (change >= 0 ? '+' : '') + change + '</p>'; }
  else if (sub) { extra = '<p style="font-size:11px;color:#94a3b8;margin-top:1px">' + sub + '</p>'; }
  if (foot) {
    var kleur = footTone === 'bad' ? '#b91c1c' : footTone === 'good' ? '#15803d' : '#64748b';
    extra += '<p style="font-size:10px;color:' + kleur + ';margin-top:2px;line-height:1.35">' + foot + '</p>';
  }
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
  stopHelpdeskBadgePoll();
  if (!currentProject) renderHome(main);
  else if (currentTab === 'Chat') renderChat(main);
  else renderProjectView(main);
}
function selectProject(name) { currentProject = name; currentTab = 'Dashboard'; weSuggestions = []; history.pushState(null, '', '#project=' + encodeURIComponent(name)); route(); }
function goHome() { currentProject = null; currentTab = 'Dashboard'; weSuggestions = []; history.pushState(null, '', '#'); route(); }
function switchView(view) { if (view === 'home') { goHome(); return; } if (view === 'chat') { currentTab = 'Chat'; route(); return; } currentTab = view; route(); }
window.addEventListener('popstate', route);

