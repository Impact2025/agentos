#!/usr/bin/env python3
"""Seed de Mission Radar watchlist voor Bewaardvoorjou met echte concurrenten
en content-gap keywords uit de concurrentieanalyse."""
import json
import urllib.request

BASE = "http://localhost:1250/api/radar"
PROJECT = "bewaardvoorjou"

# (type, value, label)
ITEMS = [
    # ── Concurrenten (site:-monitoring) ──────────────────────────────────
    ("competitor", "levensverhaal.nl", "Levensverhaal.nl (ghostwriter €6.499)"),
    ("competitor", "mijnlevensverhaal.nl", "MijnLevensverhaal.nl (zelfde eigenaar)"),
    ("competitor", "astoldby.nl", "Astoldby.nl (DIY 52 weken €79-115)"),
    ("competitor", "boekmakers.nl", "Boekmakers.nl (ghostwriter €6-10k)"),
    ("competitor", "schrijversgezocht.nl", "SchrijversGezocht.nl (vanaf €2.000)"),
    ("competitor", "deportretschrijver.nl", "De Portretschrijver (€3-5k)"),
    ("competitor", "probook.nl", "Probook.nl (zelf doen vanaf €500)"),
    ("competitor", "ditisjeleven.nl", "DitIsJeLeven.nl (€4-8k)"),
    # ── Content-gap keywords (zoekvolume uit concurrentieanalyse) ─────────
    ("keyword", "levensverhaal laten schrijven kosten", "KW: kosten levensverhaal"),
    ("keyword", "biografie laten schrijven", "KW: biografie laten schrijven"),
    ("keyword", "levensboek maken", "KW: levensboek maken"),
    ("keyword", "memoires schrijven", "KW: memoires schrijven"),
    ("keyword", "cadeau 70 jaar", "KW: cadeau 70 jaar (seizoens)"),
    ("keyword", "levensverhaal op usb", "KW: levensverhaal op usb"),
    ("keyword", "persoonlijk cadeau ouders", "KW: persoonlijk cadeau ouders"),
    ("keyword", "digitale erfenis", "KW: digitale erfenis / nalatenschap"),
    ("keyword", "levensverhaal vastleggen", "KW: levensverhaal vastleggen"),
    ("keyword", "familiearchief aanleggen", "KW: digitaal familiearchief"),
    ("keyword", "vragen interview ouders", "KW: interview ouders vragen"),
    # ── Thematische RSS (nieuws rond ouderen / zorg / erfenis) ───────────
    ("rss", "https://www.nu.nl/rss/Samenleving", "RSS: NU.nl Samenleving"),
    ("rss", "https://www.zorginstituutnederland.nl/rss", "RSS: Zorginstituut NL"),
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
    ok, skip = 0, 0
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
                print(f"  ? {status} {value} -> {body[:80]}")
        except Exception as e:
            skip += 1
            print(f"  ! {wtype} {value}: {e}")
    print(f"\nToegevoegd: {ok} | fouten: {skip}")


if __name__ == "__main__":
    main()
