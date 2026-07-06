"""
Seed de worker-profielen uit backend/config_templates/worker_profiles.yaml
in de agent_profiles-tabel. Idempotent: bestaande profielen (op naam) worden
overgeslagen.

Draai vanaf de projectroot:
    .venv\\Scripts\\python.exe scripts\\seed_worker_profiles.py
"""
import sys
import datetime
from pathlib import Path

import yaml

# Zorg dat 'backend' importeerbaar is wanneer dit script direct wordt gedraaid.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend.database import init_db, get_conn  # noqa: E402

TEMPLATE = ROOT / "backend" / "config_templates" / "worker_profiles.yaml"


def main() -> None:
    init_db()
    profiles = yaml.safe_load(TEMPLATE.read_text(encoding="utf-8"))["profiles"]
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()

    created, skipped = 0, 0
    with get_conn() as conn:
        for p in profiles:
            exists = conn.execute(
                "SELECT 1 FROM agent_profiles WHERE name = ?", (p["name"],)
            ).fetchone()
            if exists:
                skipped += 1
                continue
            conn.execute(
                "INSERT INTO agent_profiles (name, model, system_prompt, created_at) "
                "VALUES (?, ?, ?, ?)",
                (p["name"], p["model"], p["system_prompt"], now),
            )
            created += 1

    print(f"Profielen geseed: {created} nieuw, {skipped} overgeslagen (bestond al).")


if __name__ == "__main__":
    main()
