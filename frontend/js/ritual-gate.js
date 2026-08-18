// ── Agent OS — Verplichte ritueel-gate ───────────────────────────────────
// Vertaling van impactreis3's ritual-guard.tsx + weekflow.service.ts naar de
// AgentOS-SPA. Bij het openen van de Control Room (geen project geselecteerd)
// wordt /api/rituals/next-required geraadpleegd. Is er een verplicht ritueel
// dat nu mag (isAvailable), dan verschijnt een full-screen overlay met het
// formulier — de Control Room is pas bereikbaar als het ritueel is gedaan.
// Is het verplicht maar nóg niet beschikbaar (avond vóór 17:00), dan komt er
// alleen een zachte banner bovenaan. Nood-escape sluit de gate voor de sessie.
//
// Frontend-actie 'path' → formulier-functie uit tabs-rituals.js:
//   morning      → showMorningForm()
//   evening      → showEveningForm()
//   weekly-start → showWeeklyStartForm()
//   weekly-review→ showWeeklyReviewForm()

var _ritualGateBypassed = false;

// Synchronous GET (bestaande httpSync bestaat niet in deze SPA — we gebruiken
// een klassieke XHR zodat de gate vóór renderHome() kan beslissen zonder
// async-waterval). Mislukt → null (gate degradeert stil, nooit blokkeren).
function _ritualHttpSync(url) {
  try {
    var xhr = new XMLHttpRequest();
    xhr.open('GET', url, false);
    xhr.send();
    if (xhr.status >= 200 && xhr.status < 300) return JSON.parse(xhr.responseText);
    return null;
  } catch (e) {
    return null;
  }
}

function _ritualFormFor(path) {
  if (path === 'morning') return window.showMorningForm;
  if (path === 'evening') return window.showEveningForm;
  if (path === 'weekly-start') return window.showWeeklyStartForm;
  if (path === 'weekly-review') return window.showWeeklyReviewForm;
  return null;
}

// Toont de overlay en laadt daarna inline het juiste formulier. show*Form()
// schrijven naar #rituelen-panel; we lenen die id tijdelijk voor de gate.
function _renderRitualGate(mainEl, next) {
  mainEl.innerHTML =
    '<div id="ritual-gate" style="max-width:640px;margin:40px auto;padding:0 16px">' +
      '<div style="background:var(--card-bg);border:1px solid var(--card-border);border-radius:14px;' +
      'padding:22px 24px;box-shadow:0 10px 40px rgba(0,0,0,.25)">' +
        '<div style="display:flex;align-items:center;gap:10px;margin-bottom:6px">' +
          '<span style="font-size:20px">☀️</span>' +
          '<h2 style="font-size:18px;margin:0;color:var(--text)">' + escHtml(next.title) + '</h2>' +
        '</div>' +
        '<p style="font-size:13px;color:var(--text-dim);margin:0 0 14px">' + escHtml(next.reason) +
          '. Doe dit eerst — daarna pas de Control Room.</p>' +
        '<div id="ritual-gate-form" style="font-size:12px"></div>' +
        '<div style="margin-top:14px;font-size:11px">' +
          '<a href="#" onclick="return _bypassRitualGate()" style="color:var(--text-dim);text-decoration:underline">Sla over (deze sessie)</a>' +
        '</div>' +
      '</div>' +
    '</div>';

  var panel = document.getElementById('ritual-gate-form');
  var orig = document.getElementById('rituelen-panel');
  if (orig) orig.id = 'rituelen-panel-orig';
  if (panel) panel.id = 'rituelen-panel';
  var formFn = _ritualFormFor(next.path);
  if (typeof formFn === 'function') {
    try { formFn(); } catch (e) { /* form-fout mag de gate niet breken */ }
  }
  // herstel de echte panel-id zodat latere tabs-rituals-gebruik intact blijft
  var restored = document.getElementById('rituelen-panel-orig');
  if (restored) restored.id = 'rituelen-panel';
  panel = document.getElementById('ritual-gate-form');
  if (panel) panel.id = 'rituelen-panel';
}

// Hercontroleer de gate ná het opslaan van een ritueel. Wordt aangeroepen vanuit
// de save*Form()-functies in tabs-rituals.js (via .then()). Beschikbaar als
// globale functie zodat de save-flow hem simpel kan ketenen.
function afterRitualSaved() {
  if (!document.getElementById('ritual-gate')) return;
  var main = document.getElementById('main-content');
  if (!main) return;
  var res = _ritualHttpSync('/api/rituals/next-required');
  if (!res || !res.isRequired || !res.next || !res.next.isAvailable) {
    // Gedaan (of niets meer verplicht): Control Room tonen.
    var gate = document.getElementById('ritual-gate');
    if (gate && gate.parentNode) gate.parentNode.removeChild(gate);
    renderHome(main);
  } else {
    // Nog een ander verplicht ritueel — toon dat direct.
    _renderRitualGate(main, res.next);
  }
}

function checkRitualGate(mainEl) {
  // Aangeroepen vanuit route() vóórdat renderHome() de Control Room tekent.
  // Retourneert true als de gate de Control Room blokkeert.
  if (_ritualGateBypassed) return false;
  if (!domainOn('rituals')) return false;
  var res = _ritualHttpSync('/api/rituals/next-required');
  if (!res || !res.isRequired || !res.next) return false;
  if (res.next.isAvailable) {
    _renderRitualGate(mainEl, res.next);
    return true;
  }
  // Verplicht maar nog niet beschikbaar: zachte banner, geen blokkade.
  _showRitualBanner(res.next.reason);
  return false;
}

function _showRitualBanner(reason) {
  var main = document.getElementById('main-content');
  if (!main) return;
  if (document.getElementById('ritual-soft-banner')) return;
  var el = document.createElement('div');
  el.id = 'ritual-soft-banner';
  el.style.cssText = 'background:var(--warn-bg);color:var(--warn-fg);font-size:12px;' +
    'padding:8px 14px;border-radius:8px;margin:0 0 12px;display:flex;' +
    'justify-content:space-between;align-items:center;gap:10px';
  el.innerHTML = '<span>⏳ ' + escHtml(reason || 'Ritueel wacht op je') + '</span>' +
    '<button class="btn btn-sm btn-ghost" onclick="goRitualNow()">Doen</button>';
  main.insertBefore(el, main.firstChild);
}

function goRitualNow() {
  var main = document.getElementById('main-content');
  if (!main) return;
  var b = document.getElementById('ritual-soft-banner');
  if (b && b.parentNode) b.parentNode.removeChild(b);
  checkRitualGate(main);
}

function _bypassRitualGate() {
  _ritualGateBypassed = true;
  var main = document.getElementById('main-content');
  if (main) {
    var gate = document.getElementById('ritual-gate');
    if (gate && gate.parentNode) gate.parentNode.removeChild(gate);
    var b = document.getElementById('ritual-soft-banner');
    if (b && b.parentNode) b.parentNode.removeChild(b);
    renderHome(main);
  }
  return false;
}
