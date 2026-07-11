// ── Agent OS — login-gate (frontend)
// Toont een login-scherm als er geen geldige sessie is. De echte
// beveiliging zit server-side (auth_guard-middleware in backend/main.py);
// deze code is alleen de UX: zonder sessie komt de gebruiker niet bij de app.

function showLoginScreen(reason) {
  var main = document.getElementById('main-content');
  if (!main) return;
  var note = reason ? '<p style="color:#dc2626;font-size:12px;margin-bottom:12px">' + reason + '</p>' : '';
  main.innerHTML =
    '<div style="min-height:100vh;display:flex;align-items:center;justify-content:center;background:#0f172a">' +
    '<div style="width:320px;max-width:90vw;background:#1e293b;border:1px solid #334155;border-radius:12px;padding:28px 24px;box-shadow:0 10px 40px rgba(0,0,0,.4)">' +
    '<h1 style="font-size:18px;font-weight:700;color:#f8fafc;margin-bottom:4px">Agent OS</h1>' +
    '<p style="font-size:12px;color:#94a3b8;margin-bottom:20px">Log in om je mission control te openen.</p>' +
    note +
    '<input id="login-pw" type="password" placeholder="Wachtwoord" autofocus ' +
    'style="width:100%;padding:10px 12px;border-radius:8px;border:1px solid #475569;background:#0f172a;color:#f8fafc;font-size:14px;margin-bottom:12px" />' +
    '<button id="login-btn" onclick="submitLogin()" ' +
    'style="width:100%;padding:10px 12px;border-radius:8px;border:none;background:#4f46e5;color:#fff;font-size:14px;font-weight:600;cursor:pointer">Inloggen</button>' +
    '<p id="login-err" style="color:#f87171;font-size:12px;margin-top:10px;min-height:16px"></p>' +
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
  try {
    var resp = await fetch('/api/auth/me');
    var d = await resp.json();
    if (d.authenticated) { route(); return; }
  } catch (e) { /* server weg of geen me-endpoint — val door naar login */ }
  showLoginScreen();
}

async function logoutAgent() {
  try { await fetch('/api/auth/logout', { method: 'POST' }); } catch (e) {}
  showLoginScreen('Uitgelogd.');
}
