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

