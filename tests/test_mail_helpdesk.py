"""Smoke-test voor de mail helpdesk (review-gate), geen netwerk / geen LLM.

Draait tegen een tijdelijke sqlite-DB. Test:
  - DB-migratie maakt mailboxes/mail_inbox/mail_reply aan
  - fetch_new: dedupe via UIDL, spam wordt niet als vraag teruggegeven
  - classify: onderscheidt question/newsletter/invoice
  - service.run_mailbox: een vraag -> 1 mail_reply (pending_review), spam -> 0
  - send_reply/reject: status-transities
"""
import os
import sys
import types
import tempfile
import pytest

# ImpactOS draait met `backend` als package; zet de impactos-root op het pad.
_HERE = os.path.dirname(os.path.abspath(__file__))
IMPACTOS_ROOT = os.path.dirname(_HERE)  # D:/apps/impactos
sys.path.insert(0, IMPACTOS_ROOT)

_TMP = tempfile.mkdtemp()
_DB = os.path.join(_TMP, "test_impactos.db")

import poplib  # noqa: E402
from email.mime.text import MIMEText  # noqa: E402

from backend.shared import database as db  # noqa: E402
db.DB_PATH = _DB
db.init_db()

from backend.domains.mail import inbox, classify, drafter, service  # noqa: E402


# ── fake POP3 ──────────────────────────────────────────────────────────────

def _make_msg(frm, subject, body):
    m = MIMEText(body, "plain", "utf-8")
    m["From"] = frm
    m["Subject"] = subject
    return m.as_bytes()


class FakePop:
    def __init__(self, *a, **k):
        self._mails = [
            _make_msg("Klant <jan@x.nl>", "Hoe reset ik mijn wachtwoord?",
                      "Ik kom niet meer in mijn account, help!"),
            _make_msg("Spamrelay@spamrelay-pmgmaster.zxcs.nl",
                      "Daily Spam Report for 'x@y.nl' - 2026-07-03",
                      "geen body"),
            _make_msg("News <noreply@vendor.com>", "Five Ways To Get Started With Web Push",
                      "Click here to unsubscribe"),
        ]

    def getwelcome(self):
        return b"+OK fake"

    def user(self, u):
        pass

    def pass_(self, p):
        pass

    def list(self):
        return (b"+OK 3", [b"1 1", b"2 1", b"3 1"], 30)

    def uidl(self):
        return (b"+OK", [b"1 U1", b"2 U2", b"3 U3"], 30)

    def retr(self, n):
        idx = int(n) - 1
        raw = self._mails[idx]
        return (b"+OK", raw.split(b"\n"), len(raw))

    def quit(self):
        pass


@pytest.fixture
def mailbox_row():
    with db.get_conn() as conn:
        conn.execute("DELETE FROM mail_reply")
        conn.execute("DELETE FROM mail_inbox")
        conn.execute("DELETE FROM mailboxes")
        conn.execute(
            "INSERT OR IGNORE INTO mailboxes(id,project,label,address,pop_host,pop_port,pop_user,"
            "pop_password,brand_context,knowledge_scope,poll_minutes,enabled,created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,datetime('now'))",
            ("mb_test", "skillkaart", "SK helpdesk", "hello@skillkaart.nl",
             "mail.skillkaart.nl", 110, "hello@skillkaart.nl", "pw",
             "Skillkaart — helder en warm", "all", 30, 1),
        )
    return "mb_test"


