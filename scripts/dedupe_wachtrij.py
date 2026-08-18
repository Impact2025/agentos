"""Ruim bijna-identieke duplicaten in de Wachtrij op.

Aanleiding (15 aug 2026): `bijeen_worldclass_engine.py` escaleerde langs de
cross-run cap heen en zette na elke ronde `orchestrator_attempts=1` terug op het
bronrecord. Daardoor werd hetzelfde artikel tot 19x herschreven en stonden er
128 bijna-identieke concepten in de Wachtrij — 162 van de 187 openstaande
reviews hoorden bij twaalf artikelen. Een inbox met 183 items waarvan er 55
echt zijn, is geen inbox meer.

Per (site, artikel) blijft de hoogst scorende versie staan; de rest gaat op
'superseded' met `superseded_by` naar de bewaarde job. Bewust géén 'rejected':
afwijzen is een oordeel over de inhoud en zou bovendien een depublicatie-kaart
opleveren (zie CLAUDE.md — een statuswijziging is geen ingreep in de wereld).
Deze stukken zijn niet afgekeurd, ze zijn overbodig geworden.

Groeperen gebeurt op de kale artikeltitel via `integrity._kern_titel`, zodat de
keten "Herschrijf het artikel 'Herschrijf het artikel …'" als één artikel telt.

Gebruik:
    python scripts/dedupe_wachtrij.py            # droogdraaien (toont het plan)
    python scripts/dedupe_wachtrij.py --apply    # daadwerkelijk wegschrijven
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.domains.iris.integrity import _kern_titel  # noqa: E402
from backend.domains.publish import content_pipeline  # noqa: E402
from backend.shared.database import get_conn  # noqa: E402


def _score(job):
    try:
        return float(job["seo_score"] or 0)
    except (TypeError, ValueError):
        return 0.0


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true",
                    help="schrijf de wijzigingen weg (zonder deze vlag: droogdraaien)")
    ap.add_argument("--status", default="pending_review")
    args = ap.parse_args()

    with get_conn() as conn:
        rijen = conn.execute(
            "SELECT id, site_id, title, seo_score, created_at FROM content_jobs "
            "WHERE status = ? ORDER BY created_at", (args.status,)
        ).fetchall()
        namen = {r["id"]: r["name"] for r in conn.execute("SELECT id, name FROM sites")}

    groepen = {}
    for r in rijen:
        kern = _kern_titel(r["title"] or "")
        if not kern:
            continue
        groepen.setdefault((r["site_id"], kern), []).append(dict(r))

    te_sluiten = []
    for (site_id, kern), groep in sorted(groepen.items(), key=lambda kv: -len(kv[1])):
        if len(groep) < 2:
            continue
        # Hoogste score wint; bij gelijkspel de nieuwste (die kent de meeste
        # verbeterrondes). Nooit "de eerste" — dat is de zwakste versie.
        groep.sort(key=lambda j: (_score(j), j["created_at"] or ""), reverse=True)
        houden, rest = groep[0], groep[1:]
        print(f"{namen.get(site_id, site_id)[:22]:22s} {len(groep):3d}x  "
              f"{kern[:58]:58s} houd score {_score(houden):.0f}")
        for j in rest:
            te_sluiten.append((j["id"], houden["id"], _score(j)))

    print()
    print(f"Groepen met duplicaten: "
          f"{sum(1 for g in groepen.values() if len(g) > 1)}")
    print(f"Te sluiten als 'superseded': {len(te_sluiten)}")
    print(f"Blijft open in de Wachtrij: {len(rijen) - len(te_sluiten)}")

    if not args.apply:
        print("\n(droogdraaien — niets gewijzigd; herhaal met --apply)")
        return 0

    for job_id, houder_id, _ in te_sluiten:
        content_pipeline.mark_superseded(job_id, houder_id)
    print(f"\n{len(te_sluiten)} job(s) op 'superseded' gezet.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
