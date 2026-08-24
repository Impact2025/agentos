"""Tests voor de Nederlandse agenda-commandoparser (backend/domains/calendar/nl_command).

Deze tests documenteren en beschermen de belangrijkste parse-regels, inclusief
de 'hele dag'-bug die op 11 aug 2026 live naar een verkeerd voorstel leidde
("aanstaande vrijdag niet beschikbaar, blok hele dag" werd 30 min op 10:00).

'Nu' wordt bevroren op dinsdag 11 augustus 2026 (zie `dinsdag`-fixture) —
zonder dat wordt elke test die een concrete datum verwacht ("vrijdag 14
augustus") vanzelf date-flaky zodra de kalender voorbijschuift: `nl_command`
weigert bewust een datum in het verleden (zie CLAUDE.md 13a), dus "14
augustus" faalt na 14 augustus hard in plaats van stil te schuiven. Dat is
precies wat er op 19 aug 2026 gebeurde: negen tests hier faalden, niet omdat
de parser stuk was, maar omdat "nu" nooit werd vastgepind. Zelfde mechanisme
als tests/test_agenda_opdracht.py — bewust hergebruikt i.p.v. een tweede
manier om de klok te bevriezen.

Draai met:  pytest backend/tests/test_nl_command.py
"""
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from domains.calendar import nl_command as nlc

TZ = ZoneInfo("Europe/Amsterdam")
_FROZEN_NOW = datetime(2026, 8, 11, 11, 0, tzinfo=TZ)  # dinsdag


def _wd(dt: datetime) -> int:
    return dt.weekday()  # ma=0 .. zo=6


@pytest.fixture
def dinsdag(monkeypatch):
    """Bevries 'nu' op dinsdag 11 augustus 2026, 11:00 (zomertijd)."""
    monkeypatch.setattr(nlc, "_amsterdam_now", lambda: _FROZEN_NOW)


def test_whole_day_vrijdag_is_correct_date_and_all_day(dinsdag):
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


def test_explicit_date_whole_day(dinsdag):
    c = nlc.parse_command("vrijdag 14 augustus hele dag niet beschikbaar")
    assert c.all_day is True
    assert c.start.day == 14 and c.start.month == 8
    assert c.title == "Niet beschikbaar"


def test_explicit_time_window_is_not_all_day_and_uses_end_time(dinsdag):
    # Expliciete tijden => geen all_day, en de genoemde eindtijd telt.
    c = nlc.parse_command("blok vrijdag 14 augustus van 09.00 tot 17.00")
    assert c.all_day is False
    assert c.start.hour == 9 and c.start.minute == 0
    assert c.end.hour == 17 and c.end.minute == 0
    assert c.duration_min == 8 * 60


def test_whole_day_with_subject_after_voor(dinsdag):
    c = nlc.parse_command("vrijdag 14 augustus vrijhouden voor vakantie")
    assert c.all_day is True
    assert c.title == "Vakantie"


def test_simple_appointment_default_duration(dinsdag):
    c = nlc.parse_command("dinsdag 18 augustus 12.15 tandarts")
    assert c.kind == "single"
    assert c.all_day is False
    assert c.start.day == 18 and c.start.month == 8
    assert c.start.hour == 12 and c.start.minute == 15
    assert c.duration_min == 30  # default
    assert c.title == "Tandarts"


def test_recurring_weekly_block(dinsdag):
    c = nlc.parse_command("blok de komende 6 weken op maandag van 08.30 tot 10.00 voor Focustijd")
    assert c.kind == "recurring"
    assert c.recur_weekday == 0  # maandag
    assert c.recur_count == 6
    assert c.start.hour == 8 and c.start.minute == 30
    assert c.end.hour == 10 and c.end.minute == 0
    assert c.title.startswith("Focustijd")


def test_next_weekday_resolves_to_future(dinsdag):
    # "volgende vrijdag" moet een week verder dan de eerstvolgende vrijdag zijn.
    c = nlc.parse_command("volgende vrijdag vrijhouden")
    assert c.all_day is True
    assert _wd(c.start) == 4  # vrijdag
    # ten minste 8 dagen in de toekomst t.o.v. de bevroren 'nu'.
    assert (c.start.date() - _FROZEN_NOW.date()).days >= 8


def test_date_without_year_in_the_past_rolls_to_next_year(dinsdag):
    # "5 januari" genoemd op 11 augustus is dit jaar al voorbij; zonder
    # expliciet jaartal is "volgend jaar" hier de bedoelde datum (net als een
    # kale weekdag naar de eerstvolgende voorkomens rolt) — anders dan een
    # expliciete datum-met-jaar in het verleden, die wél een error moet zijn
    # (zie tests/test_agenda_opdracht.py::test_genoemde_datum_in_het_verleden...).
    c = nlc.parse_command("5 januari 10.00 tandarts")
    assert c.kind == "single"
    assert c.start.year == 2027 and c.start.month == 1 and c.start.day == 5


def test_no_date_is_error(dinsdag):
    c = nlc.parse_command("tandarts om 10.00")
    assert c.kind == "error"
