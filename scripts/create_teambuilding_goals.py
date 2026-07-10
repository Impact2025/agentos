#!/usr/bin/env python3
"""Maak de 3 autonome 'doelen' aan voor teambuildingmetimpact (zoals bewaardvoorjou
die heeft). Elk goal-plan doet live LLM-decompositie en kan >60s duren, dus per
call een ruime timeout. Goals landen in 'draft' — Vincent moet ze daarna
confirm + starten in de Doelen-tab (de agent start niet zelfstandig executie)."""
import json
import urllib.request
import urllib.error

BASE = "http://localhost:1250/api/goals"
PROJECT = "teambuildingmetimpact"

GOALS = [
    {
        "title": "Radar-warmup: signaalgeschiedenis opbouwen",
        "objective": (
            "Bouw een robuuste Mission Radar-signaalgeschiedenis voor Teambuilding met Impact "
            "op: 50+ actieve signalen, wekelijks scanrapport op basis van de "
            "teambuildingmetimpact-watchlist (concurrenten, WKR/CSRD/ESG-keywords, regio "
            "Haarlemmermeer/Schiphol, RSS Frankwatching/MKB/NU.nl). Identificeer maandelijks "
            "de top-5 contentkansen uit de signalen en schrijf die terug naar de Obsidian-vault."
        ),
    },
    {
        "title": "AEO-contentmotor: signalen naar gepubliceerde impact-content",
        "objective": (
            "Zet goedgekeurde Mission Radar-signalen om in gepubliceerde SEO-content voor "
            "teambuildingmetimpact.nl volgens de v2.0-regels (0x 'u', geen AI-woorden, eerste "
            "persoon Vincent, unieke slugs, geen parser-onvriendelijke syntax). Doel: 4 "
            "gepubliceerde artikelen/landingspagina's per maand, afgestemd op de "
            "impact-day / bedrijfsvrijwilligerswerk / LEGO Serious Play hubs. Publicatie "
            "verloopt via de seed-blogs.js-pijplijn en blijft achter de menselijke Wachtrij-gate."
        ),
    },
    {
        "title": "ESG/regio-dominatie: posities op money-keywords",
        "objective": (
            "Verover topposities voor Teambuilding met Impact op de strategische money-keywords: "
            "bedrijfsvrijwilligerswerk organiseren, impact day organiseren, mvo teambuilding esg, "
            "csrd teambuilding, wkr teambuilding 2026, lego serious play teambuilding, en de "
            "lokale termen teambuilding haarlemmermeer / hoofddorp. Gebruik de bestaande 21 "
            "artikelen + nieuwe AEO-content + hub-spoke interne link-graph om autoriteit op te "
            "bouwen. Rapporteer maandelijks positieverschil via GSC (sc-domain:teambuildingmetimpact.nl)."
        ),
    },
]


def post_plan(payload, timeout=240):
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        BASE + "/plan", data=data,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.read().decode()


def main():
    for g in GOALS:
        try:
            status, body = post_plan({**g, "project": PROJECT})
            print(f"[goal] {status} | {g['title'][:50]} -> {body[:160]}")
        except urllib.error.HTTPError as e:
            print(f"[goal] HTTP {e.code} | {g['title'][:50]} -> {e.read().decode()[:160]}")
        except Exception as e:
            print(f"[goal] ERR | {g['title'][:50]} -> {e}")


if __name__ == "__main__":
    main()
