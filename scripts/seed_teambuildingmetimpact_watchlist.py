#!/usr/bin/env python3
"""Seed de Mission Radar watchlist voor Teambuilding met Impact (bedrijfsvrijwilligerswerk,
impact days & LEGO Serious Play). Drie lagen: concurrenten (site:-monitoring),
content-gap keywords (WKR/CSRD/regio/HR 2026), RSS (HR/werk/impact).

Draait tegen de live API op localhost:1250 (dezelfde backend die de 4u-sky-scan voedt).
Idempotent: een item dat al actief bestaat (zelfde project/type/value) wordt overgeslagen,
zodat we nooit duplicaten maken (add_watch dedupeert NIET)."""
import json
import urllib.request
import urllib.error

BASE = "http://localhost:1250/api/radar"
PROJECT = "teambuildingmetimpact"

# (type, value, label)
ITEMS = [
    # ── Concurrenten (site:-monitoring) ──────────────────────────────
    ("competitor", "teambuilding.nl", "Teambuilding.nl (breed platform)"),
    ("competitor", "teamevents.nl", "TeamEvents.nl (teambuilding events)"),
    ("competitor", "eventfully.nl", "Eventfully.nl (bedrijfsuitjes)"),
    ("competitor", "flitz-events.nl", "Flitz-Events.nl (teamevents regio)"),
    ("competitor", "citygame.nl", "CityGame.nl (stadsspellen/GPS)"),
    ("competitor", "seriousplay.nl", "SeriousPlay.nl (LEGO Serious Play NL)"),
    ("competitor", "strategicseriousplay.com", "Strategic Play (LSP internationaal)"),
    ("competitor", "meevanderant.nl", "Mee van der Ant (maatschappelijk teamuitje)"),
    # ── Content-gap keywords (WKR/CSRD/regio/HR/ESG 2026) ────────────
    ("keyword", "bedrijfsvrijwilligerswerk organiseren", "KW: bedrijfsvrijwilligerswerk organiseren"),
    ("keyword", "impact day organiseren", "KW: impact day organiseren"),
    ("keyword", "maatschappelijke teambuilding", "KW: maatschappelijke teambuilding"),
    ("keyword", "bedrijfsuitje met impact", "KW: bedrijfsuitje met impact"),
    ("keyword", "mvo teambuilding esg", "KW: mvo teambuilding esg"),
    ("keyword", "csrd teambuilding", "KW: csrd teambuilding (ESRS S1)"),
    ("keyword", "wkr teambuilding 2026", "KW: wkr teambuilding 2026 (fiscaal)"),
    ("keyword", "lego serious play teambuilding", "KW: lego serious play teambuilding"),
    ("keyword", "teambuilding haarlemmermeer", "KW: teambuilding haarlemmermeer (regio)"),
    ("keyword", "teambuilding hoofddorp", "KW: teambuilding hoofddorp (regio)"),
    ("keyword", "vrijwilligersdag teambuilding", "KW: vrijwilligersdag teambuilding"),
    ("keyword", "social return teamdag", "KW: social return teamdag"),
    ("keyword", "impact meten teamdag", "KW: impact meten teamdag"),
    ("keyword", "zelfbedruipende teamdag", "KW: zelfbedruipende / duurzame teamdag"),
    # ── Thematische RSS (HR/werk/impact/MVO) ───────────────────────
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


def existing_items():
    """Haal actieve (type,value) paren op — zodat we nooit duplicaten seeden
    (add_watch aan serverkant dedupeert NIET)."""
    try:
        with urllib.request.urlopen(f"{BASE}/watch-list?project={PROJECT}", timeout=15) as r:
            rows = json.loads(r.read().decode())
        return {(str(x.get("type")), str(x.get("value"))) for x in rows
                if x.get("active", 1) == 1}
    except Exception:
        return set()


def main():
    have = existing_items()
    ok, skip, err = 0, 0, 0
    for wtype, value, label in ITEMS:
        if (wtype, value) in have:
            skip += 1
            print(f"  = {wtype:10} {value} (bestaat al, overgeslagen)")
            continue

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
