#!/usr/bin/env python3
"""Seed de Mission Radar watchlist voor IctusGo (GPS teambuilding, sociale impact).
Drie lagen: concurrenten (site:-monitoring), content-gap keywords, RSS (HR/werk).
Draait tegen de live API op localhost:1250 (dezelfde backend die de 4u-sky-scan voedt)."""
import json
import urllib.request
import urllib.error

BASE = "http://localhost:1250/api/radar"
PROJECT = "ictusgo"

# (type, value, label)
ITEMS = [
    # ── Concurrenten (site:-monitoring) ──────────────────────────────
    ("competitor", "teamevents.nl", "TeamEvents.nl (teambuilding events)"),
    ("competitor", "eventfully.nl", "Eventfully.nl (bedrijfsuitjes)"),
    ("competitor", "flitz-events.nl", "Flitz-Events.nl (teamevents regio)"),
    ("competitor", "teambuilding.nl", "Teambuilding.nl (breed platform)"),
    ("competitor", "citygame.nl", "CityGame.nl (GPS/stadsspellen)"),
    ("competitor", "scavenger.nl", "Scavenger.nl (outdoor GPS-spellen)"),
    ("competitor", "spelevent.nl", "Spelevent.nl (bedrijfsactiviteiten)"),
    ("competitor", "meetinn.nl", "Meetinn.nl (teamuitjes/meetings)"),
    # ── Content-gap keywords (uit sprint-onderzoek: WKR, CSRD, regio, HR 2026) ─
    ("keyword", "gps teambuilding", "KW: gps teambuilding"),
    ("keyword", "teambuilding hoofddorp", "KW: teambuilding hoofddorp (regio)"),
    ("keyword", "teambuilding haarlemmermeer", "KW: teambuilding haarlemmermeer (regio)"),
    ("keyword", "maatschappelijk teamuitje", "KW: maatschappelijk teamuitje"),
    ("keyword", "teamuitje sociale impact", "KW: teamuitje sociale impact"),
    ("keyword", "wkr teambuilding 2026", "KW: wkr teambuilding 2026 (fiscaal)"),
    ("keyword", "csrd teambuilding", "KW: csrd teambuilding (ESRS S1)"),
    ("keyword", "bedrijfsuitje hoofddorp schiphol", "KW: bedrijfsuitje hoofddorp schiphol"),
    ("keyword", "gps teamuitje bedrijf", "KW: gps teamuitje bedrijf"),
    ("keyword", "vrijwilligers teambuilding", "KW: vrijwilligers teambuilding"),
    ("keyword", "teambuilding zonder wkr", "KW: teambuilding zonder wkr"),
    ("keyword", "geluksmomenten team", "KW: geluksmomenten team (GMS)"),
    # ── Thematische RSS (HR/werk/arbeidsmarkt) ───────────────────────
    ("rss", "https://www.nu.nl/rss/Werk", "RSS: NU.nl Werk"),
    ("rss", "https://www.frankwatching.com/feed/", "RSS: Frankwatching (HR/marketing)"),
    ("rss", "https://www.mkb-servicedesk.nl/rss.xml", "RSS: MKB Servicedesk (ondernemen)"),
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
