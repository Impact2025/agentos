"""Tests voor de agenda-agent: mail → afspraak-voorstel achter de review-gate.

Deze keten faalde lang stil (kapotte import, NameError opgeslokt door een
except die dan 'geen conflict' teruggaf). Elke test hier dekt één van die
stille faalmodi af: als iets breekt moet er iets róód worden, niet niets.
"""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from backend.domains.calendar import agent as A
from backend.domains.calendar import service as cal
from backend.shared.database import get_conn

TZ = ZoneInfo("Europe/Amsterdam")


@pytest.fixture
def vrijdag(monkeypatch):
    """Bevries 'nu' op vrijdag 17 juli 2026, 12:00 (zomertijd)."""
    monkeypatch.setattr(A, "_amsterdam_now",
                        lambda: datetime(2026, 7, 17, 12, 0, tzinfo=TZ))


@pytest.fixture
def mailbox():
    with get_conn() as c:
        c.execute(
            "INSERT OR IGNORE INTO mailboxes(id,project,label,address,knowledge_scope,"
            "pop_host,pop_port,pop_user,pop_password,created_at) "
            "VALUES('mb_cal','WeAreImpact','Info','info@weareimpact.nl','all',"
            "'h',110,'u','p',datetime('now'))")
    return "mb_cal"


def _inbox_row(mailbox_id: str, body: str) -> int:
    import uuid
    with get_conn() as c:
        cur = c.execute(
            "INSERT INTO mail_inbox(mailbox_id,uidl,from_addr,subject,body_text,classified) "
            "VALUES(?,?,?,?,?,'appointment')",
            (mailbox_id, uuid.uuid4().hex, "klant@bedrijf.nl", "Afspraak", body))
        return cur.lastrowid


def _proposal(pid: int) -> dict:
    with get_conn() as c:
        return dict(c.execute(
            "SELECT * FROM calendar_proposals WHERE id=?", (pid,)).fetchone())


# ── De import-keten ────────────────────────────────────────────────────────

def test_mailservice_kan_agenda_agent_importeren():
    """mail/service.py deed ooit `from .calendar import` (= mail.calendar, dat
    niet bestaat). Elke afspraak-mail knalde daarop, zonder dat iets rood werd."""
    from backend.domains.mail import service as mail_service
    assert mail_service is not None
    from backend.domains.calendar import agent
    assert callable(agent.create_proposal)


# ── Datumparser ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("tekst,verwacht", [
    ("dinsdag 14:00", "2026-07-21T14:00"),
    ("volgende week dinsdag 14:00", "2026-07-28T14:00"),  # weekdag + 'volgende week'
    ("morgen 09:00", "2026-07-18T09:00"),
    ("overmorgen 16:00", "2026-07-19T16:00"),             # 'morgen' is substring
    ("23 juli 15:00", "2026-07-23T15:00"),
    ("volgende week", "2026-07-24T10:00"),                # geen tijd → 10:00
])
def test_parse_datetime(vrijdag, tekst, verwacht):
    assert A._parse_datetime(tekst).isoformat()[:16] == verwacht


def test_parse_zonder_datum_geeft_none(vrijdag):
    assert A._parse_datetime("even sparren over de offerte") is None


def test_decimaal_getal_wordt_niet_als_tijd_gelezen(vrijdag):
    """Een prijs als '1.99' matcht het [:.]-tijdpatroon maar minute=99 is geen
    geldige klok — vroeger klapte de hele mailbox-poll met 'minute must be in
    0..59'. Nu vallen we terug op de default (10:00) i.p.v. te crashen."""
    got = A._parse_datetime("kost 12.75 euro, kunnen we morgen afspreken?")
    assert got is not None
    assert got.isoformat()[:16] == "2026-07-18T10:00"


def test_geldige_tijd_na_decimaal_wordt_alsnog_gevonden(vrijdag):
    """Schijn-tijd overslaan mag een échte kloktijd verderop niet missen."""
    got = A._parse_datetime("het kost 1.99, morgen om 14:30 dan?")
    assert got.isoformat()[:16] == "2026-07-18T14:30"


