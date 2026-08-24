#!/usr/bin/env python3
# ─────────────────────────────────────────────────────────────────────────────
# Impact OS — Mission Control launcher
# Altijd-draaiende, lichtgewicht HTTP-server (alleen stdlib) op 127.0.0.1:8088.
#
# Probleem dat dit oplost:
#   De "Impact OS" knop op Vincents dashboard opende direct localhost:1250, maar
#   als de Impact OS-server niet draaide kreeg je ERR_CONNECTION_REFUSED.
#   Deze launcher Draait altijd (Windows-taak bij opstart) en biedt EEN knop die
#   eerst de server start (via D:\apps\impactos\launch.ps1) en daarna pas naar
#   1250 navigeert.
#
# Endpoints:
#   GET  /                 -> dashboard met grote "Impact OS" knop
#   GET  /api/status       -> {"impactos": "up"|"down"}
#   POST /api/launch       -> start Impact OS als die down is; poll tot bereikbaar
#                             antwoord: {"status":"starting"|"up"|"already_up"|"error", ...}
# ─────────────────────────────────────────────────────────────────────────────
import http.server
import json
import os
import subprocess
import threading
import urllib.request
from functools import lru_cache

ROOT = r"D:\apps\impactos"
LAUNCH_PS1 = os.path.join(ROOT, "launch.ps1")
IMPACTOS_URL = "http://localhost:1250/api/status"
IMPACTOS_HOME = "http://localhost:1250"
HOST = "127.0.0.1"
PORT = 8088

_launch_lock = threading.Lock()
_launching = False


def impactos_up():
    """True als de Impact OS backend antwoordt met 200."""
    try:
        req = urllib.request.Request(IMPACTOS_URL, method="GET")
        with urllib.request.urlopen(req, timeout=2) as r:
            return r.status == 200
    except Exception:
        return False


def start_impactos():
    """Start Impact OS via de bestaande launch.ps1 (verborgen, achtergrond)."""
    global _launching
    with _launch_lock:
        if _launching:
            return "starting"
        if impactos_up():
            return "already_up"
        _launching = True
    try:
        # launch.ps1 start Hermes gateway + Impact OS server + browser.
        subprocess.Popen(
            [
                "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
                "-WindowStyle", "Hidden", "-File", LAUNCH_PS1,
            ],
            cwd=ROOT,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
        )
        # Wacht tot de server echt bereikbaar is (max ~40s).
        for _ in range(40):
            if impactos_up():
                return "up"
            import time
            time.sleep(1)
        return "error"
    finally:
        with _launch_lock:
            _launching = False


