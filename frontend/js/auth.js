// ── Impact OS — login-gate (frontend)
// Toont een login-scherm als er geen geldige sessie is. De echte
// beveiliging zit server-side (auth_guard-middleware in backend/main.py);
// deze code is alleen de UX: zonder sessie komt de gebruiker niet bij de app.

function showLoginScreen(reason) {
  var main = document.getElementById('main-content');
  if (!main) return;
  var note = reason ? '<p class="login-note">' + escHtml(reason) + '</p>' : '';
  var name = window.__instanceName || 'Impact OS';
  main.innerHTML =
    '<div class="login-screen"><div class="login-card">' +
    '<img class="login-mark" src="/logo-hart.png" alt="' + escAttr(name) + '" />' +
    '<div class="login-badge">AI &amp; Innovatie in het Sociaal Domein</div>' +
    '<h1>' + escHtml(name) + '</h1>' +
    '<p class="login-sub">Log in om je mission control te openen.</p>' +
    note +
    '<input id="login-pw" type="password" placeholder="Wachtwoord" autofocus autocomplete="current-password" />' +
    '<button id="login-btn" class="login-submit" onclick="submitLogin()">Inloggen</button>' +
    '<p id="login-err"></p>' +
    '<div class="login-foot">Impact OS &middot; WeAreImpact</div>' +
    '</div></div>';
  var pw = document.getElementById('login-pw');
  if (pw) pw.addEventListener('keydown', function(e){ if (e.key === 'Enter') submitLogin(); });
}

async function submitLogin() {
  var pw = document.getElementById('login-pw');
  var btn = document.getElementById('login-btn');
  var err = document.getElementById('login-err');
  if (!pw || !pw.value) { if (err) err.textContent = 'Voer je wachtwoord in.'; return; }
  if (btn) { btn.disabled = true; btn.textContent = 'Bezig...'; }
  try {
    var resp = await fetch('/api/auth/login', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ password: pw.value }),
    });
    var d = await resp.json();
    if (d.ok) {
      // Sessie staat nu in de cookie → workshop-intro eerst, dan de app.
      playLoginIntro();
    } else {
      if (err) err.textContent = d.error || 'Inloggen mislukt.';
      if (btn) { btn.disabled = false; btn.textContent = 'Inloggen'; }
    }
  } catch (e) {
    if (err) err.textContent = 'Serverfout: ' + e.message;
    if (btn) { btn.disabled = false; btn.textContent = 'Inloggen'; }
  }
}

async function checkAuthAndStart() {
  // /api/status is publiek (ook zonder sessie) — vóór alles ophalen zodat het
  // loginscherm zélf al de instance-naam toont i.p.v. altijd "Impact OS", en
  // de sidebar straks meteen met de juiste tabs rendert i.p.v. even alles te
  // tonen en dan te herschikken.
  await loadInstanceStatus();
  document.title = window.__instanceName || 'Impact OS';
  try {
    var resp = await fetch('/api/auth/me');
    var d = await resp.json();
    if (d.authenticated) { await loadClientBridgeProjects(); route(); return; }
  } catch (e) { /* server weg of geen me-endpoint — val door naar login */ }
  showLoginScreen();
}

// Fase 2 deel 2: welke projecten een gekoppelde klant (mijn-ondernemers-os) hebben, zodat
// visibleTabs() (core.js) de 'Rituelen'-tab alleen daar toont. Vóór de eerste route() opgehaald
// (zelfde reden als loadInstanceStatus) zodat de sidebar meteen goed rendert.
async function loadClientBridgeProjects() {
  window.__clientBridgeProjects = new Set();
  try {
    var r = await fetch('/api/projects');
    var list = await r.json();
    (list || []).forEach(function (p) {
      if (p.has_client_bridge) window.__clientBridgeProjects.add(squashProjectName(p.name));
    });
  } catch (e) { /* geen koppelingen zichtbaar tot de volgende load — geen harde fout */ }
}

// ── Workshop-intro — speelt direct ná het inloggen, vervaagt zodra de video
// afloopt en opent dan pas de Control Room (Vincent, 26 aug 2026). Alleen op
// een verse login (submitLogin), niet bij checkAuthAndStart — een al geldige
// sessie (tab heropend, pagina ververst) hoeft de intro niet elke keer te
// herhalen, anders is het geen welkom meer maar een obstakel.
function playLoginIntro() {
  var main = document.getElementById('main-content');
  if (!main) { route(); return; }
  var done = false;
  var finish = function () {
    if (done) return;
    done = true;
    var wrap = document.getElementById('login-intro');
    if (!wrap) { route(); return; }
    wrap.style.opacity = '0';
    setTimeout(route, 650); // wacht de fade-transitie af vóór de Control Room rendert
  };
  main.innerHTML =
    '<div id="login-intro" style="position:fixed;inset:0;background:#000;display:flex;' +
    'align-items:center;justify-content:center;z-index:9999;opacity:1;transition:opacity .6s ease">' +
    '<video id="login-intro-video" src="/media/iris-workshop.mp4" autoplay playsinline ' +
    'style="max-width:100%;max-height:100%"></video>' +
    '<button onclick="playLoginIntroSkip()" style="position:absolute;top:20px;right:24px;' +
    'background:rgba(255,255,255,.12);color:#fff;border:none;border-radius:999px;padding:8px 16px;' +
    'font-size:12px;cursor:pointer">Overslaan &rarr;</button>' +
    '</div>';
  var vid = document.getElementById('login-intro-video');
  vid.addEventListener('ended', finish);
  vid.addEventListener('error', finish);
  // Autoplay-met-geluid vergt een user-gesture; die is er (de inlog-klik),
  // maar sommige browsers weigeren 'm alsnog — val dan terug op gedempt
  // afspelen zodat de intro nooit muisstil vaststaat i.p.v. af te spelen.
  var playPromise = vid.play();
  if (playPromise && playPromise.catch) {
    playPromise.catch(function () { vid.muted = true; vid.play().catch(finish); });
  }
  window._loginIntroFinish = finish;
}
function playLoginIntroSkip() {
  if (window._loginIntroFinish) window._loginIntroFinish();
}

// Op een klant-instance (IMPACTOS_ENABLED_DOMAINS gezet) toont /api/status welke
// domeinen gemonteerd zijn; de sidebar verbergt dan tabs die er toch niet
// achter zitten (Beursmeester/Leads/Radar op een instance die alleen
// mail+agenda+blog heeft) — anders klik je op een tab die overal leeg of 404
// teruggeeft en dat oogt kapot, niet als "minimale scope". `instance_name`
// personaliseert loginscherm/sidebar/browsertab naar de merknaam van de klant.
async function loadInstanceStatus() {
  try {
    var r = await fetch('/api/status');
    var d = await r.json();
    window.__enabledDomains = d.enabled_domains || null; // null = alles aan
    window.__instanceName = d.instance_name || 'Impact OS';
  } catch (e) { window.__enabledDomains = null; window.__instanceName = 'Impact OS'; }
}

async function logoutAgent() {
  try { await fetch('/api/auth/logout', { method: 'POST' }); } catch (e) {}
  showLoginScreen('Uitgelogd.');
}
