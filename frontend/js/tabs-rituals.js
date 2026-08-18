// ── Agent OS — Rituelen: ochtend/avond, week, wins, doelen, focus ──
// Onderdeel van de SPA: klassieke scripts, gedeelde globale scope.
// Overgezet uit impactreis3 (Next.js/Neon, alleen localStorage) naar een
// eigen backend-domein (backend/domains/rituals) zodat Iris meekijkt.
// Render-doel: <div id="rituelen-panel"> in home.js (Control Room, buiten
// projectcontext — rituelen zijn niet projectgebonden).

var _rituelenLoaded = false;

function loadRituelenSection() {
  var el = document.getElementById('rituelen-panel');
  if (!el) return;
  el.innerHTML = '<div style="color:#64748b">Laden...</div>';
  Promise.all([
    fetch('/api/rituals/status').then(function (r) { return r.json(); }),
    fetch('/api/rituals/wins?limit=5').then(function (r) { return r.json(); }),
    fetch('/api/rituals/goals?include_completed=false').then(function (r) { return r.json(); }),
  ]).then(function (res) {
    _rituelenLoaded = true;
    renderRituelenOverview(el, res[0], res[1], res[2]);
  }).catch(function (e) {
    el.innerHTML = '<div style="color:var(--danger-fg)">Rituelen laden mislukt: ' + escHtml(e.message) + '</div>';
  });
}

function _ritBadge(done, label) {
  return '<span class="pill ' + (done ? 'pill-ok' : 'pill-neutral') + '">' + escHtml(label) + '</span>';
}

function renderRituelenOverview(el, status, wins, goals) {
  var today = status.today || {}, streaks = status.streaks || {};
  var html = '<div style="display:flex;flex-wrap:wrap;gap:6px;margin-bottom:12px">' +
    _ritBadge(today.morning_done, 'Ochtend') +
    _ritBadge(today.evening_done, 'Avond') +
    _ritBadge(today.weekly_start_done, 'Weekstart') +
    _ritBadge(today.weekly_review_done, 'Weekreview') +
    '<span style="font-size:11px;color:var(--accent);font-weight:600;margin-left:4px">' +
    (streaks.morning || 0) + 'd ochtend · ' + (streaks.evening || 0) + 'd avond</span></div>';

  html += '<div style="display:flex;flex-wrap:wrap;gap:6px;margin-bottom:14px">';
  html += '<button onclick="showMorningForm()" class="btn btn-sm ' + (today.morning_done ? 'btn-ghost' : 'btn-primary') + '">' +
    (today.morning_done ? 'Ochtendritueel bekijken' : 'Ochtendritueel starten') + '</button>';
  html += '<button onclick="showEveningForm()" class="btn btn-sm ' + (today.evening_done ? 'btn-ghost' : 'btn-primary') + '">' +
    (today.evening_done ? 'Avondritueel bekijken' : 'Avondritueel starten') + '</button>';
  html += '<button onclick="showWeeklyStartForm()" class="btn btn-sm btn-ghost">Weekstart</button>';
  html += '<button onclick="showWeeklyReviewForm()" class="btn btn-sm btn-ghost">Weekreview</button>';
  html += '</div>';

  // Wins
  html += '<div style="margin-bottom:14px"><div style="display:flex;align-items:center;justify-content:space-between">' +
    '<h5 style="font-size:12px;font-weight:700;color:var(--text-dim);margin:0">Wins (Cookie Jar)</h5>' +
    '<button onclick="showAddWinForm()" class="btn btn-sm btn-ghost">+ Win</button></div>';
  if (!wins.length) {
    html += '<div style="font-size:11px;color:#94a3b8;margin-top:4px">Nog geen wins gelogd.</div>';
  } else {
    html += '<div style="margin-top:6px;display:flex;flex-direction:column;gap:4px">';
    wins.forEach(function (w) {
      html += '<div style="font-size:11px;padding:5px 8px;background:var(--warn-bg);border-radius:6px;display:flex;' +
        'justify-content:space-between;gap:8px"><span>' + escHtml(w.title) + '</span>' +
        '<span style="color:var(--warn-fg);white-space:nowrap">' + escHtml(w.date) + '</span></div>';
    });
    html += '</div>';
  }
  html += '</div>';

  // Persoonlijke doelen
  html += '<div><div style="display:flex;align-items:center;justify-content:space-between">' +
    '<h5 style="font-size:12px;font-weight:700;color:var(--text-dim);margin:0">Persoonlijke doelen</h5>' +
    '<button onclick="showAddGoalForm()" class="btn btn-sm btn-ghost">+ Doel</button></div>';
  if (!goals.length) {
    html += '<div style="font-size:11px;color:#94a3b8;margin-top:4px">Geen open persoonlijke doelen. ' +
      '(Dit is los van de projecttab "Doelen" — dat is projectuitvoering, dit is persoonlijk.)</div>';
  } else {
    html += '<div style="margin-top:6px;display:flex;flex-direction:column;gap:4px">';
    goals.forEach(function (g) {
      html += '<div style="font-size:11px;padding:6px 8px;background:var(--info-bg);border-radius:6px">' +
        '<div style="display:flex;justify-content:space-between;gap:8px">' +
        '<span style="font-weight:600">' + escHtml(g.title) + '</span>' +
        '<span style="color:var(--info-fg)">' + g.progress + '%</span></div>' +
        '<div style="height:4px;background:var(--card-border);border-radius:2px;margin-top:4px">' +
        '<div style="height:100%;width:' + g.progress + '%;background:var(--info-fg);border-radius:2px"></div></div>' +
        '</div>';
    });
    html += '</div>';
  }
  html += '</div>';

  el.innerHTML = html;
}

