"""Tests voor de social-kwaliteitslaag en de per-artikel social-toggle.

Dekt de pure bouwstenen (geen LLM/netwerk):
- _derive_hashtags / _polish_social_pack / _fallback_social_copy
- _channels_from_body (per-artikel kanaalkeuze bij goedkeuren)
- shared.websearch provider-fallback-volgorde
"""
import pytest

from backend.domains.publish.content_pipeline import (
    _derive_hashtags,
    _fallback_social_copy,
    _polish_social_pack,
)
from backend.domains.content_queue.router import _channels_from_body


# ── Hashtag-afleiding ────────────────────────────────────────────────────────

def test_derive_hashtags_from_keyword():
    tags = _derive_hashtags("erfbelasting besparen", "Bewaard Voor Jou")
    assert "#ErfbelastingBesparen" in tags
    assert "#BewaardVoorJou" in tags


def test_derive_hashtags_empty_keyword():
    assert _derive_hashtags("", "") == ""


# ── Polish: lengtes, hashtags, URL-strippen ──────────────────────────────────

def test_polish_strips_urls_and_fences():
    pack = {"linkedin": '```\n"Lees dit! https://example.com/artikel"\n```'}
    out = _polish_social_pack(pack, "seo tips", "Site")
    assert "http" not in out["linkedin"]
    assert "```" not in out["linkedin"]
    assert out["linkedin"].startswith("Lees dit!")


def test_polish_adds_hashtags_when_missing():
    out = _polish_social_pack({"facebook": "Een tekst zonder tags."}, "ai zorg", "Impact")
    assert "#" in out["facebook"]


def test_polish_trims_twitter_preserving_hashtags():
    long_text = "woord " * 80 + "#AiZorg"
    out = _polish_social_pack({"twitter": long_text}, "ai zorg", "")
    assert len(out["twitter"]) <= 270
    assert out["twitter"].endswith("#AiZorg")


def test_polish_keeps_existing_hashtags():
    out = _polish_social_pack({"instagram": "Caption.\n\n#Bestaand"}, "kw", "Site")
    assert out["instagram"].count("#Bestaand") == 1


# ── Terugval-pack: nooit meer kale titel ─────────────────────────────────────

def test_fallback_social_copy_is_complete():
    pack = _fallback_social_copy("Zo bespaar je erfbelasting", "erfbelasting", "BewaardVoorJou")
    for platform in ("linkedin", "facebook", "instagram", "twitter"):
        assert pack[platform], platform
        assert pack[platform] != "Zo bespaar je erfbelasting"
    assert len(pack["twitter"]) <= 270
    assert "#" in pack["linkedin"]


# ── Kanaalkeuze uit de request-body ─────────────────────────────────────────

def test_channels_empty_without_body():
    # Social is opt-in: zonder expliciete keuze gaat er niets naar social.
    assert _channels_from_body(None) == []
    assert _channels_from_body({}) == []


def test_channels_social_false_means_empty():
    assert _channels_from_body({"social": False}) == []


def test_channels_explicit_list_normalized():
    assert _channels_from_body({"channels": ["LinkedIn", " twitter "]}) == ["linkedin", "twitter"]


def test_channels_empty_list_means_none_posted():
    assert _channels_from_body({"channels": []}) == []


# ── Zoeklaag: fallback-volgorde en luid falen ───────────────────────────────

HITS = [{"title": "t", "url": "https://x.nl", "snippet": "s"}]


@pytest.fixture(autouse=True)
def _clean_quota_blocks():
    from backend.shared import websearch
    websearch.reset_quota_blocks()
    yield
    websearch.reset_quota_blocks()


def _boom(msg):
    def f(*a, **kw):
        raise RuntimeError(msg)
    return f


def test_websearch_falls_back_to_brave(monkeypatch):
    from backend.shared import websearch
    monkeypatch.setattr(websearch, "TAVILY_API_KEY", "key")
    monkeypatch.setattr(websearch, "BRAVE_SEARCH_API_KEY", "key")
    monkeypatch.setattr(websearch, "_tavily_search", _boom("usage limit exceeded"))
    monkeypatch.setattr(websearch, "_brave_search", lambda *a, **kw: HITS)
    assert websearch.search("q") == HITS


def test_websearch_falls_back_to_keyless_without_brave_key(monkeypatch):
    """De kern van het incident van 20 jul: Tavily-quota op én geen Brave-key.
    De keyless laag moet de keten dan alsnog dragen."""
    from backend.shared import websearch
    monkeypatch.setattr(websearch, "TAVILY_API_KEY", "key")
    monkeypatch.setattr(websearch, "BRAVE_SEARCH_API_KEY", "")
    monkeypatch.setattr(websearch, "_tavily_search", _boom("upgrade your plan"))
    monkeypatch.setattr(websearch, "_ddg_search", lambda *a, **kw: HITS)
    assert websearch.search("q") == HITS


