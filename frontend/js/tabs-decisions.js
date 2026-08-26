// ── Impact OS — Besluiten: openstaande keuzes die om een besluit vragen ──
// WeAreImpact-only, zelfde gating-patroon als Agenda/Verkoop (core.js:visibleTabs).

function renderBesluitenTab(el) {
  el.innerHTML = '<div class="loading"><div class="spinner"></div><p>Besluiten laden...</p></div>';
  besluitenLoad(el);
}

function besluitenLoad(el) {
  var proj = encodeURIComponent(currentProject);
  Promise.all([
    fetch('/api/decisions?project=' + proj + '&status=open').then(function (r) { return r.json(); }),
    fetch('/api/decisions?project=' + proj + '&status=besloten').then(function (r) { return r.json(); }),
  ]).then(function (res) {
    besluitenRender(el, res[0].decisions || [], res[1].decisions || []);
  }).catch(function (e) {
    el.innerHTML = '<div class="empty-state">Besluiten laden mislukt: ' + escHtml(e.message) + '</div>';
  });
}

function besluitenRender(el, open, resolved) {
  var html = '<div class="project-header"><div><h1>Besluiten</h1>' +
    '<p class="meta">' + escHtml(currentProject) + ' · wat om een keuze vraagt, en wat al gekozen is</p></div>' +
    '<div class="actions"><button onclick="besluitenNewForm()" class="btn btn-primary">Nieuw besluit</button></div></div>';

  html += '<div id="besluiten-new-host"></div>';

  html += '<div class="section-card" style="margin-bottom:16px">';
  html += '<h3 style="font-size:13px;font-weight:700;color:var(--text);margin-bottom:10px">Open — vraagt om een besluit (' + open.length + ')</h3>';
  if (!open.length) {
    html += '<p style="color:#64748b;font-size:12px">Niets openstaand. Klik "Nieuw besluit" als er iets speelt dat een keuze vraagt.</p>';
  } else {
    html += open.map(besluitenOpenCard).join('');
  }
  html += '</div>';

  html += '<div class="section-card">';
  html += '<h3 style="font-size:13px;font-weight:700;color:var(--text);margin-bottom:10px">Besloten (' + resolved.length + ')</h3>';
  if (!resolved.length) {
    html += '<p style="color:#64748b;font-size:12px">Nog niets afgerond.</p>';
  } else {
    html += resolved.map(besluitenResolvedCard).join('');
  }
  html += '</div>';

  el.innerHTML = html;
}

function besluitenOpenCard(d) {
  var deadline = d.deadline ? '<span style="font-size:10px;color:#c2410c;background:#fff7ed;border-radius:999px;padding:2px 8px;margin-left:6px">deadline ' + escHtml(d.deadline) + '</span>' : '';
  var opties = (d.options && d.options.length)
    ? '<p style="font-size:11px;color:#94a3b8;margin:4px 0 0">Opties: ' + d.options.map(escHtml).join(' · ') + '</p>'
    : '';
  return '<div style="border-top:1px solid #f1f5f9;padding:10px 0">' +
    '<div style="display:flex;align-items:center;justify-content:space-between;gap:8px">' +
    '<p style="margin:0;font-size:13px;font-weight:600;color:var(--text)">' + escHtml(d.title) + deadline + '</p>' +
    '<button onclick="besluitenResolveForm(' + d.id + ')" class="btn btn-sm btn-primary">Neem besluit</button>' +
    '</div>' +
    (d.context ? '<p style="margin:4px 0 0;font-size:12px;color:var(--text-dim)">' + escHtml(d.context) + '</p>' : '') +
    opties +
    '<div id="besluit-resolve-host-' + d.id + '"></div>' +
    '</div>';
}

