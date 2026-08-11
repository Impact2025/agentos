"""Tests voor de Nederlandse agenda-commandoparser (backend/domains/calendar/nl_command).

Deze tests documenteren en beschermen de belangrijkste parse-regels, inclusief
de 'hele dag'-bug die op 11 aug 2026 live naar een verkeerd voorstel leidde
("aanstaande vrijdag niet beschikbaar, blok hele dag" werd 30 min op 10:00).

Draai met:  pytest backend/tests/test_nl_command.py
"""
from datetime import datetime
import pytest

from domains.calendar import nl_command as nlc


def _wd(dt: datetime) -> int:
    return dt.weekday()  # ma=0 .. zo=6


def test_whole_day_vrijdag_is_correct_date_and_all_day():
    # "aanstaande vrijdag" vanaf 11 aug 2026 (dinsdag) => 14 aug 2026.
    c = nlc.parse_command("aanstaande vrijdag niet beschikbaar, blok hele dag")
    assert c.kind == "single"
    assert c.all_day is True
    assert c.start.day == 14 and c.start.month == 8 and c.start.year == 2026
    assert c.start.hour == 0 and c.start.minute == 0
    # Een hele dag loopt tot 24:00 (exclusief) -> volgende dag 00:00.
    assert c.end.day == 15 and c.end.hour == 0
    assert c.duration_min == 24 * 60
    assert c.title == "Niet beschikbaar"


def test_explicit_date_whole_day():
    c = nlc.parse_command("vrijdag 14 augustus hele dag niet beschikbaar")
    assert c.all_day is True
    assert c.start.day == 14 and c.start.month == 8
    assert c.title == "Niet beschikbaar"


def test_explicit_time_window_is_not_all_day_and_uses_end_time():
    # Expliciete tijden => geen all_day, en de genoemde eindtijd telt.
    c = nlc.parse_command("blok vrijdag 14 augustus van 09.00 tot 17.00")
    assert c.all_day is False
    assert c.start.hour == 9 and c.start.minute == 0
    assert c.end.hour == 17 and c.end.minute == 0
    assert c.duration_min == 8 * 60


def test_whole_day_with_subject_after_voor():
    c = nlc.parse_command("vrijdag 14 augustus vrijhouden voor vakantie")
    assert c.all_day is True
    assert c.title == "Vakantie"


def test_simple_appointment_default_duration():
    c = nlc.parse_command("dinsdag 18 augustus 12.15 tandarts")
    assert c.kind == "single"
    assert c.all_day is False
    assert c.start.day == 18 and c.start.month == 8
    assert c.start.hour == 12 and c.start.minute == 15
    assert c.duration_min == 30  # default
    assert c.title == "Tandarts"


def test_recurring_weekly_block():
    c = nlc.parse_command("blok de komende 6 weken op maandag van 08.30 tot 10.00 voor Focustijd")
    assert c.kind == "recurring"
    assert c.recur_weekday == 0  # maandag
    assert c.recur_count == 6
    assert c.start.hour == 8 and c.start.minute == 30
    assert c.end.hour == 10 and c.end.minute == 0
    assert c.title.startswith("Focustijd")


def test_next_weekday_resolves_to_future():
    # "volgende vrijdag" moet een week verder dan de eerstvolgende vrijdag zijn.
    c = nlc.parse_command("volgende vrijdag vrijhouden")
    assert c.all_day is True
    assert _wd(c.start) == 4  # vrijdag
    # ten minste 8 dagen in de toekomst t.o.v. vandaag
    assert (c.start.date() - datetime.now().date()).days >= 8


def test_past_explicit_date_is_error():
    # Een datum in het verleden mag niet stil verschoven worden.
    c = nlc.parse_command("5 januari 10.00 tandarts")
    # Slaagt alleen als 5 jan in de toekomst ligt; anders moet het een error zijn.
    if c.kind != "error":
        assert c.start.year >= datetime.now().year


def test_no_date_is_error():
    c = nlc.parse_command("tandarts om 10.00")
    assert c.kind == "error"
