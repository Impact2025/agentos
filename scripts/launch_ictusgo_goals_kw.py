#!/usr/bin/env python3
"""Lanceer 3 nieuwe langetermijndoelen voor IctusGo met Keyword Research als kernpijler.

De 'Keyword Research'-tab in Agent OS haalt GSC keyword-gaps (voor IctusGo nu
praktisch leeg: 2 queries, 0 gaps). Een Goal Mode-doel kan GSC niet vullen, maar
het KAN wel echte keyword-onderzoekstaken uitvoeren (Tavily + kennisbank):
zoekwoord-clusters, content-gaps en een keyword-map opleveren, plus de artikelen
die die clusters moeten veroveren. Tegelijk voegen we nieuwe keyword-watch-items
toe aan de Mission Radar watchlist zodat de Radar méér signalen oppikt.

Flow per doel: plan (Hermes) -> confirm -> start (achtergrond-executie-loop).
"""
import json
import time
import urllib.request
import urllib.error

BASE = "http://localhost:1250/api/goals"
RADAR = "http://localhost:1250/api/radar"
PROJECT = "ictusgo"

GOALS = [
    (
        "Keyword Research-fundament: zoekwoord-clusters & content-gaps IctusGo",
        "Voer systematisch keyword research uit voor ictusgo.nl met Tavily en de "
        "bestaande kennisbank. Bouw 5 zoekwoord-clusters rond de kern van IctusGo: "
        "(1) gps teambuilding, (2) teambuilding regio Hoofddorp/Schiphol/Haarlemmermeer, "
        "(3) maatschappelijk/sociale impact teamuitje, (4) wkr teambuilding 2026 en "
        "csrd teambuilding, (5) geluksmomenten/teamgeluk. Lever per cluster een "
        "keyword-map (hoofdkeyword, long-tail, zoekintentie, moeilijkheidsschatting) en "
        "een lijst content-gaps (onderwerpen die concurrenten ranken maar ictusgo.nl "
        "nog niet dekt). Schrijf een 'keyword-strategie' document en publiceer het als "
        "intern concept in de Wachtrij ter review. Geen auto-publicatie.",
    ),
    (
        "Regionaal content-cluster: teambuilding Hoofddorp, Schiphol & Haarlemmermeer",
        "Verover de lokale zoekvraag rond teambuilding in de regio met 4 artikelen voor "
        "ictusgo.nl, gebaseerd op keyword research naar regiospecifieke intentie: "
        "'teambuilding hoofddorp', 'teambuilding Haarlemmermeer', 'bedrijfsuitje Hoofddorp "
        "Schiphol', en 'uitje team Schiphol-regio'. Doe eerst SEO-onderzoek naar de "
        "lokale concurrenten (teamevents.nl, eventfully.nl, meetinn.nl) en hun posities, "
        "schrijf de 4 artikelen met lokale hooks, redigeer ze en publiceer ze in de "
        "Wachtrij ter review. Geen auto-publicatie.",
    ),
    (
        "Sociale-impact autoriteit: AEO + gastblog-keywords naar welzijnskoepels",
        "Versterk de autoriteit van ictusgo.nl op het snijvlak teambuilding en sociale "
        "impact. Doe keyword research naar wat welzijnskoepels, VNG en ANBI-partners "
        "ranken op (vrijwilligers teambuilding, maatschappelijke stage, buurtinitiatief "
        "team, social return on investment). Identificeer 20 backlink- en gastblog-bronnen "
        "en schrijf per bron een gepersonaliseerd outreach-concept plus gastblog-voorstel "
        "gefundeerd op de gevonden keywords. Publiceer een 'partner & impact'-pagina in "
        "de Wachtrij ter review. Geen auto-versturen.",
    ),
]

# Extra keyword-watch-items voor de Mission Radar (voedt de Radar-tab en
# de contentpijplijn met méér signalen). Deze vullen de Radar, niet de
# GSC-keyword-gaps-tab, maar vergroten wel de keyword-dekking van IctusGo.
NEW_WATCH_KEYWORDS = [
    ("gps teambuilding bedrijf", "KW: gps teambuilding bedrijf"),
    ("teambuilding schiphol regio", "KW: teambuilding schiphol regio"),
    ("maatschappelijke stage teambuilding", "KW: maatschappelijke stage teambuilding"),
    ("social return on investment team", "KW: social return on investment team"),
    ("buurtinitiatief teamuitje", "KW: buurtinitiatief teamuitje"),
    ("teamgeluk meten", "KW: teamgeluk meten"),
    ("vrijwilligers teambuilding bedrijf", "KW: vrijwilligers teambuilding bedrijf"),
    ("esrs s1 teambuilding", "KW: esrs s1 teambuilding"),
    ("wig teambuilding", "KW: wig teambuilding"),
    ("outplacement teambuilding", "KW: outplacement teambuilding"),
]


def _post(path, payload, timeout=240):
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        path, data=data,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, json.loads(r.read().decode())


def main():
    # 1) Nieuwe keyword-watch-items toevoegen aan Radar
    print("=== Mission Radar watchlist uitbreiden (keywords) ===")
    ok = skip = err = 0
    for value, label in NEW_WATCH_KEYWORDS:
        try:
            status, _ = _post(RADAR + "/watch-list", {
                "project": PROJECT, "label": label, "type": "keyword", "value": value,
            }, timeout=20)
            if status == 201:
                ok += 1
                print(f"  + {value}")
            else:
                skip += 1
        except Exception as e:
            err += 1
            print(f"  ! {value}: {repr(e)[:100]}")
    print(f"  toegevoegd: {ok} | fouten: {err}\n")

    # 2) Drie nieuwe doelen aanmaken
    created = []
    for title, objective in GOALS:
        print("=" * 70)
        print(f"DOEL: {title}")
        print("=" * 70)
        try:
            status, body = _post(BASE + "/plan", {
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
            print(f"  ~ dup -> {goal_id}; overgeslagen")
            continue

        plan = body.get("plan", {})
        print(f"  goal_id: {goal_id}")
        print(f"  plan_summary: {plan.get('plan_summary', '')[:150]}")
        for i, ph in enumerate(plan.get("phases", []), 1):
            tasks = ph.get("tasks", [])
            skills = ", ".join(sorted({t.get('skill', '?') for t in tasks}))
            print(f"    F{i}: {ph.get('title')}  [{skills}]  ({len(tasks)} taken)")

        try:
            _post(BASE + "/confirm", {"goal_id": goal_id})
            print("  [confirm] OK")
        except Exception as e:
            print(f"  ! confirm fout: {e}")
            continue
        try:
            _post(BASE + "/start", {"goal_id": goal_id})
            print("  [start] OK -- agent loopt")
        except Exception as e:
            print(f"  ! start fout: {e}")
        created.append(goal_id)
        time.sleep(1)

    print("\n" + "=" * 70)
    print(f"Doelen aangemaakt/gestart: {len(created)}")
    for g in created:
        print(f"  - {g}")
    print("=" * 70)


if __name__ == "__main__":
    main()