// ── Ochtendritueel ──────────────────────────────────────────────────
function showMorningForm() {
  var el = document.getElementById('rituelen-panel');
  if (!el) return;
  el.innerHTML = '<div style="color:#64748b">Laden...</div>';
  fetch('/api/rituals/morning').then(function (r) { return r.json(); }).then(function (d) {
    d = d || {};
    var dank = d.dankbaarheid && d.dankbaarheid.length ? d.dankbaarheid : ['', '', ''];
    var fb1 = d.focus_blok1 || {}, fb2 = d.focus_blok2 || {};
    el.innerHTML =
      '<div style="font-size:12px;font-weight:700;margin-bottom:8px">Ochtendritueel &mdash; vandaag</div>' +
      '<label style="font-size:11px;color:#64748b">Intentie voor vandaag</label>' +
      '<textarea id="rit-m-intentie" rows="2" style="width:100%;margin:2px 0 8px;font-size:12px;padding:6px;' +
      'border:1px solid var(--card-border);border-radius:6px" placeholder="Vandaag focus ik op...">' + escHtml(d.intentie || '') + '</textarea>' +
      '<div style="display:flex;gap:6px;margin-bottom:8px">' +
      '<div style="flex:1"><label style="font-size:11px;color:#64748b">Focusblok 1</label>' +
      '<input id="rit-m-fb1" style="width:100%;font-size:12px;padding:5px;border:1px solid var(--card-border);border-radius:6px" ' +
      'value="' + escAttr(fb1.onderwerp || '') + '" placeholder="Wat ga ik doen?"></div>' +
      '<div style="flex:1"><label style="font-size:11px;color:#64748b">Focusblok 2</label>' +
      '<input id="rit-m-fb2" style="width:100%;font-size:12px;padding:5px;border:1px solid var(--card-border);border-radius:6px" ' +
      'value="' + escAttr(fb2.onderwerp || '') + '" placeholder="Wat ga ik doen?"></div></div>' +
      '<div style="display:flex;gap:12px;margin-bottom:8px">' +
      '<div style="flex:1"><label style="font-size:11px;color:#64748b">Energie (1-10)</label>' +
      '<input id="rit-m-energy" type="number" min="1" max="10" value="' + (d.energy_level || 7) +
      '" style="width:100%;font-size:12px;padding:5px;border:1px solid var(--card-border);border-radius:6px"></div>' +
      '<div style="flex:1"><label style="font-size:11px;color:#64748b">Slaap (1-10)</label>' +
      '<input id="rit-m-sleep" type="number" min="1" max="10" value="' + (d.sleep_quality || 7) +
      '" style="width:100%;font-size:12px;padding:5px;border:1px solid var(--card-border);border-radius:6px"></div></div>' +
      '<label style="font-size:11px;color:#64748b">3× dankbaarheid</label>' +
      '<div style="display:flex;flex-direction:column;gap:4px;margin:2px 0 8px">' +
      [0, 1, 2].map(function (i) {
        return '<input id="rit-m-dank' + i + '" style="width:100%;font-size:12px;padding:5px;border:1px solid var(--card-border);' +
          'border-radius:6px" value="' + escAttr(dank[i] || '') + '" placeholder="Ik ben dankbaar voor...">';
      }).join('') + '</div>' +
      '<label style="font-size:11px;color:#64748b">Affirmatie</label>' +
      '<textarea id="rit-m-affirmatie" rows="2" style="width:100%;margin:2px 0 10px;font-size:12px;padding:6px;' +
      'border:1px solid var(--card-border);border-radius:6px" placeholder="Ik ben... Ik heb... Ik bereik...">' +
      escHtml(d.affirmatie || '') + '</textarea>' +
      '<div style="display:flex;gap:6px">' +
      '<button onclick="saveMorningForm()" class="btn btn-sm btn-primary">Opslaan</button>' +
      '<button onclick="loadRituelenSection()" class="btn btn-sm btn-ghost">Terug</button></div>';
  });
}

