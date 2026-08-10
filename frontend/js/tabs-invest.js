// ── Agent OS — Beursmeester-dashboard (het beleggingsbureau)
// Onderdeel van de SPA: klassieke scripts, gedeelde globale scope.
//
// Eén ophaal (/api/invest/dashboard), vier vensters. Dat het één call is, is
// geen optimalisatie maar een eis: haal je de panelen los op, dan staat de NAV
// van vóór de sync naast de posities van erná en spreken twee tabellen elkaar
// tegen zonder dat er iets stuk is.
//
// Drie weergaveregels die dit dashboard onderscheiden van een cijferbord:
//   - een ontbrekend cijfer is "—" mét de reden, nooit 0 (0 leest als oordeel);
//   - elke statistiek toont zijn n en zijn zeggingskracht — 100% trefkans over
//     twee posities is een steekproef van twee, geen prestatie;
//   - rendement staat nooit alleen: zonder de benchmark ernaast is "+4%" een
//     getal en geen resultaat.

var _beursTab = 'overzicht';
var _beursData = null;
var _beursChart = null;
var _beursTimer = null;

var BEURS_TABS = [
  { id: 'overzicht',   label: 'Overzicht' },
  { id: 'posities',    label: 'Posities & risico' },
  { id: 'trackrecord', label: 'Trackrecord' },
  { id: 'machine',     label: 'De machine' },
];

// ── Formatters ────────────────────────────────────────────────────────────
function bmEur(v, decimalen) {
  if (v === null || v === undefined) return '—';
  return '€' + Number(v).toLocaleString('nl-NL', {
    minimumFractionDigits: decimalen === undefined ? 0 : decimalen,
    maximumFractionDigits: decimalen === undefined ? 0 : decimalen,
  });
}
function bmPct(v, decimalen) {
  if (v === null || v === undefined) return '—';
  var d = decimalen === undefined ? 2 : decimalen;
  return (v > 0 ? '+' : '') + Number(v).toFixed(d) + '%';
}
function bmNum(v, decimalen) {
  if (v === null || v === undefined) return '—';
  return Number(v).toFixed(decimalen === undefined ? 2 : decimalen);
}
function bmKleur(v, omgekeerd) {
  if (v === null || v === undefined) return '#64748b';
  var goed = omgekeerd ? v <= 0 : v >= 0;
  return goed ? '#059669' : '#dc2626';
}
function bmDatum(s) { return (s || '').slice(0, 10); }

// KPI-tegel. `hint` is de regel eronder die het getal betekenis geeft — een
// waarde zonder maatstaf is geen managementinformatie maar een cijfer.
function bmTegel(label, waarde, hint, kleur, badge) {
  return '<div style="flex:1 1 170px;min-width:170px;background:#fff;border:1px solid #e2e8f0;border-radius:12px;padding:14px 16px">' +
    '<div style="display:flex;align-items:center;gap:6px">' +
    '<span style="font-size:10px;text-transform:uppercase;letter-spacing:.06em;color:#64748b;font-weight:600">' + escHtml(label) + '</span>' +
    (badge || '') + '</div>' +
    '<div style="font-size:24px;font-weight:650;margin-top:6px;letter-spacing:-.01em;color:' + (kleur || '#0f172a') + '">' + waarde + '</div>' +
    '<div style="font-size:11px;color:#64748b;margin-top:3px;line-height:1.4">' + (hint || '&nbsp;') + '</div></div>';
}
function bmBadge(tekst, tint) {
  var kleuren = { rood: ['#fef2f2', '#b91c1c'], oranje: ['#fffbeb', '#b45309'],
                  groen: ['#f0fdf4', '#15803d'], grijs: ['#f1f5f9', '#475569'] };
  var k = kleuren[tint] || kleuren.grijs;
  return '<span style="background:' + k[0] + ';color:' + k[1] + ';font-size:9px;font-weight:700;padding:2px 6px;border-radius:5px;text-transform:uppercase;letter-spacing:.04em">' + escHtml(tekst) + '</span>';
}
function bmKaart(titel, inhoud, subtitel) {
  return '<div style="background:#fff;border:1px solid #e2e8f0;border-radius:12px;padding:16px 18px;margin-bottom:14px">' +
    '<h3 style="font-size:13px;font-weight:700;color:#0f172a;margin:0 0 2px">' + titel + '</h3>' +
    (subtitel ? '<p style="font-size:11px;color:#64748b;margin:0 0 10px;line-height:1.45">' + subtitel + '</p>' : '<div style="height:10px"></div>') +
    inhoud + '</div>';
}
function bmLeeg(tekst) {
  return '<p style="font-size:12px;color:#64748b;padding:10px 0;margin:0">' + escHtml(tekst) + '</p>';
}
function bmTabel(koppen, rijen) {
  if (!rijen.length) return bmLeeg('Geen regels.');
  return '<div style="overflow-x:auto"><table style="width:100%;border-collapse:collapse;font-size:12px">' +
    '<thead><tr style="text-align:left;color:#64748b;font-size:10px;text-transform:uppercase;letter-spacing:.05em">' +
    koppen.map(function(k) { return '<th style="padding:6px 8px;font-weight:600;white-space:nowrap">' + k + '</th>'; }).join('') +
    '</tr></thead><tbody>' + rijen.join('') + '</tbody></table></div>';
}
function bmRij(cellen) {
  return '<tr style="border-top:1px solid #f1f5f9">' +
    cellen.map(function(c) { return '<td style="padding:7px 8px;white-space:nowrap">' + c + '</td>'; }).join('') + '</tr>';
}

