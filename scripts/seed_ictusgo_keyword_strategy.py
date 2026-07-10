#!/usr/bin/env python3
"""Zet de IctusGo keyword-strategie zichtbaar neer: Obsidian-note + Demand Engine-kansen.

De GSC-'Keyword Research'-tab is nu leeg omdat ictusgo.nl pas sinds 3 juli live is
(en GSC in 28d maar 2 queries / 6 impressies oplevert). Die tab vult zich pas bij
verkeer. Om de "lege" beleving nu al te vullen zetten we de door de agent opgestelde
keyword-strategie (5 clusters + content-gaps + GSC-startpunt) neer als:
  1. Een Obsidian-note (10_Projects/Ictusgo/artikels/keyword-strategie-ictusgo.md)
  2. Handmatige Demand Engine-kansen (POST /api/demand/opportunities) zodat de
     Kansen-pijplijn wél gevuld is en de agent er content voor kan maken.
"""
import json
import urllib.request
import urllib.error

BASE = "http://localhost:1250"
SITE_ID = "a304b082-741e-4781-9d99-584903b7295c"  # ictusgo

# 5 zoekwoord-clusters (uit de IctusGo-strategie + agent-analyse), elk met
# concreet target-keyword + gewenste positie 1-3 (ambitieus).
CLUSTERS = [
    {
        "query": "gps teambuilding",
        "angle": "Definitieve gids + landingspagina: GPS-teamtochten met sociale impact voor bedrijven",
        "rationale": "Kern-USP van IctusGo. Hoofdkeyword voor het hele concept; nu onzichtbaar in GSC.",
        "action": "nieuwe-content",
    },
    {
        "query": "teambuilding hoofddorp",
        "angle": "Lokale landingspagina TeamBuilding Hoofddorp met regiospecifieke hooks (Schiphol, Haarlemmermeer)",
        "rationale": "Lokale intentie, weinig concurrentie, hoge conversiekans voor HR/MT in de regio.",
        "action": "nieuwe-content",
    },
    {
        "query": "maatschappelijk teamuitje",
        "angle": "Pillar-artikel: teambuilding gekoppeld aan maatschappelijke impact (Geluksmomenten Score)",
        "rationale": "Scheidend onderscheid t.o.v. generieke teambuilding-aanbieders; E-E-A-T-hoek.",
        "action": "nieuwe-content",
    },
    {
        "query": "wkr teambuilding 2026",
        "angle": "AEO-artikel: WKR-vrijstelling voor teambuilding uitgelegd (fiscale HR-hoek)",
        "rationale": "Hoge commerciële intentie, weinig goede NL-content; snippet-kans.",
        "action": "nieuwe-content",
    },
    {
        "query": "csrd teambuilding",
        "angle": "AEO-artikel: CSRD/ESRS S1 en teambuilding — zo vink je sociale impact aan",
        "rationale": "Opkomende zoekvraag door CSRD-verplichtingen; vroege mover-positie mogelijk.",
        "action": "nieuwe-content",
    },
    {
        "query": "teambuilding haarlemmermeer",
        "angle": "Lokale landingspagina TeamBuilding Haarlemmermeer",
        "rationale": "Tweede regiopilaar naast Hoofddorp; vult het lokale cluster.",
        "action": "nieuwe-content",
    },
    {
        "query": "bedrijfsuitje hoofddorp schiphol",
        "angle": "Lokale landingspagina Bedrijfsuitje Hoofddorp/Schiphol",
        "rationale": "Exact-match lokale zoekintentie met koopbereidheid.",
        "action": "nieuwe-content",
    },
    {
        "query": "geluksmomenten team",
        "angle": "Uitlegartikel over de Geluksmomenten Score als meetbare teamgeluk-IX",
        "rationale": "Eigen concept/USp; versterkt autoriteit en merkzoekvolume.",
        "action": "nieuwe-content",
    },
]


def _post(path, payload, timeout=30):
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        BASE + path, data=data,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, json.loads(r.read().decode())


def main():
    print("=== 1) Demand Engine-kansen vullen (IctusGo) ===")
    ok = 0
    for c in CLUSTERS:
        try:
            status, body = _post("/api/demand/opportunities", {
                "site_id": SITE_ID,
                "query": c["query"],
                "angle": c["angle"],
                "rationale": c["rationale"],
                "action": c["action"],
                "opportunity_score": 95.0,
            })
            if status in (200, 201):
                ok += 1
                print(f"  + kans: {c['query']}")
            else:
                print(f"  ? {status} {c['query']}")
        except Exception as e:
            print(f"  ! {c['query']}: {repr(e)[:100]}")
    print(f"  aangemaakt: {ok}/{len(CLUSTERS)}\n")

    print("=== 2) Obsidian keyword-strategie note ===")
    try:
        from backend.domains.chat.obsidian import ObsidianService
        from backend.shared.config import OBSIDIAN_VAULT_PATH
        svc = ObsidianService(OBSIDIAN_VAULT_PATH)
        if not svc.is_configured:
            print("  Obsidian niet geconfigureerd — sla note over")
        else:
            lines = [
                "# IctusGo Keyword-strategie (agent-opgesteld, 9 juli 2026)",
                "",
                "**Doel:** ictusgo.nl naar positie 1-3 brengen op de kernzoekwoorden van",
                "GPS-teambuilding met sociale impact, regio Hoofddorp/Schiphol/Haarlemmermeer.",
                "",
                "## Huidige GSC-situatie (28d, peildatum 9-7-2026)",
                "- Totaal klikken: 0 | impressies: 6 | gem. positie: 13,5",
                "- Zichtbare queries: `teambuildingdag` (pos 11,8), `teambuilding wkr` (pos 33)",
                "- Site is pas sinds 3 juli live -> tab vult zich bij verkeer.",
                "",
                "## 5 zoekwoord-clusters (target: pos 1-3)",
                "",
            ]
            for i, c in enumerate(CLUSTERS, 1):
                lines.append(f"{i}. **{c['query']}** — {c['angle']}")
                lines.append(f"   - waarom: {c['rationale']}")
                lines.append("")
            lines += [
                "## Content-gaps (concurrent dekt, IctusGo niet)",
                "- Team余building outdoor / teamuitje zakelijk / samenwerking verbeteren",
                "- Lokale SEO (Hoofddorp, Haarlemmermeer, Schiphol) under-ontwikkeld",
                "- Geen merkgerelateerde of servicespecifieke landingspagina's met vertoningen",
                "",
                "## Eerste acties",
                "1. Technische indexatie-check (sitemap, noindex, crawlbaarheid)",
                "2. 3 themapagina's + lokale landingspagina's publiceren",
                "3. Externe zoekvolumes valideren (Keyword Planner / AnswerThePublic)",
                "4. Interne linking tussen nieuwe en bestaande pagina's",
                "",
                "> Geproduceerd door Agent OS Goal Mode (doel Keyword Research-fundament).",
            ]
            html = "\n".join(lines)
            path = svc.write_note(
                "Ictusgo", "keyword-strategie-ictusgo",
                "IctusGo Keyword-strategie", html,
                metadata={"keyword": "gps teambuilding", "type": "keyword-strategie",
                           "source": "agent-os"},
            )
            print(f"  weggeschreven: {path}")
    except Exception as e:
        print(f"  Obsidian fout: {repr(e)[:200]}")

    print("\nKLAAR.")


if __name__ == "__main__":
    main()