function saveMorningForm() {
  var body = {
    intentie: document.getElementById('rit-m-intentie').value,
    affirmatie: document.getElementById('rit-m-affirmatie').value,
    dankbaarheid: [0, 1, 2].map(function (i) { return document.getElementById('rit-m-dank' + i).value; }),
    energyLevel: parseInt(document.getElementById('rit-m-energy').value, 10) || 7,
    sleepQuality: parseInt(document.getElementById('rit-m-sleep').value, 10) || 7,
    focusBlok1: { onderwerp: document.getElementById('rit-m-fb1').value, doel: '' },
    focusBlok2: { onderwerp: document.getElementById('rit-m-fb2').value, doel: '' },
  };
  post('/api/rituals/morning', body).then(function (d) { loadRituelenSection(); afterRitualSaved(); return d; }).catch(function (e) { alert(e.message); });
}

// ── Avondritueel ────────────────────────────────────────────────────
function showEveningForm() {
  var el = document.getElementById('rituelen-panel');
  if (!el) return;
  el.innerHTML = '<div style="color:#64748b">Laden...</div>';
  fetch('/api/rituals/evening').then(function (r) { return r.json(); }).then(function (d) {
    d = d || {};
    var top3 = d.tomorrow_top3 && d.tomorrow_top3.length ? d.tomorrow_top3 : ['', '', ''];
    el.innerHTML =
      '<div style="font-size:12px;font-weight:700;margin-bottom:8px">Avondritueel &mdash; vandaag</div>' +
      _ritField('rit-e-goed', 'Wat ging goed vandaag?', d.what_went_well, true) +
      _ritField('rit-e-win', 'Grootste overwinning', d.biggest_win, false, true) +
      _ritField('rit-e-geleerd', 'Wat heb je geleerd?', d.what_learned, true) +
      _ritField('rit-e-uitdaging', 'Uitdagingen', d.challenges, true) +
      '<label style="font-size:11px;color:#64748b">Energie nu (1-10)</label>' +
      '<input id="rit-e-energy" type="number" min="1" max="10" value="' + (d.energy_level || 5) +
      '" style="width:100%;margin:2px 0 8px;font-size:12px;padding:5px;border:1px solid var(--card-border);border-radius:6px">' +
      '<label style="font-size:11px;color:#64748b">Top 3 voor morgen</label>' +
      '<div style="display:flex;flex-direction:column;gap:4px;margin:2px 0 8px">' +
      [0, 1, 2].map(function (i) {
        return '<input id="rit-e-top' + i + '" style="width:100%;font-size:12px;padding:5px;border:1px solid var(--card-border);' +
          'border-radius:6px" value="' + escAttr(top3[i] || '') + '" placeholder="Prioriteit ' + (i + 1) + '">';
      }).join('') + '</div>' +
      _ritField('rit-e-dankbaarheid', 'Dankbaarheid voor vandaag', d.gratitude, true) +
      '<div style="display:flex;gap:6px;margin-top:2px">' +
      '<button onclick="saveEveningForm()" class="btn btn-sm btn-primary">Opslaan</button>' +
      '<button onclick="loadRituelenSection()" class="btn btn-sm btn-ghost">Terug</button></div>';
  });
}