function besluitenResolvedCard(d) {
  return '<div style="border-top:1px solid #f1f5f9;padding:10px 0">' +
    '<p style="margin:0;font-size:13px;font-weight:600;color:var(--text)">' + escHtml(d.title) + '</p>' +
    '<p style="margin:4px 0 0;font-size:12px;color:var(--ok-fg)">→ ' + escHtml(d.decision) + '</p>' +
    (d.reasoning ? '<p style="margin:2px 0 0;font-size:11px;color:#94a3b8">' + escHtml(d.reasoning) + '</p>' : '') +
    '<p style="margin:4px 0 0;font-size:10px;color:#94a3b8">Besloten ' + escHtml((d.decided_at || '').slice(0, 10)) +
    ' <a href="#" onclick="besluitenReopen(' + d.id + ');return false;" style="color:#94a3b8">heropenen</a></p>' +
    '</div>';
}

function besluitenNewForm() {
  var host = document.getElementById('besluiten-new-host');
  if (!host) return;
  host.innerHTML = '<div class="section-card" style="margin-bottom:16px">' +
    '<div class="ritual-field"><label>Waar moet je een besluit over nemen?</label>' +
    '<input id="besluit-new-title" placeholder="Bijv. Wel of niet doorgaan met project X"></div>' +
    '<div class="ritual-field"><label>Context — waarom speelt dit nu?</label>' +
    '<textarea id="besluit-new-context" rows="2"></textarea></div>' +
    '<div class="ritual-field"><label>Overwogen opties (optioneel, één per regel)</label>' +
    '<textarea id="besluit-new-options" rows="2"></textarea></div>' +
    '<div class="ritual-field"><label>Deadline (optioneel)</label>' +
    '<input id="besluit-new-deadline" type="date"></div>' +
    '<div style="display:flex;gap:8px;margin-top:8px">' +
    '<button onclick="besluitenSaveNew()" class="btn btn-primary btn-sm">Opslaan</button>' +
    '<button onclick="document.getElementById(\'besluiten-new-host\').innerHTML=\'\'" class="btn btn-ghost btn-sm">Annuleren</button>' +
    '</div></div>';
}

function besluitenSaveNew() {
  var title = document.getElementById('besluit-new-title').value.trim();
  if (!title) { alert('Vul in waar het besluit over gaat.'); return; }
  var options = document.getElementById('besluit-new-options').value.split('\n').map(function (s) { return s.trim(); }).filter(Boolean);
  post('/api/decisions', {
    project: currentProject,
    title: title,
    context: document.getElementById('besluit-new-context').value,
    options: options,
    deadline: document.getElementById('besluit-new-deadline').value,
  }).then(function () {
    var el = document.getElementById('tab-content');
    if (el) besluitenLoad(el);
  }).catch(function (e) { alert(e.message); });
}

function besluitenResolveForm(id) {
  var host = document.getElementById('besluit-resolve-host-' + id);
  if (!host) return;
  if (host.innerHTML) { host.innerHTML = ''; return; }
  host.innerHTML = '<div style="margin-top:8px;padding-top:8px;border-top:1px dashed #e2e8f0">' +
    '<div class="ritual-field"><label>Wat is het besluit?</label>' +
    '<input id="besluit-resolve-decision-' + id + '" placeholder="Wat kies je?"></div>' +
    '<div class="ritual-field"><label>Waarom (optioneel)</label>' +
    '<textarea id="besluit-resolve-reasoning-' + id + '" rows="2"></textarea></div>' +
    '<button onclick="besluitenSubmitResolve(' + id + ')" class="btn btn-primary btn-sm">Bevestigen</button>' +
    '</div>';
}

function besluitenSubmitResolve(id) {
  var decision = document.getElementById('besluit-resolve-decision-' + id).value.trim();
  if (!decision) { alert('Vul in wat er besloten is.'); return; }
  var reasoning = document.getElementById('besluit-resolve-reasoning-' + id).value;
  post('/api/decisions/' + id + '/resolve', { decision: decision, reasoning: reasoning }).then(function () {
    var el = document.getElementById('tab-content');
    if (el) besluitenLoad(el);
  }).catch(function (e) { alert(e.message); });
}

function besluitenReopen(id) {
  post('/api/decisions/' + id + '/reopen', {}).then(function () {
    var el = document.getElementById('tab-content');
    if (el) besluitenLoad(el);
  }).catch(function (e) { alert(e.message); });
}
