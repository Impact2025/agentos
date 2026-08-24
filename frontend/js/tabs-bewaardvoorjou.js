// ── Impact OS — Bewaard voor Jou: bestellingen + inkoop-signalering
// Onderdeel van de SPA: klassieke scripts, gedeelde globale scope.
//
// Eén ophaal (/api/orders/dashboard): KPI's, voorraadstaat en config-status
// in één call, zodat "nog niet gekoppeld" nooit als een lege, rustige tabel
// oogt (config_state staat er expliciet bij). De bestellingenlijst is een
// aparte call (/api/orders) omdat die los filterbaar is.

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

async function renderBewaardVoorJouOrders(el) {
  el.innerHTML = '<div class="loading"><div class="spinner"></div><p>Bestellingen laden...</p></div>';
  var data;
  try {
    var resp = await fetch('/api/orders/dashboard');
    data = await resp.json();
  } catch (e) {
    el.innerHTML = '<div class="empty-state">Fout bij laden bestellingen: ' + escHtml(e.message) + '</div>';
    return;
  }

  if (data.config_state !== 'on') {
    var msg = data.config_state === 'partial'
      ? 'Bestellingen-koppeling staat half ingesteld — controleer BEWAARDVOORJOU_ORDERS_URL en BEWAARDVOORJOU_ORDERS_KEY in .env.'
      : 'Bestellingen-koppeling met life-journey-backend is nog niet ingesteld (BEWAARDVOORJOU_ORDERS_URL/BEWAARDVOORJOU_ORDERS_KEY in .env).';
    el.innerHTML = '<div class="section-card" style="margin-bottom:14px"><p style="font-size:12px;color:var(--text-dim);margin:0">' + escHtml(msg) + '</p></div>';
    return;
  }

  var html = '<div class="section-card" style="margin-bottom:14px">';
  html += '<h3 style="font-size:13px;font-weight:700;color:var(--text);margin:0 0 10px">Bestellingen</h3>';
  html += '<div style="display:flex;gap:10px;flex-wrap:wrap;margin-bottom:14px">';
  html += bvjTegel('Orders (betaald)', data.kpi.orders_totaal, '');
  html += bvjTegel('Omzet', bvjEur(data.kpi.omzet_cents), '');
  html += bvjTegel('Wacht op fulfillment', data.kpi.pending_fulfillment, '');
  html += '</div>';

  // Voorraad
  html += '<h4 style="font-size:12px;font-weight:700;color:var(--text);margin:14px 0 8px">Voorraad</h4>';
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
        '<td style="padding:7px 8px"><button type="button" class="btn btn-sm" onclick="bvjEditStock(\'' + escAttr(r.item) + '\', ' + r.on_hand + ')">Bijwerken</button></td>' +
        '</tr>';
    });
    html += '</tbody></table></div>';
  }

  html += '<div style="margin-top:12px;display:flex;gap:8px;align-items:center">' +
    '<button type="button" class="btn btn-sm btn-primary" onclick="bvjSyncOrders(this)">Nu verversen</button>' +
    '<span style="font-size:11px;color:var(--text-dim)">Laatste sync: ' + (data.last_sync ? escHtml(data.last_sync.slice(0, 16).replace('T', ' ')) : 'nog niet gesynchroniseerd') + '</span>' +
    '</div>';
  html += '</div>';

  // Bestellingenlijst
  html += '<div class="section-card" id="bvj-orders-list"><h3 style="font-size:13px;font-weight:700;color:var(--text);margin:0 0 10px">Recente bestellingen</h3><p style="font-size:12px;color:var(--text-dim)">Laden...</p></div>';

  el.innerHTML = html;
  _bvjLoadOrdersList();
}

function bvjTegel(label, waarde, hint) {
  return '<div style="flex:1 1 150px;min-width:150px;background:var(--card-bg);border:1px solid var(--card-border);border-radius:var(--radius-md);padding:12px 14px">' +
    '<div style="font-size:10px;text-transform:uppercase;letter-spacing:.06em;color:var(--text-dim);font-weight:600">' + escHtml(label) + '</div>' +
    '<div style="font-size:20px;font-weight:650;margin-top:4px">' + waarde + '</div>' +
    (hint ? '<div style="font-size:11px;color:var(--text-dim);margin-top:2px">' + hint + '</div>' : '') + '</div>';
}

async function _bvjLoadOrdersList() {
  var host = document.getElementById('bvj-orders-list');
  if (!host) return;
  try {
    var resp = await fetch('/api/orders?limit=50');
    var data = await resp.json();
    if (!data.orders || !data.orders.length) {
      host.innerHTML = '<h3 style="font-size:13px;font-weight:700;color:var(--text);margin:0 0 10px">Recente bestellingen</h3><p style="font-size:12px;color:var(--text-dim)">Nog geen bestellingen gesynchroniseerd.</p>';
      return;
    }
    var rows = data.orders.map(function (o) {
      return '<tr style="border-top:1px solid var(--card-border)">' +
        '<td style="padding:7px 8px">' + escHtml(o.status) + '</td>' +
        '<td style="padding:7px 8px">' + escHtml(o.package_type) + '</td>' +
        '<td style="padding:7px 8px">' + escHtml(o.recipient_name || '—') + '</td>' +
        '<td style="padding:7px 8px">' + bvjEur(o.price_paid) + '</td>' +
        '<td style="padding:7px 8px">' + bvjDatum(o.created_at) + '</td></tr>';
    }).join('');
    host.innerHTML = '<h3 style="font-size:13px;font-weight:700;color:var(--text);margin:0 0 10px">Recente bestellingen</h3>' +
      '<div style="overflow-x:auto"><table style="width:100%;border-collapse:collapse;font-size:12px">' +
      '<thead><tr style="text-align:left;color:var(--text-dim);font-size:10px;text-transform:uppercase;letter-spacing:.05em">' +
      '<th style="padding:6px 8px">Status</th><th style="padding:6px 8px">Pakket</th><th style="padding:6px 8px">Ontvanger</th>' +
      '<th style="padding:6px 8px">Bedrag</th><th style="padding:6px 8px">Datum</th></tr></thead><tbody>' + rows + '</tbody></table></div>';
  } catch (e) {
    host.innerHTML = '<p style="font-size:12px;color:var(--red)">Fout bij laden: ' + escHtml(e.message) + '</p>';
  }
}

async function bvjSyncOrders(btn) {
  if (btn) { btn.disabled = true; btn.textContent = 'Bezig...'; }
  try {
    await fetch('/api/orders/sync', { method: 'POST' });
  } catch (e) { /* stil — de kaart hieronder toont de verse stand toch niet als het faalde */ }
  var host = document.getElementById('proj-bvj-orders');
  if (host) renderBewaardVoorJouOrders(host);
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
    var host = document.getElementById('proj-bvj-orders');
    if (host) renderBewaardVoorJouOrders(host);
  }).catch(function (e) { alert('Bijwerken mislukt: ' + e.message); });
}
