"""Testconfiguratie: alle tests draaien tegen een wegwerp-SQLite-database.

De env-var AGENTOS_DB_PATH moet gezet zijn VOORDAT backend.shared.config
geïmporteerd wordt — daarom staat dit bovenaan conftest en importeren de
tests backend-modules pas binnen fixtures/functies.
"""
import os
import tempfile
import uuid

_TMP_DB = os.path.join(tempfile.gettempdir(), f"agentos-test-{uuid.uuid4().hex[:8]}.db")
os.environ["AGENTOS_DB_PATH"] = _TMP_DB

# Login-gate uit in tests: de auth-middleware leest AGENTOS_PASSWORD per request
# uit os.environ, en een gevulde .env zou anders elke API-test op 401 laten
# stranden. Leeg = gate uit (zelfde gedrag als lokale dev zonder wachtwoord).
os.environ["AGENTOS_PASSWORD"] = ""

import pytest  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def database():
    """Init het schema één keer per testsessie; ruim het db-bestand op."""
    from backend.shared.database import init_db
    init_db()
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
                  "goal_tasks", "content_jobs", "tasks", "vacancies", "leads"):
            c.execute(f"DELETE FROM {t}")
