// ── Impact OS — Klanten (CRM): bedrijven, deals, taken ──────────────────
// Bouwt voort op de acquisitie-funnel (Leads-tab): een gewonnen lead krijgt
// hier automatisch een bedrijf + deal. Zie backend/domains/crm/service.py.

var CRM_STAGES = ['gesprek', 'voorstel', 'onderhandeling', 'gewonnen', 'verloren'];
var CRM_STAGE_LABELS = { gesprek: 'Gesprek', voorstel: 'Voorstel', onderhandeling: 'Onderhandeling', gewonnen: 'Gewonnen', verloren: 'Verloren' };

function renderKlantenTab(el) {
  el.innerHTML = '<div class="loading"><div class="spinner"></div><p>Klanten laden...</p></div>';
  crmLoad(el);
}

function crmLoad(el) {
  Promise.all([
    fetch('/api/crm/pipeline').then(function (r) { return r.json(); }),
    fetch('/api/crm/deals').then(function (r) { return r.json(); }),
    fetch('/api/crm/companies').then(function (r) { return r.json(); }),
    fetch('/api/crm/tasks?status=open').then(function (r) { return r.json(); }),
    fetch('/api/crm/tasks/overdue').then(function (r) { return r.json(); }),
    fetch('/api/quotes').then(function (r) { return r.json(); }),
    fetch('/api/notes').then(function (r) { return r.json(); }),
  ]).then(function (res) {
    crmRender(el, res[0] || {}, res[1] || [], res[2] || [], res[3] || [], res[4] || [], res[5] || [], res[6] || []);
  }).catch(function (e) {
    el.innerHTML = '<div class="empty-state">Klanten laden mislukt: ' + escHtml(e.message) + '</div>';
  });
}

function crmEur(cents) {
  return '€' + (Number(cents || 0) / 100).toLocaleString('nl-NL', { maximumFractionDigits: 0 });
}