// ── Entry point ───────────────────────────────────────────────────────────
function renderBeursmeester(el) {
  el.innerHTML = '<div id="beurs-root"><div class="loading"><div class="spinner"></div><p>Beursmeester laden…</p></div></div>';
  laadBeursmeester();
}

function laadBeursmeester(stil) {
  fetch('/api/invest/dashboard').then(function(r) {
    if (!r.ok) throw new Error('HTTP ' + r.status);
    return r.json();
  }).then(function(d) {
    _beursData = d;
    tekenBeursmeester();
  }).catch(function(e) {
    var root = document.getElementById('beurs-root');
    if (root && !stil) {
      root.innerHTML = '<div style="background:#fef2f2;border:1px solid #fecaca;border-radius:12px;padding:16px;font-size:13px;color:#b91c1c">' +
        'Beursmeester niet bereikbaar: ' + escHtml(e.message) + '</div>';
    }
  });
}

function beursSwitch(tab) {
  _beursTab = tab;
  tekenBeursmeester();
}

function tekenBeursmeester() {
  var root = document.getElementById('beurs-root');
  if (!root || !_beursData) return;
  var d = _beursData;
  if (_beursChart) { _beursChart.destroy(); _beursChart = null; }

  var h = beursKop(d) + beursAandacht(d) + beursKpis(d);
  h += '<div style="display:flex;gap:6px;margin:16px 0 14px;border-bottom:1px solid #e2e8f0">' +
    BEURS_TABS.map(function(t) {
      var actief = t.id === _beursTab;
      return '<button onclick="beursSwitch(\'' + t.id + '\')" style="padding:8px 14px;border:none;background:none;cursor:pointer;font-size:12px;font-weight:600;color:' +
        (actief ? '#0f172a' : '#64748b') + ';border-bottom:2px solid ' + (actief ? '#f43f5e' : 'transparent') + ';margin-bottom:-1px">' +
        t.label + '</button>';
    }).join('') + '</div>';

  if (_beursTab === 'overzicht') h += beursOverzicht(d);
  else if (_beursTab === 'posities') h += beursPosities(d);
  else if (_beursTab === 'trackrecord') h += beursTrackrecord(d);
  else h += beursMachine(d);

  root.innerHTML = h;
  if (_beursTab === 'overzicht') tekenKoerslijn(d.koerslijn);
}

// ── Kop ───────────────────────────────────────────────────────────────────
function beursKop(d) {
  var pf = d.portefeuille || {};
  var run = (d.rondes && d.rondes.rondes && d.rondes.rondes[0]) || null;
  var modeBadge = pf.mode === 'paper' ? bmBadge('papier', 'grijs') : bmBadge(pf.mode || '?', 'rood');
  return '<div style="display:flex;align-items:flex-start;justify-content:space-between;gap:16px;flex-wrap:wrap;margin-bottom:14px">' +
    '<div><h2 style="font-size:19px;font-weight:700;color:#0f172a;margin:0 0 3px;letter-spacing:-.01em">📈 Beursmeester ' + modeBadge + '</h2>' +
    '<p style="font-size:11px;color:#64748b;margin:0">Peildatum ' + escHtml(bmDatum(pf.peildatum) || '—') +
    ' · portefeuille sinds ' + escHtml((d.rendement || {}).sinds || '—') +
    (run ? ' · laatste ronde ' + escHtml(run.run_date) + ' (' + escHtml(run.denkwerk || '—') + ')' : ' · nog nooit gedraaid') + '</p></div>' +
    '<div style="display:flex;gap:8px">' +
    '<button onclick="laadBeursmeester()" style="padding:8px 14px;border:1px solid #e2e8f0;background:#fff;color:#475569;border-radius:8px;font-size:12px;font-weight:600;cursor:pointer">Ververs</button>' +
    '<button onclick="beursRondeNu(this)" style="padding:8px 16px;background:#f43f5e;color:#fff;border:none;border-radius:8px;font-size:12px;font-weight:600;cursor:pointer">Ronde nu draaien</button>' +
    '</div></div>';
}

// ── Aandachtspunten ───────────────────────────────────────────────────────
// De deterministische lijst uit analytics._aandachtspunten. Zonder deze strip
// moet een mens elf tabellen lezen om te zien of er iets aan de hand is — en
// dan wordt er niet gekeken.
function beursAandacht(d) {
  var punten = d.aandachtspunten || [];
  var halt = d.risico && !d.risico.mag_handelen;
  if (!punten.length && !halt) return '';
  var h = '';
  if (halt) {
    h += '<div style="background:#fef2f2;border:1px solid #fecaca;border-left:4px solid #dc2626;border-radius:10px;padding:12px 14px;margin-bottom:8px;display:flex;align-items:center;gap:10px;flex-wrap:wrap">' +
      '<span style="font-size:12px;font-weight:700;color:#b91c1c">🛑 Handel gepauzeerd</span>' +
      '<span style="font-size:12px;color:#7f1d1d;flex:1">' + escHtml((d.risico.redenen || []).join('; ')) + '</span>' +
      '<button onclick="beursHervatten(this)" style="padding:6px 12px;border:1px solid #dc2626;background:#fff;color:#dc2626;border-radius:7px;font-size:11px;font-weight:600;cursor:pointer">Hervat</button></div>';
  }
  punten.forEach(function(p) {
    var stijl = p.ernst === 'blokkerend' ? ['#fef2f2', '#fecaca', '#b91c1c', '⛔']
              : p.ernst === 'stil' ? ['#fffbeb', '#fde68a', '#b45309', '⚠']
              : ['#f8fafc', '#e2e8f0', '#475569', 'ℹ'];
    h += '<div style="background:' + stijl[0] + ';border:1px solid ' + stijl[1] + ';border-radius:10px;padding:10px 14px;margin-bottom:8px">' +
      '<div style="font-size:12px;font-weight:600;color:' + stijl[2] + '">' + stijl[3] + ' ' + escHtml(p.tekst) + '</div>' +
      '<div style="font-size:11px;color:#64748b;margin-top:2px;line-height:1.45">' + escHtml(p.waarom || '') + '</div></div>';
  });
  return h + '<div style="height:6px"></div>';
}

