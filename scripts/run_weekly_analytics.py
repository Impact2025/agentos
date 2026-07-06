"""
Standalone script voor Windows Task Scheduler.
Voert het wekelijkse GA rapport uit zonder de FastAPI app.
Gebruik: .venv\Scripts\python.exe scripts\run_weekly_analytics.py
"""
import sys
import asyncio
from pathlib import Path

# Zorg dat de project root in het Python-pad zit
sys.path.insert(0, str(Path(__file__).parent.parent))

from backend.services.analytics_reporter import run_weekly_report  # noqa: E402

if __name__ == "__main__":
    result = asyncio.run(run_weekly_report())
    if result.get("success"):
        print(f"✓ Rapport voltooid: week {result.get('week')}")
        if result.get("session_id"):
            print(f"  Dashboard sessie: {result['session_id']}")
        if result.get("obsidian_note"):
            print(f"  Obsidian note: {result['obsidian_note']}")
        if result.get("email_sent"):
            print("  E-mail verstuurd")
    else:
        print(f"✗ Rapport mislukt: {result.get('error')}")
        sys.exit(1)