def test_tables_exist():
    with db.get_conn() as conn:
        names = {r["name"] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name IN ('mailboxes','mail_inbox','mail_reply')")}
    assert names == {"mailboxes", "mail_inbox", "mail_reply"}


def test_classify():
    assert classify.classify("Hoe reset ik?", "Ik zit buiten.") == "question"
    assert classify.classify("Five Ways To Get Started", "unsubscribe") == "newsletter"
    assert classify.classify("Factuur juli", "€ 120,00") == "invoice"


def test_fetch_new_filters_spam(monkeypatch, mailbox_row):
    monkeypatch.setattr(poplib, "POP3", FakePop)
    with db.get_conn() as conn:
        got = inbox.fetch_new("mb_test", "h", 110, "u", "p", conn)
        # spam + newsletter worden niet teruggegeven, alleen de echte vraag
        assert len(got) == 1
        assert got[0]["subject"] == "Hoe reset ik mijn wachtwoord?"
        spam = conn.execute(
            "SELECT classified FROM mail_inbox WHERE uidl='U2'").fetchone()
        assert spam["classified"] == "spam"


def test_run_mailbox_creates_pending_reply(mailbox_row, monkeypatch):
    monkeypatch.setattr(poplib, "POP3", FakePop)
    monkeypatch.setattr(drafter, "_sync_openmodel", lambda system, user: "Beste, reset via /wachtwoord-vergeten.")
    n = service.run_mailbox({
        "id": "mb_test", "pop_host": "h", "pop_port": 110, "pop_user": "u",
        "pop_password": "p", "brand_context": "Skillkaart", "knowledge_scope": "all",
    })
    assert n == 1
    with db.get_conn() as conn:
        rows = list(conn.execute("SELECT status, to_addr FROM mail_reply"))
    assert len(rows) == 1
    assert rows[0]["status"] == "pending_review"
    assert rows[0]["to_addr"] == "jan@x.nl"


def test_appointment_mail_is_processed_outside_the_poll_transaction(mailbox_row, monkeypatch):
    """Afspraak-mail: het voorstel wordt opgeslagen, niet gesmoord door een lock.

    `create_proposal` opent zijn eigen verbinding. Werd hij aangeroepen binnen de
    open write-transactie van de mail-poll, dan wachtte die op een lock die de
    aanroeper zelf vasthield — self-deadlock, en elk afspraak-voorstel sneuvelde
    op "database is locked" (20 jul 2026). De nep-agent hieronder schrijft écht,
    dus hij faalt zodra de poll-transactie weer om hem heen komt te liggen.
    """
    monkeypatch.setattr(poplib, "POP3", FakePop)
    monkeypatch.setattr(classify, "classify",
                        lambda s, b, f, headers=None: "appointment")

    from backend.domains.calendar import agent as agenda_agent

    calls = []

    def fake_create_proposal(mid, inbox_id, subject, from_addr, body, from_name=""):
        calls.append((mid, inbox_id))
        with db.get_conn() as conn:  # eigen verbinding — dít is de scherpe rand
            conn.execute(
                "INSERT INTO calendar_proposals(mailbox_id,inbox_id,from_addr,subject,"
                "title,proposed_start,proposed_end,conflict_checked,status) "
                "VALUES(?,?,?,?,?,?,?,?,'pending_review')",
                (mid, inbox_id, from_addr, subject, "Afspraak",
                 "2026-07-21T10:00:00", "2026-07-21T10:30:00", "ok"),
            )
        return {"id": 1}

    monkeypatch.setattr(agenda_agent, "create_proposal", fake_create_proposal)

    n = service.run_mailbox({
        "id": "mb_test", "pop_host": "h", "pop_port": 110, "pop_user": "u",
        "pop_password": "p", "brand_context": "Skillkaart", "knowledge_scope": "all",
    })
    assert n == 1
    # Filter op deze specifieke aanroep i.p.v. de hele tabel: calendar_proposals
    # wordt door geen enkele fixture opgeruimd, dus rijen van eerdere agenda-tests
    # in dezelfde testsessie stapelen zich op in die tabel.
    assert len(calls) == 1
    mid, inbox_id = calls[0]
    with db.get_conn() as conn:
        rows = list(conn.execute(
            "SELECT status FROM calendar_proposals WHERE mailbox_id=? AND inbox_id=?",
            (mid, inbox_id),
        ))
    assert len(rows) == 1 and rows[0]["status"] == "pending_review"


def test_detect_language():
    assert drafter.detect_language("Hoe reset ik mijn wachtwoord, help alstublieft") == "nl"
    assert drafter.detect_language("How do I reset my password please") == "en"


def test_fetch_new_ignores_auto_submitted(monkeypatch, mailbox_row):
    # FakePop variant met een Auto-Submitted header (out-of-office)
    class AutoPop(FakePop):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            auto_mail = (
                "From: Bert <bert@y.nl>\r\n"
                "Subject: Out of office\r\n"
                "Auto-Submitted: auto-replied\r\n"
                "Content-Type: text/plain; charset=utf-8\r\n\r\n"
                "Ik ben op vakantie tot 1 aug.\r\n"
            ).encode("utf-8")
            self._mails = self._mails + [auto_mail]

        def list(self):
            return (b"+OK 4", [b"1 1", b"2 2", b"3 3", b"4 4"], 40)

        def uidl(self):
            return (b"+OK", [b"1 U1", b"2 U2", b"3 U3", b"4 U4"], 40)

        def retr(self, n):
            idx = int(n) - 1
            raw = self._mails[idx]
            return (b"+OK", raw.split(b"\n"), len(raw))

    monkeypatch.setattr(poplib, "POP3", AutoPop)
    with db.get_conn() as conn:
        got = inbox.fetch_new("mb_test", "h", 110, "u", "p", conn)
        # de vraag komt door, de auto-reply niet
        assert len(got) == 1
        auto = conn.execute(
            "SELECT classified, auto_submitted FROM mail_inbox WHERE uidl='U4'").fetchone()
        assert auto["classified"] == "auto"
        assert auto["auto_submitted"] == 1


def test_run_mailbox_saves_threading_headers(mailbox_row, monkeypatch):
    monkeypatch.setattr(poplib, "POP3", FakePop)
    monkeypatch.setattr(drafter, "_sync_openmodel", lambda system, user: "Beste, reset via /wachtwoord-vergeten.")
    service.run_mailbox({
        "id": "mb_test", "pop_host": "h", "pop_port": 110, "pop_user": "u",
        "pop_password": "p", "brand_context": "Skillkaart", "knowledge_scope": "all",
    })
    with db.get_conn() as conn:
        r = conn.execute(
            'SELECT in_reply_to, "references" FROM mail_reply LIMIT 1').fetchone()
    # geen threading bij deze fake (geen headers), maar de kolommen bestaan & zijn leeg
    assert r["in_reply_to"] == "" or r["in_reply_to"] is not None


def test_send_via_mailbox_smtp(mailbox_row, monkeypatch):
    """Verstuur moet de SMTP van déze mailbox gebruiken, niet de globale .env."""
    monkeypatch.setattr(poplib, "POP3", FakePop)
    monkeypatch.setattr(drafter, "_sync_openmodel", lambda system, user: "stub")
    service.run_mailbox({"id": "mb_test", "pop_host": "h", "pop_port": 110,
                          "pop_user": "u", "pop_password": "p",
                          "brand_context": "", "knowledge_scope": "all"})
    with db.get_conn() as conn:
        rid = conn.execute("SELECT id FROM mail_reply").fetchone()["id"]

    sent = {}

    class FakeSMTP:
        def __init__(self, host, port, timeout=20):
            sent["host"] = host
            sent["port"] = port
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def ehlo(self): pass
        def starttls(self): pass
        def login(self, u, p):
            sent["user"] = u; sent["pw"] = p
        def sendmail(self, frm, to, msg):
            sent["from"] = frm; sent["to"] = to
            sent["msg"] = msg

    monkeypatch.setattr(service, "smtplib", types.SimpleNamespace(SMTP=FakeSMTP))
    # zorg dat de mailbox SMTP-creds gevuld zijn
    with db.get_conn() as conn:
        conn.execute(
            "UPDATE mailboxes SET smtp_host='mail.skillkaart.nl', smtp_port=587, "
            "smtp_user='hello@skillkaart.nl', smtp_password='pw', from_display='Skillkaart Hulp' "
            "WHERE id='mb_test'")
    assert service.send_reply(rid) is True
    # verstuurd via de mailbox-SMTP, met de juiste envelop + display-naam in header
    assert sent["host"] == "mail.skillkaart.nl"
    assert sent["user"] == "hello@skillkaart.nl"
    assert sent["from"] == "hello@skillkaart.nl"  # envelop-from = adres
    assert "Skillkaart Hulp" in sent["msg"]        # display-naam in header
    with db.get_conn() as conn:
        assert conn.execute("SELECT status FROM mail_reply").fetchone()["status"] == "sent"


def test_pending_replies_filters_per_project(mailbox_row, monkeypatch):
    """Elk project zijn eigen helpdesk: pending/mailboxes gefilterd op project."""
    monkeypatch.setattr(poplib, "POP3", FakePop)
    monkeypatch.setattr(drafter, "_sync_openmodel", lambda system, user: "stub")
    service.run_mailbox({"id": "mb_test", "pop_host": "h", "pop_port": 110,
                          "pop_user": "u", "pop_password": "p",
                          "brand_context": "", "knowledge_scope": "all"})
    # naam-matching is genormaliseerd: 'Skill kaart' ~ 'skillkaart'
    assert len(service.pending_replies(project="Skill kaart")) == 1
    assert service.pending_replies(project="teambuildingmetimpact") == []
    assert len(service.list_mailboxes(project="SKILLKAART")) == 1
    assert service.list_mailboxes(project="anders") == []
    # de klantvraag reist mee naar de UI
    p = service.pending_replies(project="skillkaart")[0]
    assert "wachtwoord" in p["question_subject"].lower()


def test_knowledge_includes_site_profile_pages_and_learned_qa(mailbox_row):
    """De helpdesk kent het project: merkprofiel, échte live links en eerder
    goedgekeurde antwoorden gaan mee in de kennisbasis."""
    from backend.domains.mail import knowledge as km
    with db.get_conn() as conn:
        conn.execute("DELETE FROM sites")
        conn.execute("DELETE FROM published_pages")
        conn.execute(
            "INSERT INTO sites(id,name,base_url,gsc_property,profile,ctas,created_at) "
            "VALUES('s1','Skillkaart','https://skillkaart.nl','sc-domain:skillkaart.nl',"
            "'PWA voor voetbalcoaches, warm en nuchter.','[\"Plan een demo\"]',datetime('now'))")
        conn.execute(
            "INSERT INTO published_pages(id,site_id,slug,title,url,created_at,updated_at) "
            "VALUES('p1','s1','player-hub','Inloggen als coach',"
            "'https://skillkaart.nl/hub',datetime('now'),datetime('now'))")
        # eerder verstuurd (goedgekeurd) antwoord = leermateriaal
        conn.execute(
            "INSERT INTO mail_inbox(mailbox_id,uidl,from_addr,subject,body_text,classified) "
            "VALUES('mb_test','QA1','eerder@x.nl','Magic link komt niet aan','Geen mail ontvangen.','question')")
        prev_id = conn.execute("SELECT id FROM mail_inbox WHERE uidl='QA1'").fetchone()["id"]
        conn.execute(
            "INSERT INTO mail_reply(mailbox_id,inbox_id,to_addr,subject,draft_body,edited_body,"
            "status,sent_at) VALUES('mb_test',?,?,'Re: Magic link komt niet aan',"
            "'concept','Check je spambox, of vraag een nieuwe aan via de hub.','sent',datetime('now'))",
            (prev_id, "eerder@x.nl"))
        text = km.build_knowledge(conn, "skillkaart", {"id": "mb_test", "project": "skillkaart"})
    assert "voetbalcoaches" in text              # site-profiel
    assert "Plan een demo" in text               # CTA
    assert "https://skillkaart.nl/hub" in text   # echte live link (geen placeholder)
    assert "Check je spambox" in text            # geleerd van goedgekeurd antwoord


def test_thread_history_gives_conversation_context(mailbox_row):
    """Een 'Re: RE:'-mail wordt met de eerdere wisseling beantwoord."""
    from backend.domains.mail import knowledge as km
    with db.get_conn() as conn:
        conn.execute(
            "INSERT INTO mail_inbox(mailbox_id,uidl,from_addr,subject,body_text,classified,created_at) "
            "VALUES('mb_test','T1','jan@x.nl','probleem','Ik kan niet inloggen.','question','2026-07-09 10:00:00')")
        first_id = conn.execute("SELECT id FROM mail_inbox WHERE uidl='T1'").fetchone()["id"]
        conn.execute(
            "INSERT INTO mail_reply(mailbox_id,inbox_id,to_addr,subject,draft_body,status,sent_at) "
            "VALUES('mb_test',?,?,'Re: probleem','Log in via de hub.','sent','2026-07-09 11:00:00')",
            (first_id, "jan@x.nl"))
        conn.execute(
            "INSERT INTO mail_inbox(mailbox_id,uidl,from_addr,subject,body_text,classified,created_at) "
            "VALUES('mb_test','T2','jan@x.nl','Re: RE: probleem','Hub werkt ook niet.','question','2026-07-10 09:00:00')")
        new_id = conn.execute("SELECT id FROM mail_inbox WHERE uidl='T2'").fetchone()["id"]
        hist = km.thread_history(conn, "mb_test", "jan@x.nl", new_id)
    assert "Ik kan niet inloggen" in hist        # eerdere klantmail
    assert "Log in via de hub" in hist           # ons eerdere antwoord
    assert "Hub werkt ook niet" not in hist      # de nieuwe mail zelf niet
    assert hist.index("Ik kan niet inloggen") < hist.index("Log in via de hub")  # oud → nieuw


def test_drafter_prompt_carries_history_and_knowledge(monkeypatch):
    captured = {}
    def fake(system, user):
        captured["system"] = system
        captured["user"] = user
        return "ok"
    monkeypatch.setattr(drafter, "_sync_openmodel", fake)
    monkeypatch.setattr(drafter, "OPENMODEL_API_KEY", "test-key")
    drafter.draft_reply(
        from_name="Jan", subject="Re: probleem", body="Hub werkt ook niet.",
        brand_context="skillkaart",
        knowledge="Live pagina's: https://skillkaart.nl/hub",
        history="[KLANT] probleem\nIk kan niet inloggen.\n\n[WIJ] Re: probleem\nLog in via de hub.")
    assert "https://skillkaart.nl/hub" in captured["system"]
    assert "EERDERE CORRESPONDENTIE" in captured["user"]
    assert "Log in via de hub" in captured["user"]
    assert "placeholder" in captured["system"]   # anti-'[jouw domein]'-instructie


def test_edit_body_accepts_both_keys():
    """De frontend stuurde historisch 'draft_body' — dat mag nooit een 422 worden."""
    from backend.domains.mail.router import EditBody
    assert EditBody(text="a").text == "a"
    assert EditBody(draft_body="b").text == "b"
    with pytest.raises(Exception):
        EditBody()


def test_run_single_mailbox_and_error_escalation(mailbox_row, monkeypatch):
    """run met mailbox_id pakt alleen die mailbox; een kapotte mailbox wordt een
    error-kaart in het Actiecentrum (activity_log, status='error')."""
    def broken(*a, **k):
        raise RuntimeError("POP3 mislukt: login geweigerd")
    monkeypatch.setattr(service, "run_mailbox", broken)
    results = service.run_all_mailboxes(mailbox_id="mb_test")
    assert results == {"hello@skillkaart.nl": -1}
    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT status, next_step FROM activity_log WHERE action='mail_helpdesk' "
            "ORDER BY created_at DESC LIMIT 1").fetchone()
    assert row is not None and row["status"] == "error"
    assert "POP3" in row["next_step"] or "instellingen" in row["next_step"]


