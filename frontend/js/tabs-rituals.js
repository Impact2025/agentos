// ── Impact OS — Rituelen: ochtend/avond, week, wins, doelen, focus ──
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
  var today = status.today || {}, streaks = status.streaks || {}, focus = status.focus_completion;
  var focusBadge = '';
  if (focus) {
    var pillClass = focus.done === focus.total ? 'pill-ok' : (focus.done === 0 ? 'pill-danger' : 'pill-warn');
    focusBadge = '<span class="pill ' + pillClass + '">Focus ' + focus.done + '/' + focus.total + ' gehaald</span>';
  }
  var html = '<div style="display:flex;flex-wrap:wrap;gap:6px;margin-bottom:12px">' +
    _ritBadge(today.morning_done, 'Ochtend') +
    _ritBadge(today.evening_done, 'Avond') +
    _ritBadge(today.weekly_start_done, 'Weekstart') +
    _ritBadge(today.weekly_review_done, 'Weekreview') +
    focusBadge +
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

// ── Gedeelde bouwstenen (professioneel formulier i.p.v. losse inline-styles) ──
function _ritualPanelHeader(icon, title) {
  return '<div style="display:flex;align-items:center;gap:8px;padding:16px 16px 0;margin-bottom:2px">' +
    '<span style="font-size:16px">' + icon + '</span>' +
    '<div style="font-size:13px;font-weight:700;color:var(--text)">' + escHtml(title) + '</div></div>';
}

function _ritualScaleField(id, label, value, defVal) {
  var v = value || defVal;
  return '<div class="ritual-field"><label>' + escHtml(label) + '</label>' +
    '<div class="ritual-scale">' +
    '<input type="range" min="1" max="10" id="' + id + '" value="' + v + '" ' +
    'oninput="document.getElementById(\'' + id + '-val\').textContent=this.value">' +
    '<span class="ritual-scale-val" id="' + id + '-val">' + v + '</span></div></div>';
}

function _ritualListField(idPrefix, label, values, placeholder, count) {
  count = count || 3;
  var vals = values && values.length ? values : new Array(count).fill('');
  var rows = [];
  for (var i = 0; i < count; i++) {
    rows.push('<div class="ritual-list-item"><span class="ritual-list-num">' + (i + 1) + '</span>' +
      '<input id="' + idPrefix + i + '" value="' + escAttr(vals[i] || '') + '" placeholder="' + escAttr(placeholder) + '"></div>');
  }
  return '<div class="ritual-field"><label>' + escHtml(label) + '</label>' +
    '<div class="ritual-list">' + rows.join('') + '</div></div>';
}

function _ritualFooter(saveFn, inGate) {
  var back = inGate ? '' : '<button onclick="loadRituelenSection()" class="btn btn-ghost">Terug</button>';
  return '<div class="ritual-footer" style="justify-content:flex-end">' + back +
    '<button onclick="' + saveFn + '()" class="btn btn-primary">Opslaan &amp; verder</button></div>';
}

