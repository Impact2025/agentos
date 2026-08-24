# Mail Helpdesk (Review-Gate) Implementation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Inkomende supportmail op een helpdesk-adres automatisch ophalen, classificeren en
een concept-antwoord laten schrijven door de LLM, dat vervolgens in het Actiecentrum belandt
als review-item. Geen mail vertrekt zonder Vincents expliciete klik (zelfde discipline als de
content-wachtrij / Iris "publiceert nooit zelf").

**Architecture:** Nieuwe `domains/mail/` module. Een scheduler-taak pollt POP3 (dedupe via
UIDL), filtert spam/automatische rapporten, laat een LLM een NL-concept-antwoord genereren
op basis van merkcontext (SCHRIJF-DNA) + Iris-kennisbank, en schrijft het als `mail_reply`
rijen in SQLite. Het Actiecentrum toont die rijen met knoppen Verstuur / Bewerk / Afwijzen.
Verzending hergebruikt de bestaande `shared/email_service.py` SMTP-sender (STARTTLS 587).

**Tech Stack:** Python 3.11, poplib (POP3), FastAPI router, SQLite (bestaande `shared/database.py`),
OpenRouter/Claude LLM-client (bestaande `domains/chat`), bestaande SMTP-sender.

**Veiligheidsmodel:** VARIANT A — co-pilot. ImpactOS verstuurt NOOIT zelf. Alles ligt klaar in
het Actiecentrum; Vincent klikt één keer. Dit is een harde eis, geen optie.

**Scope (YAGNI):** géén IMAP, géén autonome verzending, géén multi-mailbox-fusie, géén
threading/geschiedenis-dedup buiten UIDL. Eén helpdesk-adres nu; config-driven zodat een
tweede adres later 1 regel is.

---

## Voorbereiding — config & credentials

De SMTP-sender in `shared/email_service.py` leest `SMTP_HOST/PORT/USER/PASSWORD` uit
`shared/config.py` (defaults: gmail:587). Voor skillkaart/bijeen moet je ZXCS-credentials
zelf in `D:/apps/impactos/.env` zetten (Hermes mag geen .env schrijven in deze omgeving).

Voeg aan `D:/apps/impactos/.env` toe (voorbeeld skillkaart):

```
SMTP_HOST=mail.skillkaart.nl
SMTP_PORT=587
SMTP_USER=hello@skillkaart.nl
SMTP_PASSWORD=<jouw-wachtwoord>
MAIL_HELPDESK_ADDRESS=hello@skillkaart.nl
MAIL_HELPDESK_POP_HOST=mail.skillkaart.nl
MAIL_HELPDESK_POP_PORT=110
MAIL_HELPDESK_POP_USER=hello@skillkaart.nl
MAIL_HELPDESK_POP_PASSWORD=<jouw-wachtwoord>
MAIL_HELPDESK_POLL_MINUTES=30
MAIL_HELPDESK_ENABLED=1
```

Opmerking: POP3 poort 110 (geen SSL) werkt voor dit domein; 995 (SSL) timed out bij test.
Gebruik 110. SMTP 587 STARTTLS werkt.

---

## Task 1: DB-schema — `mail_inbox` + `mail_reply` tabellen

**Objective:** Permanente opslag van geziene mails en concept-antwoorden, dedupe via UIDL.

**Files:**
- Modify: `backend/shared/database.py` (na de iris-tabellen, ~regel 740)

**Step 1:** Voeg onderaan de schema-migratie toe (in dezelfde `CREATE TABLE IF NOT EXISTS`-
stijl als de rest van `database.py`):

```python
# Mail helpdesk (review-gate): inkomende supportmail → concept-antwoord → Actiecentrum
def _ensure_mail_tables(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS mail_inbox (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            uidl        TEXT UNIQUE NOT NULL,        -- POP3 UIDL, dedupe-sleutel
            from_addr   TEXT NOT NULL,
            from_name   TEXT,
            subject     TEXT,
            body_text   TEXT,
            received_at TEXT,
            classified  TEXT DEFAULT 'unknown',      -- question|invoice|spam|newsletter|other
            created_at  TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS mail_reply (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            inbox_id      INTEGER NOT NULL REFERENCES mail_inbox(id),
            to_addr       TEXT NOT NULL,
            subject       TEXT NOT NULL,
            draft_body    TEXT NOT NULL,             -- LLM-concept (markdown/NL)
            status        TEXT DEFAULT 'pending_review', -- pending_review|sent|edited|rejected
            edited_body   TEXT,                      -- als Vincent bewerkt
            created_at    TEXT DEFAULT (datetime('now')),
            sent_at       TEXT
        )
    """)
```