def test_genoemde_weekdag_die_al_voorbij_is_schuift_een_week(monkeypatch):
    """Dinsdagmiddag 'dinsdag 09:00' = volgende week dinsdag, niet woensdag."""
    monkeypatch.setattr(A, "_amsterdam_now",
                        lambda: datetime(2026, 7, 21, 15, 0, tzinfo=TZ))  # dinsdag
    assert A._parse_datetime("dinsdag 09:00").isoformat()[:10] == "2026-07-28"


def test_wintertijd_krijgt_juiste_offset(monkeypatch):
    """Een vaste UTC+2 zet elke afspraak na eind oktober een uur verkeerd."""
    monkeypatch.setattr(A, "_amsterdam_now",
                        lambda: datetime(2026, 12, 4, 12, 0, tzinfo=TZ))
    assert A._parse_datetime("23 december 15:00").utcoffset() == timedelta(hours=1)


# ── Reistijd & prioriteit ──────────────────────────────────────────────────

def test_fysieke_locatie_krijgt_reisbuffer(vrijdag, mailbox):
    body = "Dinsdag 14:00 afspreken? Locatie: Keizersgracht 123 Amsterdam. Duurt 1 uur."
    p = A.create_proposal(mailbox, _inbox_row(mailbox, body), "Afspraak", "k@b.nl", body)
    r = _proposal(p["id"])
    assert r["travel_buffer_min"] == 30
    # buffer zit vóór én na: 14:00-15:00 wordt 13:30-15:30
    assert r["proposed_start"][11:16] == "13:30"
    assert r["proposed_end"][11:16] == "15:30"


def test_teams_afspraak_krijgt_geen_reisbuffer(vrijdag, mailbox):
    body = "Zullen we donderdag 11:00 via Teams overleggen? 30 min."
    p = A.create_proposal(mailbox, _inbox_row(mailbox, body), "Overleg", "k@b.nl", body)
    r = _proposal(p["id"])
    assert r["is_remote"] == 1
    assert r["travel_buffer_min"] == 0


def test_spoed_wordt_high_priority(vrijdag, mailbox):
    body = "Kun je me morgen om 09:30 terugbellen? Urgent."
    p = A.create_proposal(mailbox, _inbox_row(mailbox, body), "Spoed", "k@b.nl", body)
    assert _proposal(p["id"])["priority"] == "high"


# ── Conflict-detectie: de kern van 'geen dubbele boekingen' ────────────────

def test_overlap_wordt_gemeld_en_krijgt_high_priority(vrijdag, mailbox, monkeypatch):
    async def bezet(start, end):
        return [{"start": "2026-07-21T14:00:00+02:00",
                 "end": "2026-07-21T15:00:00+02:00"}]
    monkeypatch.setattr(cal, "is_configured", lambda: True)
    monkeypatch.setattr(cal, "get_busy_times", bezet)
    body = "Dinsdag 14:00 bij ons op kantoor? Duurt 1 uur."
    p = A.create_proposal(mailbox, _inbox_row(mailbox, body), "Afspraak", "k@b.nl", body)
    r = _proposal(p["id"])
    assert "overlap" in r["conflict_note"].lower()
    assert r["priority"] == "high"


def test_mislukte_check_leest_niet_als_vrij(vrijdag, mailbox, monkeypatch):
    """De gevaarlijkste faalmodus: een stukke check die 'geen conflict' zegt."""
    async def stuk(start, end):
        raise RuntimeError("notFound")
    monkeypatch.setattr(cal, "is_configured", lambda: True)
    monkeypatch.setattr(cal, "get_busy_times", stuk)
    body = "Dinsdag 14:00 op kantoor?"
    p = A.create_proposal(mailbox, _inbox_row(mailbox, body), "Afspraak", "k@b.nl", body)
    note = _proposal(p["id"])["conflict_note"]
    assert "niet op dubbele boeking gecontroleerd" in note.lower()


def test_zonder_gekoppelde_agenda_leest_niet_als_vrij(vrijdag, mailbox, monkeypatch):
    monkeypatch.setattr(cal, "is_configured", lambda: False)
    body = "Dinsdag 14:00 op kantoor?"
    p = A.create_proposal(mailbox, _inbox_row(mailbox, body), "Afspraak", "k@b.nl", body)
    note = _proposal(p["id"])["conflict_note"]
    assert "niet op dubbele boeking gecontroleerd" in note.lower()