function crmRender(el, pipeline, deals, companies, tasks, overdue, quotes, notes) {
  var html = '<div class="project-header"><div><h1>Klanten</h1>' +
    '<p class="meta">Bedrijven, deals en follow-ups — een gewonnen lead landt hier automatisch</p></div>' +
    '<div class="actions"><button onclick="crmNewCompanyForm()" class="btn btn-primary">Nieuw bedrijf</button> ' +
    '<button onclick="crmNewQuoteForm()" class="btn btn-primary">Nieuwe offerte</button> ' +
    '<button onclick="crmNewNoteForm()" class="btn btn-primary">Notulen toevoegen</button> ' +
    '<button onclick="crmNewTaskForm()" class="btn btn-ghost">Nieuwe taak</button></div></div>';

  // ── Pipeline-samenvatting ────────────────────────────────────────────
  html += '<div style="display:flex;gap:10px;flex-wrap:wrap;margin-bottom:16px">';
  CRM_STAGES.forEach(function (s) {
    var st = (pipeline.by_stage && pipeline.by_stage[s]) || { count: 0, value_cents: 0 };
    html += '<div style="flex:1 1 140px;min-width:140px;background:var(--card-bg);border:1px solid var(--card-border);border-radius:8px;padding:10px 12px">' +
      '<div style="font-size:10px;text-transform:uppercase;color:var(--text-dim)">' + CRM_STAGE_LABELS[s] + '</div>' +
      '<div style="font-size:18px;font-weight:650;color:var(--text)">' + st.count + '</div>' +
      '<div style="font-size:11px;color:var(--text-dim)">' + crmEur(st.value_cents) + '</div></div>';
  });
  html += '</div>';

  html += '<div id="crm-new-host"></div>';

  // ── Deals ────────────────────────────────────────────────────────────
  html += '<div class="section-card" style="margin-bottom:16px">';
  html += '<h3 style="font-size:13px;font-weight:700;color:var(--text)">Deals (' + deals.length + ')</h3>';
  if (!deals.length) {
    html += '<p style="color:#64748b;font-size:12px;margin-top:8px">Nog geen deals. Ze ontstaan automatisch zodra een lead in de Leads-tab op "gewonnen" gaat, of maak er hier zelf een aan.</p>';
  } else {
    html += deals.map(function (d) { return crmDealRow(d, companies); }).join('');
  }
  html += '</div>';

  // ── Offertes ─────────────────────────────────────────────────────────
  html += '<div class="section-card" style="margin-bottom:16px">';
  html += '<h3 style="font-size:13px;font-weight:700;color:var(--text)">Offertes (' + quotes.length + ')</h3>';
  html += '<p style="font-size:11px;color:var(--text-dim);margin-top:2px">Geen e-sign-koppeling — verstuur per mail en zet de beslissing zelf zodra de klant reageert.</p>';
  if (!quotes.length) {
    html += '<p style="color:#64748b;font-size:12px;margin-top:8px">Nog geen offertes.</p>';
  } else {
    html += quotes.map(crmQuoteRow).join('');
  }
  html += '</div>';

  // ── Notulen ──────────────────────────────────────────────────────────
  html += '<div class="section-card" style="margin-bottom:16px">';
  html += '<h3 style="font-size:13px;font-weight:700;color:var(--text)">Notulen (' + notes.length + ')</h3>';
  html += '<p style="font-size:11px;color:var(--text-dim);margin-top:2px">Plak een transcript (Teams/Zoom-export, dicteerapp, aantekeningen) — samenvatting + actiepunten als taken hierboven.</p>';
  if (!notes.length) {
    html += '<p style="color:#64748b;font-size:12px;margin-top:8px">Nog geen notulen.</p>';
  } else {
    html += notes.map(crmNoteRow).join('');
  }
  html += '</div>';

  // ── Taken ────────────────────────────────────────────────────────────
  html += '<div class="section-card" style="margin-bottom:16px">';
  html += '<h3 style="font-size:13px;font-weight:700;color:var(--text)">Openstaande taken (' + tasks.length + (overdue.length ? ', ' + overdue.length + ' over datum' : '') + ')</h3>';
  if (!tasks.length) {
    html += '<p style="color:#64748b;font-size:12px;margin-top:8px">Niets openstaand.</p>';
  } else {
    var overdueIds = {};
    overdue.forEach(function (t) { overdueIds[t.id] = true; });
    html += tasks.map(function (t) { return crmTaskRow(t, overdueIds[t.id]); }).join('');
  }
  html += '</div>';

  // ── Bedrijven ────────────────────────────────────────────────────────
  html += '<div class="section-card">';
  html += '<h3 style="font-size:13px;font-weight:700;color:var(--text)">Bedrijven (' + companies.length + ')</h3>';
  if (!companies.length) {
    html += '<p style="color:#64748b;font-size:12px;margin-top:8px">Nog geen bedrijven.</p>';
  } else {
    html += '<table class="data-table" style="margin-top:8px"><thead><tr><th>Naam</th><th>Stad</th><th>E-mail</th><th>Telefoon</th></tr></thead><tbody>' +
      companies.map(function (c) {
        return '<tr><td>' + escHtml(c.name) + '</td><td>' + escHtml(c.city || '') + '</td>' +
          '<td>' + escHtml(c.email || '') + '</td><td>' + escHtml(c.phone || '') + '</td></tr>';
      }).join('') + '</tbody></table>';
  }
  html += '</div>';

  el.innerHTML = html;
}

function crmDealRow(d, companies) {
  var company = companies.filter(function (c) { return c.id === d.company_id; })[0];
  var options = CRM_STAGES.map(function (s) {
    return '<option value="' + s + '"' + (s === d.stage ? ' selected' : '') + '>' + CRM_STAGE_LABELS[s] + '</option>';
  }).join('');
  return '<div style="border-top:1px solid #f1f5f9;padding:10px 0;display:flex;align-items:center;justify-content:space-between;gap:8px">' +
    '<div><p style="margin:0;font-size:13px;font-weight:600;color:var(--text)">' + escHtml(d.title) + '</p>' +
    '<p style="margin:2px 0 0;font-size:11px;color:var(--text-dim)">' + escHtml(company ? company.name : '') + ' — ' + crmEur(d.value_cents) + '</p></div>' +
    '<select onchange="crmChangeStage(\'' + d.id + '\', this.value)" style="font-size:12px">' + options + '</select></div>';
}

function crmChangeStage(dealId, stage) {
  post('/api/crm/deals/' + dealId + '/stage', { stage: stage }).then(function () {
    var el = document.getElementById('tab-content'); if (el) crmLoad(el);
  }).catch(function (e) { alert(e.message); });
}

