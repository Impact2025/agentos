"""Voice-sessie logging naar de Obsidian vault (Apollo's "memory galaxy").

Elke voice-sessie (wat je zei + wat Apollo antwoordde) wordt één markdown-note
in de vault, onder 10_Projects/{project}/voice-sessions/. Zo blijft de
spraaklaag dezelfde single-source-of-truth als de rest van Impact OS: de vault
is het geheugen, niet een losse database-tabel.

De voice_artifacts-tabel (in database.py) blijft de snelle index voor de
Gallery in de UI; de Obsidian-note is de duurzame, doorzoekbare kopie.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

from ...shared.config import OBSIDIAN_VAULT_PATH

logger = logging.getLogger(__name__)


def obsidian_configured() -> bool:
    return bool(OBSIDIAN_VAULT_PATH) and Path(OBSIDIAN_VAULT_PATH).exists()


def log_session_to_obsidian(
    project: str,
    title: str,
    transcript: str,
    answer: str = "",
    goal_id: str = "",
) -> str | None:
    """Schrijf een voice-sessie als markdown-note in de vault.

    Geeft het vault-pad terug bij succes, anders None.
    """
    if not obsidian_configured():
        return None
    try:
        vault = Path(OBSIDIAN_VAULT_PATH)
        stamp = datetime.now(timezone.utc)
        date = stamp.strftime("%Y-%m-%d")
        slug = stamp.strftime("%Y%m%d-%H%M%S")
        proj = (project or "Impact OS").replace("/", "-")
        note_dir = vault / "10_Projects" / proj / "voice-sessions"
        note_dir.mkdir(parents=True, exist_ok=True)
        note_path = note_dir / f"{slug}.md"

        lines = ["---"]
        lines.append(f'title: "{title}"')
        lines.append(f"date: {date}")
        lines.append(f"type: voice-session")
        if goal_id:
            lines.append(f"goal_id: {goal_id}")
        lines.append("---")
        lines.append("")
        lines.append(f"# 🎙 {title}")
        lines.append("")
        lines.append(f"**Gezegd:** {transcript}")
        lines.append("")
        if answer:
            lines.append("**Antwoord:**")
            lines.append("")
            lines.append(answer)
            lines.append("")
        lines.append(f"_Log: {stamp.isoformat()}_")
        note_path.write_text("\n".join(lines), encoding="utf-8")
        return str(note_path)
    except Exception as e:
        logger.exception("Kon voice-sessie niet naar Obsidian schrijven")
        return None