def test_vrij_slot_geeft_geen_waarschuwing(vrijdag, mailbox, monkeypatch):
    async def vrij(start, end):
        return []
    monkeypatch.setattr(cal, "is_configured", lambda: True)
    monkeypatch.setattr(cal, "get_busy_times", vrij)
    body = "Dinsdag 14:00 op kantoor?"
    p = A.create_proposal(mailbox, _inbox_row(mailbox, body), "Afspraak", "k@b.nl", body)
    assert _proposal(p["id"])["conflict_note"] == ""


def test_meerdere_lees_agendas_worden_samengevoegd(monkeypatch):
    """Conflict-detectie moet Vincents eigen agenda én de bot-agenda zien."""
    gevraagd = {}

    async def fake_api(method, path, **kw):
        gevraagd["items"] = [i["id"] for i in kw["json"]["items"]]
        return {"calendars": {
            "eigen@x.nl": {"busy": [{"start": "2026-07-21T09:00:00+02:00",
                                     "end": "2026-07-21T10:00:00+02:00"}]},
            "bot@x.nl": {"busy": [{"start": "2026-07-21T14:00:00+02:00",
                                   "end": "2026-07-21T15:00:00+02:00"}]},
        }}
    monkeypatch.setattr(cal, "_api", fake_api)
    monkeypatch.setattr(cal, "CALENDAR_BUSY_CALENDAR_IDS", ["eigen@x.nl", "bot@x.nl"])
    import asyncio
    now = datetime.now(TZ)
    busy = asyncio.run(cal.get_busy_times(now, now + timedelta(days=1)))
    assert gevraagd["items"] == ["eigen@x.nl", "bot@x.nl"]
    assert len(busy) == 2  # blokken uit béíde agenda's


def test_een_onbereikbare_lees_agenda_maakt_check_ongeldig(monkeypatch):
    """Half controleren is niet controleren: als Vincents agenda onzichtbaar is
    mag de bot-agenda niet in z'n eentje 'vrij' concluderen."""
    async def fake_api(method, path, **kw):
        return {"calendars": {
            "eigen@x.nl": {"busy": [], "errors": [{"reason": "notFound"}]},
            "bot@x.nl": {"busy": []},
        }}
    monkeypatch.setattr(cal, "_api", fake_api)
    monkeypatch.setattr(cal, "CALENDAR_BUSY_CALENDAR_IDS", ["eigen@x.nl", "bot@x.nl"])
    import asyncio
    now = datetime.now(TZ)
    with pytest.raises(RuntimeError, match="eigen@x.nl"):
        asyncio.run(cal.get_busy_times(now, now + timedelta(days=1)))


def test_goedkeuren_weigert_ongecontroleerd_slot(vrijdag, mailbox, monkeypatch):
    """De kern van 'geen dubbele boekingen': niet gecontroleerd = niet boeken."""
    async def stuk(start, end):
        raise RuntimeError("notFound")
    monkeypatch.setattr(cal, "is_configured", lambda: True)
    monkeypatch.setattr(cal, "get_busy_times", stuk)
    body = "Dinsdag 14:00 op kantoor?"
    p = A.create_proposal(mailbox, _inbox_row(mailbox, body), "Afspraak", "k@b.nl", body)
    assert _proposal(p["id"])["conflict_checked"] == "error"

    geboekt = []
    monkeypatch.setattr(cal, "block_time",
                        lambda **kw: geboekt.append(kw) or {"event_id": "x"})
    res = A.approve_proposal(p["id"])
    assert res["ok"] is False
    assert "dubbele boeking" in res["error"].lower()
    assert geboekt == []                                  # niets geschreven
    assert _proposal(p["id"])["status"] == "pending_review"  # blijft open