function crmTaskRow(t, isOverdue) {
  return '<div style="border-top:1px solid #f1f5f9;padding:8px 0;display:flex;align-items:center;justify-content:space-between">' +
    '<div><span style="font-size:12px;color:var(--text)">' + escHtml(t.title) + '</span>' +
    (t.due_date ? '<span style="font-size:10px;margin-left:6px;color:' + (isOverdue ? '#b91c1c' : 'var(--text-dim)') + '">' + escHtml(t.due_date) + '</span>' : '') + '</div>' +
    '<button onclick="crmCompleteTask(\'' + t.id + '\')" class="btn btn-sm">Afvinken</button></div>';
}

function crmCompleteTask(id) {
  post('/api/crm/tasks/' + id + '/complete', {}).then(function () {
    var el = document.getElementById('tab-content'); if (el) crmLoad(el);
  });
}

function crmNewCompanyForm() {
  var host = document.getElementById('crm-new-host');
  if (!host) return;
  host.innerHTML = '<div class="section-card" style="margin-bottom:16px">' +
    '<div class="ritual-field"><label>Bedrijfsnaam</label><input id="crm-co-name"></div>' +
    '<div style="display:flex;gap:8px">' +
    '<div class="ritual-field" style="flex:1"><label>Stad</label><input id="crm-co-city"></div>' +
    '<div class="ritual-field" style="flex:1"><label>E-mail</label><input id="crm-co-email"></div>' +
    '</div><button onclick="crmSaveCompany()" class="btn btn-primary btn-sm">Opslaan</button> ' +
    '<button onclick="document.getElementById(\'crm-new-host\').innerHTML=\'\'" class="btn btn-ghost btn-sm">Annuleren</button></div>';
}

function crmSaveCompany() {
  var name = document.getElementById('crm-co-name').value.trim();
  if (!name) { alert('Vul een bedrijfsnaam in.'); return; }
  post('/api/crm/companies', {
    name: name,
    city: document.getElementById('crm-co-city').value,
    email: document.getElementById('crm-co-email').value,
  }).then(function () {
    var el = document.getElementById('tab-content'); if (el) crmLoad(el);
  }).catch(function (e) { alert(e.message); });
}

function crmNewTaskForm() {
  var host = document.getElementById('crm-new-host');
  if (!host) return;
  host.innerHTML = '<div class="section-card" style="margin-bottom:16px">' +
    '<div class="ritual-field"><label>Taak</label><input id="crm-task-title"></div>' +
    '<div class="ritual-field"><label>Streefdatum (optioneel)</label><input id="crm-task-due" type="date"></div>' +
    '<button onclick="crmSaveTask()" class="btn btn-primary btn-sm">Opslaan</button> ' +
    '<button onclick="document.getElementById(\'crm-new-host\').innerHTML=\'\'" class="btn btn-ghost btn-sm">Annuleren</button></div>';
}

function crmSaveTask() {
  var title = document.getElementById('crm-task-title').value.trim();
  if (!title) { alert('Vul een titel in.'); return; }
  post('/api/crm/tasks', {
    title: title,
    due_date: document.getElementById('crm-task-due').value,
  }).then(function () {
    var el = document.getElementById('tab-content'); if (el) crmLoad(el);
  }).catch(function (e) { alert(e.message); });
}

// ── Offertes ───────────────────────────────────────────────────────────

var CRM_QUOTE_STATUS_LABELS = { concept: 'Concept', verstuurd: 'Verstuurd', geaccepteerd: 'Geaccepteerd', afgewezen: 'Afgewezen' };
var CRM_QUOTE_STATUS_COLORS = { concept: '#94a3b8', verstuurd: '#0369a1', geaccepteerd: '#166534', afgewezen: '#b91c1c' };

