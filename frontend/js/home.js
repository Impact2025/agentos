// ── Agent OS — Control Room: home, Actiecentrum, digest, strategist, systeemgezondheid
// Onderdeel van de SPA: klassieke scripts, gedeelde globale scope.
// Laadvolgorde staat in index.html — core.js eerst.

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

    // ── Iris — de manager: cijfers per project, geleerd/verbeterd, advies ──
    html += '<div class="section-card" style="margin-bottom:16px;border:1px solid #ddd6fe;background:linear-gradient(135deg,#faf5ff,#f8fafc)">' +
      '<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:8px">' +
      '<h4 style="font-size:13px;font-weight:700;color:#7c3aed">\u{1F9ED} Iris — dagbriefing van je AI-manager</h4>' +
      '<div style="display:flex;gap:6px">' +
      '<button onclick="loadIrisBriefing()" style="padding:3px 10px;background:#f1f5f9;color:#475569;border:1px solid #e2e8f0;border-radius:4px;font-size:10px;cursor:pointer">Ververs</button>' +
      '<button id="iris-run-btn" onclick="runIrisNow()" style="padding:3px 10px;background:#7c3aed;color:#fff;border:none;border-radius:4px;font-size:10px;font-weight:600;cursor:pointer">Analyseer nu</button>' +
      '</div></div>' +
      '<div id="iris-panel" style="font-size:12px"><div style="color:#64748b">Laden...</div></div>' +
      '<details style="margin-top:10px;border-top:1px solid #ede9fe;padding-top:8px" ontoggle="if(this.open)loadIrisKnowledge()">' +
      '<summary style="cursor:pointer;font-size:12px;font-weight:600;color:#7c3aed">\u{1F4DA} Kennisbank — voed Iris met onderzoek (GEO, SEO, ...)</summary>' +
      '<div id="iris-knowledge-panel" style="margin-top:8px;font-size:12px"><div style="color:#64748b">Klik om te laden...</div></div></details></div>';

    // ── Ochtendrapport (inklapbaar; zelfde inhoud als de 07:00-digest) ──
    html += '<details class="section-card" style="margin-bottom:16px;padding:10px 16px" ontoggle="if(this.open)loadDigest()">' +
      '<summary style="cursor:pointer;font-size:13px;font-weight:700;color:#334155">\u{2615} Ochtendrapport — fouten · wacht-op-jou · gisteren opgeleverd · vandaag gepland</summary>' +
      '<div id="digest-panel" style="margin-top:10px;font-size:12px"><div style="color:#64748b">Klik om te laden...</div></div></details>';

    // ── Recent Activity logs (Vercel-style) ──
    html += '<div class="section-card" style="margin-bottom:16px"><div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:8px">' +
      '<h4 style="font-size:13px;font-weight:700">\u{1F4DC} Recente activiteit</h4>' +
      '<button onclick="loadActivityLogs()" style="padding:3px 10px;background:#f1f5f9;color:#475569;border:1px solid #e2e8f0;border-radius:4px;font-size:10px;cursor:pointer">Ververs</button></div>' +
      '<div id="activity-log-panel" style="background:#0f172a;border-radius:8px;padding:8px;font-family:monospace;font-size:11px;max-height:300px;overflow-y:auto">' +
      '<div style="color:#64748b;text-align:center;padding:16px">Laden...</div></div></div>';

    // ── OpenModel-credits — live verbruik per doel (llm_usage-telemetrie) ──
    html += '<div class="section-card" style="margin-bottom:16px"><div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:8px">' +
      '<h4 style="font-size:13px;font-weight:700">\u{1F4B3} OpenModel-credits — live verbruik</h4>' +
      '<button onclick="loadLlmUsage()" style="padding:3px 10px;background:#f1f5f9;color:#475569;border:1px solid #e2e8f0;border-radius:4px;font-size:10px;cursor:pointer">Ververs</button></div>' +
      '<div id="llm-usage-panel" style="font-size:12px"><div style="color:#64748b">Laden...</div></div></div>';

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
    startActionCenterRefresh();
    loadIrisBriefing();
    loadActivityLogs();
    loadLlmUsage();
    startLlmUsageRefresh();
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
  content_needs_work: { icon: '\u{1F6E0}\u{FE0F}', color: '#f59e0b', label: 'Onder kwaliteitsgrens' },
  task_approval: { icon: '✓',    color: '#0ea5e9', label: 'Taak wacht op goedkeuring' },
  vacancies:     { icon: '\u{1F4BC}', color: '#10b981', label: 'Opdracht-kansen' },
  leads:         { icon: '\u{1F465}', color: '#10b981', label: 'Nieuwe leads' },
  error:         { icon: '⚠',    color: '#ef4444', label: 'Fout' }
};

