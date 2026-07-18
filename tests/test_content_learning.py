"""Content-leerlus: cohort-indeling, GSC-koppeling en les-destillatie."""
import uuid
from datetime import datetime, timedelta, timezone

import pytest


@pytest.fixture(autouse=True)
def _clean(conn, clean_tables):
    """Eigen opruiming bovenop clean_tables: sites en gsc_history staan niet
    in de globale lijst (andere testmodules seeden die zelf per test)."""
    yield
    conn.execute("DELETE FROM gsc_history")
    conn.execute("DELETE FROM sites")
    conn.commit()


def _iso(days_ago):
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()


def _seed_site(conn, site_id="site-1"):
    conn.execute(
        "INSERT OR IGNORE INTO sites (id, name, created_at) VALUES (?, ?, ?)",
        (site_id, f"Site {site_id}", _iso(100)),
    )


def _seed_article(conn, slug, title, *, site_id="site-1", words=500, days_ago=40):
    _seed_site(conn, site_id)
    conn.execute(
        "INSERT INTO content_jobs (id, site_id, title, status, blog_html, slug, "
        "created_at, reviewed_at) VALUES (?, ?, ?, 'published', ?, ?, ?, ?)",
        (str(uuid.uuid4()), site_id, title, "<p>" + ("woord " * words) + "</p>",
         slug, _iso(days_ago + 1), _iso(days_ago)),
    )


def _seed_snapshot(conn, slug, clicks, *, site_id="site-1", date="2026-07-15"):
    conn.execute(
        "INSERT OR REPLACE INTO gsc_history (id, site_id, scope, page_url, date, "
        "clicks, created_at) VALUES (?, ?, 'page', ?, ?, ?, ?)",
        (str(uuid.uuid4()), site_id, f"https://example.com/blog/{slug}", date,
         clicks, _iso(0)),
    )


# ── Cohort-indeling ────────────────────────────────────────────────────────

def test_article_dimensions_buckets():
    from backend.domains.publish.content_learning import article_dimensions

    d = article_dimensions("Wat is thuiszorg?", "<p>" + "w " * 100 + "</p>")
    assert d == {"lengte": "compact", "titelvorm": "regulier", "titelintentie": "vraag"}

    d = article_dimensions("7 tips voor mantelzorg", "<p>" + "w " * 1500 + "</p>")
    assert d == {"lengte": "uitgebreid", "titelvorm": "lijst", "titelintentie": "statement"}

    # Vraagwoord zonder vraagteken telt ook als vraag-titel (AEO-stijl)
    assert article_dimensions("Hoe werkt een PGB", "")["titelintentie"] == "vraag"


def test_slug_match_is_padgrens_strak(conn):
    from backend.domains.publish.content_learning import _page_clicks

    _seed_site(conn)
    _seed_snapshot(conn, "ai-zorg", 50)
    conn.commit()
    # 'ai' mag niet op de 'ai-zorg'-pagina matchen → echte nul (site heeft historie)
    assert _page_clicks(conn, "site-1", "ai") == 0.0
    assert _page_clicks(conn, "site-1", "ai-zorg") == 50.0


def test_site_zonder_page_historie_is_niet_meetbaar(conn):
    from backend.domains.publish.content_learning import _page_clicks, cohort_stats

    _seed_article(conn, "artikel-x", "Titel X")
    conn.commit()
    assert _page_clicks(conn, "site-1", "artikel-x") is None
    stats = cohort_stats()
    assert all(s["n"] == 0 for per_value in stats.values() for s in per_value.values())


def test_verse_artikelen_rijpen_eerst(conn):
    from backend.domains.publish.content_learning import cohort_stats

    _seed_article(conn, "rijp", "Rijp artikel", days_ago=40)
    _seed_article(conn, "vers", "Vers artikel", days_ago=5)
    _seed_snapshot(conn, "rijp", 10)
    _seed_snapshot(conn, "vers", 10)
    conn.commit()
    stats = cohort_stats()
    assert stats["titelintentie"]["statement"]["n"] == 1


# ── Evaluatie ──────────────────────────────────────────────────────────────

