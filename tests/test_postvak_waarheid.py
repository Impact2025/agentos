"""Tests voor het Postvak: wat er in staat, wat het telt, en wie het filtert.

Elke test hier dekt één storing die op 11 aug 2026 tegelijk op één telefoonscherm
stond — zeven "mails die op jouw antwoord wachten" waarvan er vijf door Vincent
zélf waren verstuurd, "121 open · 0% beantwoord" als grootste getal, "106 nog
niet getrieerd" als permanente toestand, en geen enkele manier om een afzender
te blokkeren. Geen ervan wierp ooit een fout op.
"""
from datetime import datetime, timedelta, timezone

import pytest

from backend.domains.outlook import rules
from backend.domains.outlook import service as outlook
from backend.shared.database import get_conn


def _mail(mid, *, van="hallo@extern.nl", naam="Extern", aan="v.munster@weareimpact.nl",
          onderwerp="Vraag", folder="inbox", label="", prio=50, dagen=0,
          is_read=0, is_replied=0, thread="conv-1"):
    now = datetime.now(timezone.utc)
    with get_conn() as c:
        c.execute(
            "INSERT OR REPLACE INTO outlook_emails "
            "(id, subject, from_email, from_name, to_email, received_at, body_preview, "
            " is_read, is_replied, folder, triage_label, priority, thread_id, synced_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (mid, onderwerp, van, naam, aan,
             (now - timedelta(days=dagen)).isoformat(), "", is_read, is_replied,
             folder, label, prio, thread, now.isoformat()),
        )
    return mid


@pytest.fixture(autouse=True)
def schoon():
    """Elke test begint met een leeg postvak én zonder regels — anders lekt de
    seed van de systeemregels (60 stuks) in de matching van de volgende test."""
    with get_conn() as c:
        c.execute("DELETE FROM outlook_emails")
        c.execute("DELETE FROM mail_sender_rules")
    yield
    with get_conn() as c:
        c.execute("DELETE FROM outlook_emails")
        c.execute("DELETE FROM mail_sender_rules")


# ── De spam-knop ───────────────────────────────────────────────────────────

def test_regel_werkt_met_terugwerkende_kracht():
    """Dit is de hele belofte van de knop: één tik ruimt óók op wat er al ligt.

    Vóór 11 aug 2026 zat de filtering alleen in `triage_single`, dus raakte een
    nieuwe regel uitsluitend mail die daarná binnenkwam — en bleef de stapel
    waarvoor je op de knop drukte gewoon staan.
    """
    for i in range(3):
        _mail(f"nieuwsbrief-{i}", van="promo@webshop.nl", label="info")
    _mail("klant", van="klant@bedrijf.nl", label="actie")

    rule = rules.add_rule("promo@webshop.nl", scope=rules.SCOPE_ADRES,
                          action=rules.ACTIE_SPAM, source="mens")

    assert rule["applied"] == 3
    with get_conn() as c:
        labels = {r["id"]: r["triage_label"] for r in
                  c.execute("SELECT id, triage_label FROM outlook_emails")}
    assert labels["nieuwsbrief-0"] == "spam"
    assert labels["klant"] == "actie", "een regel mag alleen zijn eigen afzender raken"


def test_regel_intrekken_geeft_de_mail_terug():
    """Strenger filteren mag alleen als er een weg terug is. Teruggeven betekent
    opnieuw laten beoordelen, niet het oude label raden: dat zou een mail van
    vier weken geleden vandaag als urgent kunnen terugzetten."""
    _mail("m1", van="promo@webshop.nl", label="info")
    rule = rules.add_rule("promo@webshop.nl", action=rules.ACTIE_SPAM)

    uitslag = rules.deactivate_rule(rule["id"])

    assert uitslag["released"] == 1
    with get_conn() as c:
        row = c.execute("SELECT triage_label, filter_rule_id FROM outlook_emails "
                        "WHERE id='m1'").fetchone()
    assert row["triage_label"] == "", "moet opnieuw door de triage, niet met een gegokt label"
    assert row["filter_rule_id"] is None


def test_domeinregel_pakt_subdomeinen_maar_niet_een_ander_domein():
    _mail("a", van="info@vacaturebank.nl")
    _mail("b", van="alert@mail.vacaturebank.nl")
    _mail("c", van="hallo@vacaturebank.nl.echtebedrijf.nl")

    rule = rules.add_rule("vacaturebank.nl", scope=rules.SCOPE_DOMEIN,
                          action=rules.ACTIE_GEEN_KLANT)

    assert rule["applied"] == 2, "subdomein telt mee, een domein dat er alleen op lijkt niet"