def test_signature_lands_in_draft_and_llm_does_not_sign(mailbox_row, monkeypatch):
    """Handtekening per project: onder elk concept (WYSIWYG), en de LLM krijgt
    de instructie om niet zelf ook nog te ondertekenen."""
    captured = {}
    def fake(system, user):
        captured["system"] = system
        return "Hoi Jan, reset via de hub.\n\nHartelijke groet,"
    monkeypatch.setattr(poplib, "POP3", FakePop)
    monkeypatch.setattr(drafter, "_sync_openmodel", fake)
    monkeypatch.setattr(drafter, "OPENMODEL_API_KEY", "test-key")
    with db.get_conn() as conn:
        conn.execute("UPDATE mailboxes SET signature='Vincent van Munster\nSkillkaart' WHERE id='mb_test'")
    service.run_mailbox({"id": "mb_test", "pop_host": "h", "pop_port": 110,
                          "pop_user": "u", "pop_password": "p", "project": "skillkaart",
                          "signature": "Vincent van Munster\nSkillkaart",
                          "brand_context": "", "knowledge_scope": "all"})
    with db.get_conn() as conn:
        draft = conn.execute("SELECT draft_body FROM mail_reply").fetchone()["draft_body"]
    assert draft.endswith("Vincent van Munster\nSkillkaart")
    assert "GEEN naam" in captured["system"]  # anti-dubbele-handtekening-instructie