def test_geblokkeerd_voorstel_geneest_zodra_agenda_bereikbaar_is(
        vrijdag, mailbox, monkeypatch):
    """Een voorstel dat met een mislukte check ('error') is opgeslagen, moet bij
    goedkeuren opnieuw en live getoetst worden — wordt de agenda intussen
    bereikbaar, dan is het goed te keuren zonder het opnieuw te genereren."""
    async def stuk(start, end):
        raise RuntimeError("notFound")
    monkeypatch.setattr(cal, "is_configured", lambda: True)
    monkeypatch.setattr(cal, "get_busy_times", stuk)
    body = "Dinsdag 14:00 op kantoor?"
    p = A.create_proposal(mailbox, _inbox_row(mailbox, body), "Afspraak", "k@b.nl", body)
    assert _proposal(p["id"])["conflict_checked"] == "error"

    # Agenda wordt nu bereikbaar en het slot is vrij.
    async def vrij(start, end):
        return []
    monkeypatch.setattr(cal, "get_busy_times", vrij)

    async def fake_block(**kw):
        return {"event_id": "evt9", "html_link": "https://cal/evt9"}
    monkeypatch.setattr(cal, "block_time", fake_block)
    res = A.approve_proposal(p["id"])
    assert res["ok"] is True
    assert _proposal(p["id"])["status"] == "booked"
    assert _proposal(p["id"])["conflict_checked"] == "ok"  # live hertoets vastgelegd


def test_geblokkeerd_voorstel_blijft_geblokkeerd_bij_live_overlap(
        vrijdag, mailbox, monkeypatch):
    """Wordt de agenda bereikbaar maar overlapt het slot nu met een afspraak,
    dan blokkeert goedkeuren alsnog — geen dubbele boeking."""
    async def stuk(start, end):
        raise RuntimeError("notFound")
    monkeypatch.setattr(cal, "is_configured", lambda: True)
    monkeypatch.setattr(cal, "get_busy_times", stuk)
    body = "Dinsdag 14:00 op kantoor?"
    p = A.create_proposal(mailbox, _inbox_row(mailbox, body), "Afspraak", "k@b.nl", body)
    assert _proposal(p["id"])["conflict_checked"] == "error"

    async def bezet(start, end):
        return [{"start": start.isoformat(), "end": end.isoformat()}]
    monkeypatch.setattr(cal, "get_busy_times", bezet)

    geboekt = []
    async def fake_block(**kw):
        geboekt.append(kw)
        return {"event_id": "x"}
    monkeypatch.setattr(cal, "block_time", fake_block)
    res = A.approve_proposal(p["id"])
    assert res["ok"] is False
    assert res["code"] == "conflict_found"
    assert geboekt == []
    assert _proposal(p["id"])["status"] == "pending_review"


def test_goedkeuren_mag_wel_na_geslaagde_check(vrijdag, mailbox, monkeypatch):
    async def vrij(start, end):
        return []
    monkeypatch.setattr(cal, "is_configured", lambda: True)
    monkeypatch.setattr(cal, "get_busy_times", vrij)
    body = "Dinsdag 14:00 op kantoor?"
    p = A.create_proposal(mailbox, _inbox_row(mailbox, body), "Afspraak", "k@b.nl", body)
    assert _proposal(p["id"])["conflict_checked"] == "ok"

    async def fake_block(**kw):
        return {"event_id": "evt1", "html_link": "https://cal/evt1"}
    monkeypatch.setattr(cal, "block_time", fake_block)
    res = A.approve_proposal(p["id"])
    assert res["ok"] is True
    assert _proposal(p["id"])["status"] == "booked"


def test_freebusy_errors_veld_faalt_luid(monkeypatch):
    """Google verstopt 'geen toegang' in een 200-respons; dat mag geen lege
    busy-lijst worden, want dat leest als een vrije agenda."""
    async def fake_api(method, path, **kw):
        # Zet de error onder precies de agenda-id's die de code opvraagt,
        # onafhankelijk van CALENDAR_BUSY_CALENDAR_IDS in .env — anders
        # mismatcht de key met cal._cal_id() en raakt de test de verkeerde
        # (algemene 'geen antwoord') branch.
        items = [i["id"] for i in kw["json"]["items"]]
        return {"calendars": {cid: {"busy": [], "errors": [{"reason": "notFound"}]}
                               for cid in items}}
    monkeypatch.setattr(cal, "_api", fake_api)
    import asyncio
    now = datetime.now(TZ)
    with pytest.raises(RuntimeError, match="Delen met specifieke personen"):
        asyncio.run(cal.get_busy_times(now, now + timedelta(days=1)))


