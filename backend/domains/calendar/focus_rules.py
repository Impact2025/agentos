"""Focusblok-uitzondering voor de agenda-agent (Vincent, 26 aug 2026).

Regel: een Focusblok is beschermde tijd, maar geen keihard schot. Het mag
wijken voor een afspraak met een klant zolang dat ≥24 uur vóór het focusblok
wordt vastgelegd — dan is er nog tijd om het blok zelf te verzetten of te
laten vervallen. Ligt het focusblok binnen 24 uur, dan blijft het staan: dan
is er geen tijd meer om er iets mee te doen, en wint de bescherming.

Eén plek voor deze regel, want zowel de mail-flow (agent.py) als de tekst-/
spraakopdracht-flow (nl_command.py) passen 'm toe op hun eigen conflict-lijst
— twee kopieën van dezelfde regel is precies hoe zulke regels uit elkaar
lopen (zie CLAUDE.md over "twee antwoorden op dezelfde vraag").
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

FOCUS_TOKEN = "focus"
OVERRIDE_LEAD = timedelta(hours=24)


def is_focus_title(title: Optional[str]) -> bool:
    return FOCUS_TOKEN in (title or "").lower()


def overridable(focus_start: Optional[datetime], reference_now: datetime) -> bool:
    """True als het focusblok >=24u ná `reference_now` begint — dan mag het
    wijken voor een klantafspraak. Een onbekende starttijd is nooit
    overschrijfbaar: zonder bewijs van voldoende voorbereidingstijd is de
    bescherming de veilige kant."""
    if focus_start is None:
        return False
    return (focus_start - reference_now) >= OVERRIDE_LEAD
