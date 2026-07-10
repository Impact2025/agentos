#!/usr/bin/env python3
"""Lanceer 3 serieuze IctusGo-doelen in Agent OS (plan -> confirm -> start).

Stap 1 plant (Hermes-decompositie), toont het plan, bevestigt het en start de
achtergrond-executie-loop. Doelen zijn bewust multi-tool (research, seo,
analyst, content-writer, publisher->Wachtrij, outreach) zodat de agent alle
pijlers van Agent OS benut.
"""
import json
import time
import urllib.request
import urllib.error

BASE = "http://localhost:1250/api/goals"
PROJECT = "ictusgo"

# (titel, doelstelling) -- elke doelstelling trigger de planner op meerdere
# deliverables zodat verschillende skills ingezet worden.
GOALS = [
    (
        "Lead-magnet & conversie: Geluksmomenten Score als aanvraag-aanjager",
        "Bouw een lead-magnet rond de Geluksmomenten Score (GMS) voor ictusgo.nl: "
        "een downloadbare checklist '15 manieren om teamgeluk te meten' plus een gids "
        "'Zo koppel je teambuilding aan sociale impact'. Schrijf 2 ondersteunende "
        "artikelen (over de GMS en over maatschappelijke teambuilding) en publiceer de "
        "landingspagina plus artikelen in de Wachtrij ter review. Analyseer eerst de "
        "huidige conversie-punten op de bestaande landingspagina's via beschikbare data. "
        "Doel: meer gekwalificeerde aanvragen uit HR/MT rond Hoofddorp/Schiphol.",
    ),
    (
        "AEO-dominatie op de fiscale HR-hoek: WKR & CSRD teambuilding",
        "Verover de vragen rond WKR-teamuitjes en CSRD/ESRS S1 teambuilding met 3 "
        "AEO-optimale artikelen voor ictusgo.nl (incl. FAQPage-schema en "
        "featured-snippet structuur): 'WKR teambuilding 2026: wat mag wel en niet', "
        "'CSRD en teambuilding: zo vink je maatschappelijke impact aan', en "
        "'Bedrijfsuitje zonder WKR: 7 legale alternatieven'. Schrijf en publiceer ze in "
        "de Wachtrij, en doe SEO-onderzoek naar de beste snippet-posities. Geen "
        "auto-publicatie — mens keurt goed.",
    ),
    (
        "Offsite autoriteit & backlink-fundament voor IctusGo",
        "Verhoog de domeinautoriteit van ictusgo.nl door 25 relevante backlink-bronnen "
        "te identificeren (welzijnskoepels, VNG, brancheverenigingen, ANBI-directory's, "
        "regio Haarlemmermeer/Schiphol partners) en per bron een gepersonaliseerd "
        "outreach-concept plus gastblog-voorstel te schrijven. Analyseer eerst de "
        "huidige offsite-positie via beschikbare data. Publiceer een 'partner-pagina' "
        "op ictusgo.nl die de sociale-impact-relaties toont. Geen auto-versturen.",
    ),
]


def _post(path, payload):
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        BASE + path, data=data,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(req, timeout=240) as r:
        return r.status, json.loads(r.read().decode())


def main():
    created = []
    for title, objective in GOALS:
        print("\n" + "=" * 70)
        print(f"DOEL: {title}")
        print("=" * 70)

        # Stap 1: plan (Hermes-decompositie)
        try:
            status, body = _post("/plan", {
                "title": title, "objective": objective, "project": PROJECT,
            })
        except urllib.error.HTTPError as e:
            print(f"  ! plan HTTP {e.code}: {e.read().decode()[:200]}")
            continue
        except Exception as e:
            print(f"  ! plan fout: {e}")
            continue

        goal_id = body.get("goal_id")
        if body.get("duplicate_of_existing"):
            print(f"  ~ dup -> {goal_id} ({body.get('title')}); overgeslagen")
            continue

        plan = body.get("plan", {})
        print(f"  goal_id: {goal_id}")
        print(f"  plan_summary: {plan.get('plan_summary', '')[:160]}")
        print(f"  fasen: {len(plan.get('phases', []))}")
        for i, ph in enumerate(plan.get("phases", []), 1):
            tasks = ph.get("tasks", [])
            skills = ", ".join(sorted({t.get('skill', '?') for t in tasks}))
            print(f"    F{i}: {ph.get('title')}  [{skills}]  ({len(tasks)} taken)")

        # Stap 2: confirm
        try:
            _post("/confirm", {"goal_id": goal_id})
            print("  [confirm] OK")
        except Exception as e:
            print(f"  ! confirm fout: {e}")
            continue

        # Stap 3: start (achtergrond-loop in server)
        try:
            _post("/start", {"goal_id": goal_id})
            print("  [start] OK -- agent loopt")
        except Exception as e:
            print(f"  ! start fout: {e}")

        created.append(goal_id)
        time.sleep(1)

    print("\n" + "=" * 70)
    print(f"Aangemaakt/gestart: {len(created)} doelen")
    for g in created:
        print(f"  - {g}")
    print("=" * 70)


if __name__ == "__main__":
    main()
