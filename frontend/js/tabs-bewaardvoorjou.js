// ── Impact OS — Bewaard voor Jou: Verkoop & Inkoop
// Onderdeel van de SPA: klassieke scripts, gedeelde globale scope.
//
// Eigen tab (i.p.v. een blok op het Dashboard — dat blok bestond tot 24 aug
// 2026 maar rendeerde nooit: de zichtbaarheidscheck testte `currentProject
// === 'BewaardVoorJou'` terwijl currentProject gespatieerd uit de URL-hash
// komt ("Bewaard voor Jou"). Zie core.js:isBewaardVoorJouProject).
//
// Twee ophaal-calls: /api/orders/analytics (KPI's, omzettrend, pakket-mix,
// status-verdeling, promo-gebruik, voorraad — één deterministische SQL-ronde,
// geen LLM) en /api/orders (de bestellingenlijst, apart omdat die los
// filterbaar/pagineerbaar is).

function bvjEur(cents) {
  if (cents === null || cents === undefined) return '—';
  return '€' + (Number(cents) / 100).toLocaleString('nl-NL', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}
function bvjDatum(s) {
  if (!s) return '—';
  return String(s).slice(0, 10);
}
var _bvjStockPillMap = { tekort_nu: 'pill-danger', onder_drempel: 'pill-warn', ok: 'pill-ok' };
var _bvjStockLabel = { tekort_nu: 'Tekort nu', onder_drempel: 'Onder drempel', ok: 'OK' };
var _bvjStatusPillMap = { PAID: 'pill-ok', FULFILLED: 'pill-ok', PENDING: 'pill-warn', CANCELLED: 'pill-danger', REFUNDED: 'pill-danger' };

function _bvjFulfillmentStap(o) {
  if (o.status !== 'PAID' && o.status !== 'FULFILLED') return '';
  if (o.shipped_at) {
    return '<span class="pill pill-ok">Verzonden ' + bvjDatum(o.shipped_at) + '</span>';
  }
  if (o.dagbesteding_sent_at) {
    return '<button type="button" class="btn btn-sm btn-primary" onclick="bvjMarkShipped(\'' + escAttr(o.id) + '\', this)">Verzonden door dagbesteding</button>' +
      '<div style="font-size:10px;color:var(--text-dim);margin-top:3px">Naar dagbesteding: ' + bvjDatum(o.dagbesteding_sent_at) + '</div>';
  }
  return '<button type="button" class="btn btn-sm btn-primary" onclick="bvjSendToDagbesteding(\'' + escAttr(o.id) + '\', this)">Maken &amp; versturen naar dagbesteding</button>';
}

function bvjTegel(label, waarde, hint) {
  return '<div style="flex:1 1 160px;min-width:160px;background:var(--card-bg);border:1px solid var(--card-border);border-radius:var(--radius-md);padding:12px 14px">' +
    '<div style="font-size:10px;text-transform:uppercase;letter-spacing:.06em;color:var(--text-dim);font-weight:600">' + escHtml(label) + '</div>' +
    '<div style="font-size:20px;font-weight:650;margin-top:4px">' + waarde + '</div>' +
    (hint ? '<div style="font-size:11px;color:var(--text-dim);margin-top:2px">' + hint + '</div>' : '') + '</div>';
}

async function renderVerkoopTab(el) {
  el.innerHTML = '<div class="loading"><div class="spinner"></div><p>Verkoop &amp; inkoop laden...</p></div>';
  var data;
  try {
    var resp = await fetch('/api/orders/analytics');
    data = await resp.json();
  } catch (e) {
    el.innerHTML = '<div class="empty-state">Fout bij laden: ' + escHtml(e.message) + '</div>';
    return;
  }

  if (data.config_state !== 'on') {
    var msg = data.config_state === 'partial'
      ? 'Bestellingen-koppeling staat half ingesteld — controleer BEWAARDVOORJOU_ORDERS_URL en BEWAARDVOORJOU_ORDERS_KEY in .env.'
      : 'Bestellingen-koppeling met life-journey-backend is nog niet ingesteld (BEWAARDVOORJOU_ORDERS_URL/BEWAARDVOORJOU_ORDERS_KEY in .env).';
    el.innerHTML = '<div class="section-card"><p style="font-size:12px;color:var(--text-dim);margin:0">' + escHtml(msg) + '</p></div>';
    return;
  }

  var html = '<div id="verkoop-tab-root">';

  // ── KPI's ──
  html += '<div class="section-card" style="margin-bottom:14px">';
  html += '<div style="display:flex;gap:10px;flex-wrap:wrap">';
  html += bvjTegel('Orders (betaald)', data.orders_totaal, '');
  html += bvjTegel('Omzet', bvjEur(data.omzet_totaal_cents), '');
  html += bvjTegel('Gem. orderwaarde', bvjEur(data.gemiddelde_orderwaarde_cents), '');
  html += bvjTegel('Wacht op fulfillment', data.pending_fulfillment != null ? data.pending_fulfillment : '—', '');
  html += bvjTegel('Gem. fulfillment-tijd',
    data.fulfillment && data.fulfillment.gemiddelde_dagen != null ? data.fulfillment.gemiddelde_dagen + ' dag(en)' : '—',
    data.fulfillment ? ('op basis van ' + data.fulfillment.n + ' order(s)') : '');
  html += '</div>';
  html += '<div style="margin-top:12px;display:flex;gap:8px;align-items:center">' +
    '<button type="button" class="btn btn-sm btn-primary" onclick="bvjSyncOrders(this)">Nu verversen</button>' +
    '<span style="font-size:11px;color:var(--text-dim)">Laatste sync: ' + (data.last_sync ? escHtml(String(data.last_sync).slice(0, 16).replace('T', ' ')) : 'zie logboek') + '</span>' +
    '</div>';
  html += '</div>';

  // ── Omzet-trend (30 dagen) ──
  var heeftOmzetreeks = data.omzet_by_day && data.omzet_by_day.length;
  html += '<div class="section-card" style="margin-bottom:14px">' +
    '<h3 style="font-size:13px;font-weight:700;color:var(--text);margin:0 0 10px">Omzet — laatste 30 dagen</h3>' +
    (heeftOmzetreeks
      ? '<div style="height:220px"><canvas id="verkoop-omzet-chart"></canvas></div>'
      : '<p style="font-size:12px;color:var(--text-dim)">Nog geen omzet in de laatste 30 dagen.</p>') +
    '</div>';

  // ── Status-verdeling + pakket-mix, naast elkaar ──
  html += '<div style="display:flex;gap:14px;flex-wrap:wrap;margin-bottom:14px">';

  html += '<div class="section-card" style="flex:1 1 320px;min-width:280px">' +
    '<h3 style="font-size:13px;font-weight:700;color:var(--text);margin:0 0 10px">Status-verdeling</h3>';
  if (!data.status_breakdown || !data.status_breakdown.length) {
    html += '<p style="font-size:12px;color:var(--text-dim)">Nog geen bestellingen.</p>';
  } else {
    html += '<table style="width:100%;border-collapse:collapse;font-size:12px">' +
      '<thead><tr style="text-align:left;color:var(--text-dim);font-size:10px;text-transform:uppercase;letter-spacing:.05em">' +
      '<th style="padding:6px 8px">Status</th><th style="padding:6px 8px">Aantal</th><th style="padding:6px 8px">Omzet</th></tr></thead><tbody>';
    data.status_breakdown.forEach(function (r) {
      var pill = '<span class="pill ' + (_bvjStatusPillMap[r.status] || 'pill-neutral') + '">' + escHtml(r.status || '—') + '</span>';
      html += '<tr style="border-top:1px solid var(--card-border)">' +
        '<td style="padding:7px 8px">' + pill + '</td>' +
        '<td style="padding:7px 8px">' + r.n + '</td>' +
        '<td style="padding:7px 8px">' + bvjEur(r.omzet_cents) + '</td></tr>';
    });
    html += '</tbody></table>';
  }
  html += '</div>';

  html += '<div class="section-card" style="flex:1 1 320px;min-width:280px">' +
    '<h3 style="font-size:13px;font-weight:700;color:var(--text);margin:0 0 10px">Pakket-mix (betaald)</h3>';
  if (!data.package_breakdown || !data.package_breakdown.length) {
    html += '<p style="font-size:12px;color:var(--text-dim)">Nog geen betaalde orders.</p>';
  } else {
    html += '<table style="width:100%;border-collapse:collapse;font-size:12px">' +
      '<thead><tr style="text-align:left;color:var(--text-dim);font-size:10px;text-transform:uppercase;letter-spacing:.05em">' +
      '<th style="padding:6px 8px">Pakket</th><th style="padding:6px 8px">Aantal</th><th style="padding:6px 8px">Omzet</th></tr></thead><tbody>';
    data.package_breakdown.forEach(function (r) {
      html += '<tr style="border-top:1px solid var(--card-border)">' +
        '<td style="padding:7px 8px">' + escHtml(r.package_type || '—') + '</td>' +
        '<td style="padding:7px 8px">' + r.n + '</td>' +
        '<td style="padding:7px 8px">' + bvjEur(r.omzet_cents) + '</td></tr>';
    });
    html += '</tbody></table>';
  }
  html += '</div>';
  html += '</div>';

  // ── Voorraad ──
  html += '<div class="section-card" style="margin-bottom:14px">';
  html += '<h3 style="font-size:13px;font-weight:700;color:var(--text);margin:0 0 10px">Voorraad</h3>';
  if (!data.voorraad || !data.voorraad.length) {
    html += '<p style="font-size:12px;color:var(--text-dim)">Nog geen voorraad ingevoerd.</p>';
  } else {
    html += '<div style="overflow-x:auto"><table style="width:100%;border-collapse:collapse;font-size:12px">' +
      '<thead><tr style="text-align:left;color:var(--text-dim);font-size:10px;text-transform:uppercase;letter-spacing:.05em">' +
      '<th style="padding:6px 8px">Item</th><th style="padding:6px 8px">Op voorraad</th>' +
      '<th style="padding:6px 8px">Nodig (open orders)</th><th style="padding:6px 8px">Drempel</th>' +
      '<th style="padding:6px 8px">Status</th><th style="padding:6px 8px"></th></tr></thead><tbody>';
    data.voorraad.forEach(function (r) {
      var pill = '<span class="pill ' + (_bvjStockPillMap[r.status] || 'pill-neutral') + '">' + (_bvjStockLabel[r.status] || r.status) + '</span>';
      html += '<tr style="border-top:1px solid var(--card-border)">' +
        '<td style="padding:7px 8px">' + escHtml(r.item) + '</td>' +
        '<td style="padding:7px 8px">' + r.on_hand + '</td>' +
        '<td style="padding:7px 8px">' + r.demand + '</td>' +
        '<td style="padding:7px 8px">' + r.min_qty + '</td>' +
        '<td style="padding:7px 8px">' + pill + '</td>' +
        '<td style="padding:7px 8px;white-space:nowrap">' +
        '<button type="button" class="btn btn-sm" onclick="bvjEditStock(\'' + escAttr(r.item) + '\', ' + r.on_hand + ')">Voorraad</button> ' +
        '<button type="button" class="btn btn-sm btn-ghost" onclick="bvjEditThreshold(\'' + escAttr(r.item) + '\', ' + r.min_qty + ')">Drempel</button> ' +
        '<button type="button" class="btn btn-sm btn-ghost" onclick="bvjEditLeverancier(\'' + escAttr(r.item) + '\', \'' + escAttr(r.order_url || '') + '\', ' + (r.reorder_qty || 0) + ', ' + (r.unit_cost_cents || 0) + ')">' +
        (r.order_url ? 'Leverancier ✓' : 'Leverancier') + '</button>' +
        '</td></tr>';
    });
    html += '</tbody></table></div>';
  }
  html += '</div>';

  // ── Inkoopvoorstellen ──
  html += '<div class="section-card" style="margin-bottom:14px" id="verkoop-inkoop-voorstellen"><h3 style="font-size:13px;font-weight:700;color:var(--text);margin:0 0 10px">Inkoopvoorstellen</h3><p style="font-size:12px;color:var(--text-dim)">Laden...</p></div>';

  // ── Promo-gebruik ──
  if (data.promo_usage && data.promo_usage.length) {
    html += '<div class="section-card" style="margin-bottom:14px">' +
      '<h3 style="font-size:13px;font-weight:700;color:var(--text);margin:0 0 10px">Promo-codes</h3>' +
      '<table style="width:100%;border-collapse:collapse;font-size:12px">' +
      '<thead><tr style="text-align:left;color:var(--text-dim);font-size:10px;text-transform:uppercase;letter-spacing:.05em">' +
      '<th style="padding:6px 8px">Code</th><th style="padding:6px 8px">Gebruikt</th><th style="padding:6px 8px">Korting totaal</th></tr></thead><tbody>' +
      data.promo_usage.map(function (r) {
        return '<tr style="border-top:1px solid var(--card-border)">' +
          '<td style="padding:7px 8px">' + escHtml(r.code) + '</td>' +
          '<td style="padding:7px 8px">' + r.n + '</td>' +
          '<td style="padding:7px 8px">' + bvjEur(r.korting_cents) + '</td></tr>';
      }).join('') + '</tbody></table></div>';
  }

  // ── Bestellingenlijst ──
  html += '<div class="section-card" id="verkoop-orders-list"><h3 style="font-size:13px;font-weight:700;color:var(--text);margin:0 0 10px">Recente bestellingen</h3><p style="font-size:12px;color:var(--text-dim)">Laden...</p></div>';

  html += '</div>';
  el.innerHTML = html;

  if (heeftOmzetreeks) {
    renderSeriesChart('verkoop-omzet-chart', data.omzet_by_day, 'omzet', 'Omzet (€)', '#d97706');
  }
  _bvjLoadOrdersList();
  _bvjLoadInkoopVoorstellen();
}

async function _bvjLoadInkoopVoorstellen() {
  var host = document.getElementById('verkoop-inkoop-voorstellen');
  if (!host) return;
  try {
    var resp = await fetch('/api/orders/inkoop/voorstellen');
    var data = await resp.json();
    var voorstellen = data.voorstellen || [];
    if (!voorstellen.length) {
      host.innerHTML = '<h3 style="font-size:13px;font-weight:700;color:var(--text);margin:0 0 10px">Inkoopvoorstellen</h3>' +
        '<p style="font-size:12px;color:var(--text-dim)">Geen openstaande inkoopvoorstellen — de voorraad dekt de vraag.</p>';
      return;
    }
    var rows = voorstellen.map(function (v) {
      var bestelBtn = v.order_url
        ? '<a class="btn btn-sm btn-primary" href="' + escAttr(v.order_url) + '" target="_blank" rel="noopener" onclick="bvjMarkOrdered(\'' + escAttr(v.id) + '\')">Bestel bij leverancier</a>'
        : '<button type="button" class="btn btn-sm btn-primary" onclick="bvjMarkOrdered(\'' + escAttr(v.id) + '\')">Ik heb besteld</button>';
      return '<tr style="border-top:1px solid var(--card-border)">' +
        '<td style="padding:7px 8px;font-weight:600">' + v.qty + 'x ' + escHtml(v.item) + '</td>' +
        '<td style="padding:7px 8px;color:var(--text-dim)">' + escHtml(v.reden) + '</td>' +
        '<td style="padding:7px 8px">' + (v.estimated_cost_cents != null ? bvjEur(v.estimated_cost_cents) : '—') + '</td>' +
        '<td style="padding:7px 8px;white-space:nowrap">' + bestelBtn + ' ' +
        '<button type="button" class="btn btn-sm btn-ghost" onclick="bvjNegeerVoorstel(\'' + escAttr(v.id) + '\')">Negeer</button></td></tr>';
    }).join('');
    host.innerHTML = '<h3 style="font-size:13px;font-weight:700;color:var(--text);margin:0 0 10px">Inkoopvoorstellen</h3>' +
      '<div style="overflow-x:auto"><table style="width:100%;border-collapse:collapse;font-size:12px">' +
      '<thead><tr style="text-align:left;color:var(--text-dim);font-size:10px;text-transform:uppercase;letter-spacing:.05em">' +
      '<th style="padding:6px 8px">Voorstel</th><th style="padding:6px 8px">Reden</th>' +
      '<th style="padding:6px 8px">Geschatte kosten</th><th style="padding:6px 8px"></th></tr></thead><tbody>' + rows + '</tbody></table></div>';
  } catch (e) {
    host.innerHTML = '<p style="font-size:12px;color:var(--red)">Fout bij laden: ' + escHtml(e.message) + '</p>';
  }
}

function bvjMarkOrdered(proposalId) {
  fetch('/api/orders/inkoop/voorstellen/' + encodeURIComponent(proposalId) + '/bestel', { method: 'POST' })
    .then(function () { _bvjLoadInkoopVoorstellen(); })
    .catch(function (e) { alert('Bijwerken mislukt: ' + e.message); });
}

function bvjNegeerVoorstel(proposalId) {
  if (!confirm('Dit inkoopvoorstel negeren?')) return;
  fetch('/api/orders/inkoop/voorstellen/' + encodeURIComponent(proposalId) + '/negeer', { method: 'POST' })
    .then(function () { _bvjLoadInkoopVoorstellen(); })
    .catch(function (e) { alert('Bijwerken mislukt: ' + e.message); });
}

function bvjEditLeverancier(item, currentUrl, currentQty, currentCostCents) {
  var url = prompt('Bestellink voor "' + item + '" (webshop-URL, leeg = geen link):', currentUrl || '');
  if (url === null) return;
  var qtyInput = prompt('Vast aantal om te bestellen bij een tekort (leeg = automatisch berekend):', currentQty || '');
  if (qtyInput === null) return;
  var costInput = prompt('Prijs per stuk in centen (optioneel, voor een kostenschatting):', currentCostCents || '');
  if (costInput === null) return;
  var qty = parseInt(qtyInput, 10); if (isNaN(qty) || qty < 0) qty = 0;
  var cost = parseInt(costInput, 10); if (isNaN(cost) || cost < 0) cost = 0;
  fetch('/api/orders/stock/' + encodeURIComponent(item) + '/leverancier', {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ order_url: url.trim(), reorder_qty: qty, unit_cost_cents: cost }),
  }).then(function () {
    var host = document.getElementById('tab-content');
    if (host && currentTab === 'Verkoop') renderVerkoopTab(host);
  }).catch(function (e) { alert('Bijwerken mislukt: ' + e.message); });
}

