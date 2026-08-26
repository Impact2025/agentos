// ── Impact OS — Facturatie: bonnetjes, uren-conceptfacturen, debiteuren ──
// DigiBoox heeft geen API (nagezocht 25 aug 2026): bonnetjes gaan automatisch
// per mail naar DigiBoox' eigen OCR-adres, facturen/debiteuren blijven één
// handmatige import-klik in DigiBoox zelf. Niets hier verstuurt of exporteert
// zonder een expliciete klik — zelfde Wachtrij-gate als content en outreach.

function renderFacturatieTab(el) {
  el.innerHTML = '<div class="loading"><div class="spinner"></div><p>Facturatie laden...</p></div>';
  facturatieLoad(el);
}

function facturatieLoad(el) {
  Promise.all([
    fetch('/api/billing/receipts').then(function (r) { return r.json(); }),
    fetch('/api/billing/invoices').then(function (r) { return r.json(); }),
    fetch('/api/billing/debtors').then(function (r) { return r.json(); }),
    fetch('/api/billing/reminders?status=review').then(function (r) { return r.json(); }),
  ]).then(function (res) {
    facturatieRender(el, res[0] || [], res[1] || [], res[2] || {}, res[3] || []);
  }).catch(function (e) {
    el.innerHTML = '<div class="empty-state">Facturatie laden mislukt: ' + escHtml(e.message) + '</div>';
  });
}

