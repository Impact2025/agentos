// ── Agent OS — project-shell: sidebar, header, tab-loader, Dashboard-tab, pipeline-helpers
// Onderdeel van de SPA: klassieke scripts, gedeelde globale scope.
// Laadvolgorde staat in index.html — core.js eerst.

function renderSidebar() {
  return '<div class="sidebar"><div class="sidebar-logo"><img src="logo.png" alt="AO" onerror="this.style.display=\'none\'"><span>Agent OS</span></div><nav class="sidebar-nav">' +
    (currentProject ? TABS.map(function(t) {
      var badge = '';
      if (t === 'Helpdesk') badge = ' <span id="helpdesk-badge" class="nav-badge" style="display:none"></span>';
      return '<button class="' + (t===currentTab?' active':'') + '" onclick="switchView(\''+t+'\')"><span class="icon">' + (TAB_ICONS[t]||'') + '</span>' + t + badge + '</button>';
    }).join('') : '') +
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
  startHelpdeskBadgePoll();
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
    else if (currentTab === 'Helpdesk') await renderHelpdeskTab(el);
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

// ── Helpdesk-badge: toont aantal open concept-antwoorden in de sidebar ──
let _helpdeskBadgeTimer = null;
function startHelpdeskBadgePoll() {
  stopHelpdeskBadgePoll();
  pollHelpdeskBadge();
  _helpdeskBadgeTimer = setInterval(pollHelpdeskBadge, 20000);
}
function stopHelpdeskBadgePoll() { if (_helpdeskBadgeTimer) { clearInterval(_helpdeskBadgeTimer); _helpdeskBadgeTimer = null; } }
function pollHelpdeskBadge() {
  if (!currentProject) return;
  // Badge telt alleen de concepten van dít project — elk project zijn eigen helpdesk.
  fetch('/api/mail/pending?project=' + encodeURIComponent(currentProject)).then(function(r){return r.json();}).then(function(rows){
    var el = document.getElementById('helpdesk-badge');
    if (!el) return;
    var n = (rows && rows.replies && rows.replies.length) ? rows.replies.length : 0;
    if (n > 0) { el.style.display = 'inline-block'; el.textContent = n; }
    else { el.style.display = 'none'; }
  }).catch(function(){});
}

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

// ── Chart helpers (Chart.js v4, globaal geladen via CDN in index.html) ──
// Per-canvas instances zodat her-renders (tab-switch, poll) geen
// "Canvas is already in use" fout geven.
var _chartInstances = {};
function _destroyChart(id) {
  if (_chartInstances[id]) {
    try { _chartInstances[id].destroy(); } catch (e) {}
    delete _chartInstances[id];
  }
}
function _fmtChartDate(d) {
  // 'YYYY-MM-DD' -> 'DD-MM'
  if (!d) return '';
  var p = String(d).split('-');
  return p.length === 3 ? (p[2] + '-' + p[1]) : d;
}
// Lijn grafiek voor één meetreeks (klikken / impressies) uit trend.daily.
function renderSeriesChart(canvasId, daily, key, label, color) {
  if (typeof Chart === 'undefined') return;
  var cv = document.getElementById(canvasId);
  if (!cv || !daily || !daily.length) return;
  _destroyChart(canvasId);
  var labels = daily.map(function (d) { return _fmtChartDate(d.date); });
  var data = daily.map(function (d) { return d[key] != null ? d[key] : 0; });
  _chartInstances[canvasId] = new Chart(cv.getContext('2d'), {
    type: 'line',
    data: {
      labels: labels,
      datasets: [{
        label: label,
        data: data,
        borderColor: color,
        backgroundColor: color + '22',
        fill: true,
        tension: 0.3,
        pointRadius: 0,
        pointHoverRadius: 4,
        borderWidth: 2,
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: { legend: { display: false } },
      scales: {
        x: { ticks: { maxTicksLimit: 8, autoSkip: true, font: { size: 10 } }, grid: { display: false } },
        y: { beginAtZero: true, ticks: { font: { size: 10 } }, grid: { color: '#f1f5f9' } }
      }
    }
  });
}
// Positie-grafiek: deze vs vorige periode (lager = beter, dus geen beginAtZero).
function renderPositionChart(canvasId, cur, prev) {
  if (typeof Chart === 'undefined') return;
  var cv = document.getElementById(canvasId);
  if (!cv || !cur || !cur.length) return;
  _destroyChart(canvasId);
  var labels = cur.map(function (d) { return _fmtChartDate(d.date); });
  var datasets = [{
    label: 'Deze periode',
    data: cur.map(function (d) { return d.position != null ? d.position : null; }),
    borderColor: '#0ea5e9',
    backgroundColor: 'rgba(14,165,233,0.12)',
    fill: true,
    tension: 0.3,
    pointRadius: 0,
    pointHoverRadius: 4,
    borderWidth: 2,
  }];
  if (prev && prev.length) {
    var prevData = prev.slice(0, cur.length).map(function (d) { return d.position != null ? d.position : null; });
    datasets.push({
      label: 'Vorige periode',
      data: prevData,
      borderColor: '#94a3b8',
      borderDash: [5, 4],
      fill: false,
      tension: 0.3,
      pointRadius: 0,
      pointHoverRadius: 4,
      borderWidth: 1.5,
    });
  }
  _chartInstances[canvasId] = new Chart(cv.getContext('2d'), {
    type: 'line',
    data: { labels: labels, datasets: datasets },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: { legend: { display: datasets.length > 1, labels: { boxWidth: 12, font: { size: 10 } } } },
      scales: {
        x: { ticks: { maxTicksLimit: 8, autoSkip: true, font: { size: 10 } }, grid: { display: false } },
        y: { ticks: { font: { size: 10 } }, grid: { color: '#f1f5f9' } }
      }
    }
  });
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