async function _bvjLoadOrdersList() {
  var host = document.getElementById('verkoop-orders-list');
  if (!host) return;
  try {
    var resp = await fetch('/api/orders?limit=50');
    var data = await resp.json();
    if (!data.orders || !data.orders.length) {
      host.innerHTML = '<h3 style="font-size:13px;font-weight:700;color:var(--text);margin:0 0 10px">Recente bestellingen</h3><p style="font-size:12px;color:var(--text-dim)">Nog geen bestellingen gesynchroniseerd.</p>';
      return;
    }
    var rows = data.orders.map(function (o) {
      var pill = '<span class="pill ' + (_bvjStatusPillMap[o.status] || 'pill-neutral') + '">' + escHtml(o.status || '—') + '</span>';
      return '<tr style="border-top:1px solid var(--card-border)">' +
        '<td style="padding:7px 8px">' + pill + '</td>' +
        '<td style="padding:7px 8px">' + escHtml(o.package_type) + '</td>' +
        '<td style="padding:7px 8px">' + escHtml(o.recipient_name || '—') + '</td>' +
        '<td style="padding:7px 8px">' + bvjEur(o.price_paid) + '</td>' +
        '<td style="padding:7px 8px">' + bvjDatum(o.created_at) + '</td>' +
        '<td style="padding:7px 8px;white-space:nowrap">' + _bvjFulfillmentStap(o) + '</td></tr>';
    }).join('');
    host.innerHTML = '<h3 style="font-size:13px;font-weight:700;color:var(--text);margin:0 0 10px">Recente bestellingen</h3>' +
      '<div style="overflow-x:auto"><table style="width:100%;border-collapse:collapse;font-size:12px">' +
      '<thead><tr style="text-align:left;color:var(--text-dim);font-size:10px;text-transform:uppercase;letter-spacing:.05em">' +
      '<th style="padding:6px 8px">Status</th><th style="padding:6px 8px">Pakket</th><th style="padding:6px 8px">Ontvanger</th>' +
      '<th style="padding:6px 8px">Bedrag</th><th style="padding:6px 8px">Datum</th><th style="padding:6px 8px">Fulfillment</th></tr></thead><tbody>' + rows + '</tbody></table></div>';
  } catch (e) {
    host.innerHTML = '<p style="font-size:12px;color:var(--red)">Fout bij laden: ' + escHtml(e.message) + '</p>';
  }
}

