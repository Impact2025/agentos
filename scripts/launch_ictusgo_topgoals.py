#!/usr/bin/env python3
"""Lanceer 3 AMBITIEUZE IctusGo-doelen: posities 1-3 veroveren.

Vincent wil de top halen. Deze doelen zijn expliciet gericht op nr.1-posities
(op de kernzoekwoorden van IctusGo) en zetten de agent maximaal aan het werk:
meerdere pillar-artikelen + lokale landingspagina's + AEO-artikelen + interne
linkstructuur + technische indexatie-check. Alles staged in de Wachtrij (geen
auto-publicatie). De agent werkt de content af; Vincent keurt goed.

Flow: plan (Hermes) -> confirm -> start (achtergrond-executie-loop).
"""
import json
import time
import urllib.request
import urllib.error

BASE = "http://localhost:1250/api/goals"
PROJECT = "ictusgo"

# Ambitieuze doelen: target posities 1-3 op de belangrijkste zoekwoorden.
GOALS = [
    (
        "NR.1 op 'gps teambuilding': dominante pillar + cluster veroveren",
        "Mission: ictusgo.nl op positie 1-3 krijgen voor het kernzoekwoord "
        "'gps teambuilding' en het directe cluster. Schrijf een uitgebreide pillar "
        "(2500+ woorden) 'GPS teambuilding: de complete gids voor bedrijven' met "
        "FAQPage-schema en featured-snippet-structuur, plus 3 ondersteunende "
        "artikelen ('gps teamuitje bedrijf', 'gps teambuilding outdoor', 'wat is een "
        "GPS-teamtocht'). Optimaliseer de bestaande /gps-teamuitje landingspagina "
        "(title, H1, interne links, schema). Bouw een interne linkstructuur van alle "
        "GPS-artikelen naar de pillar en naar de conversiepagina. Doe SEO-onderzoek "
        "naar de top-10 concurrenten (flitz-events, citygame, scavenger) en hun "
        "zwakke punten. Publiceer alles in de Wachtrij ter review. Geen auto-publicatie.",
    ),
    (
        "Lokale NR.1 in de regio: Hoofddorp + Haarlemmermeer + Schiphol domineren",
        "Mission: positie 1-3 voor 'teambuilding hoofddorp', 'teambuilding "
        "haarlemmermeer' en 'bedrijfsuitje hoofddorp schiphol'. Schrijf drie lokale "
        "landingspagina's met regiospecifieke hooks (Schiphol, N201, Haarlemmermeerse "
        "bossen, lokale bedrijvenparken), elk met lokale testimonial-achtige "
        "voorbeelden en een duidelijke CTA. Doe keyword research naar lokale "
        "zoekintentie en concurrenten in de regio (teamevents.nl, meetinn.nl, "
        "eventfully.nl). Koppel de lokale pagina's aan de GPS-pillar via interne links. "
        "Publiceer alles in de Wachtrij ter review. Geen auto-publicatie.",
    ),
    (
        "Autoriteit op 'maatschappelijk teamuitje' + 'wkr/csrd teambuilding' (AEO nr.1)",
        "Mission: positie 1-3 voor 'maatschappelijk teamuitje', 'wkr teambuilding 2026' "
        "en 'csrd teambuilding' — de onderscheidende, hoog-conversie hoek van IctusGo. "
        "Schrijf drie AEO-optimale artikelen (FAQPage + direct-answer + E-E-A-T): "
        "(1) 'Maatschappelijk teamuitje: zo koppel je teamgeluk aan sociale impact', "
        "(2) 'WKR teambuilding 2026: wat mag wel en wat niet (fiscaal)', "
        "(3) 'CSRD en teambuilding: zo vink je maatschappelijke impact aan (ESRS S1)'. "
        "Versterk de Geluksmomenten Score als meetbaar concept in elk artikel. Doe "
        "SEO-onderzoek naar snippet-posities en concurrenten. Publiceer in de Wachtrij "
        "ter review. Geen auto-publicatie.",
    ),
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
    created = []
    for title, objective in GOALS:
        print("\n" + "=" * 70)
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