def _seed_intentie_cohorten(conn, *, winner_clicks=10, loser_clicks=0, n=5):
    """n vraag-titels vs n statement-titels; overige dimensies gelijk (compact,
    regulier) zodat alleen 'titelintentie' genoeg steekproef aan beide kanten heeft."""
    for i in range(n):
        _seed_article(conn, f"vraag-{i}", f"Wat is onderwerp nummer {'x' * (i + 1)}?")
        _seed_snapshot(conn, f"vraag-{i}", winner_clicks)
        _seed_article(conn, f"stmt-{i}", f"Onderwerp nummer {'x' * (i + 1)} uitgelegd")
        _seed_snapshot(conn, f"stmt-{i}", loser_clicks)
    conn.commit()


def test_eval_maakt_les_en_voorspelling_bij_duidelijke_kloof(conn):
    from backend.shared import learning
    from backend.domains.publish.content_learning import run_content_learning_eval

    _seed_intentie_cohorten(conn)
    out = run_content_learning_eval()
    assert any("titelintentie 'vraag'" in l for l in out["lessons"])
    lessons = learning.active_lessons("content")
    assert any("titelintentie 'vraag'" in l["lesson"] for l in lessons)
    preds = learning.predictions("content", status="open")
    assert any(p["context"] == "titelintentie:vraag>statement" for p in preds)


def test_eval_leert_niets_onder_steekproefdrempel(conn):
    from backend.shared import learning
    from backend.domains.publish.content_learning import run_content_learning_eval

    _seed_intentie_cohorten(conn, n=3)  # 3 < MIN_ARTICLES_PER_VALUE
    assert run_content_learning_eval()["lessons"] == []
    assert learning.active_lessons("content") == []


def test_eval_leert_niets_bij_kleine_kloof_of_lage_winnaar(conn):
    from backend.domains.publish.content_learning import run_content_learning_eval

    # Ratio (11)/(10) < 1.5 → geen les
    _seed_intentie_cohorten(conn, winner_clicks=10, loser_clicks=9)
    assert run_content_learning_eval()["lessons"] == []


def test_eval_lage_winnaar_mediaan_is_ruis(conn):
    from backend.domains.publish.content_learning import run_content_learning_eval

    # Ratio (3)/(1) = 3, maar winnaar-mediaan 2 < MIN_WINNER_MEDIAN → ruis
    _seed_intentie_cohorten(conn, winner_clicks=2, loser_clicks=0)
    assert run_content_learning_eval()["lessons"] == []


def test_resolver_geeft_actuele_ratio(conn):
    from backend.domains.publish.content_learning import _resolver

    _seed_intentie_cohorten(conn, winner_clicks=10, loser_clicks=0)
    assert _resolver("median_clicks_ratio", "titelintentie:vraag>statement") == 11.0
    assert _resolver("median_clicks_ratio", "onzin") is None
    assert _resolver("iets_anders", "titelintentie:vraag>statement") is None
    # Onder de steekproef → None (niet eerlijk meetbaar)
    conn.execute("DELETE FROM content_jobs")
    conn.commit()
    assert _resolver("median_clicks_ratio", "titelintentie:vraag>statement") is None


def test_herhaalde_eval_dedupet_les_en_voorspelling(conn):
    from backend.shared import learning
    from backend.domains.publish.content_learning import run_content_learning_eval

    _seed_intentie_cohorten(conn)
    run_content_learning_eval()
    run_content_learning_eval()
    lessons = [l for l in learning.active_lessons("content")
               if "titelintentie" in l["lesson"]]
    assert len(lessons) == 1
    assert lessons[0]["times_confirmed"] == 2
    preds = [p for p in learning.predictions("content", status="open")
             if p["context"].startswith("titelintentie:")]
    assert len(preds) == 1


def test_schrijfprompt_bevat_gemeten_les(clean_tables):
    from backend.shared import learning
    from backend.domains.publish.content_pipeline import _learned_writing_lessons

    assert _learned_writing_lessons() == ""
    learning.upsert_lesson("content", "Artikelen met titelintentie 'vraag' halen "
                                      "meer GSC-clicks dan 'statement'.")
    block = _learned_writing_lessons()
    assert "titelintentie 'vraag'" in block