async function bvjSyncOrders(btn) {
  if (btn) { btn.disabled = true; btn.textContent = 'Bezig...'; }
  try {
    await fetch('/api/orders/sync', { method: 'POST' });
  } catch (e) { /* stil — de kaart hieronder toont de verse stand toch niet als het faalde */ }
  var host = document.getElementById('tab-content');
  if (host && currentTab === 'Verkoop') renderVerkoopTab(host);
}

function bvjEditStock(item, current) {
  var input = prompt('Nieuwe voorraad voor "' + item + '":', current);
  if (input === null) return;
  var n = parseInt(input, 10);
  if (isNaN(n) || n < 0) { alert('Voer een geldig aantal in.'); return; }
  fetch('/api/orders/stock/' + encodeURIComponent(item), {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ on_hand: n }),
  }).then(function () {
    var host = document.getElementById('tab-content');
    if (host && currentTab === 'Verkoop') renderVerkoopTab(host);
  }).catch(function (e) { alert('Bijwerken mislukt: ' + e.message); });
}

function _bvjPrintMateriaal(order, materiaal) {
  var w = window.open('', '_blank', 'width=480,height=640');
  if (!w) { alert('Pop-up geblokkeerd — sta pop-ups toe om de sticker/kaartjestekst te printen.'); return; }
  var html = '<!doctype html><html><head><meta charset="utf-8"><title>' + escHtml(materiaal.pakket) + ' — ' + escHtml(materiaal.ontvanger || order.id) + '</title>' +
    '<style>body{font-family:Arial,sans-serif;padding:24px;color:#111}' +
    '.blok{border:1px solid #ccc;border-radius:8px;padding:16px;margin-bottom:20px;white-space:pre-wrap}' +
    'h2{font-size:14px;text-transform:uppercase;letter-spacing:.05em;color:#666;margin:0 0 8px}' +
    '.sticker{font-size:16px;font-weight:600;line-height:1.4}' +
    '.kaartje{font-size:13px;line-height:1.5;font-style:italic}' +
    '@media print{button{display:none}}</style></head><body>' +
    '<h2>Adressticker — ' + escHtml(materiaal.pakket) + '</h2>' +
    '<div class="blok sticker">' + escHtml(materiaal.sticker) + '</div>' +
    '<h2>Tekst voor het kaartje</h2>' +
    '<div class="blok kaartje">' + (materiaal.kaartje ? escHtml(materiaal.kaartje) : '(geen tekst meegegeven bij de bestelling)') + '</div>' +
    '<button onclick="window.print()">Printen</button>' +
    '</body></html>';
  w.document.write(html);
  w.document.close();
}

