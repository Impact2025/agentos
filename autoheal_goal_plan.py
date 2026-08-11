#!/usr/bin/env python3
"""
AgentOS auto-heal: voorkomt dat een goal blijvend vastloopt op
"Plan niet gevonden - voer eerst create_and_plan uit."

Wanneer create_and_plan (LLM-decompositie via de :8899 proxy) faalt of de
server herstart voordat plan.json weggeschreven is, blijft een goal in
status 'draft' hangen ZONDER plan.json. De Control Room toont die goal dan
als "wacht op jou" met de foutmelding hierboven, en de UI-knop "Mislukt -
opnieuw" roept intern alleen confirm_plan aan -> blijft falen.

Deze healer detecteert elke draft-goal zonder plan.json, schrijft een
valide plan.json (zonder LLM - alleen DB-gegevens + een generiek
3-fasen/6-taken plan) en bevestigt hem via POST /api/goals/confirm. Na
confirm staat de goal op 'ready' en verdwijnt hij uit "wacht op jou".

Idempotent: draait veilig elke 30 min; als er niets te doen is, doet hij
niets (geen output -> geen notificatie bij no_agent=True).

Auth: AgentOS sessie-cookie via /api/auth/login met AGENTOS_PASSWORD.
"""
import json
import os
import sqlite3
import urllib.request
import urllib.error

# BASE wijst naar de AgentOS-repo. Default: naast dit script, maar de cron
# draait vanuit ~/.hermes/scripts, dus override via AGENTOS_BASE of harde default.
BASE = os.environ.get("AGENTOS_BASE") or r"D:\apps\agentos"
DB = os.path.join(BASE, "data", "agentos.db")
GOALS_WS = os.path.join(BASE, "projects", "_goals")
API = "http://localhost:1250"
PASSWORD = os.environ.get("AGENTOS_PASSWORD", "Test1234")

# Generieke fallback-planstructuur die confirm_plan verwacht:
# {"phases":[{"title","description","tasks":[{"title","description","skill"}]}],"plan_summary":...}
def make_plan(title: str) -> dict:
    t = (title or "Doel").strip()
    return {
        "plan_summary": f"Automatisch hersteld plan voor: {t}. " +
                        "Voer het doel uit in een gestructureerde 3-fasen aanpak " +
                        "(voorbereiden, uitvoeren, publiceren/verifiëren).",
        "estimated_duration": "ca. 1-3 dagen",
        "phases": [
            {
                "title": "Voorbereiding",
                "description": f"Analyseer en plan de uitvoering van: {t}.",
                "tasks": [
                    {"title": f"Analyseer scope van: {t}",
                     "description": "Bepaal de concrete deelopdrachten en randvoorwaarden.",
                     "skill": "content-writer"},
                    {"title": "Stel uitvoeringsplan op",
                     "description": "Rangschik de werkzaamheden en bepaal dependencies.",
                     "skill": "content-writer"},
                ],
            },
            {
                "title": "Uitvoering",
                "description": "Voer de kernwerkzaamheden uit.",
                "tasks": [
                    {"title": f"Voer uit: {t}",
                     "description": "Realiseer de hoofdactiviteit van dit doel.",
                     "skill": "content-writer"},
                    {"title": "Kwaliteitscontrole op geleverde output",
                     "description": "Review en verbeter de output voordat deze live gaat.",
                     "skill": "content-editor"},
                ],
            },
            {
                "title": "Publicatie & verificatie",
                "description": "Rol de output uit en controleer het resultaat.",
                "tasks": [
                    {"title": "Publiceer / lever op",
                     "description": "Maak de output beschikbaar op de juiste plek.",
                     "skill": "publisher"},
                    {"title": "Verifieer eindresultaat",
                     "description": "Controleer dat het doel daadwerkelijk is bereikt.",
                     "skill": "content-judge"},
                ],
            },
        ],
    }


def api_login() -> str:
    req = urllib.request.Request(
        f"{API}/api/auth/login",
        data=json.dumps({"password": PASSWORD}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        raw = r.read().decode()
    # cookie zit in response headers; we halen de session-cookie op
    cookie = r.headers.get("Set-Cookie", "")
    return cookie


def api_confirm(goal_id: str, cookie: str) -> bool:
    req = urllib.request.Request(
        f"{API}/api/goals/confirm",
        data=json.dumps({"goal_id": goal_id}).encode(),
        headers={
            "Content-Type": "application/json",
            "Cookie": cookie,
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.status == 200


def main() -> None:
    if not os.path.isfile(DB):
        print(f"[heal] geen DB op {DB} - skip")
        return
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT id, title, status FROM goals WHERE status = 'draft'"
    ).fetchall()
    conn.close()

    fixed = []
    for row in rows:
        gid = row["id"]
        ws = os.path.join(GOALS_WS, gid)
        plan_file = os.path.join(ws, "plan.json")
        if os.path.exists(plan_file):
            continue  # heeft al een plan - niet onze verantwoordelijkheid
        # draft zonder plan.json -> herstel
        os.makedirs(ws, exist_ok=True)
        plan = make_plan(row["title"])
        with open(plan_file, "w", encoding="utf-8") as f:
            json.dump(plan, f, indent=2, ensure_ascii=False)
        try:
            cookie = api_login()
            if not cookie:
                print(f"[heal] login gaf geen cookie voor {gid} - skip")
                continue
            ok = api_confirm(gid, cookie)
            if ok:
                fixed.append(gid)
                print(f"[heal] FIXED {gid}: plan.json geschreven + confirmed")
            else:
                print(f"[heal] plan.json geschreven maar confirm faalde voor {gid}")
        except Exception as e:
            print(f"[heal] fout bij confirm {gid}: {e}")

    if not fixed:
        # geen output -> no_agent cron stuurt niets
        pass
    else:
        print(f"[heal] {len(fixed)} goal(s) hersteld.")


if __name__ == "__main__":
    main()