var _acLastItems = [];
var _acRefreshTimer = null;
function startActionCenterRefresh() {
  if (_acRefreshTimer) clearInterval(_acRefreshTimer);
  _acRefreshTimer = setInterval(function(){
    if (!document.getElementById('action-center-panel')) { clearInterval(_acRefreshTimer); _acRefreshTimer = null; return; }
    loadActionCenter();
  }, 30000);
}
function updateTabBadge(count) {
  document.title = count > 0 ? '(' + count + ') Agent OS' : 'Agent OS';
}

function loadActionCenter() {
  var el = document.getElementById('action-center-panel');
  if (!el) return;
  fetch('/api/action-center').then(function(r){return r.json();}).then(function(data){
    if (!el) return;
    var items = data.items || [];
    _acLastItems = items;
    updateTabBadge(items.length);
    if (!items.length) {
      el.innerHTML = '<div style="background:#f0fdf4;border:1px solid #86efac;border-radius:10px;padding:14px 16px;margin-bottom:16px;font-size:13px;color:#166534">' +
        '✅ <b>Inbox leeg</b> — niets wacht op jou. De agents draaien op schema.</div>';
      return;
    }
    var draftCount = items.filter(function(i){ return i.kind === 'goal_draft'; }).length;
    var bulkBar = '';
    if (draftCount >= 3) {
      bulkBar = '<div style="display:flex;gap:8px;align-items:center;margin-bottom:10px;padding:8px 12px;background:#fffbeb;border:1px solid #fde68a;border-radius:8px">' +
        '<span style="font-size:11px;color:#92400e;flex:1"><b>' + draftCount + ' doelen</b> wachten op je akkoord — in één keer afhandelen:</span>' +
        '<button onclick="acBulkDrafts(this, \'start\')" style="padding:4px 12px;background:#16a34a;color:#fff;border:none;border-radius:6px;font-size:11px;font-weight:600;cursor:pointer">▶ Start alles</button>' +
        '<button onclick="acBulkDrafts(this, \'delete\')" style="padding:4px 12px;background:#fff;color:#dc2626;border:1px solid #fecaca;border-radius:6px;font-size:11px;font-weight:600;cursor:pointer">Verwijder alles</button></div>';
    }
    var html = '<div class="section-card" style="margin-bottom:16px;border:2px solid #6366f1;background:linear-gradient(135deg,#eef2ff,#fff)">' +
      '<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:10px">' +
      '<h3 style="font-size:15px;font-weight:800;color:#312e81">\u{1F4E5} Vandaag — wacht op jou (' + items.length + ')</h3>' +
      '<span style="font-size:11px;color:#64748b">' + (data.counts.errors ? data.counts.errors + ' fout(en) · ' : '') + 'klik = klaar · ververst elke 30s</span></div>' + bulkBar;
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

var _digestLoaded = false;
function loadDigest() {
  if (_digestLoaded) return;
  var el = document.getElementById('digest-panel');
  if (!el) return;
  el.innerHTML = '<div style="color:#64748b">Laden...</div>';
  fetch('/api/action-center/digest').then(function(r){return r.json();}).then(function(d){
    _digestLoaded = true;
    el.innerHTML = '<div class="strategist-analyse-content">' + mdToHtml(d.markdown || '') + '</div>';
  }).catch(function(e){
    el.innerHTML = '<div style="color:#ef4444">Rapport laden mislukt: ' + escHtml(e.message) + '</div>';
  });
}

// ── OpenModel-credits — live verbruik (llm_usage-telemetrie) ───────
// Toont waar de credits vandaag heengaan: budgetbalk, grootverbruikers
// per doel/model en de 7-daagse trend. Ververst elke 30s, net als de inbox.
var _llmRouteLabels = {
  'iris': 'Iris (manager-analyse)',
  'content': 'Content-pipeline (schrijven & review)',
  'goal': 'Doelen (synthese)',
  'mail': 'Mail-helpdesk (concepten)',
  'outreach': 'Outreach-concepten',
  'seo-engine': 'SEO Demand Engine',
  'seo-optimizer': 'SEO-optimalisatie',
  'agent-openmodel': 'Chat-agent (tools)',
  'claude-openmodel': 'Denk-werk (ongelabeld)',
  'hermes-openmodel': 'Bulk-werk (ongelabeld)'
};

function _fmtTokens(n) {
  n = n || 0;
  if (n >= 1e6) return (n / 1e6).toFixed(2).replace('.', ',') + 'M';
  if (n >= 1000) return Math.round(n / 1000) + 'k';
  return String(n);
}

var _llmUsageTimer = null;
function startLlmUsageRefresh() {
  if (_llmUsageTimer) clearInterval(_llmUsageTimer);
  _llmUsageTimer = setInterval(function(){
    if (!document.getElementById('llm-usage-panel')) { clearInterval(_llmUsageTimer); _llmUsageTimer = null; return; }
    loadLlmUsage();
  }, 30000);
}

function loadLlmUsage() {
  var el = document.getElementById('llm-usage-panel');
  if (!el) return;
  fetch('/api/action-center/llm-usage').then(function(r){return r.json();}).then(function(d){
    if (!el) return;
    var t = d.today || {};
    var html = '';

    // Budgetbalk — vandaag t.o.v. DAILY_TOKEN_BUDGET
    var pct = t.budget_pct != null ? t.budget_pct : 0;
    var barColor = pct >= 90 ? '#dc2626' : (pct >= 70 ? '#d97706' : '#16a34a');
    html += '<div style="margin-bottom:10px">' +
      '<div style="display:flex;justify-content:space-between;gap:8px;font-size:11px;color:#475569;margin-bottom:3px;flex-wrap:wrap">' +
      '<span><b>Vandaag:</b> ' + _fmtTokens(t.total_tokens) + ' van ' + _fmtTokens(d.budget) + ' tokens (' + pct + '% van het dagbudget)' +
      (t.cost != null ? ' · ≈ $' + t.cost.toFixed(2) : '') + '</span>' +
      '<span>' + (t.calls || 0) + ' calls' + (t.errors ? ' · <span style="color:#dc2626;font-weight:600">' + t.errors + ' fout(en)</span>' : '') + '</span></div>' +
      '<div style="height:8px;background:#f1f5f9;border-radius:4px;overflow:hidden">' +
      '<div style="height:100%;width:' + Math.min(100, pct) + '%;background:' + barColor + ';border-radius:4px"></div></div></div>';

    // Grootverbruikers vandaag — per doel + model, aflopend
    var routes = d.by_route || [];
    if (!routes.length) {
      html += '<div style="color:#94a3b8">Nog geen LLM-verbruik vandaag.</div>';
    } else {
      html += '<div style="font-size:11px;font-weight:600;color:#334155;margin-bottom:4px">Grootverbruikers vandaag</div>';
      var max = routes[0].total_tokens || 1;
      routes.slice(0, 8).forEach(function(r){
        var share = t.total_tokens ? Math.round(100 * r.total_tokens / t.total_tokens) : 0;
        var label = _llmRouteLabels[r.route] || r.route;
        html += '<div style="display:flex;align-items:center;gap:8px;padding:3px 0">' +
          '<span style="flex:0 0 230px;min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis" title="route: ' + escHtml(r.route) + '">' +
          escHtml(label) + ' <span style="color:#94a3b8;font-size:10px">' + escHtml(r.model || '') + '</span></span>' +
          '<div style="flex:1;height:8px;background:#f1f5f9;border-radius:4px;overflow:hidden">' +
          '<div style="height:100%;width:' + Math.max(2, Math.round(100 * r.total_tokens / max)) + '%;background:#4f46e5;border-radius:4px"></div></div>' +
          '<span style="flex:0 0 130px;text-align:right;color:#475569">' + _fmtTokens(r.total_tokens) + ' · ' + share + '%' +
          (r.cost != null ? ' · $' + r.cost.toFixed(2) : '') + '</span>' +
          '<span style="flex:0 0 55px;text-align:right;color:#94a3b8;font-size:10px">' + r.calls + '×' +
          (r.errors ? ' <span style="color:#dc2626">' + r.errors + '⚠</span>' : '') + '</span></div>';
      });
    }

    // Trend — laatste dagen (staafjes, vandaag donker)
    var days = d.days || [];
    if (days.length > 1) {
      var dmax = 1;
      days.forEach(function(x){ if (x.total_tokens > dmax) dmax = x.total_tokens; });
      html += '<div style="margin-top:10px;border-top:1px solid #f1f5f9;padding-top:8px">' +
        '<div style="font-size:11px;font-weight:600;color:#334155;margin-bottom:4px">Laatste ' + days.length + ' dagen (tokens per dag)</div>' +
        '<div style="display:flex;align-items:flex-end;gap:6px;height:52px">' +
        days.map(function(x, i){
          var h = Math.max(2, Math.round(40 * x.total_tokens / dmax));
          var isToday = i === days.length - 1;
          return '<div title="' + x.date + ': ' + _fmtTokens(x.total_tokens) + ' tokens (' + x.calls + ' calls)' +
            (x.cost != null ? ' · $' + x.cost.toFixed(2) : '') + '" style="flex:1;display:flex;flex-direction:column;align-items:center;justify-content:flex-end;gap:2px;height:100%">' +
            '<div style="width:100%;max-width:34px;height:' + h + 'px;background:' + (isToday ? '#4f46e5' : '#c7d2fe') + ';border-radius:4px 4px 0 0"></div>' +
            '<span style="font-size:9px;color:#94a3b8">' + x.date.slice(8, 10) + '/' + x.date.slice(5, 7) + '</span></div>';
        }).join('') + '</div></div>';
    }

    if (!d.prices_configured) {
      html += '<div style="margin-top:8px;font-size:10px;color:#94a3b8">Tip: zet <code>OPENMODEL_INPUT_COST_PER_MTOK</code> en <code>OPENMODEL_OUTPUT_COST_PER_MTOK</code> in .env om het verbruik ook in dollars/credits te zien.</div>';
    }
    el.innerHTML = html;
  }).catch(function(e){
    el.innerHTML = '<div style="color:#ef4444">Verbruik laden mislukt: ' + escHtml(e.message) + '</div>';
  });
}

// ── Iris — dagbriefing van de manager-agent ────────────────────────
function _irisGradeColor(cijfer) {
  if (cijfer >= 8) return ['#dcfce7', '#166534'];
  if (cijfer >= 6) return ['#fef9c3', '#854d0e'];
  return ['#fee2e2', '#991b1b'];
}

function loadIrisBriefing() {
  var el = document.getElementById('iris-panel');
  if (!el) return;
  fetch('/api/iris/briefing').then(function(r){return r.json();}).then(function(d){
    var html = '';
    var grades = d.grades || {};
    var names = Object.keys(grades);
    if (!d.report_date) {
      // Nog geen briefing — toon het live cijferbeeld als voorproefje.
      var projs = ((d.metrics || {}).projects) || [];
      if (projs.length) {
        html += '<div style="display:flex;flex-wrap:wrap;gap:6px;margin-bottom:8px">' + projs.map(function(p){
          var c = _irisGradeColor(p.grade);
          return '<span style="padding:3px 8px;border-radius:12px;background:' + c[0] + ';color:' + c[1] + ';font-size:11px;font-weight:600">' + escHtml(p.project) + ' ' + p.grade + '</span>';
        }).join('') + '</div>';
      }
      html += '<div style="color:#64748b">Iris heeft nog geen dagbriefing gedraaid — klik <strong>Analyseer nu</strong> of wacht op de 06:45-run.</div>';
      el.innerHTML = html;
      return;
    }
    // Trefkans-badge: Iris' eigen bewezen track record (de gesloten leer-lus).
    var tr = d.track_record || {};
    if (tr.accuracy != null || tr.open) {
      var accColor = tr.accuracy == null ? ['#e2e8f0','#475569']
        : (tr.accuracy >= 60 ? ['#dcfce7','#166534'] : (tr.accuracy >= 40 ? ['#fef9c3','#854d0e'] : ['#fee2e2','#991b1b']));
      html += '<div style="margin-bottom:8px;font-size:11px;color:#475569">Mijn trefkans: ' +
        (tr.accuracy != null
          ? '<span style="padding:2px 8px;border-radius:10px;background:' + accColor[0] + ';color:' + accColor[1] + ';font-weight:700">' + tr.accuracy + '%</span> (' + (tr.correct||0) + ' raak · ' + (tr.wrong||0) + ' mis)'
          : '<span style="color:#94a3b8">nog geen afgerekende voorspellingen</span>') +
        (tr.open ? ' · <span style="color:#7c3aed;font-weight:600">' + tr.open + ' voorspelling(en) open</span>' : '') + '</div>';
    }
    // Cijfer-chips per project (laagste eerst — daar zit het werk).
    names.sort(function(a,b){ return (grades[a].cijfer||0) - (grades[b].cijfer||0); });
    html += '<div style="display:flex;flex-wrap:wrap;gap:6px;margin-bottom:8px">' + names.map(function(n){
      var g = grades[n]; var c = _irisGradeColor(g.cijfer || 0);
      return '<span title="' + escHtml(g.oordeel || '') + '" style="padding:3px 8px;border-radius:12px;background:' + c[0] + ';color:' + c[1] + ';font-size:11px;font-weight:600;cursor:default">' + escHtml(n) + ' ' + (g.cijfer != null ? g.cijfer : '?') + '</span>';
    }).join('') + '</div>';
    var advice = d.advice || [];
    if (advice.length) {
      html += '<div style="margin-bottom:8px">' + advice.slice(0,3).map(function(a){
        return '<div style="padding:4px 0;border-bottom:1px solid #f1f5f9"><strong style="color:#334155">' + (a.prio || '•') + '. ' + escHtml(a.actie || '') + '</strong> <span style="color:#64748b">— ' + escHtml(a.waarom || '') + '</span></div>';
      }).join('') + '</div>';
    }
    html += '<details><summary style="cursor:pointer;font-size:11px;font-weight:600;color:#7c3aed">Volledige briefing (' + escHtml(d.report_date) + ') — geleerd · verbeterd · advies</summary>' +
      '<div class="strategist-analyse-content" style="margin-top:8px">' + mdToHtml(d.markdown || '') + '</div></details>';
    el.innerHTML = html;
  }).catch(function(e){
    el.innerHTML = '<div style="color:#ef4444">Iris-briefing laden mislukt: ' + escHtml(e.message) + '</div>';
  });
}

// ── Iris kennisbank — Vincent voedt Iris met onderzoek ─────────────
function loadIrisKnowledge() {
  var el = document.getElementById('iris-knowledge-panel');
  if (!el) return;
  fetch('/api/iris/knowledge').then(function(r){return r.json();}).then(function(d){
    var items = d.items || [];
    var html = '';
    if (d.folder) {
      html += '<div style="font-size:11px;color:#64748b;margin-bottom:8px">Drop markdown-onderzoek in: <code style="background:#f1f5f9;padding:1px 4px;border-radius:3px">' + escHtml(d.folder) + '</code> — of plak hieronder direct.</div>';
    } else {
      html += '<div style="font-size:11px;color:#b45309;margin-bottom:8px">Geen Obsidian-vault ingesteld — je kunt kennis wel hieronder plakken.</div>';
    }
    // Plakveld voor directe kennis
    html += '<div style="margin-bottom:10px">' +
      '<input id="iris-know-title" placeholder="Titel (bv. GEO-onderzoek jan 2026)" style="width:100%;padding:6px 8px;border:1px solid #e2e8f0;border-radius:6px;font-size:12px;margin-bottom:4px">' +
      '<textarea id="iris-know-text" placeholder="Plak hier je onderzoek/notities die Iris moet leren en toepassen..." style="width:100%;min-height:70px;padding:6px 8px;border:1px solid #e2e8f0;border-radius:6px;font-size:12px;resize:vertical"></textarea>' +
      '<div style="display:flex;gap:6px;margin-top:4px">' +
      '<button id="iris-know-add" onclick="addIrisKnowledge()" style="padding:5px 12px;background:#7c3aed;color:#fff;border:none;border-radius:6px;font-size:11px;font-weight:600;cursor:pointer">Leer dit</button>' +
      '<button onclick="syncIrisKnowledge(this)" style="padding:5px 12px;background:#f1f5f9;color:#475569;border:1px solid #e2e8f0;border-radius:6px;font-size:11px;cursor:pointer">Ververs uit vault</button>' +
      '</div></div>';
    if (items.length) {
      html += '<div style="font-size:11px;color:#334155;font-weight:600;margin-bottom:4px">' + items.length + ' kennisitem(s) actief</div>';
      html += items.map(function(it){
        var tags = (it.tags||[]).map(function(t){return '<span style="background:#ede9fe;color:#6d28d9;padding:1px 5px;border-radius:8px;font-size:9px;margin-left:4px">' + escHtml(t) + '</span>';}).join('');
        var scope = (it.scope && it.scope !== 'all') ? ' <span style="color:#94a3b8">[' + escHtml(it.scope) + ']</span>' : '';
        var princ = (it.principles||[]).slice(0,4).map(function(p){return '<li style="color:#475569">' + escHtml(p) + '</li>';}).join('');
        return '<div style="border:1px solid #f1f5f9;border-radius:6px;padding:6px 8px;margin-bottom:5px">' +
          '<div style="display:flex;justify-content:space-between;align-items:center"><strong style="font-size:11px;color:#334155">' + escHtml(it.title) + scope + tags + '</strong>' +
          '<button onclick="deleteIrisKnowledge(\'' + it.id + '\')" title="Verwijderen" style="background:none;border:none;color:#cbd5e1;cursor:pointer;font-size:13px">×</button></div>' +
          (it.summary ? '<div style="font-size:10px;color:#64748b;margin:2px 0">' + escHtml(it.summary) + '</div>' : '') +
          (princ ? '<ul style="margin:2px 0;padding-left:16px;font-size:10px;line-height:1.5">' + princ + '</ul>' : '') +
          '</div>';
      }).join('');
    } else {
      html += '<div style="font-size:11px;color:#94a3b8">Nog geen kennis. Plak je GEO-onderzoek hierboven of zet een .md in de vault-map.</div>';
    }
    el.innerHTML = html;
  }).catch(function(e){ el.innerHTML = '<div style="color:#ef4444">Kennisbank laden mislukt: ' + escHtml(e.message) + '</div>'; });
}

function addIrisKnowledge() {
  var title = (document.getElementById('iris-know-title')||{}).value || '';
  var text = (document.getElementById('iris-know-text')||{}).value || '';
  if (text.trim().length < 20) { alert('Plak wat meer tekst zodat Iris er iets van kan leren.'); return; }
  var btn = document.getElementById('iris-know-add');
  if (btn) { btn.disabled = true; btn.textContent = 'Iris leest...'; }
  fetch('/api/iris/knowledge', { method: 'POST', headers: {'Content-Type':'application/json'},
    body: JSON.stringify({ title: title, text: text }) })
    .then(function(r){ if(!r.ok) throw new Error('HTTP ' + r.status); return r.json(); })
    .then(function(){ loadIrisKnowledge(); })
    .catch(function(e){ if(btn){btn.disabled=false;btn.textContent='Leer dit';} console.error('[Iris kennis]', e); });
}

function syncIrisKnowledge(btn) {
  if (btn) { btn.disabled = true; btn.textContent = 'Bezig...'; }
  fetch('/api/iris/knowledge/sync', { method: 'POST' })
    .then(function(r){ return r.json(); })
    .then(function(res){ loadIrisKnowledge(); })
    .catch(function(e){ if(btn){btn.disabled=false;btn.textContent='Ververs uit vault';} console.error('[Iris sync]', e); });
}

function deleteIrisKnowledge(id) {
  if (!confirm('Dit kennisitem verwijderen?')) return;
  fetch('/api/iris/knowledge/' + id, { method: 'DELETE' })
    .then(function(){ loadIrisKnowledge(); })
    .catch(function(e){ console.error('[Iris kennis del]', e); });
}

function runIrisNow() {
  var btn = document.getElementById('iris-run-btn');
  if (btn) { btn.disabled = true; btn.textContent = 'Iris analyseert...'; }
  fetch('/api/iris/run-now', { method: 'POST' }).then(function(r){
    if (!r.ok) throw new Error('HTTP ' + r.status);
    return r.json();
  }).then(function(){
    if (btn) { btn.disabled = false; btn.textContent = 'Analyseer nu'; }
    loadIrisBriefing();
  }).catch(function(e){
    if (btn) { btn.disabled = false; btn.textContent = 'Mislukt — opnieuw'; }
    console.error('[Iris]', e);
  });
}

function acBulkDrafts(btn, mode) {
  var drafts = _acLastItems.filter(function(i){ return i.kind === 'goal_draft'; });
  if (!drafts.length) return;
  var msg = mode === 'start'
    ? 'Alle ' + drafts.length + ' wachtende doelen bevestigen en starten? (Publiceren blijft achter de Wachtrij-gate.)'
    : 'Alle ' + drafts.length + ' draft-doelen definitief verwijderen?';
  if (!confirm(msg)) return;
  if (btn) { btn.disabled = true; btn.textContent = 'Bezig... 0/' + drafts.length; }
  var done = 0;
  var next = function(i) {
    if (i >= drafts.length) { loadActionCenter(); loadActivityLogs(); return; }
    var id = drafts[i].id;
    var p;
    if (mode === 'start') {
      p = fetch('/api/goals/confirm', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({goal_id:id}) })
        .then(function(){ return fetch('/api/goals/start', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({goal_id:id}) }); });
    } else {
      p = fetch('/api/goals/' + encodeURIComponent(id), { method:'DELETE' });
    }
    p.catch(function(e){ console.error('[Actiecentrum bulk]', id, e); }).finally(function(){
      done++;
      if (btn) btn.textContent = 'Bezig... ' + done + '/' + drafts.length;
      next(i + 1);
    });
  };
  next(0);
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
  } else if (type === 'content_regenerate') {
    if (btn) btn.textContent = 'Agent herschrijft... (kan even duren)';
    post('/api/content-queue/' + encodeURIComponent(action.id) + '/regenerate').then(done).catch(fail);
  } else if (type === 'outreach_send') {
    if (!confirm('Deze outreach-mail wordt ECHT verstuurd naar de lead. Doorgaan?')) { if (btn) { btn.disabled = false; btn.textContent = action.label; } return; }
    post('/api/leads/' + encodeURIComponent(action.id) + '/outreach-approve').then(done).catch(fail);
  } else if (type === 'outreach_dismiss') {
    post('/api/leads/' + encodeURIComponent(action.id) + '/outreach-dismiss').then(done).catch(fail);
  } else if (type === 'task_approve') {
    fetch('/api/tasks/' + encodeURIComponent(action.id) + '/status', { method:'PATCH', headers:{'Content-Type':'application/json'}, body: JSON.stringify({status:'done'}) })
      .then(function(r){ if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); }).then(done).catch(fail);
  } else if (type === 'dismiss') {
    post('/api/action-center/dismiss', { kind: action.dismiss_kind, ref_id: String(action.id) }).then(done).catch(fail);
  } else if (type === 'mail_send') {
    if (!confirm('Deze mail wordt ECHT verstuurd naar de klant. Doorgaan?')) { if (btn) { btn.disabled = false; btn.textContent = action.label; } return; }
    post('/api/mail/reply/' + encodeURIComponent(action.id) + '/send').then(done).catch(fail);
  } else if (type === 'mail_reject') {
    if (!confirm('Concept afwijzen? Wordt niet verstuurd.')) { if (btn) { btn.disabled = false; btn.textContent = action.label; } return; }
    post('/api/mail/reply/' + encodeURIComponent(action.id) + '/reject').then(done).catch(fail);
  } else if (type === 'mail_edit') {
    acMailEdit(btn, action); return;
  } else {
    fail(new Error('Onbekende actie: ' + type));
  }
}