def test_whitelist_wint_van_een_te_brede_systeemregel():
    """Zonder deze voorrang is het filter alleen strenger te maken en nooit
    milder — dan moet je een systeemregel verwijderen om één klant te redden."""
    rules.add_rule("shop", scope=rules.SCOPE_DEEL, action=rules.ACTIE_GEEN_KLANT,
                   source="systeem")
    rules.add_rule("inkoop@shopwerk.nl", scope=rules.SCOPE_ADRES,
                   action=rules.ACTIE_ALTIJD_TONEN)

    assert rules.verdict("inkoop@shopwerk.nl") is None
    assert rules.verdict("promo@shoppie.nl") is not None


def test_blokkeren_vanaf_een_mail_maakt_een_regel_op_de_afzender():
    _mail("m1", van="spam@casino.example", onderwerp="Gefeliciteerd")
    _mail("m2", van="spam@casino.example", onderwerp="Nogmaals")

    uitslag = outlook.block_sender("m1", scope="adres", action="spam")

    assert uitslag["pattern"] == "spam@casino.example"
    assert uitslag["applied"] == 2
    assert any(r["source"] == "mens" for r in rules.list_rules())


def test_teruggezette_mail_wordt_niet_opnieuw_weggefilterd():
    """"Toch tonen" op één mail moet het overleven dat de regels elke twintig
    minuten opnieuw over het postvak gaan. Zonder markering haalde de volgende
    ronde hem meteen terug weg — de regel matcht immers nog steeds — en dan doet
    de knop niets, één keer per sync."""
    _mail("m1", van="promo@webshop.nl")
    rules.add_rule("promo@webshop.nl", action=rules.ACTIE_SPAM)

    outlook.restore_email("m1")
    rules.apply_all()

    with get_conn() as c:
        row = c.execute("SELECT triage_label, filter_rule_id FROM outlook_emails "
                        "WHERE id='m1'").fetchone()
    assert row["triage_label"] == ""
    assert row["filter_rule_id"] is None


def test_regels_gelden_ook_voor_mail_die_al_gelabeld_was():
    """De standaardregels zijn geërfd uit code; zonder deze ronde krijgt oude
    mail nooit een `filter_rule_id` en is niet te achterhalen wélke regel hem
    wegnam — precies de onzichtbaarheid waar dit mechanisme vanaf moest."""
    _mail("oud", van="promo@webshop.nl", label="archief")
    rules.add_rule("webshop.nl", scope=rules.SCOPE_DOMEIN, action=rules.ACTIE_GEEN_KLANT,
                   source="systeem")

    rules.apply_all()

    with get_conn() as c:
        assert c.execute("SELECT filter_rule_id FROM outlook_emails WHERE id='oud'"
                         ).fetchone()["filter_rule_id"] is not None


def test_archiveren_blokkeert_de_afzender_niet():
    """'Ik ben klaar met dit bericht' en 'ik wil deze afzender nooit meer' zijn
    twee besluiten. Ze op één knop leggen is hoe je per ongeluk een klant kwijt
    raakt."""
    _mail("m1", van="klant@bedrijf.nl")
    outlook.archive_email("m1")

    assert rules.list_rules() == []
    with get_conn() as c:
        assert c.execute("SELECT triage_label FROM outlook_emails WHERE id='m1'"
                         ).fetchone()["triage_label"] == "archief"


# ── Tellen: wat vraagt om een handeling ────────────────────────────────────

def test_tellingen_negeren_verzonden_en_weggefilterde_mail():
    """"121 open" was het grootste getal op het scherm terwijl geen enkele knop
    het kleiner maakte: verzonden post, spam en ruis telden allemaal mee."""
    _mail("in-1", is_read=0)
    _mail("in-2", is_read=0)
    _mail("uit", van="v.munster@weareimpact.nl", aan="prospect@extern.nl", folder="sent")
    _mail("ruis", van="promo@webshop.nl", is_read=0)
    rules.add_rule("promo@webshop.nl", action=rules.ACTIE_SPAM)

    stats = outlook.get_stats()

    assert stats["unread"] == 2, "alleen echte, ongefilterde binnengekomen mail"
    assert stats["filtered"] == 1
    assert stats["sent"] == 1
    assert stats["untriaged"] == 2, "gefilterde mail hoeft geen triage-oordeel"