**Step 2:** Roep `_ensure_mail_tables(conn)` aan vanuit de bestaande `init_db()`/
migratie-functie in `database.py` (zoek de plek waar `iris_predictions` e.a. worden
aangemaakt en voeg de call toe).

**Step 3:** Verifieer dat de tabellen bestaan:

Run: `cd D:/apps/impactos/backend && python3 -c "from shared.database import get_conn; c=get_conn(); print([r['name'] for r in c.execute(\"SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'mail%'\")])"`

Expected: `['mail_inbox', 'mail_reply']`

**Step 4:** Commit.

---

## Task 2: POP3-poller — `domains/mail/inbox.py`

**Objective:** Haal nieuwe mails op, dedupe via UIDL, sla alleen ongeziene op.

**Files:**
- Create: `backend/domains/mail/__init__.py` (lege file)
- Create: `backend/domains/mail/inbox.py`
- Test: `backend/tests/test_mail_inbox.py`

**Step 1: Write failing test**

```python
# backend/tests/test_mail_inbox.py
import poplib, email
from email.header import decode_header
from domains.mail import inbox as inbox_mod

def _dm(s):
    if not s: return ''
    out=[]
    for p,enc in decode_header(s):
        out.append(p.decode(enc or 'utf-8', errors='replace') if isinstance(p,bytes) else p)
    return ''.join(out)

def test_fetch_new_marks_seen(monkeypatch, tmp_path):
    # Fake POP3 server die 1 mail teruggeeft
    class FakePop:
        def __init__(self,*a,**k): pass
        def getwelcome(self): return b'+OK fake'
        def user(self,u): pass
        def pass_(self,p): pass
        def list(self): return (b'+OK 1', [b'1 1'], 10)
        def uidl(self): return (b'+OK', [b'1 ABCUID'], 10)
        def retr(self,n):
            raw = (b'From: Klant <klant@example.com>\r\n'
                   b'Subject: Hoe reset ik mijn wachtwoord?\r\n\r\n'
                   b'Ik kom niet meer in mijn account.')
            return (b'+OK', raw.split(b'\n'), len(raw))
        def quit(self): pass
    monkeypatch.setattr(poplib, 'POP3', FakePop)
    saved = inbox_mod.fetch_new(
        host='h', port=110, user='u', pw='p',
        db_path=str(tmp_path/'impactos.db'))
    assert len(saved) == 1
    assert saved[0]['subject'] == 'Hoe reset ik mijn wachtwoord?'
    # tweede keer: geen dupes
    saved2 = inbox_mod.fetch_new(
        host='h', port=110, user='u', pw='p',
        db_path=str(tmp_path/'impactos.db'))
    assert saved2 == []
```

**Step 2: Run to verify failure** — `pytest backend/tests/test_mail_inbox.py -v`
Expected: ERROR/FAIL (module bestaat niet).

**Step 3: Write minimal implementation** `backend/domains/mail/inbox.py`:

```python
import poplib, email
from email.header import decode_header
from ..shared.database import get_conn

SPAM_SENDERS = (
    'mail.vapidkeys.com',
    'spamrelay-pmgmaster.zxcs.nl',
)

def _dm(s):
    if not s: return ''
    out=[]
    for p,enc in decode_header(s):
        out.append(p.decode(enc or 'utf-8', errors='replace') if isinstance(p,bytes) else p)
    return ''.join(out)

def _body(msg):
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type()=='text/plain' and 'attachment' not in str(part.get('Content-Disposition')):
                return (part.get_payload(decode=True) or b'').decode(
                    part.get_content_charset() or 'utf-8', errors='replace')
    return (msg.get_payload(decode=True) or b'').decode(
        msg.get_content_charset() or 'utf-8', errors='replace')

def _is_spam(from_addr: str) -> bool:
    fa = (from_addr or '').lower()
    return any(s in fa for s in SPAM_SENDERS)

def fetch_new(host, port, user, pw, db_path=None):
    """Haal ongeziene mails (dedupe via UIDL). Geef lijst opgeslagen rijen terug."""
    srv = poplib.POP3(host, int(port), timeout=15)
    try:
        srv.user(user); srv.pass_(pw)
        _, items, _ = srv.list()
        _, uidl_lines, _ = srv.uidl()
    except Exception as e:
        srv.quit()
        raise RuntimeError(f"POP3 mislukt: {e}")
    uidl_map = {}
    for line in uidl_lines:
        parts = line.decode().split()
        if len(parts) >= 2:
            uidl_map[parts[0]] = parts[1]
    out = []
    with get_conn() as conn:
        seen = {r['uidl'] for r in conn.execute("SELECT uidl FROM mail_inbox")}
        for num, uidl in uidl_map.items():
            if uidl in seen:
                continue
            _, lines, _ = srv.retr(num)
            msg = email.message_from_bytes(b'\n'.join(lines))
            from_addr = _dm(msg['From'])
            subject = _dm(msg['Subject'])
            if _is_spam(from_addr):
                # markeer als gezien zodat we niet elke poll opnieuw scrapen
                conn.execute("INSERT INTO mail_inbox(uidl,from_addr,subject,body_text,classified) VALUES(?,?,?,?,?)",
                             (uidl, from_addr, subject, '', 'spam'))
                continue
            body = _body(msg)
            conn.execute("INSERT INTO mail_inbox(uidl,from_addr,from_name,subject,body_text,classified) VALUES(?,?,?,?,?,?)",
                         (uidl, from_addr, _dm(msg['From']).split('<')[0].strip(),
                          subject, body, 'unknown'))
            out.append({'uidl': uidl, 'from_addr': from_addr, 'subject': subject})
    srv.quit()
    return out
```