// ── Ochtendritueel ──────────────────────────────────────────────────
function showMorningForm(inGate) {
  var el = document.getElementById('rituelen-panel');
  if (!el) return;
  el.innerHTML = '<div class="loading"><div class="spinner"></div>Laden...</div>';
  fetch('/api/rituals/morning').then(function (r) { return r.json(); }).then(function (d) {
    d = d || {};
    var dank = d.dankbaarheid && d.dankbaarheid.length ? d.dankbaarheid : ['', '', ''];
    var fb1 = d.focus_blok1 || {}, fb2 = d.focus_blok2 || {};
    el.innerHTML =
      (inGate ? '' : _ritualPanelHeader('☀️', 'Ochtendritueel — vandaag')) +
      '<div class="ritual-body">' +
        '<div class="ritual-section">' +
          '<div class="ritual-section-label">Intentie</div>' +
          '<div class="ritual-field">' +
            '<label>Waar focus ik vandaag op?</label>' +
            '<textarea id="rit-m-intentie" rows="2" placeholder="Vandaag focus ik op...">' + escHtml(d.intentie || '') + '</textarea>' +
          '</div>' +
          '<div class="ritual-row2">' +
            '<div class="ritual-field"><label>Focusblok 1</label>' +
              '<input id="rit-m-fb1" value="' + escAttr(fb1.onderwerp || '') + '" placeholder="Belangrijkste taak"></div>' +
            '<div class="ritual-field"><label>Focusblok 2</label>' +
              '<input id="rit-m-fb2" value="' + escAttr(fb2.onderwerp || '') + '" placeholder="Tweede prioriteit"></div>' +
          '</div>' +
        '</div>' +
        '<div class="ritual-section">' +
          '<div class="ritual-section-label">Staat van vandaag</div>' +
          '<div class="ritual-row2">' +
            _ritualScaleField('rit-m-energy', 'Energie', d.energy_level, 7) +
            _ritualScaleField('rit-m-sleep', 'Slaapkwaliteit', d.sleep_quality, 7) +
          '</div>' +
        '</div>' +
        '<div class="ritual-section">' +
          '<div class="ritual-section-label">Dankbaarheid &amp; affirmatie</div>' +
          _ritualListField('rit-m-dank', '3× dankbaarheid', dank, 'Ik ben dankbaar voor...') +
          '<div class="ritual-field"><label>Affirmatie</label>' +
            '<textarea id="rit-m-affirmatie" rows="2" placeholder="Ik ben... Ik heb... Ik bereik...">' +
            escHtml(d.affirmatie || '') + '</textarea></div>' +
        '</div>' +
      '</div>' +
      _ritualFooter('saveMorningForm', inGate);
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
function _ritualToggleRow(idx, onderwerp, done) {
  // done: true/false/null (null = nog niet beantwoord — geen kleur, geen gok)
  return '<div class="ritual-focus-row" data-focus-onderwerp="' + escAttr(onderwerp) + '">' +
    '<span>' + escHtml(onderwerp) + '</span>' +
    '<div class="ritual-toggle" id="rit-e-focus' + idx + '">' +
    '<button type="button" class="on-yes' + (done === true ? ' active' : '') + '" ' +
      'onclick="_setFocusCheck(' + idx + ', true)">Gelukt</button>' +
    '<button type="button" class="on-no' + (done === false ? ' active' : '') + '" ' +
      'onclick="_setFocusCheck(' + idx + ', false)">Niet gelukt</button>' +
    '</div></div>';
}

function _setFocusCheck(idx, done) {
  var wrap = document.getElementById('rit-e-focus' + idx);
  if (!wrap) return;
  wrap.dataset.done = done ? '1' : '0';
  var yes = wrap.querySelector('.on-yes'), no = wrap.querySelector('.on-no');
  if (yes) yes.classList.toggle('active', done === true);
  if (no) no.classList.toggle('active', done === false);
}

function showEveningForm(inGate) {
  var el = document.getElementById('rituelen-panel');
  if (!el) return;
  el.innerHTML = '<div class="loading"><div class="spinner"></div>Laden...</div>';
  Promise.all([
    fetch('/api/rituals/evening').then(function (r) { return r.json(); }),
    fetch('/api/rituals/morning').then(function (r) { return r.json(); }),
  ]).then(function (res) {
    var d = res[0] || {}, morning = res[1] || {};
    var top3 = d.tomorrow_top3 && d.tomorrow_top3.length ? d.tomorrow_top3 : ['', '', ''];
    var focusBlokken = [morning.focus_blok1, morning.focus_blok2]
      .map(function (b) { return (b && b.onderwerp || '').trim(); })
      .filter(Boolean);
    var prevChecks = d.focus_check || [];
    var focusSection = '';
    if (focusBlokken.length) {
      focusSection = '<div class="ritual-section">' +
        '<div class="ritual-section-label">☀️ Terugkoppeling op je focusblokken van vanochtend</div>' +
        '<div class="ritual-focus-check">' +
        focusBlokken.map(function (onderwerp, i) {
          var prev = prevChecks.filter(function (c) { return c.onderwerp === onderwerp; })[0];
          var done = prev ? !!prev.done : null;
          return _ritualToggleRow(i, onderwerp, done);
        }).join('') +
        '</div></div>';
    }
    el.innerHTML =
      (inGate ? '' : _ritualPanelHeader('🌙', 'Avondritueel — vandaag')) +
      '<div class="ritual-body">' +
        focusSection +
        '<div class="ritual-section">' +
          '<div class="ritual-section-label">Terugblik</div>' +
          _ritField('rit-e-goed', 'Wat ging goed vandaag?', d.what_went_well, true) +
          _ritField('rit-e-win', 'Grootste overwinning', d.biggest_win, false) +
          _ritField('rit-e-geleerd', 'Wat heb je geleerd?', d.what_learned, true) +
          _ritField('rit-e-uitdaging', 'Waar liep je tegenaan?', d.challenges, true) +
        '</div>' +
        '<div class="ritual-section">' +
          '<div class="ritual-section-label">Energie &amp; morgen</div>' +
          _ritualScaleField('rit-e-energy', 'Energie nu', d.energy_level, 5) +
          _ritField('rit-e-energy-gains', 'Gaf energie (één per regel)', '', true) +
          _ritField('rit-e-energy-costs', 'Kostte energie (één per regel)', '', true) +
          _ritualListField('rit-e-top', 'Top 3 voor morgen', top3, 'Prioriteit...') +
        '</div>' +
        '<div class="ritual-section">' +
          '<div class="ritual-section-label">Afsluiten</div>' +
          _ritField('rit-e-dankbaarheid', 'Dankbaarheid voor vandaag', d.gratitude, true) +
        '</div>' +
      '</div>' +
      _ritualFooter('saveEveningForm', inGate);
  });
}

function _ritField(id, label, value, textarea) {
  var tag = textarea
    ? '<textarea id="' + id + '" rows="2">' + escHtml(value || '') + '</textarea>'
    : '<input id="' + id + '" value="' + escAttr(value || '') + '">';
  return '<div class="ritual-field"><label>' + escHtml(label) + '</label>' + tag + '</div>';
}

function saveEveningForm() {
  var focusCheck = Array.prototype.map.call(
    document.querySelectorAll('.ritual-focus-row'),
    function (row) {
      var wrap = row.querySelector('.ritual-toggle');
      var done = wrap && wrap.dataset.done;
      return { onderwerp: row.dataset.focusOnderwerp, done: done === undefined ? null : done === '1' };
    }
  ).filter(function (c) { return c.done !== null; });
  var body = {
    whatWentWell: document.getElementById('rit-e-goed').value,
    biggestWin: document.getElementById('rit-e-win').value,
    whatLearned: document.getElementById('rit-e-geleerd').value,
    challenges: document.getElementById('rit-e-uitdaging').value,
    energyLevel: parseInt(document.getElementById('rit-e-energy').value, 10) || 5,
    tomorrowTop3: [0, 1, 2].map(function (i) { return document.getElementById('rit-e-top' + i).value; }),
    gratitude: document.getElementById('rit-e-dankbaarheid').value,
    focusCheck: focusCheck,
  };
  post('/api/rituals/evening', body).then(function (d) {
    _saveEnergyLogFromEveningForm();
    loadRituelenSection();
    afterRitualSaved();
    return d;
  }).catch(function (e) { alert(e.message); });
}

// De Sparringpartner leest dit terug via /api/coach — apart van het ritueel
// zelf opgeslagen (backend/domains/coach/coach_energy_log) zodat één dag
// meerdere activiteiten kan dragen i.p.v. één vrij tekstveld.
function _saveEnergyLogFromEveningForm() {
  var gainsEl = document.getElementById('rit-e-energy-gains');
  var costsEl = document.getElementById('rit-e-energy-costs');
  if (!gainsEl || !costsEl) return;
  var gains = gainsEl.value.split('\n').map(function (s) { return s.trim(); }).filter(Boolean);
  var costs = costsEl.value.split('\n').map(function (s) { return s.trim(); }).filter(Boolean);
  var entries = gains.map(function (activity) { return { activity: activity, direction: 'gain' }; })
    .concat(costs.map(function (activity) { return { activity: activity, direction: 'cost' }; }));
  if (!entries.length) return;
  var today = new Date().toISOString().slice(0, 10);
  post('/api/coach/energy-log', { date: today, entries: entries }).catch(function (e) {
    console.error('[coach energy-log]', e);
  });
}

// ── Weekstart / Weekreview ──────────────────────────────────────────
function showWeeklyStartForm(inGate) {
  var el = document.getElementById('rituelen-panel');
  if (!el) return;
  el.innerHTML = '<div class="loading"><div class="spinner"></div>Laden...</div>';
  fetch('/api/rituals/weekly-start').then(function (r) { return r.json(); }).then(function (d) {
    d = d || {};
    var goals = d.main_goals && d.main_goals.length ? d.main_goals : ['', '', ''];
    el.innerHTML =
      (inGate ? '' : _ritualPanelHeader('🧭', 'Weekstart')) +
      '<div class="ritual-body">' +
        '<div class="ritual-section">' +
          '<div class="ritual-section-label">Richting voor deze week</div>' +
          _ritField('rit-ws-intentie', 'Weekintentie', d.week_intention, true) +
          '<div class="ritual-field"><label>Hoofddoelen (3-5)</label>' +
          '<div id="rit-ws-goals" class="ritual-list">' +
          goals.map(function (g, i) {
            return '<div class="ritual-list-item"><span class="ritual-list-num">' + (i + 1) + '</span>' +
              '<input class="rit-ws-goal" value="' + escAttr(g || '') + '" placeholder="Doel ' + (i + 1) + '"></div>';
          }).join('') + '</div></div>' +
        '</div>' +
        '<div class="ritual-section">' +
          '<div class="ritual-section-label">Leren &amp; risico</div>' +
          _ritField('rit-ws-leer', 'Leerdoel', d.learning_goal, false) +
          _ritField('rit-ws-obstakels', 'Verwachte obstakels', d.obstacles, true) +
          _ritField('rit-ws-succes', 'Waaraan zie ik dat het gelukt is?', d.success_metrics, false) +
        '</div>' +
      '</div>' +
      _ritualFooter('saveWeeklyStartForm', inGate);
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

function showWeeklyReviewForm(inGate) {
  var el = document.getElementById('rituelen-panel');
  if (!el) return;
  el.innerHTML = '<div class="loading"><div class="spinner"></div>Laden...</div>';
  fetch('/api/rituals/weekly-review').then(function (r) { return r.json(); }).then(function (d) {
    d = d || {};
    var wins = d.wins && d.wins.length ? d.wins.join(', ') : '';
    el.innerHTML =
      (inGate ? '' : _ritualPanelHeader('📊', 'Weekreview')) +
      '<div class="ritual-body">' +
        '<div class="ritual-section">' +
          '<div class="ritual-section-label">Terugblik op de week</div>' +
          _ritField('rit-wr-wins', 'Grootste overwinningen (komma-gescheiden)', wins, true) +
          _ritField('rit-wr-uitdaging', 'Wat ging niet zoals gepland?', d.challenges, true) +
          _ritField('rit-wr-lessen', 'Belangrijkste lessen', d.learnings, true) +
          '<div class="ritual-row2">' +
            _ritualScaleField('rit-wr-prod', 'Productiviteit', d.productivity_score, 7) +
            _ritualScaleField('rit-wr-energy', 'Energie', d.energy_score, 7) +
          '</div>' +
        '</div>' +
        '<div class="ritual-section">' +
          '<div class="ritual-section-label">Meenemen naar volgende week</div>' +
          _ritField('rit-wr-meenemen', 'Wat neem je mee?', d.carry_forward, true) +
          _ritField('rit-wr-loslaten', 'Wat laat je achter?', d.leave_behind, true) +
        '</div>' +
        '<div class="ritual-quality">' +
          '<div class="ritual-section-label">🎯 Tony Robbins Quality Questions</div>' +
          _ritField('rit-wr-gaf', 'Wat heb ik deze week GEGEVEN?', d.what_gave, true) +
          _ritField('rit-wr-leerde', 'Wat heb ik deze week GELEERD?', d.what_learned, true) +
          _ritField('rit-wr-bijdrage', 'Hoe heeft deze week bijgedragen?', d.how_contributed, true) +
          _ritField('rit-wr-beter', 'Hoe kan ik volgende week beter maken?', d.how_make_better, true) +
        '</div>' +
      '</div>' +
      _ritualFooter('saveWeeklyReviewForm', inGate);
  });
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
