#!/usr/bin/env python3
"""Maak de 3 autonome 'doelen' aan voor Pootgelukkig (zoals BewaardVoorJou /
teambuildingmetimpact die hebben). Elk goal-plan doet live LLM-decompositie en
kan >60s duren, dus per call een ruime timeout. Goals landen in 'draft' — de
agent confirmt + start ze zelf via de API (zie start-doelen hieronder)."""
import json
import urllib.request
import urllib.error

BASE = "http://localhost:1250/api/goals"
PROJECT = "pootgelukkig"

GOALS = [
    {
        "title": "Radar-warmup: signaalgeschiedenis opbouwen",
        "objective": (
            "Bouw een robuuste Mission Radar-signaalgeschiedenis voor Pootgelukkig op: "
            "50+ actieve signalen, wekelijks scanrapport op basis van de pootgelukkig-watchlist "
            "(concurrenten dierenbescherming/ikzoekbaas/verhuisdieren, adoptie-keywords "
            "hond/kat/herplaatsing/konijn, RSS Dierenbescherming/LICG/Dierennoodhulp). "
            "Identificeer maandelijks de top-5 contentkansen uit de signalen en schrijf die "
            "terug naar de Obsidian-vault (10_Projects/_trends/)."
        ),
    },
    {
        "title": "AEO-contentmotor: signalen naar adoptie-content",
        "objective": (
            "Zet goedgekeurde Mission Radar-signalen om in gepubliceerde SEO-content voor "
            "pootgelukkig.nl volgens de wereldklasse-SEO-regels (focuskeyword in titel + intro, "
            "FAQ/HowTo-schema, interne links naar /blog en /kennisbank, externe links naar "
            "dierenbescherming.nl / licg.nl). Doel: 4 gepubliceerde artikelen/landingspagina's "
            "per maand, afgestemd op de adoptant- en asiel-hubs. Publicatie blijft achter de "
            "menselijke Wachtrij-gate (Pootgelukkig publiceert via eigen Next.js/Prisma-stack, "
            "dus de AEO-motor schrijft een workspace-concept + 'Plaats in Wachtrij'-taak; de "
            "daadwerkelijke push naar de site vereist de Pootgelukkig-publish-pijplijn)."
        ),
    },
    {
        "title": "Adoptie-autoriteit: posities op money-keywords",
        "objective": (
            "Verover topposities voor Pootgelukkig op de strategische money-keywords: "
            "hond adopteren, kat adopteren, asieldier adopteren, herplaatster hond, "
            "konijn adopteren, wat kost een huisdier, vrijwilliger dierenasiel. Gebruik de "
            "bestaande 28 blog + 25 KB-artikelen + nieuwe AEO-content + hub-spoke interne "
            "link-graph om autoriteit op te bouwen. Rapporteer maandelijks positieverschil "
            "via GSC (zodra pootgelukkig.nl aan GSC is gekoppeld in ImpactOS)."
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