// ── Inline editor voor het Actiecentrum (mail_reply → Bewerk) ──────
// Haal het volledige concept op en vervang de actieknoppen door een
// tekstvak + opslaan/annuleren. Zelfde backend-gate als de helpdesk-tab.
async function acMailEdit(btn, action) {
  var actionsDiv = btn ? btn.parentNode : null;
  var card = actionsDiv ? actionsDiv.closest('[id^="ac-item-"]') : null;
  if (!card || !actionsDiv) { alert('Editor kon niet worden geopend — ververs de pagina.'); return; }
  var inner = actionsDiv.parentNode;
  try {
    var replies = (await (await fetch('/api/mail/pending')).json()).replies || [];
    var r = replies.find(function(x){ return String(x.id) === String(action.id); });
    if (!r) { alert('Concept niet meer gevonden (al verstuurd of afgewezen?).'); return; }
    var ta = document.createElement('textarea');
    ta.value = r.draft_body || '';
    ta.style.cssText = 'width:100%;min-height:120px;font-size:12px;line-height:1.5;padding:8px;border:1px solid #e2e8f0;border-radius:6px;resize:vertical;font-family:inherit;background:#fffbeb;margin-top:8px';
    var saveBtn = document.createElement('button');
    saveBtn.textContent = 'Opslaan';
    saveBtn.style.cssText = 'padding:4px 12px;background:#4f46e5;color:#fff;border:none;border-radius:6px;font-size:11px;font-weight:600;cursor:pointer;margin:8px 6px 0 0';
    var cancelBtn = document.createElement('button');
    cancelBtn.textContent = 'Annuleren';
    cancelBtn.style.cssText = 'padding:4px 12px;background:#f8fafc;color:#475569;border:1px solid #e2e8f0;border-radius:6px;font-size:11px;cursor:pointer;margin-top:8px';
    actionsDiv.innerHTML = '';
    inner.appendChild(ta);
    actionsDiv.appendChild(saveBtn);
    actionsDiv.appendChild(cancelBtn);
    cancelBtn.onclick = function(){ loadActionCenter(); };
    saveBtn.onclick = async function(){
      saveBtn.disabled = true; saveBtn.textContent = 'Opslaan...';
      try {
        var resp = await fetch('/api/mail/reply/' + encodeURIComponent(action.id) + '/edit', {
          method:'POST', headers:{'Content-Type':'application/json'},
          body: JSON.stringify({ text: ta.value })
        });
        var d = await resp.json();
        if (d.ok) { loadActionCenter(); }
        else { alert('❌ ' + (d.error || 'onbekend')); saveBtn.disabled = false; saveBtn.textContent = 'Opslaan'; }
      } catch(e) { alert('❌ ' + e.message); saveBtn.disabled = false; saveBtn.textContent = 'Opslaan'; }
    };
  } catch(e) { alert('❌ ' + e.message); }
}

