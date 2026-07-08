"""Mission Radar — zet de top-5 signalen per brand op 'targeted' en schrijf
een vault-note (10_Projects/_trends/) voor elk. Top = hoogste signal_score.
Idempotent: als een signaal al 'targeted'/'converted' is, overslaan voor status
maar note zo nodig alsnog schrijven."""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))
from datetime import datetime
from backend.domains.radar.service import get_service, _now

TOP_N = 5
PROJECTS = ["bewaardvoorjou", "bijeen"]


def main():
    svc = get_service()
    for proj in PROJECTS:
        sigs = svc.list_signals(project=proj, limit=500)
        ranked = sorted(sigs, key=lambda s: (s.get("signal_score") or 0), reverse=True)[:TOP_N]
        print(f"\n=== {proj}: top-{TOP_N} → targeted ===")
        for s in ranked:
            # status zetten
            if s.get("status") not in ("targeted", "converted"):
                with __import__("backend.shared.database", fromlist=["get_conn"]).get_conn() as conn:
                    conn.execute(
                        "UPDATE radar_signals SET status = 'targeted', updated_at = ? WHERE id = ?",
                        (_now(), s["id"]),
                    )
            # vault-note (write_trend_note is idempotent qua bestand)
            rel = svc.write_trend_note(s)
            print(f"  • score {s['signal_score']} | {s['title'][:60]} | vault: {rel or 'niet geschreven'}")


if __name__ == "__main__":
    main()
