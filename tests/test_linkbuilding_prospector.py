"""Linkbuilding-prospector — de zoekfase mag nooit stil falen.

Een dode zoek-API (quota op, key weg) leverde eerder 0 kandidaten op, wat in de
UI leest als "geen linkkansen gevonden" terwijl er niets gezocht is. Deze tests
leggen vast dat zo'n storing zichtbaar wordt, en dat de kwalificatie geen
prospects verliest op URL-schrijfwijze.

Geen echte websearch of LLM: Tavily en _qualify worden gemonkeypatcht.
"""
import uuid
from datetime import datetime, timezone

import pytest


def _make_site(**overrides):
    from backend.shared.database import get_conn
    site_id = overrides.pop("id", str(uuid.uuid4()))
    now = datetime.now(timezone.utc).isoformat()
    fields = {"name": "Testsite", "base_url": "https://testsite.nl",
              "created_at": now}
    fields.update(overrides)
    cols = ", ".join(fields)
    marks = ", ".join("?" for _ in fields)
    with get_conn() as conn:
        conn.execute(f"INSERT INTO sites (id, {cols}) VALUES (?, {marks})",
                     [site_id, *fields.values()])
    return {"id": site_id, **fields}


def _make_page_snapshot(site_id, page_url, clicks=5, top_query="testterm"):
    from backend.shared.database import get_conn
    now = datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO gsc_history (id, site_id, scope, page_url, date, clicks, "
            "impressions, ctr, position, top_query, created_at) "
            "VALUES (?, ?, 'page', ?, ?, ?, ?, ?, ?, ?, ?)",
            (str(uuid.uuid4()), site_id, page_url, "2026-07-17", clicks, 100,
             0.05, 7.0, top_query, now),
        )


def test_search_web_falls_back_to_ddg_on_quota(clean_tables, monkeypatch):
    """Quota op = terugval op DuckDuckGo, niet meteen SearchUnavailable."""
    from backend.domains.linkbuilding import prospector

    class _Client:
        def __init__(self, **_):
            pass

        def search(self, **_):
            raise RuntimeError("This request exceeds your plan's set usage limit.")

    import tavily  # _search_web importeert 'm lazy; hier alvast voor de patch

    monkeypatch.setattr(prospector, "TAVILY_API_KEY", "fake-key")
    monkeypatch.setattr(tavily, "TavilyClient", _Client)
    monkeypatch.setattr(prospector, "_search_ddg",
                        lambda q, m=8: [{"title": "t", "url": "https://x.nl", "snippet": "s"}])

    hits = prospector._search_web("wat dan ook")
    assert hits and hits[0]["url"] == "https://x.nl"


def test_search_web_uses_ddg_without_api_key(clean_tables, monkeypatch):
    """Geen Tavily-key = direct via DuckDuckGo, geen harde fout."""
    from backend.domains.linkbuilding import prospector
    monkeypatch.setattr(prospector, "TAVILY_API_KEY", "")
    monkeypatch.setattr(prospector, "_search_ddg",
                        lambda q, m=8: [{"title": "t", "url": "https://y.nl", "snippet": "s"}])
    hits = prospector._search_web("wat dan ook")
    assert hits and hits[0]["url"] == "https://y.nl"


def test_search_web_raises_when_both_search_paths_fail(clean_tables, monkeypatch):
    """Faalt óók de terugval, dan pas SearchUnavailable — niet stil op 0."""
    from backend.domains.linkbuilding import prospector

    class _Client:
        def __init__(self, **_):
            pass

        def search(self, **_):
            raise RuntimeError("This request exceeds your plan's set usage limit.")

    import tavily

    def _ddg_down(_q, _m=8):
        raise prospector.SearchUnavailable("DuckDuckGo-terugval faalde: rate limit")

    monkeypatch.setattr(prospector, "TAVILY_API_KEY", "fake-key")
    monkeypatch.setattr(tavily, "TavilyClient", _Client)
    monkeypatch.setattr(prospector, "_search_ddg", _ddg_down)

    with pytest.raises(prospector.SearchUnavailable):
        prospector._search_web("wat dan ook")


@pytest.mark.asyncio
async def test_dead_search_logs_error_outcome(clean_tables, monkeypatch):
    """De storing landt als status='error' in het Actiecentrum, met oorzaak."""
    from backend.domains.linkbuilding import prospector
    from backend.shared.database import get_conn

    site = _make_site()

    def _boom(_site):
        raise prospector.SearchUnavailable("quota op")

    monkeypatch.setattr(prospector, "_collect_candidates", _boom)
    report = await prospector.run_prospecting_for_site(site)

    assert report["qualified"] == 0
    assert "quota op" in report["error"]

    with get_conn() as conn:
        row = conn.execute(
            "SELECT status, detail, next_step FROM activity_log "
            "WHERE action = 'linkbuilding_prospectie' ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
    assert row is not None, "een dode zoek-API hoort een uitkomstkaart te loggen"
    assert row["status"] == "error"
    assert "quota op" in row["detail"]
    assert "Tavily" in row["next_step"]


def test_target_pages_use_gsc_history_not_just_homepage(clean_tables):
    """Linkdoelen komen uit de GSC-dagsnapshots; anders is alles homepage-links."""
    from backend.domains.linkbuilding import prospector

    site = _make_site()
    _make_page_snapshot(site["id"], "https://testsite.nl/goede-pagina",
                        clicks=9, top_query="beste term")

    targets = prospector._target_pages(site)
    urls = [t["url"] for t in targets]

    assert "https://testsite.nl/goede-pagina" in urls
    assert "https://testsite.nl" in urls, "homepage blijft een geldig doel"
    # De ranking-context stuurt de ankertekst.
    diep = next(t for t in targets if t["url"].endswith("/goede-pagina"))
    assert "beste term" in diep["title"]


def test_url_key_normalises_scheme_www_and_slash():
    from backend.domains.linkbuilding.prospector import _url_key
    assert _url_key("https://www.Voorbeeld.nl/pad/") == _url_key("http://voorbeeld.nl/pad")
    assert _url_key("https://x.nl?utm=1") == _url_key("https://x.nl")


@pytest.mark.asyncio
async def test_qualification_matches_despite_url_rewrite(clean_tables, monkeypatch):
    """De LLM mag de URL herschrijven; de prospect hoort niet stil te verdwijnen."""
    from backend.domains.linkbuilding import prospector

    site = _make_site()
    monkeypatch.setattr(prospector, "_collect_candidates", lambda _s: [
        {"url": "https://www.partner.nl/blog/", "title": "Partner",
         "snippet": "over ons", "domain": "partner.nl"},
    ])
    monkeypatch.setattr(prospector, "_target_pages",
                        lambda _s, **_k: [{"url": "https://testsite.nl", "title": "home"}])

    async def _judged(*_a, **_k):
        # Zelfde pagina, andere schrijfwijze dan wat de zoekmachine gaf.
        return [{"url": "http://partner.nl/blog", "score": 90, "type": "gastblog",
                 "reason": "past", "target_url": "https://testsite.nl",
                 "anchor_text": "slimme link"}]

    async def _no_email(_url):
        return ""

    monkeypatch.setattr(prospector, "_qualify", _judged)
    monkeypatch.setattr(prospector, "_scrape_contact_email", _no_email)

    report = await prospector.run_prospecting_for_site(site)
    assert report["qualified"] == 1, "URL-herschrijving mag de prospect niet lozen"
