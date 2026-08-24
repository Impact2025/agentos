#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Toby — Workforce Watchdog (Allie K. Miller: "Workforce Watchdog").

Monitort proactief het reilen en zeilen van de agent-infrastructuur en signaleert
frictiepunten vóórdat ze rode kaarten worden. Stdlib-only, ontworpen om als
no_agent-cron te draaien (levert stdout + schrijft een vault-rapport).

Probes:
  * ImpactOS :1250 /api/healthcheck  (status, backends, gateway, calendar, social, budget, stalled goals)
  * Hermes gateway :8899            (socket)
  * Ollama :11434                   (http /v1/models)
  * AI Diary digest-verversing      (leeftijd AI_DIARY_DIGEST.md)

Severity:
  RED    = ImpactOS down, gateway down, of Ollama down
  AMBER  = budget>85%, stalled goals>0, of diary >2d oud
  GREEN  = alles gezond

Output: schrijft 10_Projects/_ai_diary/Toby-LATEST.md + Toby-Report-<date>.md
        en print een beknopte samenvatting (cron-levering).
"""
import datetime
import json
import os
import socket
import subprocess
import time
import urllib.request

VAULT = r"D:/APPS/Hermes Brein/Hermes Breind/10_Projects"
DIARY_DIR = os.path.join(VAULT, "_ai_diary")
IMPACTOS = "http://localhost:1250/api/healthcheck"
GATEWAY_HOST, GATEWAY_PORT = "127.0.0.1", 8899
OLLAMA = "http://localhost:11434/v1/models"

def _http_json(url, timeout=12):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8")), r.status
    except Exception as e:
        return {"_error": str(e)}, None

def _socket_up(host, port, timeout=3):
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except Exception:
        return False

def probe_impactos():
    data, status = _http_json(IMPACTOS)
    if status != 200 or "_error" in data:
        return {"ok": False, "detail": data.get("_error", "HTTP %s" % status)}
    return {"ok": True, "raw": data}

def probe_gateway():
    return _socket_up(GATEWAY_HOST, GATEWAY_PORT)

def probe_ollama():
    _, status = _http_json(OLLAMA, timeout=4)
    return status == 200

def diary_age_days():
    p = os.path.join(DIARY_DIR, "AI_DIARY_DIGEST.md")
    if not os.path.exists(p):
        return None
    age = (datetime.datetime.now() - datetime.datetime.fromtimestamp(os.path.getmtime(p))).days
    return age

def _heal_gateway():
    """Start de Omniroute-supervisor als :8899 dood is, met zelfcontrole op de poort."""
    sup = r"D:/apps/llm-proxy/supervisor.py"
    venv_py = r"D:/APPS/impactos/.venv/Scripts/python.exe"
    if not (os.path.exists(sup) and os.path.exists(venv_py)):
        return False
    try:
        subprocess.Popen([venv_py, "-B", sup],
                         cwd=os.path.dirname(sup),
                         creationflags=0x00000008,  # DETACHED_PROCESS
                         stdout=open(os.path.join(os.path.dirname(sup), "supervisor_manual.log"), "a"),
                         stderr=subprocess.STDOUT)
    except Exception:
        return False
    for _ in range(30):
        if _socket_up(GATEWAY_HOST, GATEWAY_PORT, timeout=2):
            return True
        time.sleep(1)
    return _socket_up(GATEWAY_HOST, GATEWAY_PORT, timeout=2)


def build_report(retry_impactos=True):
    now = datetime.datetime.now()
    findings = []
    sev = "GREEN"

    # Self-heal eerst: als de gateway down is, start de supervisor vóórdat we
    # de afhankelijke probes (ImpactOS) doen — voorkomt valse 'geen route'-race.
    healed = False
    if not probe_gateway():
        healed = _heal_gateway()

    ao = probe_impactos()
    if not ao["ok"]:
        findings.append(("RED", "ImpactOS :1250 onbereikbaar — %s" % ao["detail"]))
        sev = "RED"
    else:
        r = ao["raw"]
        if r.get("status") == "degraded":
            findings.append(("AMBER", "ImpactOS status=degraded — %s" % r.get("reden", "")))
            sev = "AMBER" if sev == "GREEN" else sev
        # backends
        b = r.get("backend", {})
        for name in ("local", "openmodel", "ollama"):
            node = b.get(name, {})
            if node.get("configured") and not node.get("live"):
                findings.append(("AMBER", "Backend '%s' geconfigureerd maar niet live" % name))
                sev = "AMBER" if sev == "GREEN" else sev
        # gateway (binnen ImpactOS-health)
        gw = r.get("gateway", {})
        if gw.get("configured") and not gw.get("live"):
            if healed:
                findings.append(("AMBER", "Hermes-gateway :8899 was DOWN — Toby heeft supervisor automatisch herstart, opstart bezig"))
                sev = "AMBER" if sev == "GREEN" else sev
            else:
                findings.append(("RED", "Hermes-gateway :8899 down — %s" % gw.get("error", "")))
                sev = "RED"
        # calendar / social
        if r.get("calendar", {}).get("configured") and not r.get("calendar", {}).get("live"):
            findings.append(("AMBER", "Calendar-sync niet live"))
            sev = "AMBER" if sev == "GREEN" else sev
        if r.get("social", {}).get("configured") and not r.get("social", {}).get("live"):
            findings.append(("AMBER", "Social-inbox niet live"))
            sev = "AMBER" if sev == "GREEN" else sev
        # budget
        budget_pct = (r.get("llm") or {}).get("today", {}).get("budget_pct", 0) or 0
        if budget_pct > 85:
            findings.append(("AMBER", "LLM-budget %.1f%% verbruikt vandaag" % budget_pct))
            sev = "AMBER" if sev == "GREEN" else sev
        # stalled goals (alleen als nog niet via degraded-reden gemeld)
        stalled = len((r.get("bugs") or {}).get("stalled_goals", []) or [])
        if stalled > 0 and not any("vastgelopen doel" in f[1].lower() for f in findings):
            findings.append(("AMBER", "%d vastgelopen doel(en) — zie ImpactOS" % stalled))
            sev = "AMBER" if sev == "GREEN" else sev
        # context-samenvatting voor rapport
        summary = r.get("summary", "")

    if healed:
        # gateway was down, Toby heeft 'm herstart — herhaal ImpactOS-probe voor
        # een schone eindstatus (vermijd valse 'geen LLM-route'-race)
        time.sleep(4)
        ao = probe_impactos()
        if ao["ok"] and ao["raw"].get("status") != "degraded":
            findings = [f for f in findings if "geen enkele LLM-route" not in f[1]]

    if not probe_ollama():
        findings.append(("RED", "Ollama :11434 DOWN — lokale fallback weggevallen"))
        sev = "RED"

    age = diary_age_days()
    if age is None:
        findings.append(("AMBER", "AI Diary digest bestaat nog niet — draai `ai_diary.py digest`"))
        sev = "AMBER" if sev == "GREEN" else sev
    elif age > 2:
        findings.append(("AMBER", "AI Diary digest is %d dagen oud — context mogelijk verouderd" % age))
        sev = "AMBER" if sev == "GREEN" else sev

    if not findings:
        findings.append(("GREEN", "Alle systemen gezond — geen frictie gedetecteerd"))

    # Race-guard: bij een eenmalige 'geen LLM-route' (koude Ollama/gateway-blip),
    # herprobeer ImpactOS één keer na 4s voordat we AMBER rapporteren.
    if retry_impactos and sev == "AMBER" and any("geen enkele LLM-route" in f[1] for f in findings):
        time.sleep(4)
        ao2 = probe_impactos()
        if ao2["ok"] and ao2["raw"].get("status") != "degraded":
            findings = [f for f in findings if "geen enkele LLM-route" not in f[1]]
            if not findings:
                findings.append(("GREEN", "Alle systemen gezond — geen frictie gedetecteerd"))
            sev = "GREEN"

    report = {
        "timestamp": now.isoformat(timespec="seconds"),
        "severity": sev,
        "findings": [{"level": l, "msg": m} for l, m in findings],
        "impactos_summary": summary if ao["ok"] else None,
        "impactos_bugs": (ao["raw"].get("bugs", {}) if ao["ok"] else {}),
        "gateway": {
            "live": bool(probe_gateway()),
            "host": GATEWAY_HOST, "port": GATEWAY_PORT,
        },
        "ollama": {"live": bool(probe_ollama())},
        "diary_age_days": age,
    }
    return report, sev

DASHBOARD_HTML = """<!doctype html><html lang="nl"><head><meta charset="utf-8">
<meta http-equiv="refresh" content="120"><title>Toby — Workforce Watchdog</title>
<style>
body{font-family:system-ui,Segoe UI,Roboto,sans-serif;background:#0f1115;color:#e6e6e6;margin:0;padding:24px}
h1{font-size:20px;margin:0 0 4px}
.meta{color:#8a93a3;font-size:13px;margin-bottom:18px}
.badge{display:inline-block;padding:4px 14px;border-radius:999px;font-weight:700;font-size:14px;letter-spacing:.04em}
.GREEN{background:#1f7a3f;color:#d6ffe0}.AMBER{background:#8a6a13;color:#fff3cf}.RED{background:#8a1f1f;color:#ffd6d6}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px;margin:18px 0}
.card{background:#171b22;border:1px solid #262c36;border-radius:10px;padding:14px}
.card .k{color:#8a93a3;font-size:12px}.card .v{font-size:18px;font-weight:700;margin-top:4px}
ul{list-style:none;padding:0;margin:0}
li{padding:9px 12px;border-radius:8px;margin-bottom:8px;font-size:14px;background:#171b22;border-left:4px solid #333}
li.RED{border-color:#e0533a}li.AMBER{border-color:#e0b53a}li.GREEN{border-color:#3ae07a}
code{background:#0a0c10;padding:1px 5px;border-radius:4px;color:#9fe0ff}
</style></head><body>
<h1>Toby — Workforce Watchdog</h1>
<div class="meta" id="meta"></div>
<div><span class="badge" id="sev"></span></div>
<div class="grid" id="cards"></div>
<h3 style="margin:22px 0 10px">Bevindingen</h3>
<ul id="findings"></ul>
<script>
fetch('Toby-LATEST.json').then(r=>r.json()).then(d=>{
  document.getElementById('meta').textContent='Laatste check: '+d.timestamp;
  const sev=document.getElementById('sev'); sev.textContent=d.severity; sev.className='badge '+d.severity;
  const cards=document.getElementById('cards');
  const gw=d.gateway&&d.gateway.live?'live':'DOWN';
  const ol=d.ollama&&d.ollama.live?'live':'DOWN';
  const bugs=(d.impactos_bugs&&(d.impactos_bugs.stalled_goals||[]).length)||0;
  cards.innerHTML=[
    ['Gateway :8899',gw],['Ollama :11434',ol],
    ['ImpactOS-bugs',bugs],['Diary (dagen)',d.diary_age_days??'-']
  ].map(([k,v])=>`<div class="card"><div class="k">${k}</div><div class="v">${v}</div></div>`).join('');
  document.getElementById('findings').innerHTML=d.findings.map(f=>
    `<li class="${f.level}">[${f.level}] ${f.msg}</li>`).join('');
}).catch(e=>{document.getElementById('meta').textContent='Kon Toby-LATEST.json niet laden: '+e});
</script></body></html>
"""

def render(report):
    lines = []
    lines.append("# Toby — Workforce Watchdog Report")
    lines.append("")
    lines.append(f"- **Tijd**: {report['timestamp']}")
    lines.append(f"- **Status**: {report['severity']}")
    lines.append("")
    lines.append("## Bevindingen")
    lines.append("")
    for f in report["findings"]:
        lines.append(f"- **[{f['level']}]** {f['msg']}")
    if report.get("impactos_summary"):
        lines.append("")
        lines.append("## ImpactOS samenvatting")
        lines.append("")
        lines.append("> " + report["impactos_summary"])
    lines.append("")
    lines.append("---")
    lines.append("_Gegenereerd door Toby (Workforce Watchdog) — automatisch draaiend via cron._")
    return "\n".join(lines)

def main():
    report, sev = build_report()
    md = render(report)
    os.makedirs(DIARY_DIR, exist_ok=True)
    date = datetime.date.today().isoformat()
    with open(os.path.join(DIARY_DIR, "Toby-LATEST.md"), "w", encoding="utf-8") as f:
        f.write(md)
    with open(os.path.join(DIARY_DIR, f"Toby-Report-{date}.md"), "w", encoding="utf-8") as f:
        f.write(md)
    # machine-leesbaar + dashboard
    import json as _json
    with open(os.path.join(DIARY_DIR, "Toby-LATEST.json"), "w", encoding="utf-8") as f:
        _json.dump(report, f, ensure_ascii=False, indent=2)
    with open(os.path.join(DIARY_DIR, "dashboard.html"), "w", encoding="utf-8") as f:
        f.write(DASHBOARD_HTML)
    # stdout: beknopt voor cron-levering
    print(f"[TOBY {sev}] {datetime.datetime.now().isoformat(timespec='seconds')}")
    for f in report["findings"]:
        print(f"  [{f['level']}] {f['msg']}")
    print(f"  -> rapport: {os.path.join(DIARY_DIR, 'Toby-LATEST.md')}")
    print(f"  -> dashboard: {os.path.join(DIARY_DIR, 'dashboard.html')}")

if __name__ == "__main__":
    main()