// ── KPI-rij ───────────────────────────────────────────────────────────────
function beursKpis(d) {
  var r = d.rendement || {}, pf = d.portefeuille || {}, tk = d.trefkans || {};
  var orisk = d.open_risico || {}, tr = d.trackrecord || {}, expo = d.blootstelling || {};
  var risicoMaten = (d.koerslijn && d.koerslijn.risico) || {};

  var h = '<div style="display:flex;gap:10px;flex-wrap:wrap">';

  h += bmTegel('Waarde', pf.volledig ? bmEur(pf.nav) : '—',
    pf.volledig ? bmEur(pf.cash) + ' cash · ' + bmNum(expo.belegd_pct, 0) + '% belegd'
                : 'NAV onvolledig — zie waarschuwing hierboven');

  h += bmTegel('Rendement', bmPct(r.rendement_pct),
    'sinds ' + escHtml(r.sinds || '—'), bmKleur(r.rendement_pct));

  h += bmTegel('Benchmark', bmPct(r.benchmark_pct),
    escHtml(r.benchmark_symbol || '') + ' — de meetlat', '#475569');

  h += bmTegel('Verschil', bmPct(r.alpha_pct),
    r.alpha_pct === null || r.alpha_pct === undefined ? 'nog niet te bepalen'
      : (r.alpha_pct >= 0 ? 'beter dan de index' : 'het geld had beter in de index gestaan'),
    bmKleur(r.alpha_pct));

  // Het cijfer dat zelden op een dashboard staat en er wél hoort: wat het kost
  // als élke stop vandaag raakt.
  h += bmTegel('Risico open', orisk.risico_pct_nav === null || orisk.risico_pct_nav === undefined ? '—' : bmNum(orisk.risico_pct_nav, 2) + '%',
    bmEur(orisk.risico_eur) + ' als alle stops raken' + (orisk.zonder_stop && orisk.zonder_stop.length ? ' · ' + orisk.zonder_stop.length + ' zonder stop' : ''),
    orisk.zonder_stop && orisk.zonder_stop.length ? '#b45309' : '#0f172a',
    orisk.volledig ? '' : bmBadge('onvolledig', 'oranje'));

  h += bmTegel('Terugval', d.risico && d.risico.drawdown_pct !== null && d.risico.drawdown_pct !== undefined ? bmNum(d.risico.drawdown_pct, 2) + '%' : '—',
    'vanaf de top · stop bij ' + bmNum(risicoMaten.grens_drawdown_pct || 20, 0) + '%',
    d.risico && d.risico.drawdown_pct > 10 ? '#dc2626' : '#0f172a');

  h += bmTegel('Verwachting', tr.n ? bmEur(tr.verwachting_eur, 0) : '—',
    tr.n ? 'per afgesloten idee · n=' + tr.n : 'nog geen afgesloten posities',
    tr.n ? bmKleur(tr.verwachting_eur) : '#64748b',
    tr.n ? bmBadge(tr.zeggingskracht, tr.zeggingskracht === 'betekenisvol' ? 'groen' : 'oranje') : '');

  h += bmTegel('Trefkans agent', tk.accuracy === null || tk.accuracy === undefined ? '—' : tk.accuracy + '%',
    (tk.correct || 0) + ' raak / ' + (tk.wrong || 0) + ' mis · ' + (tk.open || 0) + ' lopend',
    '#0f172a');

  return h + '</div>';
}

