"""Tests voor de AEO-autonomie-uitbreiding.

Deze tests draaien tegen een wegwerp-DB (conftest zet AGENTOS_DB_PATH).
We mocken de agent-runner zodat we de pijplijn-logica testen zonder echte
LLM-backend, en testen de deterministic fallback los daarvan.
"""
import os
import pytest

# Zet autonomie-vlaggen expliciet voor de tests (voordat config geïmporteerd
# wordt). config.py leest ze één keer bij import, dus moet dit vóór de import.
os.environ.setdefault("AEO_AUTO_ATTACK", "1")
os.environ.setdefault("AEO_AUTO_MIN_SCORE", "75")
os.environ.setdefault("AEO_AUTO_MAX_PER_SCAN", "3")
os.environ.setdefault("HERMES_LOCAL_FALLBACK", "1")


@pytest.fixture()
def radar():
    from backend.domains.radar.service import RadarService
    return RadarService()


def _make_signal(radar, title, score, status="new", project="bijeen"):
    from backend.shared.database import get_conn
    import json, uuid
    sig_id = str(uuid.uuid4())
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO radar_signals "
            "(id, watch_id, project, keyword, title, url, source, snippet, "
            "published_days_ago, signal_score, ai_hook, ai_angle, ai_titles, "
            "ai_match_score, status, obsidian_path, scanned_at, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (sig_id, str(uuid.uuid4()), project, "vrijwilligers werven", title,
             "https://reddit.com/" + sig_id, "reddit", "snippet", 2, score,
             "hook", "angle", json.dumps(["Titel A", "Titel B"]), 80,
             status, "", "2026-07-08T00:00:00Z", "2026-07-08T00:00:00Z",
             "2026-07-08T00:00:00Z"),
        )
    return sig_id


def test_auto_aeo_selects_top_signals(radar):
    """Alleen 'new' signalen boven de drempel worden aangevallen, max N."""
    _make_signal(radar, "laag", 40)          # onder drempel
    _make_signal(radar, "middel", 78)        # boven drempel
    _make_signal(radar, "hoog1", 95)         # boven drempel
    _make_signal(radar, "hoog2", 90)         # boven drempel
    _make_signal(radar, "al-converted", 99, status="converted")

    # aeo_attack maakt taken aan — tel hoeveel er bijkomen.
    from backend.shared.database import get_conn
    with get_conn() as c:
        before = len(c.execute("SELECT id FROM tasks").fetchall())
    attacked = radar._auto_aeo_top_signals()
    with get_conn() as c:
        after = len(c.execute("SELECT id FROM tasks").fetchall())

    # 3 aangevallen (middel 78, hoog1 95, hoog2 90) × 3 kanalen = 9 taken.
    # "laag" (40) en "al-converted" (99) worden overgeslagen.
    assert "middel" in attacked
    assert "hoog1" in attacked
    assert "hoog2" in attacked
    assert "laag" not in attacked
    assert "al-converted" not in attacked
    assert after - before == 9  # 3 signalen × 3 kanalen


def test_auto_aeo_is_idempotent(radar):
    """Een tweede run valt terug op reeds-'converted' signalen."""
    _make_signal(radar, "uniek", 88)
    radar._auto_aeo_top_signals()
    # Na de eerste run is "uniek" → 'converted'. Tweede run mag niets nieuws doen.
    from backend.shared.database import get_conn
    with get_conn() as c:
        before = len(c.execute("SELECT id FROM tasks").fetchall())
    radar._auto_aeo_top_signals()
    with get_conn() as c:
        after = len(c.execute("SELECT id FROM tasks").fetchall())
    assert after == before


def test_conveyor_quality_assessment():
    from backend.domains.pipeline.conveyor import _assess_output

    # Te kort → niet ok.
    assert not _assess_output("kort", {"workspace_path": "x/listicle.md"})["ok"]
    # Goed met koppen en voldoende lengte → ok.
    good = (
        "# Titel van het artikel over vrijwilligers werven\n\n"
        "## Sectie 1: waarom dit werkt\n"
        "Een lange genoeg tekst met uitleg en context zodat de quality-check "
        "de lengte-eis van tweehonderd tekens ruimschoots haalt en we kunnen "
        "concluderen dat dit een geldig SEO-concept is met voldoende body.\n\n"
        "## Sectie 2: hoe je het toepast\n"
        "Nog meer uitleg met praktische voorbeelden en een duidelijkeStructuur "
        "die laat zien dat de agent zelfstandig bruikbare content produceert."
    )
    assert _assess_output(good, {"workspace_path": "x/listicle.md"})["ok"]
    # Listicle zonder koppen → niet ok.
    flat = ("dit is een hele lange muur van tekst zonder enige koppen of "
            "structuur die ook maar enigszins zou kunnen dienen als SEO-concept "
            "en dat is precies waarom deze test moet falen want het is waardeloos "
            "als concept en de pijplijn moet het weigeren als output.")
    assert not _assess_output(flat, {"workspace_path": "x/listicle.md"})["ok"]


def test_local_fallback_produces_concept():
    """Zonder backend levert de fallback een gemarkeerd concept (geen crash)."""
    from backend.shared.agent_runner import _local_template_fill

    async def _run():
        events = []
        async for ev in _local_template_fill(
            [{"role": "user", "content": "# Mijn testtitel\n\nBeschrijving hier"}],
            "systeem",
        ):
            events.append(ev)
        return events

    import asyncio
    events = asyncio.run(_run())
    texts = [e.get("text", "") for e in events if e.get("type") == "text"]
    joined = "\n".join(texts)
    assert "Mijn testtitel" in joined
    assert "CONCEPT" in joined
    assert "lokale fallback" in joined