def test_quota_error_puts_provider_in_backoff(monkeypatch):
    """Een uitgeputte quota is een toestand: de tweede query mag Tavily niet
    opnieuw aanroepen, maar meteen doorschuiven naar de terugval."""
    from backend.shared import websearch
    calls = []
    monkeypatch.setattr(websearch, "TAVILY_API_KEY", "key")
    monkeypatch.setattr(websearch, "BRAVE_SEARCH_API_KEY", "")

    def tavily(*a, **kw):
        calls.append(1)
        raise RuntimeError("This request exceeds your plan's set usage limit")

    monkeypatch.setattr(websearch, "_tavily_search", tavily)
    monkeypatch.setattr(websearch, "_ddg_search", lambda *a, **kw: HITS)
    assert websearch.search("q1") == HITS
    assert websearch.search("q2") == HITS
    assert len(calls) == 1


def test_non_quota_error_does_not_block_provider(monkeypatch):
    """Een timeout mag een werkende provider niet 6 uur uitschakelen."""
    from backend.shared import websearch
    calls = []
    monkeypatch.setattr(websearch, "TAVILY_API_KEY", "key")
    monkeypatch.setattr(websearch, "BRAVE_SEARCH_API_KEY", "")

    def tavily(*a, **kw):
        calls.append(1)
        raise RuntimeError("timed out")

    monkeypatch.setattr(websearch, "_tavily_search", tavily)
    monkeypatch.setattr(websearch, "_ddg_search", lambda *a, **kw: HITS)
    websearch.search("q1")
    websearch.search("q2")
    assert len(calls) == 2


def test_websearch_raises_when_all_fail(monkeypatch):
    """Falen álle providers, dan luid — nooit stil een lege lijst.

    Patch de keten via `_PROVIDERS`, niet via een handmatige opsomming: die
    liep achter toen de keyless `ddg_html`-achtervang erbij kwam, waardoor de
    test een échte zoekopdracht deed en niet meer testte wat hij beweert.
    """
    from backend.shared import websearch
    monkeypatch.setattr(websearch, "TAVILY_API_KEY", "key")
    monkeypatch.setattr(websearch, "BRAVE_SEARCH_API_KEY", "key")
    for func_name in websearch._PROVIDERS.values():
        monkeypatch.setattr(websearch, func_name, _boom("kapot"))
    assert set(websearch.providers_configured()) <= set(websearch._PROVIDERS)
    with pytest.raises(websearch.WebSearchError):
        websearch.search("q")


def test_websearch_keyless_chain_without_any_key(monkeypatch):
    """Zonder enige API-key is de keten niet leeg meer: DDG heeft er geen nodig."""
    from backend.shared import websearch
    monkeypatch.setattr(websearch, "TAVILY_API_KEY", "")
    monkeypatch.setattr(websearch, "BRAVE_SEARCH_API_KEY", "")
    monkeypatch.setattr(websearch, "_ddg_search", lambda *a, **kw: HITS)
    assert websearch.search("q") == HITS


# ── Iris' lead_search_run (zonder netwerk) ──────────────────────────────────

def test_iris_lead_search_run_happy_path(monkeypatch):
    import asyncio
    from backend.domains.iris import actions
    from backend.domains.prospecting.service import LeadsService

    calls = {}

    def fake_batch(self, queries, lead_type="overig", max_per_query=4):
        calls["queries"] = list(queries)
        calls["lead_type"] = lead_type
        return {"queries": len(queries), "failed_queries": 0, "found": 3, "saved": 2}

    monkeypatch.setattr(LeadsService, "run_search_batch", fake_batch)
    monkeypatch.setattr(actions, "_already_done_today", lambda *a: False)
    done = asyncio.run(actions.lead_search_run(
        ["notariskantoor amsterdam", "  ", "uitvaart utrecht"], "funnel droog"))
    assert done and "2 nieuwe lead" in done
    assert calls["queries"] == ["notariskantoor amsterdam", "uitvaart utrecht"]


def test_iris_lead_search_run_template_fallback(monkeypatch):
    import asyncio
    from backend.domains.iris import actions
    from backend.domains.prospecting.service import BATCH_TEMPLATES, LeadsService

    calls = {}

    def fake_batch(self, queries, lead_type="overig", max_per_query=4):
        calls["queries"] = list(queries)
        calls["lead_type"] = lead_type
        return {"queries": len(queries), "failed_queries": 0, "found": 0, "saved": 0}

    monkeypatch.setattr(LeadsService, "run_search_batch", fake_batch)
    monkeypatch.setattr(actions, "_already_done_today", lambda *a: False)
    done = asyncio.run(actions.lead_search_run(None, "reden", template="zorg_nl"))
    assert done is not None
    assert calls["lead_type"] == "zorg"
    assert all(q in BATCH_TEMPLATES["zorg_nl"] for q in calls["queries"])


def test_iris_lead_search_run_dedupes_per_day(monkeypatch):
    import asyncio
    from backend.domains.iris import actions

    monkeypatch.setattr(actions, "_already_done_today", lambda *a: True)
    done = asyncio.run(actions.lead_search_run(["x"], "reden"))
    assert done and "draaide vandaag al" in done
