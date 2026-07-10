"""WeAreImpact — uitbreiding van de Mission Radar watchlist.

Voegt toe (idempotent: slaat bestaande waarden over):
  - Concurrenten / aanpalende spelers (type 'site' -> Tavily site: monitor)
  - Ontbrekende hoogintentie keywords (WMO, jeugdzorg, GGZ, gemeente AI,
    datagedreven welzijn, SROI, interim sociaal domein)
  - Autoritatieve bron-feeds (RSS: VNG, Rijksoverheid, Nictiz, Zorgvisie,
    Binnenlands Bestuur) zodat de agent op beleid & nieuws kan reageren.

Run:  .venv/Scripts/python scripts/seed_weareimpact_watchlist_ext.py
"""
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.domains.radar.service import get_service

PROJECT = "weareimpact"

# (type, label, value)
NEW_ITEMS = [
    # ── Concurrenten / aanpalende spelers (site:-monitor via Tavily) ──
    ("site", "TNO — gezondheid & zorg", "tno.nl"),
    ("site", "Murakami — verandermanagement", "murakami.nl"),
    ("site", "Novi — AI opleidingen", "novi.nl"),
    ("site", "Brightest — AI consultancy", "brightest.nl"),
    ("site", "KPMG — public sector AI", "kpmg.com/nl"),
    ("site", "Nictiz — zorginformatie", "nictiz.nl"),
    ("site", "Zorgvisie — vakmedia zorg", "zorgvisie.nl"),
    ("site", "Binnenlands Bestuur — gemeenten", "binnenlandsbestuur.nl"),
    ("site", "VNG — gemeenten", "vng.nl"),

    # ── Ontbrekende hoogintentie keywords ──
    ("keyword", "WMO AI ondersteuning", "wmo ai ondersteuning"),
    ("keyword", "Jeugdzorg digitalisering", "jeugdzorg digitalisering"),
    ("keyword", "GGZ wachtlijsten AI", "ggz wachtlijsten ai"),
    ("keyword", "Gemeente AI beleid", "gemeente ai beleid"),
    ("keyword", "Datagedreven welzijn", "datagedreven welzijn"),
    ("keyword", "SROI social return", "sroi social return on investment"),
    ("keyword", "Interim sociaal domein opdracht", "interim sociaal domein opdracht"),
    ("keyword", "AI governance AVG zorg", "ai governance avg zorg"),
    ("keyword", "Eenzaamheid aanpakken gemeente", "eenzaamheid aanpakken gemeente"),
    ("keyword", "Wijkteams digitalisering", "wijkteam digitalisering"),

    # ── Autoritatieve bron-feeds (RSS) ──
    ("rss", "VNG Nieuws", "https://www.vng.nl/nieuws/rss"),
    ("rss", "Rijksoverheid Nieuws", "https://www.rijksoverheid.nl/feed.rss"),
    ("rss", "Nictiz Nieuws", "https://www.nictiz.nl/nieuws/rss"),
    ("rss", "Zorgvisie Nieuws", "https://www.zorgvisie.nl/rss.xml"),
    ("rss", "Binnenlands Bestuur", "https://www.binnenlandsbestuur.nl/rss.xml"),
]


def main() -> None:
    svc = get_service()
    # Gebruik de service zijn connectie-pad via get_conn.
    from backend.shared.database import get_conn

    added, skipped = 0, 0
    with get_conn() as conn:
        existing = {
            (r["project"].lower(), r["value"].lower())
            for r in conn.execute(
                "SELECT project, value FROM radar_watchlist"
            ).fetchall()
        }
        rows = []
        from datetime import datetime, timezone
        _now = datetime.now(timezone.utc).isoformat()
        for t, label, value in NEW_ITEMS:
            key = (PROJECT, value.lower())
            if key in existing:
                skipped += 1
                continue
            rows.append((
                f"wl-{PROJECT}-{abs(hash((label, value))) % 10**9:09d}",
                PROJECT, label, t, value, 1, _now,
            ))
            added += 1
        if rows:
            conn.executemany(
                "INSERT INTO radar_watchlist "
                "(id, project, label, type, value, active, created_at) VALUES (?,?,?,?,?,?,?)",
                rows,
            )
    print(f"WeAreImpact watchlist uitbreiding: +{added} nieuw, {skipped} overgeslagen (bestonden al).")
    from backend.shared.database import get_conn as _gc
    with _gc() as conn:
        total = conn.execute(
            "SELECT COUNT(*) FROM radar_watchlist WHERE LOWER(project)=?", (PROJECT,)
        ).fetchone()[0]
    print(f"Totaal WAI watchlist nu: {total}")


if __name__ == "__main__":
    main()