function crmQuoteRow(q) {
  var actions = '<a href="/api/quotes/' + q.id + '/html" target="_blank" class="btn btn-sm">Bekijk / print naar PDF</a>';
  if (q.status === 'concept') {
    actions += ' <button onclick="crmSendQuote(\'' + q.id + '\')" class="btn btn-primary btn-sm">Verstuur</button>' +
      ' <button onclick="crmDeleteQuote(\'' + q.id + '\')" class="btn btn-ghost btn-sm">Verwijder</button>';
  } else if (q.status === 'verstuurd') {
    actions += ' <button onclick="crmDecideQuote(\'' + q.id + '\',\'geaccepteerd\')" class="btn btn-sm" style="color:#166534">Klant accepteerde</button>' +
      ' <button onclick="crmDecideQuote(\'' + q.id + '\',\'afgewezen\')" class="btn btn-sm" style="color:#b91c1c">Klant wees af</button>';
  }
  return '<div style="border-top:1px solid #f1f5f9;padding:10px 0;display:flex;align-items:center;justify-content:space-between;gap:8px;flex-wrap:wrap">' +
    '<div><p style="margin:0;font-size:13px;font-weight:600;color:var(--text)">' + escHtml(q.title) + '</p>' +
    '<p style="margin:2px 0 0;font-size:11px;color:var(--text-dim)">' + escHtml(q.client_name) + ' — ' + crmEur(q.total_cents) +
    ' <span style="color:' + (CRM_QUOTE_STATUS_COLORS[q.status] || '#64748b') + '">' + CRM_QUOTE_STATUS_LABELS[q.status] + '</span></p></div>' +
    '<div>' + actions + '</div></div>';
}

function crmNewQuoteForm() {
  var host = document.getElementById('crm-new-host');
  if (!host) return;
  if (host.innerHTML) { host.innerHTML = ''; return; }
  host.innerHTML = '<div class="section-card" style="margin-bottom:16px">' +
    '<div class="ritual-field"><label>Klantnaam</label><input id="crm-q-client"></div>' +
    '<div class="ritual-field"><label>Klant e-mail (voor verzenden)</label><input id="crm-q-email"></div>' +
    '<div class="ritual-field"><label>Titel offerte</label><input id="crm-q-title"></div>' +
    '<div class="ritual-field"><label>Inleidende tekst (optioneel)</label><textarea id="crm-q-intro" rows="2"></textarea></div>' +
    '<div class="ritual-field"><label>Regels — één per regel: omschrijving;aantal;prijs per stuk in euro</label>' +
    '<textarea id="crm-q-items" rows="3" placeholder="Strategiesessie;2;500\nRapportage;1;250"></textarea></div>' +
    '<div style="display:flex;gap:8px">' +
    '<div class="ritual-field" style="flex:1"><label>BTW%</label><input id="crm-q-vat" type="number" value="21"></div>' +
    '<div class="ritual-field" style="flex:1"><label>Geldig (dagen)</label><input id="crm-q-days" type="number" value="30"></div>' +
    '</div><button onclick="crmSaveQuote()" class="btn btn-primary btn-sm">Opslaan als concept</button> ' +
    '<button onclick="document.getElementById(\'crm-new-host\').innerHTML=\'\'" class="btn btn-ghost btn-sm">Annuleren</button></div>';
}

function crmSaveQuote() {
  var client = document.getElementById('crm-q-client').value.trim();
  var title = document.getElementById('crm-q-title').value.trim();
  var lines = document.getElementById('crm-q-items').value.split('\n').map(function (s) { return s.trim(); }).filter(Boolean);
  if (!client || !title || !lines.length) { alert('Vul klantnaam, titel en minstens één regel in.'); return; }
  var items;
  try {
    items = lines.map(function (line) {
      var parts = line.split(';').map(function (s) { return s.trim(); });
      if (parts.length !== 3) throw new Error('Regel "' + line + '" moet drie delen hebben: omschrijving;aantal;prijs');
      var qty = Number(parts[1]), price = Number(parts[2]);
      if (!parts[0] || isNaN(qty) || isNaN(price)) throw new Error('Regel "' + line + '" is ongeldig');
      return { description: parts[0], quantity: qty, unit_price_cents: Math.round(price * 100) };
    });
  } catch (e) { alert(e.message); return; }
  post('/api/quotes', {
    client_name: client,
    client_email: document.getElementById('crm-q-email').value,
    title: title,
    intro: document.getElementById('crm-q-intro').value,
    items: items,
    vat_percent: Number(document.getElementById('crm-q-vat').value || 21),
    valid_days: Number(document.getElementById('crm-q-days').value || 30),
  }).then(function () {
    var el = document.getElementById('tab-content'); if (el) crmLoad(el);
  }).catch(function (e) { alert(e.message); });
}