// ── Tab: overzicht ────────────────────────────────────────────────────────
function beursOverzicht(d) {
  var lijn = d.koerslijn || {};
  var risicoMaten = lijn.risico || {};
  var h = '';

  var grafiekSub = lijn.punten && lijn.punten.length
    ? 'Beide op 100 gezet op ' + escHtml(lijn.vanaf || '') + '. Herschalen is geen cosmetica: met twee assen kun je elke lijn laten winnen.'
      + (lijn.gaten ? ' <span style="color:#b45309">' + lijn.gaten + ' handelsdag(en) ontbreken in de reeks.</span>' : '')
    : 'Nog geen NAV-reeks — die groeit met één punt per ronde.';
  var grafiek = lijn.punten && lijn.punten.length > 1
    ? '<div style="height:260px;position:relative"><canvas id="beurs-koerslijn"></canvas></div>'
    : bmLeeg('Minimaal twee meetpunten nodig voor een koerslijn.');
  h += bmKaart('Portefeuille tegen de benchmark', grafiek, grafiekSub);

  // Risicomaten: bij te weinig punten of te veel gaten staat er een reden in
  // plaats van een cijfer. Een volatiliteit over acht dagen is ruis met een
  // decimaal erachter.
  var maten;
  if (risicoMaten.reden) {
    maten = '<p style="font-size:12px;color:#64748b;margin:0">Nog niet te berekenen: ' + escHtml(risicoMaten.reden) + '.</p>';
  } else {
    maten = '<div style="display:flex;gap:10px;flex-wrap:wrap">' +
      bmTegel('Volatiliteit', bmNum(risicoMaten.volatiliteit_pct, 1) + '%', 'op jaarbasis · ' + risicoMaten.meetpunten + ' meetpunten') +
      bmTegel('Grootste terugval', bmNum(risicoMaten.max_drawdown_pct, 2) + '%', 'diepste dal in de reeks') +
      bmTegel('Rendement per risico', bmNum(risicoMaten.sharpe, 2), risicoMaten.sharpe_voet || '') +
      '</div>';
  }
  h += bmKaart('Risicomaten', maten,
    'Berekend over de vastgelegde NAV-reeks. Dagen met een onvolledige NAV worden bewust niet vastgelegd — daarom telt het dashboard de gaten mee en zwijgt het liever dan te vleien.');

  // Voorstellen die op een mens wachten — de gate van dit domein.
  h += bmKaart('Wacht op jouw besluit (' + (d.voorstellen || []).length + ')',
    beursVoorstellen(d.voorstellen || []),
    'Dit is de enige weg naar een order. Bij goedkeuren draait de risicotoets opnieuw: tussen voorstel en klik kan een dag zitten.');

  return h;
}

function beursVoorstellen(items) {
  if (!items.length) return bmLeeg('Geen openstaande voorstellen. Nul is geen fout: een analist die niets vindt en dat zegt, is beter dan een die elke dag iets moet verzinnen.');
  var h = '';
  items.forEach(function(v) {
    var risicoPct = (v.ref_price && v.stop) ? ((v.ref_price - v.stop) / v.ref_price * 100) : null;
    var rr = (v.target && v.stop && v.ref_price && v.ref_price > v.stop)
      ? (v.target - v.ref_price) / (v.ref_price - v.stop) : null;
    h += '<div style="border:1px solid #e2e8f0;border-radius:10px;padding:12px 14px;margin-bottom:10px">' +
      '<div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">' +
      '<strong style="font-size:14px;color:#0f172a">' + escHtml((v.side || '').toUpperCase()) + ' ' + escHtml(v.symbol) + '</strong>' +
      bmBadge(v.asset_class || '?', 'grijs') +
      (v.confidence ? bmBadge('vertrouwen ' + v.confidence, 'grijs') : '') +
      (v.denkwerk === 'terugval' ? bmBadge('terugval', 'oranje') : bmBadge('claude code', 'groen')) +
      '<span style="margin-left:auto;font-size:11px;color:#64748b">koers ' + bmNum(v.ref_price) + ' van ' + escHtml(v.ref_date) + '</span></div>' +
      '<div style="display:flex;gap:18px;flex-wrap:wrap;margin:8px 0;font-size:11px;color:#475569">' +
      '<span>Stuks <strong>' + bmNum(v.qty, 4) + '</strong></span>' +
      '<span>Stop <strong>' + bmNum(v.stop) + '</strong>' + (risicoPct !== null ? ' (' + bmNum(risicoPct, 1) + '% eronder)' : '') + '</span>' +
      '<span>Doel <strong>' + bmNum(v.target) + '</strong>' + (rr !== null ? ' · verhouding ' + bmNum(rr, 1) + ':1' : '') + '</span>' +
      '<span>Horizon <strong>' + (v.horizon_days || '—') + ' dagen</strong></span></div>' +
      (v.thesis ? '<p style="font-size:12px;color:#334155;margin:6px 0;line-height:1.5">' + escHtml(v.thesis) + '</p>' : '') +
      (v.invalidation ? '<p style="font-size:11px;color:#b45309;margin:4px 0">Ongeldig als: ' + escHtml(v.invalidation) + '</p>' : '') +
      (v.risk_note ? '<p style="font-size:11px;color:#64748b;margin:4px 0">Risicotoets: ' + escHtml(v.risk_note) + '</p>' : '') +
      (v.backtest_ref ? '<p style="font-size:11px;color:#64748b;margin:4px 0">Backtest: <code style="font-size:10px">' + escHtml(v.backtest_ref) + '</code></p>'
                      : '<p style="font-size:11px;color:#b91c1c;margin:4px 0">Geen backtest-artefact.</p>') +
      '<div style="display:flex;gap:8px;margin-top:10px">' +
      '<button onclick="beursKeurGoed(this,\'' + escAttr(v.id) + '\',\'' + escAttr(v.symbol) + '\')" style="padding:7px 14px;background:#059669;color:#fff;border:none;border-radius:7px;font-size:12px;font-weight:600;cursor:pointer">Goedkeuren &amp; uitvoeren</button>' +
      '<button onclick="beursWijsAf(this,\'' + escAttr(v.id) + '\')" style="padding:7px 14px;background:#fff;color:#475569;border:1px solid #e2e8f0;border-radius:7px;font-size:12px;font-weight:600;cursor:pointer">Afwijzen</button>' +
      '</div></div>';
  });
  return h;
}