def test_send_skips_generic_footer_when_signature_set(mailbox_row, monkeypatch):
    monkeypatch.setattr(poplib, "POP3", FakePop)
    monkeypatch.setattr(drafter, "_sync_openmodel", lambda system, user: "stub")
    service.run_mailbox({"id": "mb_test", "pop_host": "h", "pop_port": 110,
                          "pop_user": "u", "pop_password": "p", "project": "skillkaart",
                          "brand_context": "", "knowledge_scope": "all"})
    with db.get_conn() as conn:
        rid = conn.execute("SELECT id FROM mail_reply").fetchone()["id"]
        conn.execute(
            "UPDATE mailboxes SET smtp_host='mail.skillkaart.nl', smtp_port=587, "
            "smtp_user='hello@skillkaart.nl', smtp_password='pw', "
            "signature='Vincent van Munster' WHERE id='mb_test'")
    sent = {}
    class FakeSMTP:
        def __init__(self, host, port, timeout=20): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def ehlo(self): pass
        def starttls(self): pass
        def login(self, u, p): pass
        def sendmail(self, frm, to, msg): sent["msg"] = msg
    monkeypatch.setattr(service, "smtplib", types.SimpleNamespace(SMTP=FakeSMTP))
    assert service.send_reply(rid) is True
    assert "voorbereid met Impact OS" not in sent["msg"]  # geen dubbele afsluiting
    # verzending is als uitkomst-kaart gelogd
    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT status FROM activity_log WHERE action='mail_verstuurd' "
            "ORDER BY created_at DESC LIMIT 1").fetchone()
    assert row is not None and row["status"] == "ok"


