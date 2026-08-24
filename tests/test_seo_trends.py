"""Trend-brug (Mission Radar → Demand Engine) + IndexNow-verificatie:
de deterministische, LLM-vrije onderdelen.

Wat deze suite sinds 3 aug 2026 vooral bewaakt: **het zoekwoord komt uit het
signaal, niet uit de watchlist.** `_signal_query` gaf `sig["keyword"]` terug —
de regel die Vincent zelf had ingetypt — waardoor alle 38 kansen die de brug
ooit maakte letterlijk een watchlist-regel waren. Omdat de dedupe op exacte
querytekst liep, was elk watchlist-woord na één conversie voor altijd verbruikt:
sinds 27 juli leverde de brug nul kansen bij ongewijzigde aanvoer, zonder dat
iets dat meldde. De oude tests legden dat gedrag vast en gingen er dus vanuit
dat het klopte.
"""
import uuid

import pytest


def _make_site(name="TrendSite", base_url=""):
    from backend.domains.seo import sites as sites_service
    s = sites_service.create_site({"name": name, "base_url": base_url})
    return sites_service.get_site(s["id"])


def _insert_signal(project, keyword, score, status="new", title=None,
                   ai_angle="Unieke invalshoek", ai_hook="Sterke hook",
                   match=80, url=None, watch_id=""):
    """Eén radarsignaal. `title` draagt het onderwerp — dát wordt het zoekwoord.

    `keyword` blijft het gemonitorde watchlist-woord en hoort nadrukkelijk NIET
    in de kans terecht te komen; verschillende defaults maken dat zichtbaar.
    """
    from backend.domains.radar.models import ensure_schema
    from backend.shared.database import get_conn
    ensure_schema()
    sig_id = str(uuid.uuid4())
    now = "2026-07-07T07:00:00+00:00"
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO radar_signals
               (id, watch_id, project, keyword, title, url, source, signal_score,
                ai_hook, ai_angle, ai_match_score, status, scanned_at,
                created_at, updated_at, filter_reason)
               VALUES (?, ?, ?, ?, ?, ?, 'news', ?, ?, ?, ?, ?, ?, ?, ?, '')""",
            (sig_id, watch_id, project, keyword, title if title is not None else keyword,
             url or f"https://bron.nl/blog/{sig_id}",
             score, ai_hook, ai_angle, match, status, now, now, now),
        )
    return sig_id


def _insert_watch(project, wtype, value="merk"):
    from backend.domains.radar.models import ensure_schema
    from backend.shared.database import get_conn
    ensure_schema()
    wid = str(uuid.uuid4())
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO radar_watchlist (id, project, label, type, value, active, "
            "last_scanned_at, created_at) VALUES (?, ?, ?, ?, ?, 1, '', datetime('now'))",
            (wid, project, value, wtype, value),
        )
    return wid


@pytest.fixture()
def trend_site():
    from backend.domains.seo import sites as sites_service
    from backend.shared.database import get_conn
    site = _make_site()
    yield site
    with get_conn() as c:
        c.execute("DELETE FROM opportunities WHERE site_id = ?", (site["id"],))
        c.execute("DELETE FROM radar_signals")
        c.execute("DELETE FROM radar_watchlist WHERE project = ?", (site["name"].lower(),))
    sites_service.delete_site(site["id"])


def test_trend_sync_converts_top_signal(trend_site):
    from backend.domains.seo.trends import sync_trend_opportunities
    from backend.domains.seo.engine import list_opportunities
    from backend.shared.database import get_conn

    sig_id = _insert_signal("TrendSite", "hermes desktop",
                            82, title="Hermes desktop installatie in vijf stappen")
    result = sync_trend_opportunities(trend_site)
    assert result["created"] == 1

    kansen = list_opportunities(site_id=trend_site["id"], status="new")
    assert len(kansen) == 1
    kans = kansen[0]
    # Uit het signaal, niet uit de watchlist.
    assert kans["query"] == "Hermes desktop installatie in vijf stappen"
    assert kans["opportunity_score"] == 82
    assert kans["action"] == "nieuwe-content"
    # De rationale zegt wat het ís. "Trending (Radar-score 82)" stond boven alle
    # 38 kansen die de brug ooit maakte, óók boven zoekwoorden die gewoon uit de
    # watchlist kwamen — een onware bewering over de herkomst.
    assert kans["rationale"].startswith("Radarsignaal")
    assert "bron.nl" in kans["rationale"]

    with get_conn() as c:
        row = c.execute("SELECT status FROM radar_signals WHERE id = ?", (sig_id,)).fetchone()
    assert row["status"] == "targeted"

    # Idempotent: tweede sync maakt geen duplicaat.
    assert sync_trend_opportunities(trend_site)["created"] == 0


def test_trend_sync_skips_low_scores_and_other_projects(trend_site):
    from backend.domains.seo.trends import sync_trend_opportunities
    _insert_signal("TrendSite", "zwak signaal onder de drempel", 40)
    _insert_signal("AnderProject", "andermans trend over van alles", 90)
    assert sync_trend_opportunities(trend_site)["created"] == 0


def test_trend_sync_slaat_geweerde_en_onbeoordeelde_signalen_over(trend_site):
    """Twee poorten waar de brug niet omheen mag.

    Een vacature of dienstpagina van een concurrent werd anders een
    artikelopdracht, en een signaal zonder relevantie-oordeel (`-1`) kon via de
    heuristiek alsnog boven de trend-drempel komen.
    """
    from backend.domains.seo import trends
    from backend.shared.database import get_conn

    geweerd = _insert_signal("TrendSite", "vacature bij een concurrent gevonden", 90)
    with get_conn() as c:
        c.execute("UPDATE radar_signals SET filter_reason = 'vacature' WHERE id = ?",
                  (geweerd,))
    _insert_signal("TrendSite", "signaal zonder relevantie oordeel", 90, match=-1)
    assert trends.sync_trend_opportunities(trend_site)["created"] == 0


def test_trend_sync_slaat_merkvermeldingen_over(trend_site):
    """Een merkvermelding is PR-bewijs ('iemand noemde ons'), geen content-
    onderwerp. Zonder deze uitsluiting werd een positieve persvermelding een
    artikelopdracht met de merknaam als zoekwoord — precies het omgekeerde
    van wat een `brand_mention`-watch beoogt te meten."""
    from backend.domains.seo import trends
    wid = _insert_watch("TrendSite", "brand_mention", "WeAreImpact")
    _insert_signal("TrendSite", "WeAreImpact", 90,
                   title="WeAreImpact genoemd als koploper AI in sociaal domein",
                   watch_id=wid)
    assert trends.sync_trend_opportunities(trend_site)["created"] == 0


def test_trend_sync_dedupes_against_existing_opportunity(trend_site):
    from backend.domains.seo.engine import create_manual_opportunity
    from backend.domains.seo.trends import sync_trend_opportunities
    create_manual_opportunity(trend_site["id"], "Dubbele Trend Onderwerp", "angle", "why")
    _insert_signal("TrendSite", "x", 75, title="Dubbele trend onderwerp")
    result = sync_trend_opportunities(trend_site)
    assert result["created"] == 0
    assert result["skipped"] >= 1


def test_trend_sync_caps_per_run(trend_site):
    """Trends mogen de GSC-kansen niet verdringen, dus max N per sync.

    De onderwerpen moeten écht verschillen: 'Trend nummer 1' en 'Trend nummer 2'
    zijn voor `is_same_topic` hetzelfde onderwerp (het cijfer is geen
    inhoudswoord), en dan meet deze test de dedupe in plaats van de klem.
    """
    from backend.domains.seo import trends
    onderwerpen = [
        "Zo kies je een dierenasiel dat bij je past",
        "Wat kost een hond in het eerste jaar echt",
        "Vrijwilligers werven voor een kleine vereniging",
        "Teambuilding met maatschappelijke impact organiseren",
        "Levensverhalen vastleggen voor je kleinkinderen",
    ]
    for i, titel in enumerate(onderwerpen):
        _insert_signal("TrendSite", "x", 90 - i, title=titel)
    assert trends.sync_trend_opportunities(trend_site)["created"] == trends.TREND_MAX_PER_SYNC


class TestZoekwoordKomtUitHetSignaal:
    """De zwaarste fout van de radar, en de vorm waarin hij terug kan komen."""

    def test_watchlist_keyword_wordt_niet_het_zoekwoord(self):
        from backend.domains.seo.trends import _signal_query
        sig = {"keyword": "hond adopteren",
               "title": "Hoe bereid je je voor op het adopteren van een asieldier?"}
        assert _signal_query(sig) != sig["keyword"]
        assert "asieldier" in _signal_query(sig)

    @pytest.mark.parametrize("titel,verwacht", [
        ("Concurrent lanceert tool - Vilans", "Concurrent lanceert tool"),
        ("  Lange   titel over een onderwerp  ", "Lange titel over een onderwerp"),
        ("Vrijwilligerswerk bij een dierenasiel, hoe begin je ...",
         "Vrijwilligerswerk bij een dierenasiel, hoe begin je"),
    ])
    def test_merkstaart_en_afkapping_gaan_eraf(self, titel, verwacht):
        from backend.domains.seo.trends import _signal_query
        assert _signal_query({"keyword": "x", "title": titel}) == verwacht

    def test_zonder_bruikbare_titel_geen_zoekwoord(self):
        from backend.domains.seo.trends import _signal_query, _bruikbare_query
        assert _signal_query({"keyword": "ai agents", "title": "Link to reddit.com"}) == ""
        assert _bruikbare_query("Nieuws") is False


# ── IndexNow-verificatie ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_verify_indexnow_without_base_url_or_key():
    from backend.domains.publish.indexing import verify_indexnow
    assert (await verify_indexnow({"base_url": "", "indexnow_key": "k"}))["status"] == "geen-base-url"
    assert (await verify_indexnow({"base_url": "https://x.nl", "indexnow_key": ""}))["status"] == "geen-key"
