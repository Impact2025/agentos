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
      // Sessie staat nu in de cookie → start de app.
      route();
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
    if (d.authenticated) { route(); return; }
  } catch (e) { /* server weg of geen me-endpoint — val door naar login */ }
  showLoginScreen();
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