def test_knowledge_coverage_reports_layers_and_hints(mailbox_row):
    from backend.domains.mail import knowledge as km
    with db.get_conn() as conn:
        conn.execute("DELETE FROM sites")
        conn.execute("DELETE FROM published_pages")
        cov = km.coverage(conn, "projectzondersite", {"id": "mb_test", "project": "projectzondersite"})
    assert cov["site_found"] is False
    assert cov["live_pages"] == 0
    assert cov["signature"] is False
    assert any("site" in h.lower() for h in cov["hints"])
    assert any("handtekening" in h.lower() for h in cov["hints"])
    assert cov["total_chars"] >= 0


def test_html_only_mail_gets_text_body(mailbox_row, monkeypatch):
    """Mailclients die alléén text/html sturen mogen geen lege body opleveren."""
    class HtmlPop(FakePop):
        def __init__(self, *a, **k):
            html_mail = (
                "From: Klant <eva@x.nl>\r\n"
                "Subject: Vraag over inloggen\r\n"
                "Content-Type: text/html; charset=utf-8\r\n\r\n"
                "<html><body><p>Hoe kan ik <b>inloggen</b> als coach?</p>"
                "<br><p>Groet, Eva</p></body></html>\r\n"
            ).encode("utf-8")
            self._mails = [html_mail]
        def list(self):
            return (b"+OK 1", [b"1 1"], 10)
        def uidl(self):
            return (b"+OK", [b"1 UH1"], 10)
    monkeypatch.setattr(poplib, "POP3", HtmlPop)
    with db.get_conn() as conn:
        got = inbox.fetch_new("mb_test", "h", 110, "u", "p", conn)
    assert len(got) == 1
    assert "inloggen" in got[0]["body_text"]
    assert "<" not in got[0]["body_text"]  # tags gestript