// ── Tab: posities & risico ────────────────────────────────────────────────
function beursPosities(d) {
  var orisk = d.open_risico || {}, expo = d.blootstelling || {};
  var h = '';

  var rijen = (orisk.posities || []).map(function(p) {
    return bmRij([
      '<strong>' + escHtml(p.symbol) + '</strong><div style="font-size:10px;color:#94a3b8">' + escHtml(p.asset_class || '') + '</div>',
      bmNum(p.qty, 4),
      bmEur(p.waarde),
      '<span style="color:' + bmKleur(p.pnl_pct) + '">' + bmPct(p.pnl_pct) + '</span>',
      p.stop ? bmNum(p.stop) + (p.afstand_stop_pct !== undefined ? '<div style="font-size:10px;color:#94a3b8">' + bmNum(p.afstand_stop_pct, 1) + '% eronder</div>' : '')
             : '<span style="color:#b91c1c;font-weight:600">geen</span>',
      p.risico_eur === null || p.risico_eur === undefined ? '<span style="color:#b45309">onbekend</span>' : bmEur(p.risico_eur),
      p.rr_resterend !== undefined ? bmNum(p.rr_resterend, 1) + ':1' : '—',
      (p.dagen_open === null ? '—' : p.dagen_open + '/' + (p.horizon_days || '?')) +
        (p.horizon_verstreken ? ' <span style="color:#b45309" title="De ronde hoort deze positie te sluiten">⚠</span>' : ''),
    ]);
  });
  h += bmKaart('Open posities (' + (orisk.posities || []).length + ')',
    bmTabel(['Instrument', 'Stuks', 'Waarde', 'Resultaat', 'Stop', 'Risico', 'Nog te winnen', 'Dagen/horizon'], rijen),
    'De kolom <strong>risico</strong> is wat deze positie kost als de stop raakt — samen ' + bmEur(orisk.risico_eur) +
    ' (' + bmNum(orisk.risico_pct_nav, 2) + '% van de NAV). Dát is het bedrag waarop je een slechte week beoordeelt, niet de waarde.');

  // Blootstelling met benutting van de klemmen. Die klemmen zijn onzichtbaar
  // tot ze iets blokkeren; dan is het te laat om te begrijpen waarom.
  var klassen = (expo.klassen || []).map(function(k) {
    var vulling = Math.min(100, k.benutting_pct || 0);
    var kleur = vulling >= 90 ? '#dc2626' : vulling >= 70 ? '#f59e0b' : '#0ea5e9';
    return '<div style="margin-bottom:10px">' +
      '<div style="display:flex;justify-content:space-between;font-size:12px;color:#334155;margin-bottom:3px">' +
      '<span><strong>' + escHtml(k.klasse) + '</strong> ' + bmEur(k.waarde_eur) + '</span>' +
      '<span style="color:#64748b">' + bmNum(k.pct_nav, 1) + '% van NAV · grens ' + bmNum(k.grens_pct, 0) + '%</span></div>' +
      '<div style="height:7px;background:#f1f5f9;border-radius:4px;overflow:hidden">' +
      '<div style="height:100%;width:' + vulling + '%;background:' + kleur + ';border-radius:4px"></div></div></div>';
  }).join('');
  var cashRegel = '<div style="font-size:12px;color:#334155;margin-top:12px;padding-top:10px;border-top:1px solid #f1f5f9">' +
    'Cash: <strong>' + bmEur(expo.cash_eur) + '</strong> (' + bmNum(expo.cash_pct, 1) + '%)' +
    (expo.grootste_positie ? ' · grootste positie: <strong>' + escHtml(expo.grootste_positie.symbol) + '</strong> ' +
      bmNum(expo.grootste_positie.pct_nav, 1) + '% (grens ' + bmNum(expo.grens_positie_pct, 0) + '%)' : '') + '</div>';
  h += bmKaart('Blootstelling en klemmen', (klassen || bmLeeg('Nog geen posities — alles staat in cash.')) + cashRegel,
    'De balken tonen de benutting van de grens per assetklasse uit <code>risk.py</code>. Vol betekent: het volgende voorstel in die klasse wordt geweigerd.');

  var tradeRijen = (d.trades || []).map(function(t) {
    return bmRij([
      escHtml(t.executed_on),
      '<span style="color:' + (t.side === 'buy' ? '#0369a1' : '#b45309') + ';font-weight:600">' + escHtml((t.side || '').toUpperCase()) + '</span>',
      '<strong>' + escHtml(t.symbol) + '</strong>',
      bmNum(t.qty, 4),
      bmNum(t.price),
      t.ref_price ? bmNum(t.ref_price) : '—',
      bmEur(t.fee, 2),
      escHtml(t.reason || ''),
    ]);
  });
  h += bmKaart('Grootboek — laatste transacties', bmTabel(['Datum', 'Kant', 'Instrument', 'Stuks', 'Fill', 'Besluitkoers', 'Kosten', 'Reden'], tradeRijen),
    'De fill is de koers van de <em>volgende</em> handelsdag, inclusief slippage. Het verschil met de besluitkoers is wat het echte leven kost — een backtest die op de eigen instapkoers vult, verslaat elke index en levert niets op.');

  return h;
}

