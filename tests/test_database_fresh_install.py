"""Eén migratie-pas op een échte verse database moet het volledige schema opleveren.

Aanleiding (19 aug 2026): `calendar_proposals` had twee migratieblokken voor
dezelfde kolommen in `_migrate()` — een vroeg blok dat `all_day` en
`reminder_sent` toevoegde, maar alleen ALS de tabel al bestond (`cp_exists`),
en de tabel werd pas ván honderden regels verderop in dezelfde functie voor
het eerst aangemaakt. Op een verse installatie is er nooit een moment waarop
beide waar zijn binnen één migratie-pas: het vroege blok slaat zichzelf altijd
over. `reminder_sent` en `all_day` stonden NERGENS anders, dus ontbraken ze
op elke fabrieksnieuwe database — `calendar/reminder.py` en
`bridge/actions.py:_cmd_calendar_add` crashten daar pas op zodra ze voor het
eerst draaiden, wat in de gewone testrun (meerdere migratie-passes door
elkaar) niet opviel.

Deze test simuleert precies één migratie-pas op een lege database — zoals een
echte verse installatie, en anders dan de gedeelde sessie-DB in conftest.py
die door de hele testsuite heen meermaals gemigreerd kan worden.
"""
import os
import sqlite3
import tempfile
import uuid


def _fresh_migrated_conn():
    path = os.path.join(tempfile.gettempdir(), f"impactos-fresh-{uuid.uuid4().hex[:8]}.db")
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    from backend.shared.database import DDL, _migrate
    conn.executescript(DDL)
    _migrate(conn)
    return conn, path


def test_calendar_proposals_heeft_alle_kolommen_na_een_enkele_migratiepas():
    conn, path = _fresh_migrated_conn()
    try:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(calendar_proposals)").fetchall()}
        # all_day en reminder_sent zijn de twee kolommen die ooit alleen in
        # het vroegtijdige, dode migratieblok stonden.
        for verwacht in ("all_day", "reminder_sent", "recur_weekday", "recur_count",
                         "conflict_checked"):
            assert verwacht in cols, f"kolom '{verwacht}' ontbreekt na één migratie-pas op een verse DB"
    finally:
        conn.close()
        for suffix in ("", "-wal", "-shm"):
            try:
                os.remove(path + suffix)
            except OSError:
                pass


def test_migratie_is_idempotent_bij_herhaalde_pas():
    """Een tweede migratie-pas (zoals bij een herstart) mag niet crashen op
    'duplicate column' — elke ALTER hoort achter een kolom-afwezigheidscheck."""
    conn, path = _fresh_migrated_conn()
    try:
        from backend.shared.database import _migrate
        _migrate(conn)  # tweede pas — mag geen exception geven
    finally:
        conn.close()
        for suffix in ("", "-wal", "-shm"):
            try:
                os.remove(path + suffix)
            except OSError:
                pass