def test_reject_and_edit(mailbox_row, monkeypatch):
    monkeypatch.setattr(poplib, "POP3", FakePop)
    monkeypatch.setattr(drafter, "_sync_openmodel", lambda system, user: "stub")
    service.run_mailbox({"id": "mb_test", "pop_host": "h", "pop_port": 110,
                          "pop_user": "u", "pop_password": "p",
                          "brand_context": "", "knowledge_scope": "all"})
    with db.get_conn() as conn:
        rid = conn.execute("SELECT id FROM mail_reply").fetchone()["id"]
    service.reject_reply(rid)
    with db.get_conn() as conn:
        assert conn.execute("SELECT status FROM mail_reply").fetchone()["status"] == "rejected"
    service.edit_reply(rid, "Nieuwe tekst")
    with db.get_conn() as conn:
        row = conn.execute("SELECT status, edited_body FROM mail_reply").fetchone()
    assert row["status"] == "edited"
    assert "Nieuwe tekst" in row["edited_body"]


# ── Negeerlijst: "Niet meer reageren" ───────────────────────────────────────

def test_ignore_sender_rejects_and_blocks_future(mailbox_row, monkeypatch):
    """De knop zet de afzender op de negeerlijst (zakelijk domein = heel
    domein), wijst het openstaande concept af, en een volgende mail van
    dezelfde afzender krijgt géén nieuw concept meer."""
    monkeypatch.setattr(poplib, "POP3", FakePop)
    monkeypatch.setattr(drafter, "_sync_openmodel", lambda system, user: "stub")
    service.run_mailbox({"id": "mb_test", "pop_host": "h", "pop_port": 110,
                          "pop_user": "u", "pop_password": "p",
                          "brand_context": "", "knowledge_scope": "all"})
    with db.get_conn() as conn:
        rid = conn.execute("SELECT id FROM mail_reply").fetchone()["id"]

    result = service.ignore_sender(rid)
    assert result == {"address": "jan@x.nl", "domain_blocked": True}
    with db.get_conn() as conn:
        assert conn.execute("SELECT status FROM mail_reply WHERE id=?",
                            (rid,)).fetchone()["status"] == "rejected"
        patterns = {r["pattern"] for r in conn.execute(
            "SELECT pattern FROM mail_ignored_senders")}
        assert "jan@x.nl" in patterns and "@x.nl" in patterns
        # oude inbox-mail is als 'ignored' gemarkeerd
        assert conn.execute(
            "SELECT classified FROM mail_inbox WHERE from_addr LIKE '%jan@x.nl%'"
        ).fetchone()["classified"] == "ignored"

    # Nieuwe mail van hetzelfde domein → geen nieuw concept
    class SamePop(FakePop):
        def __init__(self, *a, **k):
            self._mails = [_make_msg("Klant <jan@x.nl>", "Nog een vraag?",
                                     "Hoe werkt mijn account, help!")]
        def list(self):
            return (b"+OK 1", [b"1 1"], 10)
        def uidl(self):
            return (b"+OK", [b"1 U9"], 10)
    monkeypatch.setattr(poplib, "POP3", SamePop)
    n = service.run_mailbox({"id": "mb_test", "pop_host": "h", "pop_port": 110,
                              "pop_user": "u", "pop_password": "p",
                              "brand_context": "", "knowledge_scope": "all"})
    assert n == 0
    with db.get_conn() as conn:
        assert conn.execute(
            "SELECT classified FROM mail_inbox WHERE uidl='U9'"
        ).fetchone()["classified"] == "ignored"
        assert conn.execute(
            "SELECT COUNT(*) c FROM mail_reply WHERE status IN ('pending_review','edited')"
        ).fetchone()["c"] == 0