// ── Tab: trackrecord ──────────────────────────────────────────────────────
function beursTrackrecord(d) {
  var t = d.trackrecord || {};
  var h = '';

  if (!t.n) {
    h += bmKaart('Trackrecord', bmLeeg(t.toelichting || 'Nog geen afgesloten posities.'),
      'Zodra posities sluiten, staat hier of de strategie geld verdient — en waarmee.');
    return h + beursTrechterKaart(d);
  }

  var tegels = '<div style="display:flex;gap:10px;flex-wrap:wrap">' +
    bmTegel('Resultaat', bmEur(t.resultaat_eur), 'na ' + bmEur(t.kosten_eur, 0) + ' kosten · n=' + t.n, bmKleur(t.resultaat_eur)) +
    bmTegel('Trefpercentage', bmNum(t.trefpercentage, 1) + '%', t.winnaars + ' winst / ' + t.verliezers + ' verlies',
      '#0f172a', bmBadge(t.zeggingskracht, t.zeggingskracht === 'betekenisvol' ? 'groen' : 'oranje')) +
    bmTegel('Payoff', bmNum(t.payoff, 2), 'gem. winst ' + bmEur(t.gem_winst_eur, 0) + ' vs verlies ' + bmEur(t.gem_verlies_eur, 0)) +
    bmTegel('Profit factor', bmNum(t.profit_factor, 2), 'winst ÷ verlies · boven 1 is winstgevend', bmKleur(t.profit_factor === null ? null : t.profit_factor - 1)) +
    bmTegel('Verwachting', bmEur(t.verwachting_eur, 0), 'per idee' + (t.verwachting_r !== null && t.verwachting_r !== undefined ? ' · ' + bmNum(t.verwachting_r, 2) + 'R' : ''), bmKleur(t.verwachting_eur)) +
    bmTegel('Looptijd', t.gem_looptijd_dagen === null ? '—' : bmNum(t.gem_looptijd_dagen, 1) + ' d', 'gemiddeld open') +
    '</div>';
  h += bmKaart('Wat de strategie oplevert', tegels,
    'Trefpercentage alléén stuurt de verkeerde kant op — dat verhoog je door winst te vroeg te pakken. Daarom staat de payoff ernaast, en is de <strong>verwachting per idee</strong> het cijfer waarop je stuurt: alleen dat zegt of méér ideeën ook méér geld betekenen.' +
    (t.n_zonder_stop ? ' <span style="color:#b45309">' + t.n_zonder_stop + ' positie(s) zonder stop — daarvoor bestaat geen R-veelvoud.</span>' : '') +
    (t.n_onmeetbaar ? ' <span style="color:#b45309">' + t.n_onmeetbaar + ' positie(s) vallen buiten de euro-statistiek (geen wisselkoers).</span>' : ''));

  var redenRijen = Object.keys(t.per_reden || {}).map(function(k) {
    var v = t.per_reden[k];
    return bmRij(['<strong>' + escHtml(k) + '</strong>', v.n,
      '<span style="color:' + bmKleur(v.resultaat_eur) + '">' + bmEur(v.resultaat_eur) + '</span>']);
  });
  h += bmKaart('Hoe posities eindigen', bmTabel(['Sluitreden', 'Aantal', 'Resultaat'], redenRijen),
    'Sluit alles op <em>horizon</em>, dan heeft de portefeuille geen these maar een klok. Sluit alles op <em>stop</em>, dan staan de stops te dicht op de koers.');

  var gesloten = (d.gesloten || []).map(function(p) {
    return bmRij([
      '<strong>' + escHtml(p.symbol) + '</strong>',
      escHtml(p.opened_on) + ' → ' + escHtml(p.closed_on),
      (p.looptijd_dagen === null ? '—' : p.looptijd_dagen + ' d'),
      escHtml(p.close_reason || '—'),
      '<span style="color:' + bmKleur(p.resultaat_pct) + '">' + bmPct(p.resultaat_pct) + '</span>',
      p.resultaat_eur === null ? '<span style="color:#b45309">onmeetbaar</span>'
        : '<span style="color:' + bmKleur(p.resultaat_eur) + '">' + bmEur(p.resultaat_eur, 2) + '</span>',
      p.r_multiple === null ? '—' : bmNum(p.r_multiple, 2) + 'R',
      bmEur(p.kosten_eur, 2) + (p.fx_benadering ? ' <span title="Meerdere deelverkopen in vreemde valuta: het euro-bedrag rust op de wisselkoers van de sluitdag" style="color:#b45309">*</span>' : ''),
    ]);
  });
  h += bmKaart('Afgesloten posities', bmTabel(['Instrument', 'Periode', 'Looptijd', 'Einde', 'Resultaat %', 'Resultaat €', 'R-veelvoud', 'Kosten'], gesloten),
    'Verliezers blijven staan. Zonder hen is elk rendementscijfer gevleid — dat is geen boekhoudkundig detail maar precies hoe een trackrecord zichzelf mooi rekent.');

  return h + beursTrechterKaart(d);
}