function _ritField(id, label, value, textarea, marginSmall) {
  var tag = textarea
    ? '<textarea id="' + id + '" rows="2" style="width:100%;font-size:12px;padding:6px;border:1px solid var(--card-border);' +
      'border-radius:6px">' + escHtml(value || '') + '</textarea>'
    : '<input id="' + id + '" style="width:100%;font-size:12px;padding:6px;border:1px solid var(--card-border);' +
      'border-radius:6px" value="' + escAttr(value || '') + '">';
  return '<label style="font-size:11px;color:#64748b">' + escHtml(label) + '</label>' +
    '<div style="margin:2px 0 8px">' + tag + '</div>';
}

function saveEveningForm() {
  var body = {
    whatWentWell: document.getElementById('rit-e-goed').value,
    biggestWin: document.getElementById('rit-e-win').value,
    whatLearned: document.getElementById('rit-e-geleerd').value,
    challenges: document.getElementById('rit-e-uitdaging').value,
    energyLevel: parseInt(document.getElementById('rit-e-energy').value, 10) || 5,
    tomorrowTop3: [0, 1, 2].map(function (i) { return document.getElementById('rit-e-top' + i).value; }),
    gratitude: document.getElementById('rit-e-dankbaarheid').value,
  };
  post('/api/rituals/evening', body).then(function (d) { loadRituelenSection(); afterRitualSaved(); return d; }).catch(function (e) { alert(e.message); });
}

// ── Weekstart / Weekreview ──────────────────────────────────────────
function showWeeklyStartForm() {
  var el = document.getElementById('rituelen-panel');
  if (!el) return;
  el.innerHTML = '<div style="color:#64748b">Laden...</div>';
  fetch('/api/rituals/weekly-start').then(function (r) { return r.json(); }).then(function (d) {
    d = d || {};
    var goals = d.main_goals && d.main_goals.length ? d.main_goals : ['', '', ''];
    el.innerHTML =
      '<div style="font-size:12px;font-weight:700;margin-bottom:8px">Weekstart</div>' +
      _ritField('rit-ws-intentie', 'Weekintentie', d.week_intention, true) +
      '<label style="font-size:11px;color:#64748b">Hoofddoelen (3-5)</label>' +
      '<div id="rit-ws-goals" style="display:flex;flex-direction:column;gap:4px;margin:2px 0 8px">' +
      goals.map(function (g, i) {
        return '<input class="rit-ws-goal" style="width:100%;font-size:12px;padding:5px;border:1px solid var(--card-border);' +
          'border-radius:6px" value="' + escAttr(g || '') + '" placeholder="Doel ' + (i + 1) + '">';
      }).join('') + '</div>' +
      _ritField('rit-ws-leer', 'Leerdoel', d.learning_goal, false) +
      _ritField('rit-ws-obstakels', 'Obstakels', d.obstacles, true) +
      _ritField('rit-ws-succes', 'Succes metrics', d.success_metrics, false) +
      '<div style="display:flex;gap:6px">' +
      '<button onclick="saveWeeklyStartForm()" class="btn btn-sm btn-primary">Opslaan</button>' +
      '<button onclick="loadRituelenSection()" class="btn btn-sm btn-ghost">Terug</button></div>';
  });
}

function saveWeeklyStartForm() {
  var goalInputs = document.querySelectorAll('.rit-ws-goal');
  var body = {
    weekIntention: document.getElementById('rit-ws-intentie').value,
    mainGoals: Array.prototype.map.call(goalInputs, function (i) { return i.value; }).filter(Boolean),
    learningGoal: document.getElementById('rit-ws-leer').value,
    obstacles: document.getElementById('rit-ws-obstakels').value,
    successMetrics: document.getElementById('rit-ws-succes').value,
  };
  post('/api/rituals/weekly-start', body).then(function (d) { loadRituelenSection(); afterRitualSaved(); return d; }).catch(function (e) { alert(e.message); });
}

