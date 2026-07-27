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


def test_search_web_delegates_to_shared_layer(clean_tables, monkeypatch):
    """De prospector zoekt via `shared.websearch` — daar zit de volledige
    providerketen (Tavily → Brave → DuckDuckGo → Bing). Geen tweede,
    domein-eigen fallback die uit de pas kan gaan lopen."""
    from backend.domains.linkbuilding import prospector
    from backend.shared import websearch

    monkeypatch.setattr(websearch, "search",
                        lambda q, max_results=8: [
                            {"title": "t", "url": "https://x.nl", "snippet": "s"}])

    hits = prospector._search_web("wat dan ook")
    assert hits and hits[0]["url"] == "https://x.nl"


def test_search_web_passes_max_results_through(clean_tables, monkeypatch):
    """Het aantal gevraagde resultaten mag niet stilletjes wegvallen."""
    from backend.domains.linkbuilding import prospector
    from backend.shared import websearch

    seen = {}

    def _fake(q, max_results=8):
        seen["q"], seen["max"] = q, max_results
        return []

    monkeypatch.setattr(websearch, "search", _fake)
    assert prospector._search_web("linkkansen relatiecoaching", max_results=3) == []
    assert seen == {"q": "linkkansen relatiecoaching", "max": 3}


def test_operator_query_valt_terug_op_gewone_trefwoorden(clean_tables, monkeypatch):
    """De zoekrecepten zijn Tavily-syntax. Neemt DuckDuckGo het over (Tavily-quota
    op), dan geeft die op zo'n opdracht nul resultaten — wat als 'geen linkkansen'
    leest terwijl de vraag alleen verkeerd gesteld was."""
    from backend.domains.linkbuilding import prospector
    from backend.shared import websearch

    gevraagd = []

    def _fake(q, max_results=8):
        gevraagd.append(q)
        if '"' in q or " OR " in q:
            return []
        return [{"title": "t", "url": "https://gevonden.nl", "snippet": "s"}]

    monkeypatch.setattr(websearch, "search", _fake)

    hits = prospector._search_web(
        'mentale last verdelen "handige links" OR "bronnen"')
    assert hits and hits[0]["url"] == "https://gevonden.nl"
    assert gevraagd[-1] == "mentale last verdelen handige links"


def test_terugval_ook_na_een_harde_zoekfout(clean_tables, monkeypatch):
    from backend.domains.linkbuilding import prospector
    from backend.shared import websearch

    def _fake(q, max_results=8):
        if '"' in q:
            raise websearch.WebSearchError("alle providers faalden")
        return [{"title": "t", "url": "https://gevonden.nl", "snippet": "s"}]

    monkeypatch.setattr(websearch, "search", _fake)
    hits = prospector._search_web('relatie "gastblog" OR "schrijf voor ons"')
    assert hits and hits[0]["url"] == "https://gevonden.nl"


def test_simplify_query_ontdoet_de_tavily_syntax():
    from backend.domains.linkbuilding.prospector import simplify_query
    assert simplify_query('term "handige links" OR "bronnen"') == "term handige links"
    assert simplify_query('"Mijn Site" -site:mijnsite.nl') == "Mijn Site"
    assert simplify_query("gewone query") == "gewone query"


def test_lege_uitslag_blijft_leeg_zonder_operatoren(clean_tables, monkeypatch):
    """Een gewone query die niets vindt is écht 'niets gevonden' — geen fout."""
    from backend.domains.linkbuilding import prospector
    from backend.shared import websearch

    monkeypatch.setattr(websearch, "search", lambda q, max_results=8: [])
    assert prospector._search_web("gewone query") == []


def test_search_web_raises_when_all_providers_fail(clean_tables, monkeypatch):
    """Falen álle providers, dan luid SearchUnavailable — niet stil op 0.
    Dit is de kern: 0 kandidaten leest in de UI als 'geen linkkansen gevonden',
    terwijl er in werkelijkheid niets is gezocht."""
    from backend.domains.linkbuilding import prospector
    from backend.shared import websearch

    def _down(_q, max_results=8):
        raise websearch.WebSearchError("alle providers faalden")

    monkeypatch.setattr(websearch, "search", _down)

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
