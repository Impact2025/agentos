"""Koppelt een klant-bridge-token (gegenereerd door mijn-ondernemers-os' scripts/
create-client-bridge-token.mjs, Fase 2 deel 1) aan een ImpactOS-project, zodat diens
'Rituelen'-tab in de Control Room verschijnt (Fase 2 deel 2).

Gebruik: python scripts/link_client_bridge_token.py <project-slug> <token> "<label>"

<project-slug> mag de dropdown-naam zijn ("Bewaard voor Jou") of de mapnaam
("bewaardvoorjou") — wordt gesquasht naar dezelfde canonical vorm die de router gebruikt.
"""
import sys

sys.path.insert(0, r"D:\apps\agentos")

from backend.domains.rituals.models import ensure_schema
from backend.shared.database import get_conn
from backend.shared.projects import squash_project


def main() -> None:
    if len(sys.argv) < 4:
        print('Gebruik: python scripts/link_client_bridge_token.py <project-slug> <token> "<label>"')
        sys.exit(1)

    project_slug = squash_project(sys.argv[1])
    token = sys.argv[2]
    label = sys.argv[3]

    ensure_schema()
    from datetime import datetime, timezone

    with get_conn() as conn:
        conn.execute(
            """INSERT INTO project_bridge_tokens (project_slug, token, label, created_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(project_slug) DO UPDATE SET
                 token = excluded.token, label = excluded.label, created_at = excluded.created_at""",
            (project_slug, token, label, datetime.now(timezone.utc).isoformat()),
        )

    print(f'Gekoppeld: project "{project_slug}" -> "{label}".')


if __name__ == "__main__":
    main()