function showWeeklyReviewForm() {
  var el = document.getElementById('rituelen-panel');
  if (!el) return;
  el.innerHTML = '<div style="color:#64748b">Laden...</div>';
  fetch('/api/rituals/weekly-review').then(function (r) { return r.json(); }).then(function (d) {
    d = d || {};
    var wins = d.wins && d.wins.length ? d.wins.join(', ') : '';
    el.innerHTML =
      '<div style="font-size:12px;font-weight:700;margin-bottom:8px">Weekreview</div>' +
      _ritField('rit-wr-wins', 'Grootste overwinningen (komma-gescheiden)', wins, true) +
      _ritField('rit-wr-uitdaging', 'Wat ging niet zoals gepland?', d.challenges, true) +
      _ritField('rit-wr-lessen', 'Belangrijkste lessen', d.learnings, true) +
      '<div style="display:flex;gap:12px;margin-bottom:8px">' +
      '<div style="flex:1"><label style="font-size:11px;color:#64748b">Productiviteit (1-10)</label>' +
      '<input id="rit-wr-prod" type="number" min="1" max="10" value="' + (d.productivity_score || 7) +
      '" style="width:100%;font-size:12px;padding:5px;border:1px solid var(--card-border);border-radius:6px"></div>' +
      '<div style="flex:1"><label style="font-size:11px;color:#64748b">Energie (1-10)</label>' +
      '<input id="rit-wr-energy" type="number" min="1" max="10" value="' + (d.energy_score || 7) +
      '" style="width:100%;font-size:12px;padding:5px;border:1px solid var(--card-border);border-radius:6px"></div></div>' +
      _ritField('rit-wr-meenemen', 'Wat neem je mee?', d.carry_forward, true) +
      _ritField('rit-wr-loslaten', 'Wat laat je achter?', d.leave_behind, true) +
      '<div style="background:#0f172a;border-radius:8px;padding:10px;margin-bottom:8px">' +
      '<div style="font-size:11px;color:#f59e0b;font-weight:600;margin-bottom:6px">Tony Robbins Quality Questions</div>' +
      _ritFieldDark('rit-wr-gaf', 'Wat heb ik deze week GEGEVEN?', d.what_gave) +
      _ritFieldDark('rit-wr-leerde', 'Wat heb ik deze week GELEERD?', d.what_learned) +
      _ritFieldDark('rit-wr-bijdrage', 'Hoe heeft deze week bijgedragen?', d.how_contributed) +
      _ritFieldDark('rit-wr-beter', 'Hoe kan ik volgende week beter maken?', d.how_make_better) +
      '</div>' +
      '<div style="display:flex;gap:6px">' +
      '<button onclick="saveWeeklyReviewForm()" class="btn btn-sm btn-primary">Opslaan</button>' +
      '<button onclick="loadRituelenSection()" class="btn btn-sm btn-ghost">Terug</button></div>';
  });
}

function _ritFieldDark(id, label, value) {
  return '<label style="font-size:10px;color:#cbd5e1">' + escHtml(label) + '</label>' +
    '<textarea id="' + id + '" rows="2" style="width:100%;margin:2px 0 6px;font-size:11px;padding:5px;' +
    'background:rgba(255,255,255,.08);border:1px solid rgba(255,255,255,.15);border-radius:5px;color:#fff">' +
    escHtml(value || '') + '</textarea>';
}

function saveWeeklyReviewForm() {
  var body = {
    wins: document.getElementById('rit-wr-wins').value.split(',').map(function (s) { return s.trim(); }).filter(Boolean),
    challenges: document.getElementById('rit-wr-uitdaging').value,
    learnings: document.getElementById('rit-wr-lessen').value,
    productivityScore: parseInt(document.getElementById('rit-wr-prod').value, 10) || 7,
    energyScore: parseInt(document.getElementById('rit-wr-energy').value, 10) || 7,
    carryForward: document.getElementById('rit-wr-meenemen').value,
    leaveBehind: document.getElementById('rit-wr-loslaten').value,
    whatGave: document.getElementById('rit-wr-gaf').value,
    whatLearned: document.getElementById('rit-wr-leerde').value,
    howContributed: document.getElementById('rit-wr-bijdrage').value,
    howMakeBetter: document.getElementById('rit-wr-beter').value,
  };
  post('/api/rituals/weekly-review', body).then(function (d) { loadRituelenSection(); afterRitualSaved(); return d; }).catch(function (e) { alert(e.message); });
}