# ── De review-gate ─────────────────────────────────────────────────────────

def test_voorstel_gaat_nooit_direct_de_agenda_in(vrijdag, mailbox):
    body = "Dinsdag 14:00 op kantoor?"
    p = A.create_proposal(mailbox, _inbox_row(mailbox, body), "Afspraak", "k@b.nl", body)
    assert _proposal(p["id"])["status"] == "pending_review"
    assert any(x["id"] == p["id"] for x in A.pending_proposals())


# ── Router-contract: bewust geblokkeerd ≠ serverfout ───────────────────────
# De bug: de approve-endpoint gooide bij een geweigerd slot een 502, waardoor
# de SPA dat las als "server kapot" en de zorgvuldig geformuleerde
# deel-instructie in de browserconsole verdween i.p.v. bij de gebruiker.
def test_router_blocked_returns_200_niet_502(vrijdag, mailbox, monkeypatch):
    """conflict_unchecked → 200 met ok:false + error (instructie blijft leesbaar)."""
    async def stuk(start, end):
        raise RuntimeError("notFound")
    monkeypatch.setattr(cal, "is_configured", lambda: True)
    monkeypatch.setattr(cal, "get_busy_times", stuk)
    body = "Dinsdag 14:00 op kantoor?"
    p = A.create_proposal(mailbox, _inbox_row(mailbox, body), "Afspraak", "k@b.nl", body)

    from fastapi.testclient import TestClient
    from backend.main import app
    c = TestClient(app)
    r = c.post("/api/calendar/proposals/approve",
               json={"proposal_id": p["id"]})
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is False
    assert "dubbele boeking" in data["error"].lower()
    # niets geschreven, voorstel blijft open
    assert _proposal(p["id"])["status"] == "pending_review"


def test_router_booking_error_returns_502(vrijdag, mailbox, monkeypatch):
    """Echt mislukt boeken (Google fout) → 502, maar wél met de fouttekst."""
    async def vrij(start, end):
        return []
    monkeypatch.setattr(cal, "is_configured", lambda: True)
    monkeypatch.setattr(cal, "get_busy_times", vrij)
    body = "Dinsdag 14:00 op kantoor?"
    p = A.create_proposal(mailbox, _inbox_row(mailbox, body), "Afspraak", "k@b.nl", body)
    assert _proposal(p["id"])["conflict_checked"] == "ok"

    async def fake_block(**kw):
        raise RuntimeError("Google: 403 forbidden")
    monkeypatch.setattr(cal, "block_time", fake_block)
    from fastapi.testclient import TestClient
    from backend.main import app
    c = TestClient(app)
    r = c.post("/api/calendar/proposals/approve",
               json={"proposal_id": p["id"]})
    assert r.status_code == 502
    assert "403" in r.json()["detail"]



# ── Horizon-controle (1 aug 2026) ───────────────────────────────────────────
# Een nieuwsbrief over Apple en AI bevatte de zin "op 30 mei presenteerde het
# bedrijf...". De parser pakte die datum, zag dat mei vóór augustus ligt, telde
# er een jaar bij op en stelde een afspraak voor op 30 mei 2027. Een datum die
# maanden vooruit ligt is geen afspraak maar een misparse.

def test_datum_ver_in_de_toekomst_wordt_niet_als_afspraak_gelezen():
    from backend.domains.calendar import agent as ag
    nu = ag._amsterdam_now()
    # Kies een maand die zeker vóór de huidige ligt, zodat de jaar-ophoging
    # aanslaat en de uitkomst ~10 maanden vooruit ligt.
    maand = "januari" if nu.month > 2 else "december"
    assert ag._parse_datetime(f"op 30 {maand} presenteerde het bedrijf zijn plannen") is None


def test_normale_afspraak_binnen_de_horizon_blijft_werken():
    from datetime import timedelta
    from backend.domains.calendar import agent as ag
    resultaat = ag._parse_datetime("zullen we volgende week om 14:00 bellen?")
    assert resultaat is not None
    assert resultaat <= ag._amsterdam_now() + timedelta(days=ag._MAX_HORIZON_DAGEN)