async function bvjSendToDagbesteding(orderId, btn) {
  if (!confirm('Order naar de dagbesteding sturen om gemaakt te worden? Dit verbruikt usb/giftbox uit de voorraad.')) return;
  if (btn) { btn.disabled = true; btn.textContent = 'Bezig...'; }
  try {
    var resp = await fetch('/api/orders/' + encodeURIComponent(orderId) + '/dagbesteding/versturen', { method: 'POST' });
    var data = await resp.json();
    if (!resp.ok) { alert('Mislukt: ' + (data.detail || resp.status)); return; }
    _bvjPrintMateriaal(data.order, data.materiaal);
  } catch (e) {
    alert('Mislukt: ' + e.message);
  } finally {
    var host = document.getElementById('tab-content');
    if (host && currentTab === 'Verkoop') renderVerkoopTab(host);
  }
}

async function bvjMarkShipped(orderId, btn) {
  if (!confirm('Markeren dat de dagbesteding dit pakket heeft verzonden?')) return;
  if (btn) { btn.disabled = true; btn.textContent = 'Bezig...'; }
  try {
    var resp = await fetch('/api/orders/' + encodeURIComponent(orderId) + '/dagbesteding/verzonden', { method: 'POST' });
    var data = await resp.json();
    if (!resp.ok) { alert('Mislukt: ' + (data.detail || resp.status)); return; }
  } catch (e) {
    alert('Mislukt: ' + e.message);
  } finally {
    var host = document.getElementById('tab-content');
    if (host && currentTab === 'Verkoop') renderVerkoopTab(host);
  }
}

function bvjEditThreshold(item, current) {
  var input = prompt('Nieuwe veiligheidsdrempel voor "' + item + '" (waarschuwen zodra de voorraad na de openstaande vraag hieronder zakt):', current);
  if (input === null) return;
  var n = parseInt(input, 10);
  if (isNaN(n) || n < 0) { alert('Voer een geldig aantal in.'); return; }
  fetch('/api/orders/stock/' + encodeURIComponent(item) + '/threshold', {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ min_qty: n }),
  }).then(function () {
    var host = document.getElementById('tab-content');
    if (host && currentTab === 'Verkoop') renderVerkoopTab(host);
  }).catch(function (e) { alert('Bijwerken mislukt: ' + e.message); });
}