def test_gesorteerde_inbox_bevat_geen_verzonden_mail():
    """De storing zelf: vijf van de zeven regels onder 'wacht op jouw antwoord'
    waren door Vincent zelf verstuurde outreach."""
    _mail("outreach", van="v.munster@weareimpact.nl", aan="info@prospect.nl",
          folder="sent", label="actie", prio=80)
    _mail("echt", label="actie", prio=60)

    sorted_inbox = outlook.list_sorted_db()

    ids = [m["id"] for m in sorted_inbox["needs_reply"]]
    assert ids == ["echt"]


def test_triage_slaat_verzonden_mail_over():
    _mail("outreach", van="v.munster@weareimpact.nl", aan="info@prospect.nl", folder="sent")

    import asyncio

    async def draai():
        return [e async for e in outlook.triage_single("outreach")]

    events = asyncio.run(draai())
    assert events and events[0]["type"] == "triage_skipped"


def test_triage_gebruikt_de_regels_en_slaat_de_llm_over():
    """De regels draaien vóór het model: het LLM-budget ging op aan mail die
    toch wegvalt (zelfde volgorde-argument als de signaalpoort van de radar)."""
    _mail("m1", van="noreply@digest.example")
    rules.add_rule("digest.example", scope=rules.SCOPE_DOMEIN,
                   action=rules.ACTIE_GEEN_KLANT)

    import asyncio

    async def draai():
        return [e async for e in outlook.triage_single("m1")]

    events = asyncio.run(draai())
    assert events[-1]["auto_archived"] is True
    assert events[-1]["label"] == "archief"


# ── Invarianten ────────────────────────────────────────────────────────────

def test_invariant_ziet_eigen_verzonden_mail_in_het_postvak(monkeypatch):
    from backend.domains.iris import integrity

    monkeypatch.setattr(integrity, "_postvak_eigen_adressen",
                        lambda: {"v.munster@weareimpact.nl"})
    _mail("fout", van="v.munster@weareimpact.nl", aan="info@prospect.nl", folder="inbox")
    _mail("goed", van="klant@bedrijf.nl", folder="inbox")

    bevindingen = integrity._check_postvak_eigen_verzonden()

    assert [b.subject for b in bevindingen] == ["mail:fout"]


def test_invariant_ziet_een_regel_die_niets_deed():
    """De toets op de belofte 'werkt met terugwerkende kracht'. Slaat hij aan,
    dan haalt een pad mail binnen zonder de regels te raadplegen."""
    from backend.domains.iris import integrity

    rules.add_rule("promo@webshop.nl", action=rules.ACTIE_SPAM)
    # Bewust ná de regel ingevoegd, alsof een sync de regels oversloeg.
    _mail("gemist", van="promo@webshop.nl", label="info")

    bevindingen = integrity._check_postvak_regel_zonder_effect()

    assert len(bevindingen) == 1
    assert "promo@webshop.nl" in bevindingen[0].detail


def test_invariant_zwijgt_als_de_regels_wel_zijn_toegepast():
    from backend.domains.iris import integrity

    _mail("gefilterd", van="promo@webshop.nl", label="info")
    rules.add_rule("promo@webshop.nl", action=rules.ACTIE_SPAM)

    assert integrity._check_postvak_regel_zonder_effect() == []


def test_invariant_meldt_dat_er_nooit_een_antwoord_is_waargenomen():
    from backend.domains.iris import integrity

    for i in range(25):
        _mail(f"m{i}", dagen=1)

    bevindingen = integrity._check_postvak_beantwoord_niet_waargenomen()
    assert len(bevindingen) == 1

    with get_conn() as c:
        c.execute("UPDATE outlook_emails SET replied_at = ? WHERE id='m1'",
                  (datetime.now(timezone.utc).isoformat(),))
    assert integrity._check_postvak_beantwoord_niet_waargenomen() == []


def test_invariant_zwijgt_bij_een_leeg_postvak():
    """Een verse installatie is geen storing — dezelfde regel als _baseline in
    de scheduler-inhaalslag."""
    from backend.domains.iris import integrity

    assert integrity._check_postvak_beantwoord_niet_waargenomen() == []