// ── Tab: de machine ───────────────────────────────────────────────────────
function beursMachine(d) {
  var r = d.rondes || {};
  var h = beursTrechterKaart(d);

  var verdeling = r.denkwerk_30d || {};
  var stukken = Object.keys(verdeling).map(function(k) {
    var tint = k === 'claude_code' ? 'groen' : k === 'terugval' ? 'oranje' : 'grijs';
    return bmBadge(k + ': ' + verdeling[k], tint);
  }).join(' ');
  var rondeRijen = (r.rondes || []).map(function(x) {
    return bmRij([
      escHtml(x.run_date),
      x.denkwerk === 'claude_code' ? bmBadge('claude code', 'groen') : x.denkwerk === 'terugval' ? bmBadge('terugval', 'oranje') : bmBadge(x.denkwerk || 'geen', 'grijs'),
      x.status === 'error' ? '<span style="color:#b91c1c;font-weight:600">fout</span>' : 'ok',
      x.proposals,
      Math.round((x.duration_ms || 0) / 1000) + 's',
      '<span style="font-size:11px;color:#64748b">' + escHtml((x.error || x.note || '').slice(0, 90)) + '</span>',
    ]);
  });
  h += bmKaart('Analyse-rondes', '<div style="margin-bottom:10px">' + (stukken || '') +
      (r.echt_denkwerk_pct !== null && r.echt_denkwerk_pct !== undefined
        ? ' <span style="font-size:11px;color:#64748b;margin-left:6px">' + bmNum(r.echt_denkwerk_pct, 0) + '% écht denkwerk (30 dagen)</span>' : '') + '</div>' +
    bmTabel(['Datum', 'Denkwerk', 'Status', 'Voorstellen', 'Duur', 'Notitie'], rondeRijen),
    'Een ronde op de <em>terugval</em> draait zonder werkmap en kan dus niets backtesten — die levert per definitie nul voorstellen op. In de cijfers ziet dat er precies zo uit als "de analist vond niets", en dat is een heel ander bericht.');

  // Datakwaliteit: elk besluit hierboven rust hierop.
  var dek = d.dekking || {};
  var verouderd = d.verouderd || [];
  var dekInhoud = '<div style="display:flex;gap:10px;flex-wrap:wrap">' +
    bmTegel('Symbolen met historie', dek.symbolen === undefined ? '—' : dek.symbolen,
      (dek.rijen ? Number(dek.rijen).toLocaleString('nl-NL') + ' koersdagen' : 'nog geen koersen')) +
    bmTegel('Reeks loopt tot', escHtml(dek.tot || '—'), dek.van ? 'vanaf ' + escHtml(dek.van) : '') +
    bmTegel('Verouderd', verouderd.length, verouderd.length ? 'besluiten zouden op oude data rusten' : 'alle koersen actueel',
      verouderd.length ? '#b45309' : '#059669') +
    '</div>';
  if (verouderd.length) {
    dekInhoud += '<p style="font-size:12px;color:#b45309;margin:10px 0 0">Te oud: ' +
      escHtml(verouderd.map(function(v) {
        return v.symbol + ' (' + (v.laatste_dag || 'geen koers') + (v.dagen_oud !== null && v.dagen_oud !== undefined ? ', ' + v.dagen_oud + ' dagen' : '') + ')';
      }).join(', ')) + '</p>';
  }
  dekInhoud += '<button onclick="beursSyncKoersen(this)" style="margin-top:12px;padding:7px 14px;border:1px solid #e2e8f0;background:#fff;color:#475569;border-radius:7px;font-size:12px;font-weight:600;cursor:pointer">Koersen nu ophalen</button>';
  h += bmKaart('Datakwaliteit', dekInhoud,
    'Crypto en ETF\'s hebben een eigen houdbaarheid: een koers van vrijdag is maandag voor een ETF de meest recente die bestaat, en voor bitcoin drie dagen oud.');

  return h;
}

function beursTrechterKaart(d) {
  var f = d.trechter || {};
  var stappen = [
    ['Voorgesteld', f.voorgesteld, '#0ea5e9'],
    ['Geblokkeerd door risico', f.geblokkeerd, '#f59e0b'],
    ['Wacht op review', f.in_review, '#8b5cf6'],
    ['Afgewezen', f.afgewezen, '#94a3b8'],
    ['Uitgevoerd', f.uitgevoerd, '#059669'],
  ];
  var max = Math.max.apply(null, stappen.map(function(s) { return s[1] || 0; }).concat([1]));
  var balken = stappen.map(function(s) {
    var breedte = Math.round((s[1] || 0) / max * 100);
    return '<div style="margin-bottom:8px"><div style="display:flex;justify-content:space-between;font-size:12px;color:#334155;margin-bottom:3px">' +
      '<span>' + s[0] + '</span><strong>' + (s[1] || 0) + '</strong></div>' +
      '<div style="height:7px;background:#f1f5f9;border-radius:4px;overflow:hidden"><div style="height:100%;width:' + breedte + '%;background:' + s[2] + ';border-radius:4px"></div></div></div>';
  }).join('');
  var redenen = (f.blokkade_redenen || []).length
    ? '<div style="margin-top:12px;padding-top:10px;border-top:1px solid #f1f5f9"><div style="font-size:11px;font-weight:600;color:#64748b;margin-bottom:5px">Waarom de risicotoets weigerde</div>' +
      f.blokkade_redenen.map(function(b) {
        return '<div style="font-size:11px;color:#475569;margin-bottom:3px">· ' + escHtml(b.reden) + ' <strong>(' + b.n + '×)</strong></div>';
      }).join('') + '</div>'
    : '';
  return bmKaart('Van idee naar order — laatste ' + (f.dagen || 90) + ' dagen',
    balken + redenen +
    '<div style="margin-top:10px;font-size:12px;color:#334155">Conversie: <strong>' +
    (f.conversie_pct === null || f.conversie_pct === undefined ? '—' : bmNum(f.conversie_pct, 1) + '%') +
    '</strong> van de voorstellen werd een order.</div>',
    'Zonder deze verhouding weet je niet of de agent te weinig ideeën heeft of te strakke klemmen — en dat zijn tegengestelde ingrepen.');
}

