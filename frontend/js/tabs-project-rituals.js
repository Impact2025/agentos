// Impact OS — Rituelen-tab per klantproject (Fase 2 deel 2).
//
// Alleen zichtbaar op projecten met een gekoppeld bridge-token (project_bridge_tokens, zie
// visibleTabs() in core.js). Bewust READ-ONLY: dit toont wat een gekoppelde klant in zijn eigen
// mijn-ondernemers-os heeft ingevuld — Vincent bewerkt dat niet vanuit ImpactOS namens de klant,
// dat blijft bij de klant zelf. Hergebruikt dezelfde /api/rituals/*-routes als tabs-rituals.js
// (Vincents eigen, project-loze paneel op de home), nu met ?project= erbij.

async function renderProjectRitualsTab(el) {
  var project = currentProject;
  el.innerHTML = '<div class="loading"><div class="spinner"></div><p>Rituelen laden...</p></div>';

  var qp = '?project=' + encodeURIComponent(project);
  var results = await Promise.all([
    fetch('/api/rituals/morning' + qp).then(function (r) { return r.json(); }).catch(function () { return {}; }),
    fetch('/api/rituals/evening' + qp).then(function (r) { return r.json(); }).catch(function () { return {}; }),
    fetch('/api/rituals/weekly-start' + qp).then(function (r) { return r.json(); }).catch(function () { return {}; }),
    fetch('/api/rituals/weekly-review' + qp).then(function (r) { return r.json(); }).catch(function () { return {}; }),
    fetch('/api/rituals/wins' + qp + '&limit=5').then(function (r) { return r.json(); }).catch(function () { return []; }),
    fetch('/api/rituals/focus' + qp).then(function (r) { return r.json(); }).catch(function () { return []; }),
  ]);
  var morning = results[0] || {}, evening = results[1] || {}, weekStart = results[2] || {},
      weekReview = results[3] || {}, wins = results[4] || [], focus = results[5] || [];

  var doneChip = function (label, done) {
    return '<span class="pill ' + (done ? 'pill-success' : 'pill-neutral') + '" style="margin-right:6px">' +
      (done ? '✓ ' : '— ') + escHtml(label) + '</span>';
  };

  var html = '<div class="rituals-project-tab">' +
    '<div class="card" style="padding:16px;margin-bottom:12px">' +
    '<p style="font-size:12px;color:#94a3b8;margin-bottom:8px">Status deze week — alleen-lezen, ingevuld door de klant zelf</p>' +
    doneChip('Ochtend vandaag', !!morning.intentie) +
    doneChip('Avond vandaag', !!evening.whatWentWell) +
    doneChip('Weekstart', !!weekStart.weekIntention) +
    doneChip('Weekreview', !!(weekReview.wins && weekReview.wins.length)) +
    '</div>';

  if (morning.intentie) {
    html += '<div class="card" style="padding:16px;margin-bottom:12px">' +
      '<p style="font-size:12px;font-weight:600;text-transform:uppercase;letter-spacing:.04em;color:#94a3b8;margin-bottom:6px">Ochtend-intentie</p>' +
      '<p>' + escHtml(morning.intentie) + '</p>' +
      '</div>';
  }

  if (evening.whatWentWell || evening.biggestWin) {
    html += '<div class="card" style="padding:16px;margin-bottom:12px">' +
      '<p style="font-size:12px;font-weight:600;text-transform:uppercase;letter-spacing:.04em;color:#94a3b8;margin-bottom:6px">Avond-reflectie</p>' +
      (evening.whatWentWell ? '<p><strong>Wat ging goed:</strong> ' + escHtml(evening.whatWentWell) + '</p>' : '') +
      (evening.biggestWin ? '<p><strong>Grootste overwinning:</strong> ' + escHtml(evening.biggestWin) + '</p>' : '') +
      (typeof evening.energyLevel === 'number' ? '<p><strong>Energie:</strong> ' + evening.energyLevel + '/10</p>' : '') +
      '</div>';
  }

  var focusToday = (focus || []).filter(function (f) { return f.completed; });
  html += '<div class="card" style="padding:16px;margin-bottom:12px">' +
    '<p style="font-size:12px;font-weight:600;text-transform:uppercase;letter-spacing:.04em;color:#94a3b8;margin-bottom:6px">Focus-sessies</p>' +
    '<p>' + focusToday.length + ' van ' + (focus || []).length + ' voltooid</p>' +
    '</div>';

  html += '<div class="card" style="padding:16px">' +
    '<p style="font-size:12px;font-weight:600;text-transform:uppercase;letter-spacing:.04em;color:#94a3b8;margin-bottom:6px">Recente wins</p>';
  if (!wins.length) {
    html += '<p class="empty-state">Nog geen wins gelogd.</p>';
  } else {
    html += '<ul style="margin:0;padding-left:18px">' +
      wins.map(function (w) { return '<li>' + escHtml(w.title) + '</li>'; }).join('') +
      '</ul>';
  }
  html += '</div></div>';

  el.innerHTML = html;
}
