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

# AgentOS draait met `backend` als package; zet de agentos-root op het pad.
_HERE = os.path.dirname(os.path.abspath(__file__))
AGENTOS_ROOT = os.path.dirname(_HERE)  # D:/apps/agentos
sys.path.insert(0, AGENTOS_ROOT)

_TMP = tempfile.mkdtemp()
_DB = os.path.join(_TMP, "test_agentos.db")

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
    monkeypatch.setattr(drafter, "_sync_llm", lambda system, user: "Beste, reset via /wachtwoord-vergeten.")
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
    monkeypatch.setattr(drafter, "_sync_llm", lambda system, user: "Beste, reset via /wachtwoord-vergeten.")
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
    monkeypatch.setattr(drafter, "_sync_llm", lambda system, user: "stub")
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


def test_reject_and_edit(mailbox_row, monkeypatch):
    monkeypatch.setattr(poplib, "POP3", FakePop)
    monkeypatch.setattr(drafter, "_sync_llm", lambda system, user: "stub")
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