**Step 4: Run test** — `pytest backend/tests/test_mail_inbox.py -v` → PASS

**Step 5:** Commit.

---

## Task 3: Classifier — `domains/mail/classify.py`

**Objective:** Bepaal type (question/newsletter/invoice/other) zodat nous alleen échte vragen
naar een concept-antwoord sturen; spam staat al in inbox als 'spam' en wordt genegeerd.

**Files:**
- Create: `backend/domains/mail/classify.py`
- Test: voeg toe aan `backend/tests/test_mail_inbox.py`

**Step 1: test**

```python
from domains.mail import classify
def test_classify_question():
    assert classify.classify('Hoe reset ik mijn wachtwoord?', 'Ik kom niet meer in mijn account.') == 'question'
def test_classify_newsletter():
    assert classify.classify('Five Ways To Get Started With Web Push', 'Click here to unsubscribe') == 'newsletter'
```

**Step 2: implementatie**

```python
import re
NEWSLETTER_HINTS = ('unsubscribe', 'newsletter', 'web push', 'built for developers', 'view in browser')
QUESTION_HINTS = ('?', 'hoe', 'wat', 'kan', 'kunt', 'help', 'vraag', 'probleem', 'werkt niet', 'niet meer')

def classify(subject: str, body: str) -> str:
    s = (subject or '').lower()
    b = (body or '').lower()
    if any(h in s or h in b for h in NEWSLETTER_HINTS):
        return 'newsletter'
    if 'factuur' in s or 'invoice' in s or re.search(r'batig|€\s?\d+', b):
        return 'invoice'
    if any(h in s or h in b for h in QUESTION_HINTS):
        return 'question'
    return 'other'
```

**Step 3:** Run tests → PASS. Commit.

---

## Task 4: Draft generator — `domains/mail/drafter.py`

**Objective:** LLM schrijft een NL-helpdesk-concept op basis van merkcontext + kennisbank.
Hergebruik de bestaande chat-client (`domains/chat`).

**Files:**
- Create: `backend/domains/mail/drafter.py`
- Test: `backend/tests/test_mail_drafter.py` (mock LLM — drafter mag geen netwerk doen in test)

**Step 1: test (mock)**

```python
from domains.mail import drafter
def test_build_prompt_includes_brand_and_question(monkeypatch):
    captured = {}
    def fake_complete(system, user):
        captured['system'] = system; captured['user'] = user
        return "Beste klant, u kunt uw wachtwoord resetten via…"
    monkeypatch.setattr(drafter, 'llm_complete', fake_complete)
    out = drafter.draft_reply(
        from_name='Jan', subject='Wachtwoord reset',
        body='Ik kom niet meer in mijn account.',
        brand_context='Wij zijn Skillkaart, warm en helder.',
        knowledge='FAQ: reset via /wachtwoord-vergeten')
    assert 'Skillkaart' in captured['system']
    assert 'Wachtwoord reset' in captured['user']
    assert 'reset' in out.lower()
```

**Step 2: implementatie** — `drafter.py`:

```python
from ..chat import claude  # bestaande LLM-client (OpenRouter/Claude)

SYSTEM = (
    "Je bent de eerste-lijn helpdesk voor {brand}. "
    "Schrijf een helder, warm NL-antwoord in Vincents stem (kort, concreet, "
    "geen robot-taal). Geef waar mogelijk een directe stap of link. "
    "Verzin geen garanties of prijzen die niet in de kennis staan. "
    "Max 150 woorden. Eindig met groet."
)

def llm_complete(system, user):
    # wrap bestaande chat-client; pas aan op de echte signature in domains/chat/claude.py
    return claude.complete(system=system, user=user)

def draft_reply(from_name, subject, body, brand_context, knowledge):
    system = SYSTEM.format(brand=brand_context)
    if knowledge:
        system += f"\n\nBeschikbare kennis:\n{knowledge}"
    user = f"Van: {from_name}\nOnderwerp: {subject}\n\n{body}"
    return llm_complete(system, user)
```

Let op: controleer de exacte signature van `domains/chat/claude.py` en pas `llm_complete`
daarop aan (NamedProblem: de bestaande client heet misschien `ask` i.p.v. `complete`).

**Step 3:** Run test → PASS. Commit.

---

## Task 5: Pipeline-orkestrator — `domains/mail/service.py`

**Objective:** Koppel poller → classifier → drafter → DB rij `mail_reply` (pending_review),
alleen voor `question`. Andere types krijgen een `classified`-label en niets verder.

**Files:**
- Create: `backend/domains/mail/service.py`
- Test: `backend/tests/test_mail_service.py` (monkeypatch fetch_new + drafter)

**Step 1: test**

```python
from domains.mail import service
def test_pipeline_creates_reply_for_question(monkeypatch, tmp_path):
    monkeypatch.setattr(service.inbox, 'fetch_new', lambda **k: [
        {'uidl':'X1','from_addr':'jan@x.nl','subject':'Hoe reset ik?'}])
    # body ophalen via stub: zet mail_inbox direct
    from ..shared.database import get_conn
    with get_conn() as c:
        c.execute("INSERT INTO mail_inbox(uidl,from_addr,subject,body_text,classified) VALUES(?,?,?,?,?)",
                  ('X1','jan@x.nl','Hoe reset ik?','Ik zit buiten.', 'question'))
    monkeypatch.setattr(service.drafter, 'draft_reply',
        lambda **k: 'Beste Jan, reset via /wachtwoord-vergeten.')
    n = service.run_mail_helpdesk(
        host='h',port=110,user='u',pw='p',
        brand_context='Skillkaart', knowledge='', db_path=str(tmp_path/'a.db'))
    assert n == 1
    with get_conn() as c:
        rows = list(c.execute("SELECT status,draft_body FROM mail_reply"))
    assert rows and rows[0]['status'] == 'pending_review'
```

**Step 2: implementatie**

```python
from ..shared.database import get_conn
from . import inbox, classify, drafter

def run_mail_helpdesk(host, port, user, pw, brand_context, knowledge, db_path=None):
    fetched = inbox.fetch_new(host, port, user, pw, db_path=db_path)
    if not fetched:
        return 0
    created = 0
    with get_conn() as conn:
        for m in fetched:
            row = conn.execute(
                "SELECT id,body_text,from_name,subject FROM mail_inbox WHERE uidl=?",
                (m['uidl'],)).fetchone()
            if not row:
                continue
            kind = classify.classify(row['subject'], row['body_text'])
            conn.execute("UPDATE mail_inbox SET classified=? WHERE id=?", (kind, row['id']))
            if kind != 'question':
                continue  # spam/newsletter/invoice/other → geen concept, wacht in inbox
            draft = drafter.draft_reply(
                from_name=row['from_name'] or m['from_addr'],
                subject=row['subject'], body=row['body_text'],
                brand_context=brand_context, knowledge=knowledge)
            conn.execute(
                "INSERT INTO mail_reply(inbox_id,to_addr,subject,draft_body) VALUES(?,?,?,?)",
                (row['id'], m['from_addr'], 'Re: ' + row['subject'], draft))
            created += 1
    return created
```

**Step 3:** Run test → PASS. Commit.

---

## Task 6: Actiecentrum-integratie — toon `mail_reply` als review-item

**Objective:** Mail-concepten verschijnen in Vincents inbox met Verstuur/Bewerk/Afwijzen.

**Files:**
- Modify: `backend/domains/action_center/service.py` (in `build_inbox()`, na content_review-blok)
- Modify: `backend/domains/action_center/router.py` (endpoint voor de 3 acties)