// ── Wins ─────────────────────────────────────────────────────────────
function showAddWinForm() {
  var el = document.getElementById('rituelen-panel');
  if (!el) return;
  el.innerHTML =
    '<div style="font-size:12px;font-weight:700;margin-bottom:8px">Nieuwe win</div>' +
    '<input id="rit-win-title" style="width:100%;font-size:12px;padding:6px;border:1px solid var(--card-border);' +
    'border-radius:6px;margin-bottom:8px" placeholder="Titel">' +
    '<textarea id="rit-win-desc" rows="2" style="width:100%;font-size:12px;padding:6px;border:1px solid var(--card-border);' +
    'border-radius:6px;margin-bottom:8px" placeholder="Omschrijving (optioneel)"></textarea>' +
    '<div style="display:flex;gap:12px;margin-bottom:10px">' +
    '<div style="flex:1"><label style="font-size:11px;color:#64748b">Categorie</label>' +
    '<select id="rit-win-cat" style="width:100%;font-size:12px;padding:5px;border:1px solid var(--card-border);border-radius:6px">' +
    '<option value="business">Business</option><option value="personal" selected>Persoonlijk</option>' +
    '<option value="health">Gezondheid</option><option value="learning">Leren</option></select></div>' +
    '<div style="flex:1"><label style="font-size:11px;color:#64748b">Impact (1-5)</label>' +
    '<input id="rit-win-impact" type="number" min="1" max="5" value="3" style="width:100%;font-size:12px;' +
    'padding:5px;border:1px solid var(--card-border);border-radius:6px"></div></div>' +
    '<div style="display:flex;gap:6px">' +
    '<button onclick="saveWinForm()" class="btn btn-sm btn-primary">Toevoegen</button>' +
    '<button onclick="loadRituelenSection()" class="btn btn-sm btn-ghost">Terug</button></div>';
}

function saveWinForm() {
  var title = document.getElementById('rit-win-title').value.trim();
  if (!title) { alert('Titel is verplicht.'); return; }
  post('/api/rituals/wins', {
    title: title,
    description: document.getElementById('rit-win-desc').value,
    category: document.getElementById('rit-win-cat').value,
    impactLevel: parseInt(document.getElementById('rit-win-impact').value, 10) || 3,
  }).then(loadRituelenSection).catch(function (e) { alert(e.message); });
}

// ── Persoonlijke doelen ─────────────────────────────────────────────
function showAddGoalForm() {
  var el = document.getElementById('rituelen-panel');
  if (!el) return;
  el.innerHTML =
    '<div style="font-size:12px;font-weight:700;margin-bottom:8px">Nieuw persoonlijk doel</div>' +
    '<input id="rit-goal-title" style="width:100%;font-size:12px;padding:6px;border:1px solid var(--card-border);' +
    'border-radius:6px;margin-bottom:8px" placeholder="Titel">' +
    _ritField('rit-goal-why', 'Waarom is dit belangrijk?', '', true) +
    _ritField('rit-goal-pain', 'Wat kost het als ik dit niet doe?', '', true) +
    _ritField('rit-goal-pleasure', 'Wat levert het op als ik dit wel doe?', '', true) +
    '<label style="font-size:11px;color:#64748b">Categorie</label>' +
    '<select id="rit-goal-cat" style="width:100%;font-size:12px;padding:5px;border:1px solid var(--card-border);' +
    'border-radius:6px;margin:2px 0 10px"><option value="business">Business</option>' +
    '<option value="health">Gezondheid</option><option value="relationships">Relaties</option>' +
    '<option value="personal" selected>Persoonlijk</option></select>' +
    '<div style="display:flex;gap:6px">' +
    '<button onclick="saveGoalForm()" class="btn btn-sm btn-primary">Toevoegen</button>' +
    '<button onclick="loadRituelenSection()" class="btn btn-sm btn-ghost">Terug</button></div>';
}

function saveGoalForm() {
  var title = document.getElementById('rit-goal-title').value.trim();
  if (!title) { alert('Titel is verplicht.'); return; }
  post('/api/rituals/goals', {
    title: title,
    why: document.getElementById('rit-goal-why').value,
    painIfNot: document.getElementById('rit-goal-pain').value,
    pleasureIfDone: document.getElementById('rit-goal-pleasure').value,
    category: document.getElementById('rit-goal-cat').value,
  }).then(loadRituelenSection).catch(function (e) { alert(e.message); });
}
