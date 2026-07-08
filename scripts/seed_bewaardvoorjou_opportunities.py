#!/usr/bin/env python3
"""Maak manual Demand-kansen aan voor Bewaardvoorjou op de 3 prioriteits-
gap-keywords uit de concurrentieanalyse, zodat de content-machine ze kan
uitwerken tot publicatie-klare artikelen."""
import json
import urllib.request

BASE = "http://localhost:1250/api/demand/opportunities"
SITE = "1e3e5bc6-982e-489f-bfb9-22313b33edb4"

OPS = [
    {
        "query": "levensverhaal laten schrijven kosten",
        "angle": "Prijsvergelijking-gids: Bewaardvoorjou (gratis te starten) vs ghostwriters "
                 "(€2.000-€10.000). Toon de echte kosten en waarom een digitaal platform "
                 "dezelfde waarde levert voor een fractie.",
        "rationale": "Zoekvolume 100-300/mnd, hoge koopintentie. Concurrenten hebben geen "
                     "informatieve content op deze term — grote gap-kans.",
        "action": "nieuwe-content", "opportunity_score": 92.0,
    },
    {
        "query": "biografie laten schrijven",
        "angle": "Complete gids 'biografie laten schrijven': wat komt erbij kijken, DIY vs "
                 "ghostwriter, en hoe Bewaardvoorjou's AI-interviewer het verschil maakt.",
        "rationale": "Zoekvolume 200-500/mnd. Breed informatief — voedt de gehele "
                     "levensverhaal-cluster.",
        "action": "nieuwe-content", "opportunity_score": 88.0,
    },
    {
        "query": "cadeau 70 jaar",
        "angle": "Cadeau-gids voor 70-jarigen: 10 originele ideeën, met levensverhaal/"
                 "familiearchief als persoonlijk, blijvend cadeau (seizoensgebonden piek).",
        "rationale": "Zoekvolume 200-400/mnd, seizoensgebonden (verjaardagen). "
                     "Hoge CTR-kans voor cadeau-gevers (kinderen).",
        "action": "nieuwe-content", "opportunity_score": 85.0,
    },
]


def post(payload):
    data = json.dumps(payload).encode()
    req = urllib.request.Request(BASE, data=data,
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=15) as r:
        return r.status, r.read().decode()


def main():
    for op in OPS:
        p = dict(op, site_id=SITE)
        try:
            st, body = post(p)
            print(f"  {st} {op['query']}")
        except Exception as e:
            print(f"  ! {op['query']}: {e}")


if __name__ == "__main__":
    main()