**Step 1:** Voeg in `build_inbox()` toe (na het content_review-blok, vóór `return`):

```python
        # ── 3b. Mail helpdesk: concept-antwoorden wachten op goedkeuring ──
        for r in conn.execute(
            "SELECT r.id, r.to_addr, r.subject, r.draft_body, i.from_name "
            "FROM mail_reply r JOIN mail_inbox i ON i.id=r.inbox_id "
            "WHERE r.status='pending_review' ORDER BY r.created_at DESC"
        ):
            items.append({
                "kind": "mail_reply",
                "dismiss_kind": "mail",
                "id": r["id"],
                "title": f"Mail van {r['from_name'] or r['to_addr']}: {r['subject']}",
                "project": "Helpdesk",
                "created_at": r["created_at"],
                "summary": (r["draft_body"][:240] + ("…" if len(r["draft_body"])>240 else "")),
                "actions": [
                    {"label": "Verstuur", "type": "mail_send", "id": r["id"]},
                    {"label": "Bewerk", "type": "mail_edit", "id": r["id"]},
                    {"label": "Afwijzen", "type": "mail_reject", "id": r["id"], "danger": True},
                ],
            })
```

**Step 2:** In `action_center/router.py` voeg endpoints toe:

```python
@router.post("/api/action-center/mail/{reply_id}/send")
def mail_send(reply_id: int):
    from ...shared.email_service import send_report
    from ...shared.database import get_conn
    with get_conn() as conn:
        r = conn.execute("SELECT to_addr,subject,draft_body FROM mail_reply WHERE id=?",(reply_id,)).fetchone()
        if not r: raise HTTPException(404)
        ok = send_report(subject=r["subject"], body=r["draft_body"], to=r["to_addr"])
        if ok:
            conn.execute("UPDATE mail_reply SET status='sent', sent_at=datetime('now') WHERE id=?",(reply_id,))
            return {"ok": True}
        raise HTTPException(502, "Versturen mislukt")

@router.post("/api/action-center/mail/{reply_id}/reject")
def mail_reject(reply_id: int):
    with get_conn() as conn:
        conn.execute("UPDATE mail_reply SET status='rejected' WHERE id=?",(reply_id,))
    return {"ok": True}

@router.post("/api/action-center/mail/{reply_id}/edit")
def mail_edit(reply_id: int, body: dict):
    with get_conn() as conn:
        conn.execute("UPDATE mail_reply SET draft_body=?, status='edited' WHERE id=?",
                     (body.get("text",""), reply_id))
    return {"ok": True}
```

**Step 3:** Verifieer (handmatig na deploy): een testmail sturen naar het helpdesk-adres,
poll laten lopen, `build_inbox()` bevragen en controleren dat het item verschijnt.

**Step 4:** Commit.

---

## Task 7: Scheduler-taak — poll elke N minuten

**Objective:** Koppel `run_mail_helpdesk` aan de bestaande APScheduler in `backend/scheduler.py`.

**Files:**
- Modify: `backend/scheduler.py` (bij de andere `_scheduler.add_job`-blokken, ~regel 300)

**Step 1:** Voeg import + job toe:

```python
from .domains.mail.service import run_mail_helpdesk
from ..shared.config import (
    MAIL_HELPDESK_ENABLED, MAIL_HELPDESK_POLL_MINUTES,
    MAIL_HELPDESK_POP_HOST, MAIL_HELPDESK_POP_PORT,
    MAIL_HELPDESK_POP_USER, MAIL_HELPDESK_POP_PASSWORD,
    MAIL_HELPDESK_ADDRESS, BRAND_CONTEXT, IRIS_KNOWLEDGE,
)
if str(MAIL_HELPDESK_ENABLED) == "1":
    _scheduler.add_job(
        lambda: run_mail_helpdesk(
            host=MAIL_HELPDESK_POP_HOST, port=MAIL_HELPDESK_POP_PORT,
            user=MAIL_HELPDESK_POP_USER, pw=MAIL_HELPDESK_POP_PASSWORD,
            brand_context=BRAND_CONTEXT, knowledge=IRIS_KNOWLEDGE),
        IntervalTrigger(minutes=int(MAIL_HELPDESK_POLL_MINUTES or 30)),
        id="mail_helpdesk", replace_existing=True,
        misfire_grace_time=600, coalesce=True,
    )
```

**Step 2:** Voeg de nieuwe config-vars toe in `backend/shared/config.py` (na `REPORT_EMAIL_TO`):