// ── Systeemgezondheid (herbruikbaar: Control Room + per-project Dashboard) ──
function loadSystemHealth(elId, btnId) {
  elId = elId || 'system-health-panel';
  btnId = btnId || 'autoheal-btn';
  var el = document.getElementById(elId);
  if (!el) return;
  // Op het project-dashboard filteren we op dat project, zodat een fout in een
  // ander project (bv. Bijeen) niet op het Bewaardvoorjou-scherm verschijnt.
  var projectParam = (elId === 'system-health-panel-proj' && currentProject)
    ? '?project=' + encodeURIComponent(currentProject) : '';
  fetch('/api/strategist/health' + projectParam).then(function(r){return r.json();}).then(function(h){
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
          var startedBadge = created[g].auto_started
            ? ' <span style="color:#fff;background:#16a34a;padding:1px 6px;border-radius:4px;font-size:10px">▶ direct gestart</span>'
            : ' <span style="color:#92400e;background:#fef3c7;padding:1px 6px;border-radius:4px;font-size:10px">wacht op jouw akkoord — zie inbox</span>';
          html += '<p style="font-size:11px;color:#166534;margin-top:4px">- ' + escHtml(created[g].title) + ' <span style="color:#15803d">(' + created[g].project + ')</span>' + startedBadge + '</p>';
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