// ── Grafiek ───────────────────────────────────────────────────────────────
function tekenKoerslijn(lijn) {
  if (!lijn || !lijn.punten || lijn.punten.length < 2) return;
  var canvas = document.getElementById('beurs-koerslijn');
  if (!canvas || typeof Chart === 'undefined') return;
  if (_beursChart) { _beursChart.destroy(); _beursChart = null; }
  _beursChart = new Chart(canvas.getContext('2d'), {
    type: 'line',
    data: {
      labels: lijn.punten.map(function(p) { return p.date; }),
      datasets: [
        { label: 'Portefeuille', data: lijn.punten.map(function(p) { return p.nav_index; }),
          borderColor: '#f43f5e', backgroundColor: 'rgba(244,63,94,.08)', borderWidth: 2,
          fill: true, tension: .2, pointRadius: 0, pointHoverRadius: 4 },
        { label: 'Benchmark', data: lijn.punten.map(function(p) { return p.bench_index; }),
          borderColor: '#64748b', borderWidth: 1.5, borderDash: [5, 4], fill: false,
          tension: .2, pointRadius: 0, pointHoverRadius: 4, spanGaps: true },
      ],
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      interaction: { mode: 'index', intersect: false },
      plugins: {
        legend: { position: 'bottom', labels: { boxWidth: 12, font: { size: 11 } } },
        tooltip: { callbacks: { label: function(c) { return c.dataset.label + ': ' + (c.parsed.y === null ? '—' : c.parsed.y.toFixed(2)); } } },
      },
      scales: {
        y: { ticks: { font: { size: 10 } }, grid: { color: '#f1f5f9' } },
        x: { ticks: { font: { size: 10 }, maxTicksLimit: 8 }, grid: { display: false } },
      },
    },
  });
}

// ── Acties ────────────────────────────────────────────────────────────────
// Alles wat de wereld verandert zit achter een menselijke klik. `beursKeurGoed`
// is de enige weg naar een order; de risicotoets draait daarbij opnieuw, dus
// een 409 is geen bug maar de gate die zijn werk doet.
function beursKeurGoed(btn, id, symbol) {
  if (!confirm('Order uitvoeren voor ' + symbol + '?\n\nDe risicotoets draait opnieuw en bepaalt de definitieve grootte. De fill komt op de eerstvolgende slotkoers, inclusief kosten en slippage.')) return;
  btn.disabled = true; btn.textContent = 'Uitvoeren…';
  post('/api/invest/proposals/' + encodeURIComponent(id) + '/approve').then(function(res) {
    var t = res && res.trade;
    alert(t ? ('Uitgevoerd: ' + t.side.toUpperCase() + ' ' + t.qty + ' ' + t.symbol + ' @ ' + t.price + ' (' + t.fill_dag + '), kosten €' + t.fee)
            : 'Uitgevoerd.');
    laadBeursmeester();
  }).catch(function(e) {
    alert('Niet uitgevoerd: ' + e.message);
    btn.disabled = false; btn.textContent = 'Goedkeuren & uitvoeren';
  });
}

function beursWijsAf(btn, id) {
  var reden = prompt('Waarom wijs je dit voorstel af? (helpt de agent leren)') || '';
  btn.disabled = true;
  post('/api/invest/proposals/' + encodeURIComponent(id) + '/reject?reden=' + encodeURIComponent(reden))
    .then(function() { laadBeursmeester(); })
    .catch(function(e) { alert('Fout: ' + e.message); btn.disabled = false; });
}

function beursRondeNu(btn) {
  btn.disabled = true; btn.textContent = 'Bezig…';
  post('/api/invest/run').then(function() {
    btn.textContent = 'Gestart — duurt enkele minuten';
    // De ronde draait op de achtergrond; we halen het beeld daarna stil op in
    // plaats van de gebruiker te laten raden of er iets gebeurt.
    if (_beursTimer) clearInterval(_beursTimer);
    _beursTimer = setInterval(function() {
      if (!document.getElementById('beurs-root')) { clearInterval(_beursTimer); _beursTimer = null; return; }
      laadBeursmeester(true);
    }, 30000);
  }).catch(function(e) {
    alert('Fout: ' + e.message); btn.disabled = false; btn.textContent = 'Ronde nu draaien';
  });
}

function beursHervatten(btn) {
  if (!confirm('Handelsstop opheffen?\n\nKijk eerst of de oorzaak structureel is — een stop die vanzelf verloopt, leert niemand iets.')) return;
  btn.disabled = true;
  post('/api/invest/resume').then(function() { laadBeursmeester(); })
    .catch(function(e) { alert('Fout: ' + e.message); btn.disabled = false; });
}

function beursSyncKoersen(btn) {
  btn.disabled = true; btn.textContent = 'Ophalen…';
  post('/api/invest/sync-history').then(function(res) {
    btn.textContent = 'Klaar';
    laadBeursmeester();
  }).catch(function(e) {
    alert('Fout: ' + e.message); btn.disabled = false; btn.textContent = 'Koersen nu ophalen';
  });
}