INDEX_HTML = """<!DOCTYPE html>
<html lang="nl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Mission Control</title>
<style>
  :root { color-scheme: dark; }
  * { box-sizing: border-box; }
  body { margin:0; min-height:100vh; display:flex; align-items:center; justify-content:center;
         background:radial-gradient(1200px 600px at 50% -10%, #1e293b, #0b1120);
         font-family: Inter, system-ui, -apple-system, Segoe UI, Roboto, sans-serif; color:#e2e8f0; }
  .card { background:#0f172a; border:1px solid #1e293b; border-radius:18px; padding:40px 48px;
          box-shadow:0 20px 60px rgba(0,0,0,.5); text-align:center; min-width:360px; }
  h1 { font-size:20px; font-weight:700; margin:0 0 4px; letter-spacing:.3px; }
  p.sub { color:#94a3b8; font-size:13px; margin:0 0 28px; }
  .btn { display:inline-flex; align-items:center; gap:12px; padding:18px 34px; border:none;
         border-radius:14px; font-size:17px; font-weight:700; cursor:pointer; color:#fff;
         background:linear-gradient(135deg,#6366f1,#7c3aed); transition:.15s transform,.15s box-shadow;
         box-shadow:0 10px 30px rgba(99,102,241,.35); }
  .btn:hover { transform:translateY(-2px); box-shadow:0 14px 38px rgba(99,102,241,.5); }
  .btn:active { transform:translateY(0); }
  .btn:disabled { opacity:.6; cursor:progress; transform:none; }
  .dot { width:10px; height:10px; border-radius:50%; background:#f59e0b; box-shadow:0 0 12px #f59e0b; }
  .dot.up { background:#22c55e; box-shadow:0 0 12px #22c55e; }
  .status { margin-top:22px; font-size:13px; color:#94a3b8; min-height:18px; }
  .spin { display:inline-block; width:14px; height:14px; border:2px solid #c7d2fe;
          border-top-color:transparent; border-radius:50%; animation:r 1s linear infinite; vertical-align:-2px; }
  @keyframes r { to { transform:rotate(360deg); } }
  .foot { position:fixed; bottom:14px; left:0; right:0; text-align:center; color:#475569; font-size:11px; }
</style>
</head>
<body>
  <div class="card">
    <h1>Mission Control</h1>
    <p class="sub">Start Impact OS met één klik — Hermes + server worden automatisch opgestart</p>
    <button class="btn" id="go" onclick="launchImpactOS()">
      <span class="dot" id="dot"></span>
      <span id="label">Open Impact OS</span>
    </button>
    <div class="status" id="status"></div>
  </div>
  <div class="foot">Mission Control draait lokaal op 127.0.0.1:""" + str(PORT) + """</div>
<script>
  function setStatus(t){ document.getElementById('status').textContent = t; }
  function refreshDot(){
    fetch('/api/status').then(r=>r.json()).then(d=>{
      var up = d.impactos === 'up';
      document.getElementById('dot').className = 'dot' + (up?' up':'');
      document.getElementById('label').textContent = up ? 'Open Impact OS' : 'Start Impact OS';
    }).catch(()=>{});
  }
  function launchImpactOS(){
    var btn = document.getElementById('go');
    btn.disabled = true;
    document.getElementById('dot').className = 'dot';
    document.getElementById('label').innerHTML = '<span class="spin"></span> Bezig met opstarten...';
    setStatus('Impact OS en Hermes worden gestart — even geduld (ong. 10-30s)...');
    fetch('/api/launch', {method:'POST'}).then(r=>r.json()).then(d=>{
      if (d.status === 'up' || d.status === 'already_up'){
        setStatus('Klaar. Impact OS wordt geopend...');
        setTimeout(function(){ window.location.href = '""" + IMPACTOS_HOME + """'; }, 600);
      } else if (d.status === 'starting'){
        setStatus('Opstarten is al bezig...');
        pollUp();
      } else {
        setStatus('Starten mislukt. Controleer D:\\\\apps\\\\impactos\\\\impactos.log');
        btn.disabled = false;
        refreshDot();
      }
    }).catch(function(e){
      setStatus('Fout: ' + e);
      btn.disabled = false;
    });
  }
  function pollUp(){
    var btn = document.getElementById('go');
    fetch('/api/status').then(r=>r.json()).then(d=>{
      if (d.impactos === 'up'){
        setStatus('Klaar. Impact OS wordt geopend...');
        setTimeout(function(){ window.location.href = '""" + IMPACTOS_HOME + """'; }, 600);
      } else {
        setTimeout(pollUp, 1500);
      }
    }).catch(function(){ setTimeout(pollUp, 1500); });
  }
  refreshDot();
  setInterval(refreshDot, 10000);
</script>
</body>
</html>
"""


class Handler(http.server.BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="application/json"):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/" or self.path.startswith("/?"):
            self._send(200, INDEX_HTML, "text/html; charset=utf-8")
        elif self.path == "/api/status":
            self._send(200, json.dumps({"impactos": "up" if impactos_up() else "down"}))
        else:
            self._send(404, json.dumps({"error": "not found"}))

    def do_POST(self):
        if self.path == "/api/launch":
            result = start_impactos()
            self._send(200, json.dumps({"status": result}))
        else:
            self._send(404, json.dumps({"error": "not found"}))

    def log_message(self, *args):
        pass  # stil


def main():
    server = http.server.ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"[Mission Control] luistert op http://{HOST}:{PORT}")
    server.serve_forever()


if __name__ == "__main__":
    main()