```python
MAIL_HELPDESK_ENABLED: str = os.getenv("MAIL_HELPDESK_ENABLED", "0")
MAIL_HELPDESK_POLL_MINUTES: str = os.getenv("MAIL_HELPDESK_POLL_MINUTES", "30")
MAIL_HELPDESK_ADDRESS: str = os.getenv("MAIL_HELPDESK_ADDRESS", "")
MAIL_HELPDESK_POP_HOST: str = os.getenv("MAIL_HELPDESK_POP_HOST", "")
MAIL_HELPDESK_POP_PORT: str = os.getenv("MAIL_HELPDESK_POP_PORT", "110")
MAIL_HELPDESK_POP_USER: str = os.getenv("MAIL_HELPDESK_POP_USER", "")
MAIL_HELPDESK_POP_PASSWORD: str = os.getenv("MAIL_HELPDESK_POP_PASSWORD", "")
```

Controleer dat `BRAND_CONTEXT` en `IRIS_KNOWLEDGE` al in `config.py` bestaan; zo niet,
laad ze uit je vault (zie Task 8).

**Step 3:** Start ImpactOS-lokaal, wacht één poll-interval, check logs op "mail_helpdesk".
Controleer DB: `SELECT count(*) FROM mail_reply`.

**Step 4:** Commit.

---

## Task 8: Merkcontext & kennisbank (SCHRIJF-DNA) laden

**Objective:** De drafter krijgt Vincents stem + FAQ-kennis, anders klinkt het als een robot.

**Files:**
- Modify: `backend/shared/config.py` (BRAND_CONTEXT / IRIS_KNOWLEDGE uit vault of .env)

**Step 1:** Bepaal waar je SCHRIJF-DNA + FAQ staan (vault: `D:/APPS/Hermes Brein/.../SCHRIJF-DNA-Vincent.md`
en eventuele FAQ-markdown). Zet die tekst in `D:/apps/impactos/.env`:

```
BRAND_CONTEXT="Skillkaart — [jouw merkzin in 1-2 zinnen]"
IRIS_KNOWLEDGE="FAQ: wachtwoord reset → /wachtwoord-vergeten. ..."
```

of laad ze in `config.py` via `os.getenv("BRAND_CONTEXT","")` / `os.getenv("IRIS_KNOWLEDGE","")`.

**Step 2:** Test dat `config.BRAND_CONTEXT` niet leeg is bij opstart (assert in service).

**Step 3:** Commit.

---

## Task 9: End-to-end smoke test

**Objective:** Bewijs dat een echte vraagmail → concept → Actiecentrum → (na klik) verzending.

**Files:** geen nieuwe; draai tegen de lokale ImpactOS.

**Step 1:** Stel `MAIL_HELPDESK_ENABLED=1` in `.env`, start backend.

**Step 2:** Stuur een testmail vanaf een ander adres naar het helpdesk-adres
(bv. via `mail_check.py`-achtige SMTP of je eigen mailclient).

**Step 3:** Wacht ≤ poll-interval. Controleer:

```bash
cd D:/apps/impactos/backend && python3 -c "
from shared.database import get_conn
c=get_conn()
print('inbox:', list(c.execute('SELECT from_addr,classified FROM mail_inbox')))
print('replies:', list(c.execute('SELECT to_addr,status,substr(draft_body,1,60) FROM mail_reply')))
"
```

Expected: 1 rij in `mail_reply` met `status='pending_review'`.

**Step 4:** Roep `POST /api/action-center/mail/{id}/send` aan (curl of UI). Controleer dat de
testmail daadwerkelijk aankomt bij de afzender en dat `status='sent'`.

**Step 5:** Commit (alleen als groen).

---

## Acceptatiecriteria
- [ ] Geen enkele mail vertrekt zonder expliciete `mail_send`-call (variante A harde eis).
- [ ] Spam (Vapid/ZXCS-spamreport) wordt nooit tot een concept verwerkt.
- [ ] Eén echte vraag → precies één `mail_reply` (pending_review), geen dupes over polls.
- [ ] Verstuurde mail komt aan bij de originele afzender, van het helpdesk-adres.
- [ ] Config-driven: tweede adres = 1 blok .env-vars + 1 extra scheduler-job.

## Verificatie-summary (commands)
- Tabellen: `python3 -c "from shared.database import get_conn; ..."` → mail_inbox, mail_reply
- Unit: `pytest backend/tests/test_mail_inbox.py backend/tests/test_mail_drafter.py backend/tests/test_mail_service.py -v`
- E2E: zie Task 9.
