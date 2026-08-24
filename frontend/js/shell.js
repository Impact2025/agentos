// ── Impact OS — project-shell: sidebar, header, tab-loader, Dashboard-tab, pipeline-helpers
// Onderdeel van de SPA: klassieke scripts, gedeelde globale scope.
// Laadvolgorde staat in index.html — core.js eerst.

// HTML-attribuut-escaper: voorkomt dat quotes/apostrofen in actie-strings de
// inline handlers breken (o.a. de "Oplossen"-knoppen op het dashboard).
function escAttr(s) { if (!s) return ''; return String(s).replace(/&/g,'&amp;').replace(/"/g,'&quot;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }

// Gedelegeerde listener voor alle advies-actieknoppen. We lezen de actie uit een
// data-attribuut i.p.v. een inline onclick met string-interpolatie — daardoor
// blijven apostrofen (bv. "pagina's") veilig en werkt de klik altijd.
if (!window.__adviceActionBound) {
  window.__adviceActionBound = true;
  document.addEventListener('click', function(e) {
    var holder = e.target.closest('[data-advice-action]');
    if (!holder) return;
    var action = holder.getAttribute('data-advice-action');
    var btn = holder.classList.contains('dash-alert') ? (holder.querySelector('button') || holder) : holder;
    handleAdviceAction(btn, action);
  });
}

function renderSidebar() {
  var visible = currentProject ? visibleTabs() : [];
  var visibleSet = {};
  visible.forEach(function(t) { visibleSet[t] = true; });
  function navButton(t) {
    var badge = '';
    if (t === 'Helpdesk') badge = ' <span id="helpdesk-badge" class="nav-badge" style="display:none"></span>';
    return '<button class="' + (t===currentTab?' active':'') + '" onclick="switchView(\''+t+'\')"><span class="icon">' + (TAB_ICONS[t]||'') + '</span>' + t + badge + '</button>';
  }
  var nav = NAV_GROUPS.map(function(g) {
    if (g.subs) {
      var subHtml = g.subs.map(function(s) {
        var subTabs = s.tabs.filter(function(t) { return visibleSet[t]; });
        if (!subTabs.length) return '';
        return '<div class="nav-subgroup-label">' + escHtml(s.label) + '</div>' + subTabs.map(navButton).join('');
      }).join('');
      if (!subHtml) return '';
      return '<div class="nav-group"><div class="nav-group-label">' + escHtml(g.label) + '</div>' + subHtml + '</div>';
    }
    var tabs = g.tabs.filter(function(t) { return visibleSet[t]; });
    if (!tabs.length) return '';
    return '<div class="nav-group"><div class="nav-group-label">' + escHtml(g.label) + '</div>' + tabs.map(navButton).join('') + '</div>';
  }).join('');
  return '<div class="sidebar"><div class="sidebar-logo">' + apertureMark(24, 'sidebar-logo-mark') + '<span>' + escHtml(window.__instanceName || 'Impact OS') + '</span></div><nav class="sidebar-nav">' + nav +
    '<div class="sidebar-footer">' + (currentProject ? '<button onclick="switchView(\'chat\')"><span class="icon">✎</span>Chat</button>' : '') +
    (currentProject ? '<button onclick="switchView(\'voice\')"><span class="icon">🎙</span>Voice</button>' : '') +
    '<button onclick="goHome()"><span class="icon">←</span>Projecten</button>' +
    '<button onclick="logoutAgent()"><span class="icon">⏻</span>Uitloggen</button></div></div>';
}
function renderHeader() {
  if (!currentProject) return '';
  return '<div class="project-header"><div><h1>' + escHtml(currentProject) + ' <span id="agent-status-indicator" style="margin-left:6px;vertical-align:middle"></span> <span id="resolve-failed-btn-container" style="vertical-align:middle"></span></h1><p class="meta">' + escHtml(currentTab) + ' &middot; ' + escHtml(DESCS[currentProject]||'') + '</p></div>' +
    '<div class="actions">' + (currentTab !== 'Dashboard' ? '<button onclick="switchView(\'Dashboard\')">Dashboard</button>' : '') +
    '<button onclick="switchView(\'chat\')">Chat</button><button onclick="switchView(\'voice\')">🎙 Voice</button><button onclick="togglePrint()" class="no-print">Export</button></div></div>';
}

// ── Mobiele kopbalk ────────────────────────────────────────────────────────
// Op een telefoon is er geen ruimte voor een vaste zijbalk van 180px náást de
// inhoud; die wordt een lade. De kopbalk is dan de enige plek waar je nog kunt
// zien wáár je bent — vandaar project + tab, en niet alleen een hamburger.
// Bestaat alleen visueel onder 820px (zie app.css); op desktop is hij verborgen.
function renderMobileBar() {
  return '<div class="nav-scrim" onclick="closeNav()"></div>' +
    '<header class="mobile-topbar">' +
    '<button class="mt-btn" type="button" onclick="toggleNav()" aria-label="Menu" aria-expanded="false" id="nav-toggle"><span class="burger"></span></button>' +
    '<div class="mt-title"><strong>' + escHtml(currentProject || window.__instanceName || 'Impact OS') + '</strong>' +
    (currentProject ? '<span class="mt-sub">' + escHtml(currentTab) + '</span>' : '') + '</div>' +
    '<span id="agent-status-indicator-mobile"></span>' +
    '<button class="mt-btn" type="button" onclick="goHome()" aria-label="Naar projecten">&#8962;</button>' +
    '</header>';
}
function toggleNav() {
  var open = document.body.classList.toggle('nav-open');
  var b = document.getElementById('nav-toggle');
  if (b) b.setAttribute('aria-expanded', open ? 'true' : 'false');
}
function closeNav() {
  document.body.classList.remove('nav-open');
  var b = document.getElementById('nav-toggle');
  if (b) b.setAttribute('aria-expanded', 'false');
}
// Escape sluit de lade — een open overlay zonder ontsnapping is een val.
document.addEventListener('keydown', function (e) { if (e.key === 'Escape') closeNav(); });

function renderProjectView(main) {
  // De lade hoort dicht te zijn na elke navigatie: route() bouwt de DOM opnieuw
  // op, maar de klasse staat op <body> en zou anders blijven hangen.
  closeNav();
  main.innerHTML = renderSidebar() + renderMobileBar() + '<div class="main-content">' + renderHeader() + '<div class="tab-content" id="tab-content"><div class="loading"><div class="spinner"></div><p>' + currentTab + ' laden...</p></div></div></div>';
  startAgentStatusPoll();
  startHelpdeskBadgePoll();
  loadCurrentTab();
}
async function loadCurrentTab() {
  var el = document.getElementById('tab-content'); if (!el) return;
  try {
    if (currentTab === 'Dashboard') await renderDashboardTab(el);
    else if (currentTab === 'Agenten') await renderAgentControlTab(el);
    else if (currentTab === 'Postvak') await renderPostvakTab(el);
    else if (currentTab === 'Kansen') await renderKansenTab(el);
    else if (currentTab === 'Optimalisatie') await renderOptimalisatieTab(el);
    else if (currentTab === 'GEO') await renderGeoTab(el);
    else if (currentTab === 'Wachtrij') await renderWachtrijTab(el);
    else if (currentTab === 'Prestaties') await renderPrestatiesTab(el);
    else if (currentTab === 'Radar') await renderRadarTab(el);
    else if (currentTab === 'Doelen') await renderDoelenTab(el);
    else if (currentTab === 'Geheugen') await renderGeheugenTab(el);
    else if (currentTab === 'Leads') await renderLeadsTab(el);
    else if (currentTab === 'Links') await renderLinksTab(el);
    else if (currentTab === 'Opdrachten') await renderOpdrachtenTab(el);
    else if (currentTab === 'Technisch') await renderTechTab(el);
    else if (currentTab === 'Activiteit') await renderActiviteitTab(el);
    else if (currentTab === 'Social Creatie') await renderSocialCreatieTab(el);
    else if (currentTab === 'Omni') await renderOmniTab(el);
    else if (currentTab === 'Gauntlet') await renderGauntletTab(el);
    else if (currentTab === 'Facebook') await renderFacebookTab(el);
    else if (currentTab === 'Helpdesk') await renderHelpdeskTab(el);
    else if (currentTab === 'WhatsApp') await renderWhatsAppTab(el);
    else if (currentTab === 'Agenda') await renderAgendaTab(el);
    else if (currentTab === 'Health') await renderHealthTab(el);
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
  var bg, border, pillClass, label, actionAttr = '';
  if (b.type === 'running') { bg = 'var(--ok-bg)'; border = 'var(--green)'; pillClass = 'pill-ok'; label = 'Bezig'; }
  else if (b.type === 'failed') { bg = 'var(--danger-bg)'; border = 'var(--red)'; pillClass = 'pill-danger'; label = 'Mislukt'; actionAttr = ' onclick="retryFailedGoal(\'' + b.action.replace('retry_goal:','') + '\')" style="cursor:pointer"'; }
  else { bg = 'var(--neutral-bg)'; border = 'var(--text-dim)'; pillClass = 'pill-neutral'; label = 'Status'; }
  return '<div class="dash-status-banner" style="background:' + bg + ';border-left:4px solid ' + border + ';border-radius:var(--radius-md);padding:10px 14px;margin-bottom:12px;display:flex;align-items:center;gap:10px"' + actionAttr + '>' +
    '<span class="pill ' + pillClass + '" style="flex-shrink:0">' + label + '</span>' +
    '<span style="font-size:13px;font-weight:600;color:var(--text);flex:1">' + escHtml(b.text) + '</span>' +
    (advice.running_goal ? '<div style="display:flex;align-items:center;gap:6px"><div style="width:80px;height:6px;background:var(--card-border);border-radius:3px;overflow:hidden"><div style="height:100%;width:' + advice.running_goal.percent + '%;background:var(--green);border-radius:3px;transition:width .5s"></div></div><span style="font-size:11px;color:var(--text-dim)">' + advice.running_goal.progress + '</span></div>' : '') +
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

// ── Recente reeks onder een KPI-tegel ──────────────────────────────────────
// De grote delta vergelijkt 28 dagen met de 28 daarvoor. Deze regel toont de
// laatste 7 GSC-dagen tegen de 7 daarvóór — de meting die zegt wat er nú
// gebeurt. Staat er geen historie, dan staat er niets: "geen data" mag nooit
// als "geen verandering" op het scherm komen.
function recentFoot(advice, key) {
  var k = advice && advice.dash_kpi;
  if (!k) return '';
  if (key === 'position') {
    if (k.recent_position == null) return '';
    var d = k.delta_position_7d;
    if (d == null) return 'laatste 7 dagen: ' + k.recent_position;
    // Positie: lager is beter, dus een positieve delta is verslechtering.
    return 'laatste 7 dagen: ' + k.recent_position + ' (' + (d > 0 ? '+' : '') + d + ')';
  }
  if (k.recent_clicks == null) return '';
  var dc = k.delta_clicks_7d;
  return 'laatste 7 dagen: ' + k.recent_clicks + (dc == null ? '' : ' (' + (dc > 0 ? '+' : '') + dc + ')');
}
function recentTone(advice, key) {
  var k = advice && advice.dash_kpi;
  if (!k) return '';
  var d = key === 'position' ? k.delta_position_7d : k.delta_clicks_7d;
  if (d == null || d === 0) return '';
  var slechter = key === 'position' ? d > 0 : d < 0;
  return slechter ? 'bad' : 'good';
}

async function renderDashboardTab(el) {
  if (currentProject === 'Finance Expert') { renderFinanceExpert(el); return; }
  if (currentProject === 'WeAreImpact') { renderWeAreImpactDashboard(el); return; }
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

  // ── 1. WACHT OP JOU — de focus-sectie van dit project ──────────
  // Bovenaan, vóór de GSC-grafieken: alles wat hier op een beslissing van
  // Vincent wacht (content ter review, fouten, mail, social, …), gescopeerd
  // op dít project. Hertgebruikt dezelfde kaart-stijl als het Actiecentrum.
  html += '<div id="proj-ac"></div>';

  // ── Bestellingen + inkoop-signalering (alleen Bewaard voor Jou) ──
  if (currentProject === 'BewaardVoorJou') {
    html += '<div id="proj-bvj-orders"></div>';
  }

  // ── 2. ALERTS (hele card klikbaar - tekst + knop) ──
  if (advice && advice.alerts && advice.alerts.length) {
    var _alertMeta = {
      danger: { bg: 'var(--danger-bg)', border: 'var(--danger-border)', pill: 'pill-danger', label: 'Let op' },
      warning: { bg: 'var(--warn-bg)', border: 'var(--warn-border)', pill: 'pill-warn', label: 'Waarschuwing' },
      opportunity: { bg: 'var(--ok-bg)', border: 'var(--ok-border)', pill: 'pill-ok', label: 'Kans' },
    };
    advice.alerts.forEach(function(a) {
      var m = _alertMeta[a.type] || { bg: 'var(--neutral-bg)', border: 'var(--card-border)', pill: 'pill-neutral', label: 'Info' };
      var hasAction = !!a.action;
      var dataAttr = hasAction ? ' data-advice-action="' + escAttr(a.action) + '"' : '';
      var onClickStyle = hasAction ? ' style="cursor:pointer"' : '';
      var actionBtn = hasAction ? '<button type="button" ' + dataAttr + ' class="btn btn-sm btn-primary" style="flex-shrink:0;white-space:nowrap">' + escHtml(a.action_label || 'Oplossen') + '</button>' : '';
      html += '<div class="dash-alert" ' + dataAttr + onClickStyle + ' style="display:flex;align-items:center;gap:8px;padding:8px 12px;margin-bottom:6px;background:' + m.bg + ';border:1px solid ' + m.border + ';border-radius:var(--radius-sm)">' +
        '<span class="pill ' + m.pill + '" style="flex-shrink:0">' + m.label + '</span>' +
        '<span style="font-size:12px;color:var(--text-dim);line-height:1.5;flex:1">' + escHtml(a.text) + '</span>' + actionBtn + '</div>';
    });
  }

  // ── 3. NEXT STEP  // ── 3. NEXT STEP + QUICK ACTIONS ──
  if (advice && advice.next_step) {
    html += '<div class="section-card" style="margin-bottom:16px;background:var(--info-bg);border:1px solid var(--info-border)">' +
      '<div style="display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:8px">' +
      '<div style="display:flex;align-items:center;gap:8px;flex:1;min-width:200px"><span style="font-size:13px;font-weight:600;color:var(--text);flex-shrink:0">Beste volgende stap:</span><span style="font-size:12px;color:var(--text-dim)">' + escHtml(advice.next_step) + '</span></div>' +
      '<div style="display:flex;gap:6px;flex-wrap:wrap;align-items:center">' +
      (advice.next_step_action ? '<button type="button" data-advice-action="' + escAttr(advice.next_step_action) + '" class="btn btn-sm btn-primary">Nu uitvoeren</button>' : '') +
      (advice.quick_actions && advice.quick_actions.length ? advice.quick_actions.map(function(qa) {
        return '<button type="button" data-advice-action="' + escAttr(qa.action) + '" class="btn btn-sm ' + (qa.primary ? 'btn-primary' : 'btn-ghost') + '">' + escHtml(qa.label) + '</button>';
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
    html += '<div class="empty-state"><p style="font-size:15px;font-weight:600;color:var(--neutral-fg);margin-bottom:4px">Nog geen data</p><p style="color:var(--text-muted)">' + escHtml(gsc.error||'Geen GSC-data') + '</p></div>';
  } else {
    var s = gsc.summary;
    html += '<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:12px"><h3 style="font-size:15px;font-weight:700">Prestatieoverzicht</h3>' +
      '<div class="actions no-print" style="display:flex;gap:6px"><button onclick="switchView(\'Prestaties\')" class="btn btn-sm btn-ghost">Trends & Analyse</button><button onclick="togglePrint()" class="btn btn-sm btn-ghost">Export PDF</button></div></div>' +
      '<div class="kpi-grid">' +
      kpiBox('Geïndexeerd', s.indexed_pages, s.indexed_pages_change,'') +
      kpiBox('Klikken', s.total_clicks, s.total_clicks_change, '',
             recentFoot(advice, 'clicks'), recentTone(advice, 'clicks')) +
      kpiBox('CTR', s.avg_ctr + '%', '', s.total_impressions + ' impressies') +
      // avg_position_change = vorige - huidige: positief = verbeterd (groen), negatief = verslechterd (rood)
      kpiBox('Positie', s.avg_position, (s.avg_position_change !== undefined ? s.avg_position_change.toFixed(1) : ''), '',
             recentFoot(advice, 'position'), recentTone(advice, 'position')) +
      '</div>' +
      '<div class="grid-2">' +
      '<div class="section-card"><h3>Top pagina\'s</h3>' + tbl(gsc.top_pages||[], ['pagina','page'], ['Klikken','clicks'], ['Impressies','impressions'], ['CTR','ctr'], ['Positie','position']) + '</div>' +
      '<div class="section-card"><h3>Top zoekwoorden</h3>' + tbl(gsc.top_queries||[], ['zoekwoord','query'], ['Klikken','clicks'], ['Impressies','impressions'], ['CTR','ctr'], ['Positie','position']) + '</div></div>';
    if (gsc.page_comparison && gsc.page_comparison.length) html += '<div class="section-card"><h3>Week-op-week</h3>' + tbl(gsc.page_comparison.slice(0,10), ['pagina','page'], ['Klikken (deze)','clicks_current'], ['Verschil','clicks_change'], ['Positie','position_current'], ['Pos. verschil','position_change']) + '</div>';
  }

  // ── 6. DOELEN op dashboard ──
  if (goals && goals.length) {
    html += '<div class="section-card" style="margin-bottom:16px">' +
      '<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:8px"><h3>Doelen (' + goals.length + ')</h3><button onclick="switchView(\'Doelen\')" class="btn btn-sm btn-ghost">Beheer</button></div>' +
      goals.slice(0,3).map(function(g) {
        var pillClass = {draft:'pill-neutral',ready:'pill-info',running:'pill-warn',paused:'pill-neutral',completed:'pill-ok',failed:'pill-danger'}[g.status] || 'pill-neutral';
        var total = g.task_count || 1;
        var done = g.completed_tasks || 0;
        return '<div style="display:flex;align-items:center;gap:8px;padding:6px 0;border-bottom:1px solid var(--card-border);font-size:12px">' +
          '<span style="flex:1;color:var(--text)">' + escHtml(g.title) + '</span>' +
          '<span class="pill ' + pillClass + '">' + escHtml(g.status) + '</span>' +
          (g.status==='running' ? '<span style="font-size:10px;color:var(--text-dim)">' + done + '/' + total + '</span>' : '') +
          '</div>';
      }).join('') +
      '</div>';
  }

  // ── 7. ACTIVITY ──
    html += '<div class="section-card"><div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:8px">' +
      '<h3 style="font-size:13px;font-weight:700">Activiteit</h3>' +
      '<button onclick="loadProjectActivityLogs()" class="btn btn-sm btn-ghost">Ververs</button></div>' +
      '<div id="project-activity-panel" style="background:#0f172a;border-radius:8px;padding:8px;font-family:monospace;font-size:11px;max-height:200px;overflow-y:auto">' +
      '<div style="color:#64748b;text-align:center;padding:12px">Laden...</div></div></div>';

    el.innerHTML = html;

  // ── PROJECT-ACTIECENTRUM ──
  // "Wacht op jou" voor dít project, bovenaan de Dashboard-tab. Eigen poll
  // (30s) die stopt zodra je het project of de tab verlaat.
  loadProjectActionCenter(currentProject);
  startProjectActionCenterRefresh(currentProject);
  if (currentProject === 'BewaardVoorJou') {
    var bvjEl = document.getElementById('proj-bvj-orders');
    if (bvjEl) renderBewaardVoorJouOrders(bvjEl);
  }

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

// ── Project-Actiecentrum ─────────────────────────────────────────────────
// Dezelfde "wat wacht er op mij"-kaarten als het algemene Actiecentrum, maar
// dan gescopeerd op één project (via GET /api/action-center?project=<naam>).
// Staat bovenaan de per-project Dashboard-tab zodat je per project focust.
var _projAcTimer = null;
function startProjectActionCenterRefresh(project) {
  if (_projAcTimer) clearInterval(_projAcTimer);
  _projAcTimer = setInterval(function () {
    var el = document.getElementById('proj-ac');
    if (!el || currentProject !== project || currentTab !== 'Dashboard') {
      clearInterval(_projAcTimer); _projAcTimer = null; return;
    }
    loadProjectActionCenter(project, true);
  }, 30000);
}

function loadProjectActionCenter(project, isRefresh) {
  var el = document.getElementById('proj-ac');
  if (!el || !project) return;
  if (!isRefresh) el.innerHTML = '<div class="section-card" style="margin-bottom:16px"><div style="color:#64748b;font-size:12px;padding:8px 0">Wacht-op-jou laden...</div></div>';
  fetch('/api/action-center?project=' + encodeURIComponent(project)).then(function (r) { return r.json(); }).then(function (data) {
    if (!el || currentProject !== project) return;
    var items = data.items || [];
    if (!items.length) {
      el.innerHTML = '<div class="section-card" style="margin-bottom:16px;background:var(--ok-bg);border-color:var(--ok-border)">' +
        '<span style="font-size:13px;color:var(--ok-fg);font-weight:600">Niets wacht op jou voor ' + escHtml(project) + '.</span> ' +
        '<span style="font-size:12px;color:var(--ok-fg)">Alles is aan het lopen.</span></div>';
      return;
    }
    var errorCount = items.filter(function (i) { return i.kind === 'error'; }).length;
    var bulkBar = '';
    if (errorCount >= 1) {
      bulkBar = '<div style="display:flex;gap:8px;align-items:center;margin-bottom:10px;padding:8px 12px;background:var(--info-bg);border:1px solid var(--info-border);border-radius:var(--radius-sm)">' +
        '<span style="font-size:11px;color:var(--info-fg);flex:1"><b>' + errorCount + ' foutkaart(en)</b> — laat Iris ze allemaal analyseren &amp; afhandelen:</span>' +
        '<button onclick="acTriageAll(this)" class="btn btn-primary btn-sm">Analyseer alle fouten</button></div>';
    }
    var html = '<div class="section-card" style="margin-bottom:16px">' +
      '<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:10px">' +
      '<h3 style="font-size:14px;font-weight:700;color:var(--text)">Wacht op jou — ' + escHtml(project) + ' (' + items.length + ')</h3>' +
      '<span style="font-size:11px;color:#94a3b8">' + (data.counts && data.counts.errors ? data.counts.errors + ' fout(en) · ' : '') + 'klik = klaar</span></div>' +
      bulkBar;
    items.forEach(function (it, idx) {
      var meta = (_acKindMeta && _acKindMeta[it.kind]) || { pill: 'pill-neutral', label: it.kind };
      if (it.kind === 'content_review') {
        var ct = (it.content_type || 'blog').toLowerCase();
        if (ct === 'linkedin_outreach') meta = { pill: 'pill-warn', label: 'LinkedIn · géén site-pagina' };
        else if (ct === 'hook' || ct === 'snippet' || ct === 'social_snippet') meta = { pill: 'pill-warn', label: 'SEO-hook · géén artikel' };
        else meta = { pill: 'pill-info', label: 'Artikel · wordt gepubliceerd' };
      }
      var when = it.created_at ? '<span style="color:#94a3b8;font-size:10px;flex-shrink:0">' + escHtml(_fmtNlDateTime(it.created_at)) + '</span>' : '';
      html += '<div id="proj-ac-item-' + idx + '" style="padding:10px 4px 10px 12px;border-bottom:1px solid #f1f5f9;border-left:3px solid ' + _pillBorderColor(meta.pill) + '">' +
        '<div style="flex:1;min-width:0">' +
        '<div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:2px">' +
        '<span class="pill ' + meta.pill + '">' + ((_acKindMeta && _acKindMeta[it.kind]) ? _acKindMeta[it.kind].icon + ' ' : '') + escHtml(meta.label) + '</span>' +
        (it.flag ? '<span style="font-size:10px;color:#065f46;background:#d1fae5;padding:1px 6px;border-radius:4px;font-weight:600">' + escHtml(it.flag) + '</span>' : '') + when + '</div>' +
        '<p style="font-size:13px;font-weight:600;color:var(--text);margin:2px 0">' + escHtml(it.title) + '</p>' +
        '<p style="font-size:11px;color:#64748b;margin-bottom:6px">' + escHtml(it.summary || '') + '</p>' +
        '<div style="display:flex;gap:6px;flex-wrap:wrap">' +
        it.actions.map(function (a) {
          var cls = a.danger ? 'btn-danger-outline' : (a.accent ? 'btn-primary' : (a.type === 'open_tab' || a.type === 'dismiss') ? 'btn-ghost' : 'btn-primary');
          return '<button onclick=\'acAction(this, ' + JSON.stringify(a).replace(/'/g, '&#39;') + ', ' + JSON.stringify(it.project || project) + ')\' class="btn btn-sm ' + cls + '">' + escHtml(a.label) + '</button>';
        }).join('') +
        (it.kind === 'mail_reply' && !it.sender_known ? '<button onclick="acMarkSenderKnown(this, ' + String(it.id) + ')" class="btn btn-sm btn-ghost">Markeer als bekend</button>' : '') +
        '</div></div></div>';
    });
    html += '</div>';
    el.innerHTML = html;
  }).catch(function (e) {
    if (el) el.innerHTML = '<div class="section-card" style="margin-bottom:16px;background:var(--danger-bg);border-color:var(--danger-border)"><span style="font-size:12px;color:var(--danger-fg)">Project-actiecentrum laden mislukt: ' + escHtml(e.message) + '</span></div>';
  });
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

// Kleur per projectcijfer (0-10) — dezelfde drempels als _irisGradeColor in
// home.js, maar als volle Chart.js-vlakkleur i.p.v. een gedempte pil-badge.
function _scoreBarColor(score) {
  if (score >= 8) return '#16a34a';
  if (score >= 6) return '#d97706';
  return '#ef4444';
}
// Horizontale balkgrafiek: één balk per project = Iris' rapportcijfer
// (0-10, laagste eerst — dat is precies "wie heeft aandacht nodig").
function renderProjectScoreBar(canvasId, projects) {
  if (typeof Chart === 'undefined') return;
  var cv = document.getElementById(canvasId);
  if (!cv || !projects || !projects.length) return;
  _destroyChart(canvasId);
  var labels = projects.map(function (p) { return p.project; });
  var data = projects.map(function (p) { return p.score || 0; });
  var colors = data.map(_scoreBarColor);
  _chartInstances[canvasId] = new Chart(cv.getContext('2d'), {
    type: 'bar',
    data: { labels: labels, datasets: [{ data: data, backgroundColor: colors, borderRadius: 4, maxBarThickness: 18 }] },
    options: {
      indexAxis: 'y',
      responsive: true,
      maintainAspectRatio: false,
      plugins: { legend: { display: false } },
      scales: {
        x: { min: 0, max: 10, ticks: { font: { size: 10 } }, grid: { color: '#f1f5f9' } },
        y: { ticks: { font: { size: 11 } }, grid: { display: false } }
      }
    }
  });
}
// Radar met het gemiddelde van Iris' vier pijlers (content/seo/uitvoering/
// hygiëne) over alle projecten — één dataset, geen vergelijking nodig.
function renderPillarRadar(canvasId, avgPillars) {
  if (typeof Chart === 'undefined') return;
  var cv = document.getElementById(canvasId);
  if (!cv || !avgPillars) return;
  _destroyChart(canvasId);
  _chartInstances[canvasId] = new Chart(cv.getContext('2d'), {
    type: 'radar',
    data: {
      labels: ['Content', 'SEO', 'Uitvoering', 'Hygiëne'],
      datasets: [{
        label: 'Gemiddeld',
        data: [avgPillars.content, avgPillars.seo, avgPillars.uitvoering, avgPillars.hygiene],
        borderColor: '#4f46e5',
        backgroundColor: 'rgba(79,70,229,0.15)',
        pointBackgroundColor: '#4f46e5',
        borderWidth: 2,
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: { legend: { display: false } },
      scales: { r: { min: 0, max: 10, ticks: { stepSize: 2.5, font: { size: 9 }, backdropColor: 'transparent' }, pointLabels: { font: { size: 11 } }, grid: { color: '#f1f5f9' } } }
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
  if (action === 'new_goal') { switchViewThen('Doelen', showNewGoalForm); return; }
  if (action.startsWith('retry_goal:')) { retryFailedGoal(action.split(':')[1]); return; }
  // Een alert die al twee keer op een vastloper strandde stuurt naar dát doel
  // in plaats van er een derde te starten (zie `_knop_of_blokkade`).
  if (action.startsWith('open_goal:')) {
    var goalId = action.slice('open_goal:'.length);
    switchViewThen('Doelen', function() { loadGoalDetail(goalId); });
    return;
  }
  if (action.startsWith('open_tab:')) { switchView(action.split(':')[1]); return; }
  if (action.startsWith('write_article:')) {
    var keyword = action.split(':').slice(1).join(':');
    writeArticleForKeyword(keyword, btn);
    return;
  }
  if (action.startsWith('optimize_page:')) {
    optimizePageForKeyword(action.split(':').slice(1).join(':'), btn);
    return;
  }
  console.warn('Onbekende action:', action);
}

// ── "Optimaliseer pagina": de knop die hoort bij de diagnose "de pagina rankt
// al, maar niemand klikt". Levert 3 concrete title+meta-varianten op voor de
// pagina die Google voor dit zoekwoord toont — geen tweede artikel over
// hetzelfde onderwerp, want dat is kannibalisatie.
async function optimizePageForKeyword(keyword, btn) {
  var orig = btn ? btn.textContent : null;
  if (btn) { btn.disabled = true; btn.textContent = 'Analyseren...'; }
  try {
    var resp = await fetch('/api/seo-optimizer/' + encodeURIComponent(currentProject) +
      '/optimize-query?query=' + encodeURIComponent(keyword), { method: 'POST' });
    var data = await resp.json();
    if (!resp.ok) throw new Error(data.detail || 'Optimalisatie mislukt');
    if (data.outcome === 'geen-pagina') {
      // Geen ranking-pagina gevonden: dan is schrijven wél het juiste werk.
      // Niet stil omvallen, maar de gebruiker naar de juiste actie sturen.
      if (confirm(data.detail + '\n\nWil je er een artikel voor schrijven?')) {
        writeArticleForKeyword(keyword, btn);
        return;
      }
    } else if (data.outcome === 'geen-varianten') {
      // Gedeeltelijk resultaat: de bevinding staat er wél. Dat verzwijgen zou
      // de gebruiker hetzelfde werk nog eens laten doen.
      alert(data.detail);
      switchView('Optimalisatie');
      return;
    } else if (data.outcome === 'varianten') {
      alert('3 varianten klaar voor ' + data.page + '\n\n' +
        (data.variants || []).map(function(v, i) {
          return (i + 1) + '. ' + v.title + '\n   ' + v.meta;
        }).join('\n\n') +
        '\n\nZe staan in de Optimalisatie-tab; daar kies je er één.');
      switchView('Optimalisatie');
      return;
    } else {
      alert(data.detail || 'Er viel niets te optimaliseren.');
    }
  } catch (e) {
    alert('Optimalisatie mislukt: ' + e.message);
  } finally {
    if (btn) { btn.disabled = false; if (orig) btn.textContent = orig; }
  }
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

    // Poll-lus met fouttolerantie: de job draait server-side als achtergrondtaak,
    // dus een enkele mislukte poll (netwerk-blip, trage lokale-LLM die de server
    // even bezet houdt, korte herstart) mag de job niet als 'mislukt' afserveren.
    // Pas na meerdere opeenvolgende mislukkingen geven we op.
    var pollFails = 0;
    while (true) {
      await new Promise(function(r){ setTimeout(r, 1500); });
      var st;
      try {
        var resp = await fetch('/api/projects/' + encodeURIComponent(currentProject) + '/write-and-publish/' + start.job_id);
        if (!resp.ok) throw new Error('HTTP ' + resp.status);
        st = await resp.json();
        pollFails = 0;
      } catch (e) {
        pollFails++;
        if (pollFails >= 20) return {success: false, detail: 'Verbinding met de server verloren tijdens het schrijven (job draait mogelijk nog door). Ververs de pagina om de status te zien.'};
        if (btn) btn.textContent = 'Verbinding kwijt, opnieuw proberen (' + pollFails + ')...';
        continue;
      }
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
    if (btn) btn.textContent = 'Gestart';
    alert('✅ Agent gestart: "' + title + '"');
    loadCurrentTab();
  } catch(e) { alert('❌ Fout: ' + e.message); }
  finally { if (btn) { btn.disabled = false; btn.style.opacity = ''; btn.textContent = origLabel; btn.style.minWidth = ''; } }
}

function startGoalFromAction(opts, btn) {
  if (!confirm('Agent laten starten met: "' + opts.title + '"?')) return;
  createAndStartGoal(opts.title, opts.objective || opts.title, opts.project, btn);
}

async function solveAlert(objective, btn) {
  // 60 = `_ACTIEPUNT_TITELCAP` in backend/domains/projects/router.py. De backend
  // dedupliceert op precies deze titel; wijken de twee af, dan matcht de dedupe
  // nooit en biedt het dashboard hetzelfde actiepunt eeuwig opnieuw aan.
  var title = 'Actiepunt: ' + objective.slice(0, 60);
  if (!confirm('Actiepunt als gedaan markeren?\n\n"' + objective.slice(0, 160) + (objective.length > 160 ? '...' : '') + '"\n\nHet vinkje wordt direct in je Obsidian-vault gezet. De agent kan daarna optioneel verder werken.')) return;
  // 1) Bron van waarheid direct afvinken in de vault — item verdwijnt meteen uit de todo.
  try {
    var doneResp = await fetch('/api/projects/' + encodeURIComponent(currentProject) + '/action/done', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({text: objective})
    });
    if (doneResp.ok) {
      if (btn) { btn.textContent = 'Gedaan'; btn.disabled = true; }
      loadCurrentTab();
    }
  } catch(e) { /* niet blokkerend — agent-start hieronder probeert het alsnog */ }
  // 2) Agent op de achtergrond laten werken (niet-blokkerend, geen confirm-loop).
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
  if (!items||!items.length) return '<p style="color:var(--text-muted);font-size:12px;text-align:center;padding:16px">Geen data</p>';
  // De tabel schuift binnen zijn eigen kader; zonder die wikkel duwt hij op een
  // telefoon de hele pagina breder dan het scherm en schuift álles mee.
  return '<div class="table-scroll"><table class="data-table"><thead><tr><th>' + labelA[0] + '</th>' + cols.map(function(c){return '<th class="num">'+c[0]+'</th>';}).join('') + '</tr></thead><tbody>' +
    items.map(function(it){
      var cell = '<td class="url-cell">' + escHtml(it[labelA[1]]||it.page||it.query||'-') + '</td>';
      var vals = cols.map(function(c){
        var v = it[c[1]]; if (v===undefined||v===null) return '<td class="num" style="color:var(--text-muted)">-</td>';
        if (c[1]==='ctr') return '<td class="num">' + (typeof v==='number'?v.toFixed(1):v) + '%</td>';
        if (c[1]==='position'||c[1]==='position_current') return '<td class="num" style="'+(v<=5?'color:var(--green)':v<=15?'color:var(--amber)':'')+'">'+(typeof v==='number'?v.toFixed(1):v)+'</td>';
        if (c[1]==='clicks_change'||c[1]==='position_change') { var cls=v>0?'color:var(--green)':v<0?'color:var(--red)':'color:var(--text-muted)'; return '<td class="num" style="'+cls+'">'+(v>0?'+':'')+(typeof v==='number'?v.toFixed(1):v)+'</td>'; }
        return '<td class="num">'+v+'</td>';
      }).join('');
      return '<tr>' + cell + vals + '</tr>';
    }).join('') + '</tbody></table></div>';
}

// ═══════════════════════════════════════════════════════════════════
//  HEALTH TAB — systeemgezondheid: agents, LLM-gateways, bugs
// ═══════════════════════════════════════════════════════════════════
let _healthTimer = null;
function _statusBadge(live, configured) {
  if (live === true) return '<span class="pill pill-ok">● live</span>';
  if (live === false) return '<span class="pill pill-danger">● down</span>';
  return '<span class="pill pill-neutral">○ n.v.t.</span>';
}
function _healthCard(title, detail, live, note) {
  var color = live === true ? 'var(--ok-border)' : (live === false ? 'var(--red)' : 'var(--card-border)');
  return '<div style="background:#fff;border:1px solid ' + color + ';border-radius:8px;padding:12px 14px">' +
    '<div style="display:flex;align-items:center;justify-content:space-between"><b style="font-size:13px;color:var(--text)">' + escHtml(title) + '</b>' + _statusBadge(live, true) + '</div>' +
    (detail ? '<div style="font-size:11px;color:var(--text-dim);margin-top:4px">' + escHtml(detail) + '</div>' : '') +
    (note ? '<div style="font-size:10px;color:var(--text-muted);margin-top:2px">' + escHtml(note) + '</div>' : '') +
    '</div>';
}
function renderHealthTab(el) {
  el.innerHTML = '<div class="loading"><div class="spinner"></div><p>Gezondheidscheck...</p></div>';
  loadHealthData(el);
  stopHealthPoll();
  _healthTimer = setInterval(function() {
    if (currentTab !== 'Health') { stopHealthPoll(); return; }
    loadHealthData(el);
  }, 20000);
}
function stopHealthPoll() { if (_healthTimer) { clearInterval(_healthTimer); _healthTimer = null; } }
async function loadHealthData(el) {
  try {
    var r = await fetch('/api/healthcheck');
    var h = await r.json();
  } catch (e) {
    el.innerHTML = '<div class="empty-state">Kon healthcheck niet laden: ' + escHtml(e.message) + '</div>';
    return;
  }
  var statusColor = h.status === 'ok' ? 'var(--ok-bg)' : (h.status === 'warning' ? 'var(--warn-bg)' : 'var(--danger-bg)');
  var statusBorder = h.status === 'ok' ? 'var(--green)' : (h.status === 'warning' ? 'var(--amber)' : 'var(--red)');
  var statusLabel = h.status === 'ok' ? 'Gezond' : (h.status === 'warning' ? 'Waarschuwing' : 'Degraded');

  var html = '';
  // ── Hoofdstatus ──
  html += '<div style="background:' + statusColor + ';border-left:4px solid ' + statusBorder + ';border-radius:8px;padding:14px 16px;margin-bottom:16px;display:flex;align-items:center;gap:12px">' +
    '<span class="pill ' + (h.status === 'ok' ? 'pill-ok' : (h.status === 'warning' ? 'pill-warn' : 'pill-danger')) + '">' + statusLabel + '</span>' +
    '<span style="font-size:13px;color:var(--text);flex:1">' + escHtml(h.summary || '') + '</span>' +
    (h.reden ? '<span style="font-size:11px;color:var(--text-dim)">oorzaak: ' + escHtml(h.reden) + '</span>' : '') +
    '<span style="font-size:10px;color:var(--text-muted)" id="health-ts">' + new Date().toLocaleTimeString('nl-NL') + '</span>' +
    '</div>';

  // ── Component-kaarten ──
  var b = h.backend || {};
  var g = h.gateway || {};
  var cal = h.calendar || {};
  html += '<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:10px;margin-bottom:16px">' +
    _healthCard('LLM-backend (actief)', b.active || '', b.openmodel && b.openmodel.live, 'model: ' + (b.openmodel_model || '')) +
    _healthCard('Lokale gateway :8899', g.url || 'http://127.0.0.1:8899', g.live, g.live ? 'Omniroute router live' : (g.error ? g.error.slice(0,80) : 'supervisor/omniroute down')) +
    _healthCard('Ollama (fallback)', (b.ollama && b.ollama.url) || '', b.ollama && b.ollama.live, 'model: ' + ((b.ollama && b.ollama.model) || '')) +
    _healthCard('Google Agenda', cal.calendar_id || 'n.v.t.', cal.live, cal.note || '') +
    '</div>';

  // ── Tokenverbruik ──
  var llm = h.llm || {};
  var t = (llm.today) || {};
  if (llm.budget) {
    var pct = t.budget_pct || 0;
    var barColor = pct > 90 ? 'var(--red)' : (pct > 70 ? 'var(--amber)' : 'var(--green)');
    html += '<div class="section-card" style="margin-bottom:16px"><h3 style="font-size:14px;font-weight:700;margin-bottom:8px">Tokenverbruik vandaag</h3>' +
      '<div style="display:flex;align-items:center;gap:10px">' +
      '<div style="flex:1;height:10px;background:var(--card-border);border-radius:5px;overflow:hidden"><div style="height:100%;width:' + pct + '%;background:' + barColor + ';border-radius:5px;transition:width .5s"></div></div>' +
      '<span style="font-size:12px;color:var(--text-dim);white-space:nowrap">' + (t.total_tokens||0).toLocaleString('nl-NL') + ' / ' + llm.budget.toLocaleString('nl-NL') + ' (' + pct + '%)</span>' +
      '</div>' +
      (t.calls ? '<div style="font-size:11px;color:var(--text-muted);margin-top:6px">' + t.calls + ' calls · ' + (t.errors||0) + ' fouten · quota-backoff: ' + (llm.quota_backoff_active ? 'actief' : 'nee') + '</div>' : '') +
      '</div>';
  }

  // ── Actieve agents ──
  var aw = h.active_work || {};
  var nGoals = (aw.goals || []).length, nDel = (aw.delegations || []).length, nLoops = (aw.loops || []).length, nTasks = (aw.tasks_running || []).length;
  html += '<div class="section-card" style="margin-bottom:16px"><h3 style="font-size:14px;font-weight:700;margin-bottom:8px">Actieve agents / lopend werk</h3>' +
    '<div style="display:flex;gap:16px;flex-wrap:wrap;font-size:12px;color:var(--text-dim)">' +
    '<span>◉ ' + nGoals + ' doelen</span><span>⌘ ' + nDel + ' delegaties</span><span>↻ ' + nLoops + ' loops</span><span>▤ ' + nTasks + ' taken</span>' +
    (aw.subagents_running != null ? '<span>⊕ ' + aw.subagents_running + ' subagents</span>' : '') + '</div>';
  if (nGoals || nTasks) {
    html += '<div style="margin-top:8px;max-height:160px;overflow-y:auto">';
    (aw.goals || []).forEach(function(gg) {
      html += '<div style="font-size:11px;color:var(--text-dim);padding:3px 0;border-bottom:1px solid var(--card-border)">' + escHtml((gg.project||'') + ' · ' + (gg.title||'')) + ' — <b>' + escHtml(gg.status||'') + '</b></div>';
    });
    html += '</div>';
  }
  html += '</div>';

  // ── Scheduler ──
  var s = h.scheduler || {};
  if (s) {
    html += '<div class="section-card" style="margin-bottom:16px"><h3 style="font-size:14px;font-weight:700;margin-bottom:8px">Scheduler (achtergrond-jobs)</h3>' +
      '<div style="font-size:12px;color:var(--text-dim)">running: ' + (s.running ? 'ja' : 'nee') + ' · jobs: ' + (s.jobs_total||0) + ' · fouten: ' + (s.jobs_error ? s.jobs_error.length : 0) + '</div></div>';
  }

  // ── BUGS / ERRORS ──
  var bugs = h.bugs || {};
  var hasBugs = (bugs.scheduler_errors && bugs.scheduler_errors.length) || (bugs.stalled_goals && bugs.stalled_goals.length) || (bugs.recurring_bugs && bugs.recurring_bugs.length);
  html += '<div class="section-card" style="margin-bottom:16px"><div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:8px"><h3 style="font-size:14px;font-weight:700">Bugs & fouten</h3>' +
    (hasBugs ? '<span class="pill pill-danger">' + ((bugs.scheduler_errors||[]).length + (bugs.stalled_goals||[]).length + (bugs.recurring_bugs||[]).length) + ' gevonden</span>' : '<span class="pill pill-ok">geen</span>') + '</div>';
  if (!hasBugs) {
    html += '<div style="font-size:12px;color:var(--ok-fg);padding:8px 0">Geen openstaande bugs of vastgelopen processen.</div>';
  } else {
    if (bugs.scheduler_errors && bugs.scheduler_errors.length) {
      html += '<p style="font-size:11px;font-weight:600;color:var(--danger-fg);margin:6px 0 2px">Scheduler-jobs die faalden</p>';
      bugs.scheduler_errors.forEach(function(e) {
        html += '<div style="font-size:11px;color:var(--text-dim);padding:4px 6px;border-left:3px solid var(--red);background:var(--danger-bg);margin-bottom:3px;border-radius:0 4px 4px 0"><b>' + escHtml(e.job) + '</b> · ' + escHtml((e.error||'').slice(0,160)) + (e.last_run_at ? ' <span style="color:var(--text-muted)">(' + escHtml(e.last_run_at.slice(0,16)) + ')</span>' : '') + '</div>';
      });
    }
    if (bugs.stalled_goals && bugs.stalled_goals.length) {
      html += '<p style="font-size:11px;font-weight:600;color:var(--danger-fg);margin:6px 0 2px">Vastgelopen doelen</p>';
      bugs.stalled_goals.forEach(function(gl) {
        html += '<div style="font-size:11px;color:var(--text-dim);padding:4px 6px;border-left:3px solid var(--amber);background:var(--warn-bg);margin-bottom:3px;border-radius:0 4px 4px 0"><b>' + escHtml(gl.project || '') + '</b> · ‘' + escHtml(gl.title) + '’ (' + escHtml(gl.status) + ')' +
          (gl.failed_tasks && gl.failed_tasks.length ? '<br><span style="color:var(--text-muted)">mislukte taken: ' + gl.failed_tasks.map(function(t){return escHtml(t.title);}).join(', ') + '</span>' : '') + '</div>';
      });
    }
    if (bugs.recurring_bugs && bugs.recurring_bugs.length) {
      html += '<p style="font-size:11px;font-weight:600;color:var(--warn-fg);margin:6px 0 2px">Terugkerende bugs (agent-remedies)</p>';
      bugs.recurring_bugs.forEach(function(rb) {
        html += '<div style="font-size:11px;color:var(--text-dim);padding:4px 6px;border-left:3px solid var(--amber);background:var(--warn-bg);margin-bottom:3px;border-radius:0 4px 4px 0"><b>' + escHtml(rb.project || '') + '</b> · ' + escHtml((rb.diagnosis||'').slice(0,120)) + ' <span style="color:var(--text-muted)">(' + rb.failures + ' failures / ' + rb.occurrences + '×)</span></div>';
      });
    }
  }
  html += '</div>';

  el.innerHTML = html;
}


