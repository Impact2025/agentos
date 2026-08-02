"""Zet bestaande niet-organisaties in de leadvoorraad op 'lost'.

De zeef in prospecting/validate.py houdt nieuwe rommel tegen, maar de 165 leads
die er op 27 juli 2026 al stonden zijn daar nooit langs geweest. Zolang die
blijven staan meet de acquisitieformule de kwaliteit van de zoekresultaten in
plaats van de kwaliteit van de verkoop.

Alleen leads in 'new' of 'enriched' worden geraakt: vanaf 'contacted' is er een
mens bij betrokken geweest en dan is de status geen gok meer. 'lost' i.p.v.
verwijderen, zodat de historie klopt en dedupe blijft werken (is_duplicate kijkt
naar de URL — een verwijderde lead wordt morgen opnieuw gevonden).

Gebruik:
    .venv/Scripts/python.exe scripts/cleanup_junk_leads.py          # rapporteren
    .venv/Scripts/python.exe scripts/cleanup_junk_leads.py --fix    # opschonen
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):  # pragma: no cover
        pass

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.domains.prospecting import validate  # noqa: E402
from backend.shared.database import get_conn  # noqa: E402

_OPEN_STATUSSEN = ("new", "enriched")


def main(fix: bool = False) -> int:
    with get_conn() as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT id, org_name, website, summary, status, email, phone, "
            "       kvk_number "
            "FROM leads WHERE status IN (?, ?)", _OPEN_STATUSSEN
        )]

    rommel: list[tuple[dict, str]] = []
    onbereikbaar: list[dict] = []
    for lead in rows:
        geschikt, reden = validate.looks_like_organisation(
            lead["org_name"] or "", lead["website"] or "", lead["summary"] or "")
        if not geschikt:
            rommel.append((lead, reden))
        elif not validate.usable_contact(lead):
            onbereikbaar.append(lead)

    print(f"{len(rows)} open leads · {len(rommel)} geen organisatie · "
          f"{len(onbereikbaar)} zonder bruikbaar contact\n")
    for lead, reden in rommel:
        print(f"  [{lead['status']:9}] {(lead['org_name'] or '')[:58]:60} {reden}")

    if not fix:
        print("\n(alleen gerapporteerd — draai met --fix om ze op 'lost' te zetten)")
        return len(rommel)

    with get_conn() as conn:
        for lead, reden in rommel:
            conn.execute(
                "UPDATE leads SET status='lost', lost_at=datetime('now'), "
                "summary = CASE WHEN summary = '' THEN ? ELSE summary END "
                "WHERE id = ?",
                (f"Automatisch afgeschreven: {reden}", lead["id"]),
            )
    print(f"\n{len(rommel)} leads op 'lost' gezet.")
    if onbereikbaar:
        print(f"{len(onbereikbaar)} leads zijn wél een organisatie maar hebben geen "
              f"e-mail/telefoon/KvK — draai POST /api/leads/cleanup-unmailable "
              f"als je die ook wilt afschrijven.")
    return len(rommel)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fix", action="store_true")
    args = parser.parse_args()
    main(args.fix)
