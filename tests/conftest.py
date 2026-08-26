"""Testconfiguratie: alle tests draaien tegen een wegwerp-SQLite-database.

De env-var IMPACTOS_DB_PATH moet gezet zijn VOORDAT backend.shared.config
geïmporteerd wordt — daarom staat dit bovenaan conftest en importeren de
tests backend-modules pas binnen fixtures/functies.
"""
import os
import tempfile
import uuid

_TMP_DB = os.path.join(tempfile.gettempdir(), f"impactos-test-{uuid.uuid4().hex[:8]}.db")
os.environ["IMPACTOS_DB_PATH"] = _TMP_DB

# Login-gate uit in tests: de auth-middleware leest IMPACTOS_PASSWORD per request
# uit os.environ, en een gevulde .env zou anders elke API-test op 401 laten
# stranden. Leeg = gate uit (zelfde gedrag als lokale dev zonder wachtwoord).
os.environ["IMPACTOS_PASSWORD"] = ""

# Geen echte mail vanuit tests. email_service.is_configured() / resend_service
# .is_configured() lezen deze vars uit de echte .env — tests die run_morning_
# briefing() of run_daily_digest() aanroepen (test_iris.py e.a.) raken dat pad
# ongemockt. Zolang SMTP_PASSWORD leeg was, verstuurde dat stil niets; zodra
# Resend geconfigureerd werd, stuurde elke testrun ineens écht meerdere e-mails
# naar de productie-inbox (11 aug 2026: 3 testruns = een stortvloed identieke
# "Testsite"-dagbriefingen). Beide kanalen expliciet leeg in tests, ongeacht
# wat er in .env staat.
os.environ["RESEND_API_KEY"] = ""
os.environ["SMTP_PASSWORD"] = ""

import pytest  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def database():
    """Init het schema één keer per testsessie; ruim het db-bestand op."""
    from backend.shared.database import init_db
    init_db()
    # crm/billing hebben een eigen lazy ensure_schema() (zie hun models.py) —
    # zonder dit hier al te draaien crasht clean_tables op "no such table" in
    # een testrun die geen van beide domeinen aanraakt.
    from backend.domains.crm.models import ensure_schema as _ensure_crm_schema
    from backend.domains.billing.models import ensure_schema as _ensure_billing_schema
    from backend.domains.quotes.models import ensure_schema as _ensure_quotes_schema
    from backend.domains.notes.models import ensure_schema as _ensure_notes_schema
    _ensure_crm_schema()
    _ensure_billing_schema()
    _ensure_quotes_schema()
    _ensure_notes_schema()
    yield
    for suffix in ("", "-wal", "-shm"):
        try:
            os.remove(_TMP_DB + suffix)
        except OSError:
            pass


@pytest.fixture()
def conn():
    from backend.shared.database import get_conn
    with get_conn() as c:
        yield c


# Alias zodat nieuwe tests ook `db_conn` kunnen gebruiken (gelijk aan `conn`).
@pytest.fixture()
def db_conn():
    from backend.shared.database import get_conn
    with get_conn() as c:
        yield c


@pytest.fixture()
def clean_tables():
    """Leeg de tabellen die tests vullen, zodat tests elkaar niet besmetten."""
    from backend.shared.database import get_conn
    yield
    with get_conn() as c:
        for t in ("activity_log", "inbox_dismissals", "goals", "goal_phases",
                  "goal_tasks", "content_jobs", "tasks", "vacancies", "leads",
                  "agent_lessons", "agent_predictions",
                  "crm_companies", "crm_contacts", "crm_deals", "crm_activities", "crm_tasks",
                  "billing_receipts", "billing_invoice_drafts", "billing_invoice_lines",
                  "billing_debtor_snapshots", "billing_debtor_rows", "billing_reminders",
                  "quotes", "meeting_notes"):
            c.execute(f"DELETE FROM {t}")