function fbEur(cents) {
  return '€' + (Number(cents || 0) / 100).toLocaleString('nl-NL', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function facturatieRender(el, receipts, invoices, debtors, reminders) {
  var html = '<div class="project-header"><div><h1>Facturatie</h1>' +
    '<p class="meta">Bonnetjes, uren naar factuur, debiteurenbeheer — DigiBoox importeert het laatste stapje zelf</p></div></div>';

  // ── Bonnetjes ──────────────────────────────────────────────────────────
  html += '<div class="section-card" style="margin-bottom:16px">';
  html += '<div style="display:flex;align-items:center;justify-content:space-between">' +
    '<h3 style="font-size:13px;font-weight:700;color:var(--text)">Bonnetjes & inkoopfacturen (' + receipts.length + ')</h3>' +
    '<label class="btn btn-primary btn-sm" style="cursor:pointer">Upload bonnetje' +
    '<input type="file" id="fb-receipt-input" style="display:none" onchange="fbUploadReceipt(this)"></label></div>';
  if (!receipts.length) {
    html += '<p style="color:#64748b;font-size:12px;margin-top:8px">Nog niets geupload.</p>';
  } else {
    html += receipts.map(fbReceiptRow).join('');
  }
  html += '</div>';

  // ── Uren -> factuur ────────────────────────────────────────────────────
  html += '<div class="section-card" style="margin-bottom:16px">';
  html += '<div style="display:flex;align-items:center;justify-content:space-between">' +
    '<h3 style="font-size:13px;font-weight:700;color:var(--text)">Conceptfacturen (' + invoices.length + ')</h3>' +
    '<button onclick="fbNewInvoiceForm()" class="btn btn-primary btn-sm">Genereer uit agenda-uren</button></div>';
  html += '<div id="fb-invoice-new-host"></div>';
  if (!invoices.length) {
    html += '<p style="color:#64748b;font-size:12px;margin-top:8px">Nog geen conceptfacturen.</p>';
  } else {
    html += invoices.map(fbInvoiceCard).join('');
  }
  html += '</div>';

  // ── Debiteuren ─────────────────────────────────────────────────────────
  html += '<div class="section-card" style="margin-bottom:16px">';
  html += '<div style="display:flex;align-items:center;justify-content:space-between">' +
    '<h3 style="font-size:13px;font-weight:700;color:var(--text)">Debiteuren</h3>' +
    '<label class="btn btn-ghost btn-sm" style="cursor:pointer">Importeer export' +
    '<input type="file" accept=".csv" id="fb-debtor-input" style="display:none" onchange="fbUploadDebtors(this)"></label></div>';
  if (!debtors.snapshot) {
    html += '<p style="color:#64748b;font-size:12px;margin-top:8px">Nog geen debiteuren-export geimporteerd. Exporteer de openstaande-postenlijst uit DigiBoox en importeer \'m hier.</p>';
  } else {
    var staleBadge = debtors.is_stale
      ? '<span style="font-size:10px;color:#b91c1c;background:#fef2f2;border-radius:999px;padding:2px 8px;margin-left:6px">verouderd (' + debtors.stale_days + ' dagen)</span>'
      : '<span style="font-size:10px;color:#166534;background:#f0fdf4;border-radius:999px;padding:2px 8px;margin-left:6px">vers (' + debtors.stale_days + ' dagen)</span>';
    html += '<p style="font-size:12px;color:var(--text-dim);margin-top:8px">' + escHtml(debtors.snapshot.filename) +
      ' — ' + debtors.snapshot.row_count + ' openstaande posten' + staleBadge + '</p>';
    html += '<button onclick="fbGenerateReminders()" class="btn btn-sm" style="margin-top:6px">Genereer herinneringen</button>';
    html += '<table class="data-table" style="margin-top:10px"><thead><tr><th>Klant</th><th>Factuur</th><th>Vervaldatum</th><th>Bedrag</th></tr></thead><tbody>';
    (debtors.snapshot.rows || []).forEach(function (r) {
      html += '<tr><td>' + escHtml(r.client_name) + '</td><td>' + escHtml(r.invoice_number) + '</td>' +
        '<td>' + escHtml(r.due_date) + '</td><td>' + fbEur(r.amount_cents) + '</td></tr>';
    });
    html += '</tbody></table>';
  }
  html += '</div>';

  // ── Herinneringen ──────────────────────────────────────────────────────
  html += '<div class="section-card">';
  html += '<h3 style="font-size:13px;font-weight:700;color:var(--text)">Herinneringen klaar voor verzending (' + reminders.length + ')</h3>';
  if (!reminders.length) {
    html += '<p style="color:#64748b;font-size:12px;margin-top:8px">Niets openstaand.</p>';
  } else {
    html += reminders.map(fbReminderCard).join('');
  }
  html += '</div>';

  el.innerHTML = html;
}

function fbReceiptRow(r) {
  var badge = { nieuw: '#94a3b8', doorgestuurd: '#166534', mislukt: '#b91c1c' }[r.status] || '#94a3b8';
  var retry = r.status === 'mislukt'
    ? '<button onclick="fbRetryReceipt(\'' + r.id + '\')" class="btn btn-sm" style="margin-left:8px">Opnieuw</button>' : '';
  return '<div style="border-top:1px solid #f1f5f9;padding:8px 0;display:flex;align-items:center;justify-content:space-between">' +
    '<span style="font-size:12px;color:var(--text)">' + escHtml(r.filename) + '</span>' +
    '<span><span style="font-size:10px;color:' + badge + '">' + escHtml(r.status) + '</span>' + retry + '</span></div>' +
    (r.status === 'mislukt' && r.forward_error ? '<p style="font-size:11px;color:#b91c1c;margin:2px 0 0">' + escHtml(r.forward_error) + '</p>' : '');
}

function fbUploadReceipt(input) {
  var file = input.files[0];
  if (!file) return;
  var fd = new FormData();
  fd.append('file', file);
  fetch('/api/billing/receipts', { method: 'POST', body: fd })
    .then(function (r) { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); })
    .then(function () { var el = document.getElementById('tab-content'); if (el) facturatieLoad(el); })
    .catch(function (e) { alert('Upload mislukt: ' + e.message); });
}

function fbRetryReceipt(id) {
  post('/api/billing/receipts/' + id + '/retry', {}).then(function () {
    var el = document.getElementById('tab-content'); if (el) facturatieLoad(el);
  }).catch(function (e) { alert(e.message); });
}

function fbNewInvoiceForm() {
  var host = document.getElementById('fb-invoice-new-host');
  if (!host) return;
  if (host.innerHTML) { host.innerHTML = ''; return; }
  var today = new Date().toISOString().slice(0, 10);
  host.innerHTML = '<div style="margin:10px 0;padding:10px;border:1px dashed var(--card-border);border-radius:8px">' +
    '<div class="ritual-field"><label>Klantnaam (moet overeenkomen met je agenda-titels)</label>' +
    '<input id="fb-inv-client"></div>' +
    '<div style="display:flex;gap:8px">' +
    '<div class="ritual-field" style="flex:1"><label>Periode start</label><input id="fb-inv-start" type="date"></div>' +
    '<div class="ritual-field" style="flex:1"><label>Periode eind</label><input id="fb-inv-end" type="date" value="' + today + '"></div>' +
    '</div><div style="display:flex;gap:8px">' +
    '<div class="ritual-field" style="flex:1"><label>Uurtarief (EUR)</label><input id="fb-inv-rate" type="number" value="125"></div>' +
    '<div class="ritual-field" style="flex:1"><label>BTW%</label><input id="fb-inv-vat" type="number" value="21"></div>' +
    '</div><button onclick="fbGenerateInvoice()" class="btn btn-primary btn-sm">Genereer</button></div>';
}

function fbGenerateInvoice() {
  var client = document.getElementById('fb-inv-client').value.trim();
  var start = document.getElementById('fb-inv-start').value;
  var end = document.getElementById('fb-inv-end').value;
  if (!client || !start || !end) { alert('Vul klant, start- en einddatum in.'); return; }
  post('/api/billing/invoices/generate', {
    client_name: client, period_start: start, period_end: end,
    hourly_rate_cents: Math.round(Number(document.getElementById('fb-inv-rate').value || 0) * 100),
    vat_percent: Number(document.getElementById('fb-inv-vat').value || 21),
  }).then(function () {
    var el = document.getElementById('tab-content'); if (el) facturatieLoad(el);
  }).catch(function (e) { alert(e.message); });
}

function fbInvoiceCard(d) {
  var statusBadge = { concept: '#94a3b8', geexporteerd: '#166534' }[d.status] || '#94a3b8';
  var lines = (d.lines || []).map(function (l) {
    return '<tr style="' + (l.excluded ? 'opacity:.4;text-decoration:line-through' : '') + '">' +
      '<td>' + escHtml(l.description) + '</td><td>' + escHtml(l.event_date) + '</td><td>' + l.hours + ' u</td>' +
      (d.status === 'concept' ? '<td><button onclick="fbToggleLine(\'' + d.id + '\',\'' + l.id + '\',' + (!l.excluded) + ')" class="btn btn-sm">' +
        (l.excluded ? 'Insluiten' : 'Uitsluiten') + '</button></td>' : '<td></td>') + '</tr>';
  }).join('');
  var actions = d.status === 'concept'
    ? '<button onclick="fbApproveInvoice(\'' + d.id + '\')" class="btn btn-primary btn-sm">Keur goed & exporteer</button> ' +
      '<button onclick="fbDeleteInvoice(\'' + d.id + '\')" class="btn btn-ghost btn-sm">Verwerp</button>'
    : '<a href="/api/billing/invoices/' + d.id + '/export" class="btn btn-sm">Download CSV</a>';
  return '<div style="border-top:1px solid #f1f5f9;padding:10px 0">' +
    '<div style="display:flex;align-items:center;justify-content:space-between">' +
    '<span style="font-size:13px;font-weight:600;color:var(--text)">' + escHtml(d.client_name) + ' — ' + escHtml(d.period_start) + ' t/m ' + escHtml(d.period_end) + '</span>' +
    '<span style="font-size:10px;color:' + statusBadge + '">' + escHtml(d.status) + '</span></div>' +
    '<p style="font-size:12px;color:var(--text-dim);margin:4px 0">' + d.total_hours + ' uur — ' + fbEur(d.total_amount_cents) + '</p>' +
    '<table class="data-table"><thead><tr><th>Omschrijving</th><th>Datum</th><th>Uren</th><th></th></tr></thead><tbody>' + lines + '</tbody></table>' +
    '<div style="margin-top:8px">' + actions + '</div></div>';
}

function fbToggleLine(draftId, lineId, excluded) {
  post('/api/billing/invoices/' + draftId + '/lines/' + lineId, { excluded: excluded }).then(function () {
    var el = document.getElementById('tab-content'); if (el) facturatieLoad(el);
  });
}

function fbApproveInvoice(id) {
  if (!confirm('Factuur goedkeuren en CSV exporteren? Je importeert die zelf in DigiBoox.')) return;
  post('/api/billing/invoices/' + id + '/approve', {}).then(function () {
    var el = document.getElementById('tab-content'); if (el) facturatieLoad(el);
  }).catch(function (e) { alert(e.message); });
}

function fbDeleteInvoice(id) {
  if (!confirm('Conceptfactuur verwijderen?')) return;
  fetch('/api/billing/invoices/' + id, { method: 'DELETE' }).then(function () {
    var el = document.getElementById('tab-content'); if (el) facturatieLoad(el);
  });
}

function fbUploadDebtors(input) {
  var file = input.files[0];
  if (!file) return;
  var fd = new FormData();
  fd.append('file', file);
  fetch('/api/billing/debtors/import', { method: 'POST', body: fd })
    .then(function (r) { return r.json().then(function (d) { if (!r.ok) throw new Error(d.detail || 'HTTP ' + r.status); return d; }); })
    .then(function () { var el = document.getElementById('tab-content'); if (el) facturatieLoad(el); })
    .catch(function (e) { alert('Import mislukt: ' + e.message); });
}

function fbGenerateReminders() {
  post('/api/billing/reminders/generate', {}).then(function (rows) {
    var el = document.getElementById('tab-content'); if (el) facturatieLoad(el);
    if (!rows.length) alert('Geen nieuwe herinneringen nodig (alles binnen de betaaltermijn of al gemaakt).');
  }).catch(function (e) { alert(e.message); });
}

function fbReminderCard(r) {
  var toneColor = { vriendelijk: '#0369a1', dringend: '#c2410c', aanmaning: '#b91c1c' }[r.tone] || '#64748b';
  return '<div style="border-top:1px solid #f1f5f9;padding:10px 0">' +
    '<div style="display:flex;align-items:center;justify-content:space-between">' +
    '<span style="font-size:13px;font-weight:600;color:var(--text)">' + escHtml(r.client_name) + '</span>' +
    '<span style="font-size:10px;color:' + toneColor + '">' + escHtml(r.tone) + ' — ' + r.days_overdue + ' dagen te laat</span></div>' +
    '<p style="font-size:12px;color:var(--text-dim);margin:4px 0">' + escHtml(r.subject) + '</p>' +
    '<pre style="font-size:11px;white-space:pre-wrap;background:var(--card-bg-alt,#f8fafc);padding:8px;border-radius:6px">' + escHtml(r.draft) + '</pre>' +
    '<button onclick="fbSendReminder(\'' + r.id + '\')" class="btn btn-primary btn-sm">Verstuur</button> ' +
    '<button onclick="fbSkipReminder(\'' + r.id + '\')" class="btn btn-ghost btn-sm">Sla over</button></div>';
}

function fbSendReminder(id) {
  if (!confirm('Deze herinnering wordt ECHT verstuurd. Doorgaan?')) return;
  post('/api/billing/reminders/' + id + '/send', {}).then(function () {
    var el = document.getElementById('tab-content'); if (el) facturatieLoad(el);
  }).catch(function (e) { alert(e.message); });
}

function fbSkipReminder(id) {
  post('/api/billing/reminders/' + id + '/skip', {}).then(function () {
    var el = document.getElementById('tab-content'); if (el) facturatieLoad(el);
  });
}
