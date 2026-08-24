// Headless test van tour.js via jsdom — simuleert de ImpactOS SPA-omgeving.
const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');

const tourSrc = fs.readFileSync(path.join(__dirname, 'js/tour.js'), 'utf8');

// Bouw een realistische DOM: Control Room + sidebar + een projectdashboard
// met een subset van de echte elementen (Nicole-instance: minimaal).
function makeDom(withProject) {
  const html = `<!DOCTYPE html><html><head></head><body>
    <main id="main-content"></main>
  </body></html>`;
  const dom = new JSDOM(html, { runScripts: 'outside-only', pretendToBeVisual: true, url: 'http://localhost/' });
  const { window } = dom;
  // Globale SPA-hooks die tour.js aanroept
  window.switchView = function (t) { window.__tab = t; };
  window.selectProject = function (p) { window.__project = p; };
  window.currentTab = null;
  window.console = console;
  // localStorage komt van jsdom zelf (url=http://localhost/), geen shim nodig.
  window.escHtml = function (s) { return s == null ? '' : String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); };
  return dom;
}

function buildControlRoom(window) {
  const main = window.document.getElementById('main-content');
  main.innerHTML =
    '<div class="page-head"><h2>WE SHAPE THE FUTURE</h2></div>' +
    '<div id="action-center-panel"></div>' +
    '<div id="iris-panel"></div>' +
    '<div class="project-card"><div class="pc-name">WeShapeTheFuture</div></div>' +
    '<div class="sidebar-footer"></div>';
  window.currentTab = 'Dashboard';
}

function buildProjectDashboard(window) {
  const main = window.document.getElementById('main-content');
  main.innerHTML =
    '<div class="sidebar"><nav class="sidebar-nav">' +
      '<button onclick="">Dashboard</button><button>Leads</button><button>Kansen</button>' +
    '</nav><div class="sidebar-footer"></div></div>' +
    '<div id="dash-banner-container"></div>' +
    '<div class="dash-alert">Signaal</div>' +
    '<div class="section-card">Beste volgende stap: doe X</div>' +
    '<div class="kpi-grid"></div>' +
    '<canvas id="dash-chart-clicks"></canvas>' +
    '<div id="project-activity-panel"></div>' +
    '<div class="section-card">Doelen (0)</div>' +
    '<button id="batch-btn">Start</button>' +
    '<input id="linkedin-query"><div id="lead-list"></div>';
}

(async () => {
  const dom = makeDom(true);
  const { window } = dom;
  // tour.js laden in de window-context
  const runScript = new window.Function(tourSrc + '\n//# sourceURL=tour.js');
  runScript.call(window);
  // initTour draait zelf via DOMContentLoaded/readyState — forceer handmatig
  window.initTour && window.initTour();

  // 1) CONTROL ROOM
  buildControlRoom(window);
  window.startTour();
  await new Promise(r => setTimeout(r, 80)); // wacht op async showStep(0)
  const card0 = window.document.getElementById('iris-tour-card');
  console.log('TEST 1 card exists after startTour:', !!card0);
  console.log('TEST 1 step label:', window.document.getElementById('iris-tour-step').textContent);
  console.log('TEST 1 avatar src:', window.document.querySelector('#iris-tour-card .iris-tour-avatar')?.getAttribute('src'));

  // 2) naar project + tools tonen
  buildProjectDashboard(window);
  // Simuleer "Volgende" tot we bij een project-stap zijn
  let guard = 0;
  let safetyOk = true;
  function clickNext() {
    window.document.getElementById('iris-tour-next').click();
  }
  // loop een paar stappen en check geen crash
  for (let i = 0; i < 6; i++) {
    try { clickNext(); } catch (e) { safetyOk = false; console.log('CRASH at', i, e.message); break; }
    await new Promise(r => setTimeout(r, 80)); // wacht async render
  }
  console.log('TEST 2 navigated 6 steps without crash:', safetyOk);
  console.log('TEST 2 current step:', window.document.getElementById('iris-tour-step').textContent);

  // 3) toggle aan/uit
  window.setTourEnabled(false);
  console.log('TEST 3 setTourEnabled(false) stored:', window.localStorage.getItem('impactos.tour.enabled'));
  window.setTourEnabled(true);
  console.log('TEST 3 setTourEnabled(true) stored:', window.localStorage.getItem('impactos.tour.enabled'));

  // 4) settings HTML bevat toggle + start-knop
  const settingsHtml = window.renderTourSettings();
  console.log('TEST 4 settings has checkbox:', settingsHtml.includes('type="checkbox"'));
  console.log('TEST 4 settings has start button:', settingsHtml.includes('Start tour opnieuw'));

  // 5) sidebar Tour-knop toegevoegd (na buildProjectDashboard is .sidebar-footer aanwezig)
  await new Promise(r => setTimeout(r, 50));
  console.log('TEST 5 tour button in sidebar:', !!window.document.getElementById('iris-tour-btn'));

  console.log('\nALL TESTS DONE');
  dom.window.close();
})().catch(e => { console.error('FATAL', e); process.exit(1); });
