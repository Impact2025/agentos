#!/usr/bin/env python3
"""Seed de Mission Radar watchlist voor Pootgelukkig (asieldier-adoptieplatform).

Drie lagen (zoals IctusGo/Teambuilding):
  - competitor: NL dieren-asiel / adoptie / dierenwelzijn spelers (site:-monitoring)
  - keyword:    content-gap + money-keywords uit de Pootgelukkig SEO-inventaris
  - rss:        NL dierenwelzijn / huisdier / zorg-nieuws (geen Tavily-quota)

Idempotent: slaat over als (project,type,value) al actief bestaat.
Draait tegen de live API op localhost:1250.
"""
import json
import urllib.request
import urllib.error

BASE = "http://localhost:1250/api/radar"
PROJECT = "pootgelukkig"

# (type, value, label)
ITEMS = [
    # ── Concurrenten / verwante NL adoptie- & dierenwelzijn-spelers ──────
    ("competitor", "dierenbescherming.nl", "Dierenbescherming (landelijk asiel/dierenwelzijn)"),
    ("competitor", "ikzoekbaas.nl", "IkZoekBaas.nl (adoptie-marktplaats dieren)"),
    ("competitor", "verhuisdieren.nl", "Verhuisdieren.nl (herplaatsing)"),
    ("competitor", "dierentehuis.nl", "Dierentehuis.nl (asiel-directory)"),
    ("competitor", "zooplaats.nl", "Zooplaats.nl (dierenmarktplaats)"),
    ("competitor", "dierenasiels.nl", "Dierenasiels.nl (asiel-overzicht)"),
    ("competitor", "licg.nl", "LICG (Landelijk Informatiecentrum Gezelschapsdieren)"),
    ("competitor", "diervriendelijk.nl", "Diervriendelijk.nl (asiel-nieuws)"),

    # ── Content-gap / money-keywords (SEO-onderzoek Pootgelukkig) ────────
    ("keyword", "hond adopteren", "KW: hond adopteren (head)"),
    ("keyword", "kat adopteren", "KW: kat adopteren (head)"),
    ("keyword", "asieldier adopteren", "KW: asieldier adopteren"),
    ("keyword", "herplaatser hond", "KW: herplaatser hond (alternatief voor asiel)"),
    ("keyword", "konijn adopteren", "KW: konijn adopteren (niche dier)"),
    ("keyword", "puppy adopteren", "KW: puppy adopteren"),
    ("keyword", "senior kat adopteren", "KW: senior kat adopteren"),
    ("keyword", "angstige hond adopteren", "KW: angstige hond adopteren"),
    ("keyword", "wat kost een huisdier", "KW: wat kost een huisdier (kosten-informatie)"),
    ("keyword", "kennismakingsprotocol hond", "KW: kennismakingsprotocol hond"),
    ("keyword", "dierenasiel bij mij in de buurt", "KW: dierenasiel bij mij in de buurt (lokaal)"),
    ("keyword", "vrijwilliger dierenasiel", "KW: vrijwilliger dierenasiel"),
    ("keyword", "werkdruk in asielen", "KW: werkdruk in asielen (B2B asiel)"),
    ("keyword", "medische dossiers asiel digitaliseren", "KW: medische dossiers asiel digitaliseren (B2B)"),
    ("keyword", "retourpercentage adoptie verlagen", "KW: retourpercentage adoptie verlagen (B2B)"),
    ("keyword", "ai matching asiel", "KW: ai matching asiel (B2B differentiator)"),
    ("keyword", "impactrapportage asiel", "KW: impactrapportage asiel (B2B)"),

    # ── Thematische RSS (dierenwelzijn / huisdier / zorg-nieuws) ─────────
    ("rss", "https://www.dierenbescherming.nl/rss.xml", "RSS: Dierenbescherming nieuws"),
    ("rss", "https://www.licg.nl/feed/", "RSS: LICG (gezelschapsdieren)"),
    ("rss", "https://www.dierennoodhulp.nl/feed/", "RSS: Dierennoodhulp"),
    ("rss", "https://www.zooplaats.nl/rss", "RSS: Zooplaats nieuws"),
]


def post(path, payload):
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        BASE + path, data=data,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        return r.status, r.read().decode()


def main():
    ok, skip, err = 0, 0, 0
    for wtype, value, label in ITEMS:
        try:
            status, body = post("/watch-list", {
                "project": PROJECT, "label": label,
                "type": wtype, "value": value,
            })
            if status == 201:
                ok += 1
                print(f"  + {wtype:10} {value}")
            else:
                skip += 1
                print(f"  ? {status} {value} -> {body[:80]}")
        except urllib.error.HTTPError as e:
            err += 1
            print(f"  ! {wtype} {value}: HTTP {e.code} {e.read().decode()[:120]}")
        except Exception as e:
            err += 1
            print(f"  ! {wtype} {value}: {e}")
    print(f"\nToegevoegd: {ok} | overgeslagen: {skip} | fouten: {err}  (totaal geprobeerd: {len(ITEMS)})")


if __name__ == "__main__":
    main()