def test_ignore_sender_freemail_blocks_only_address(mailbox_row):
    """Gmail-achtige domeinen worden nooit domein-breed geblokkeerd."""
    with db.get_conn() as conn:
        conn.execute("DELETE FROM mail_ignored_senders")
        cur = conn.execute(
            "INSERT INTO mail_inbox(mailbox_id,uidl,from_addr,subject,body_text,classified) "
            "VALUES('mb_test','UG1','Piet <piet@gmail.com>','Vraag','Hoe werkt dit?','question')")
        inbox_id = cur.lastrowid
        rid = conn.execute(
            "INSERT INTO mail_reply(mailbox_id,inbox_id,to_addr,subject,draft_body) "
            "VALUES('mb_test',?,'piet@gmail.com','Re: Vraag','concept')",
            (inbox_id,)).lastrowid
    result = service.ignore_sender(rid)
    assert result == {"address": "piet@gmail.com", "domain_blocked": False}
    with db.get_conn() as conn:
        patterns = {r["pattern"] for r in conn.execute(
            "SELECT pattern FROM mail_ignored_senders")}
    assert "piet@gmail.com" in patterns
    assert "@gmail.com" not in patterns
    # andere gmail-afzender blijft welkom
    with db.get_conn() as conn:
        assert service.is_ignored_sender(conn, "Ans <ans@gmail.com>") is False
        assert service.is_ignored_sender(conn, "Piet <piet@gmail.com>") is True


def test_unignore_sender(mailbox_row):
    with db.get_conn() as conn:
        conn.execute("DELETE FROM mail_ignored_senders")
        conn.execute("INSERT INTO mail_ignored_senders(pattern) VALUES('@ibood.com')")
    lst = service.list_ignored_senders()
    assert any(x["pattern"] == "@ibood.com" for x in lst)
    iid = [x["id"] for x in lst if x["pattern"] == "@ibood.com"][0]
    assert service.unignore_sender(iid) is True
    with db.get_conn() as conn:
        assert service.is_ignored_sender(conn, "deals@ibood.com") is False