function crmSendQuote(id) {
  if (!confirm('Deze offerte wordt per mail verstuurd naar de klant. Doorgaan?')) return;
  post('/api/quotes/' + id + '/send', {}).then(function () {
    var el = document.getElementById('tab-content'); if (el) crmLoad(el);
  }).catch(function (e) { alert(e.message); });
}

function crmDecideQuote(id, status) {
  post('/api/quotes/' + id + '/decision', { status: status }).then(function () {
    var el = document.getElementById('tab-content'); if (el) crmLoad(el);
  }).catch(function (e) { alert(e.message); });
}

function crmDeleteQuote(id) {
  if (!confirm('Conceptofferte verwijderen?')) return;
  fetch('/api/quotes/' + id, { method: 'DELETE' }).then(function () {
    var el = document.getElementById('tab-content'); if (el) crmLoad(el);
  });
}

// ── Notulen ────────────────────────────────────────────────────────────

var CRM_NOTE_STATUS_LABELS = { nieuw: 'Wordt samengevat...', samengevat: 'Samengevat', mislukt: 'Mislukt' };
var CRM_NOTE_STATUS_COLORS = { nieuw: '#94a3b8', samengevat: '#166534', mislukt: '#b91c1c' };

function crmNoteRow(n) {
  var items = (n.action_items || []).map(function (a) {
    return '<li style="font-size:12px;color:var(--text)">' + escHtml(a.text) + '</li>';
  }).join('');
  return '<div style="border-top:1px solid #f1f5f9;padding:10px 0">' +
    '<div style="display:flex;align-items:center;justify-content:space-between">' +
    '<span style="font-size:13px;font-weight:600;color:var(--text)">' + escHtml(n.title) + '</span>' +
    '<span style="font-size:10px;color:' + (CRM_NOTE_STATUS_COLORS[n.status] || '#64748b') + '">' + (CRM_NOTE_STATUS_LABELS[n.status] || n.status) + '</span></div>' +
    (n.summary ? '<p style="font-size:12px;color:var(--text-dim);margin:4px 0">' + escHtml(n.summary) + '</p>' : '') +
    (items ? '<ul style="margin:4px 0 0 18px;padding:0">' + items + '</ul>' : '') +
    (n.status === 'mislukt' ? '<p style="font-size:11px;color:#b91c1c;margin:4px 0 0">Kon niet worden samengevat — probeer het opnieuw aan te maken.</p>' : '') +
    '</div>';
}

function crmNewNoteForm() {
  var host = document.getElementById('crm-new-host');
  if (!host) return;
  if (host.innerHTML) { host.innerHTML = ''; return; }
  host.innerHTML = '<div class="section-card" style="margin-bottom:16px">' +
    '<div class="ritual-field"><label>Titel</label><input id="crm-note-title" placeholder="Bijv. Kennismaking Acme BV"></div>' +
    '<div class="ritual-field"><label>Transcript / aantekeningen</label>' +
    '<textarea id="crm-note-transcript" rows="8" placeholder="Plak hier het transcript of je aantekeningen..."></textarea></div>' +
    '<button onclick="crmSaveNote()" id="crm-note-save-btn" class="btn btn-primary btn-sm">Samenvatten</button> ' +
    '<button onclick="document.getElementById(\'crm-new-host\').innerHTML=\'\'" class="btn btn-ghost btn-sm">Annuleren</button></div>';
}

function crmSaveNote() {
  var title = document.getElementById('crm-note-title').value.trim();
  var transcript = document.getElementById('crm-note-transcript').value.trim();
  if (!title || !transcript) { alert('Vul titel en transcript in.'); return; }
  var btn = document.getElementById('crm-note-save-btn');
  if (btn) { btn.disabled = true; btn.textContent = 'Bezig met samenvatten...'; }
  post('/api/notes', { title: title, transcript: transcript }).then(function () {
    var el = document.getElementById('tab-content'); if (el) crmLoad(el);
  }).catch(function (e) {
    alert(e.message);
    if (btn) { btn.disabled = false; btn.textContent = 'Samenvatten'; }
  });
}
