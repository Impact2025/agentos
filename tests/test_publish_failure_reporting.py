"""Waarom een publicatie mislukte moet zichtbaar zijn — en een titel is een kop.

Drie dingen die het Actiecentrum onbruikbaar maakten:
  1. de oorzaak stond alleen in `publish_result`, dus elke kaart meldde
     "Onbekende fout";
  2. een job zonder <h1> kreeg het `angle`-veld als titel — een hele alinea;
  3. de kaart zélf had status 'ok', en dan is het een logregel in plaats van
     een beslissing (2 aug 2026).
"""
import json
import uuid

import pytest

from backend.domains.publish.content_pipeline import (
    _clean_title, _extract_title, publish_failure_reason)
from backend.shared.database import get_conn


# ── Titel ───────────────────────────────────────────────────────────────────

def test_h1_wins():
    assert _extract_title("<h1>Relatie verdiepen in 5 stappen</h1><p>x</p>",
                          fallback="iets anders") == "Relatie verdiepen in 5 stappen"


def test_h2_when_no_h1():
    assert _extract_title("<div><h2>Microgewoontes die werken</h2></div>",
                          fallback="iets") == "Microgewoontes die werken"


def test_paragraph_fallback_is_cut_to_a_headline():
    alinea = ("In tegenstelling tot het advies om 'gewoon te praten over je "
              "gevoelens', richt dit artikel zich op concrete, haalbare "
              "microgewoontes. Het legt de nadruk op verbinding.")
    title = _extract_title("<div><p>geen kop</p></div>", fallback=alinea)
    assert len(title) <= 90
    assert "\n" not in title
    assert title.startswith("In tegenstelling tot het advies")


def test_clean_title_strips_html_and_whitespace():
    assert _clean_title("  <em>Zo\n doe je dat</em> ") == "Zo doe je dat"


def test_short_title_untouched():
    assert _clean_title("Ouder inzicht zonder druk") == "Ouder inzicht zonder druk"


# ── Foutoorzaak ─────────────────────────────────────────────────────────────

def test_reason_prefers_live_check():
    reden = publish_failure_reason({
        "live_check": "live-controle: URL gaf HTTP 404",
        "site": {"error": "HTTP 500: iets"}})
    assert reden.startswith("live-controle")


def test_404_is_explained_as_missing_endpoint():
    reden = publish_failure_reason({"site": {"success": False, "error": "HTTP 404: NOT_FOUND"}})
    assert "endpoint" in reden.lower()
    assert "404" in reden


def test_500_is_explained_as_server_error():
    reden = publish_failure_reason(
        {"site": {"success": False, "error": 'HTTP 500: {"error":"Interne fout"}'}})
    assert "serverfout" in reden.lower()


def test_netlify_error_used_when_site_absent():
    assert "kapot" in publish_failure_reason({"netlify": {"error": "kapot"}})


def test_no_result_gives_empty_string():
    assert publish_failure_reason(None) == ""
    assert publish_failure_reason({}) == ""


# ── Afwijzen ná publicatie ──────────────────────────────────────────────────
#
# 2 aug 2026: negen pagina's stonden live met een 'rejected'-job eronder,
# waaronder 'Impact OS end-to-end publicatietest' op de site van een klant.
# Afwijzen verandert de rij, niet de wereld — en omdat de job daarna uit élk
# overzicht verdwijnt, kijkt niemand er ooit nog naar.

@pytest.fixture()
def gepubliceerde_job():
    sid = f"s-{uuid.uuid4().hex[:8]}"
    jid = f"j-{uuid.uuid4().hex[:8]}"
    with get_conn() as c:
        c.execute("INSERT INTO sites (id, name, base_url, created_at) "
                  "VALUES (?, 'RejectSite', 'https://reject.test', datetime('now'))", (sid,))
        c.execute(
            "INSERT INTO content_jobs (id, site_id, title, status, slug, publish_result, "
            "created_at) VALUES (?, ?, 'Impact OS end-to-end publicatietest', 'published', "
            "'agent-os-e2e', ?, datetime('now'))",
            (jid, sid, json.dumps({"success": True, "url": "https://reject.test/blog/e2e"})))
    yield jid
    with get_conn() as c:
        c.execute("DELETE FROM content_jobs WHERE id = ?", (jid,))
        c.execute("DELETE FROM sites WHERE id = ?", (sid,))
        c.execute("DELETE FROM activity_log WHERE project = 'RejectSite'")


def _kaarten_voor(project):
    with get_conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM activity_log WHERE project = ?", (project,))]


def test_afwijzen_van_live_pagina_geeft_een_foutkaart(gepubliceerde_job):
    from backend.domains.publish import content_pipeline

    content_pipeline.reject_job(gepubliceerde_job)

    kaarten = _kaarten_voor("RejectSite")
    assert len(kaarten) == 1
    kaart = kaarten[0]
    assert kaart["status"] == "error", "afwijzen haalt een pagina niet offline"
    assert kaart["action"] == "afgekeurd_maar_live"
    assert "reject.test/blog/e2e" in kaart["artifact"]
    assert "offline" in kaart["next_step"]


def test_afwijzen_vóór_publicatie_blijft_een_gewone_afwijzing():
    """Het normale geval mag geen rode kaart worden — dan is de inbox binnen een
    week onbruikbaar."""
    from backend.domains.publish import content_pipeline

    sid = f"s-{uuid.uuid4().hex[:8]}"
    jid = f"j-{uuid.uuid4().hex[:8]}"
    with get_conn() as c:
        c.execute("INSERT INTO sites (id, name, base_url, created_at) "
                  "VALUES (?, 'RejectSite2', 'https://reject.test', datetime('now'))", (sid,))
        c.execute("INSERT INTO content_jobs (id, site_id, title, status, created_at) "
                  "VALUES (?, ?, 'Concept dat niet goed genoeg was', 'pending_review', "
                  "datetime('now'))", (jid, sid))
    try:
        content_pipeline.reject_job(jid)
        kaarten = _kaarten_voor("RejectSite2")
        assert len(kaarten) == 1
        assert kaarten[0]["status"] == "ok"
        assert kaarten[0]["action"] == "afgekeurd"
    finally:
        with get_conn() as c:
            c.execute("DELETE FROM content_jobs WHERE id = ?", (jid,))
            c.execute("DELETE FROM sites WHERE id = ?", (sid,))
            c.execute("DELETE FROM activity_log WHERE project = 'RejectSite2'")


# ── Depubliceren: niet elke site-CMS deelt hetzelfde contract ──────────────
# Aanleiding (13 aug 2026, Bewaard voor Jou): PUBLISH_URL eindigde op
# '/api/v1/publish', niet '/api/publish' — de regex die '/api/publish' naar
# '/api/unpublish' herschrijft matchte niet, de geraden '/../unpublish'-
# fallback kreeg een schone 404, en het écht bestaande contract
# (`DELETE {publish_url}/{slug}`, uit de CMS's eigen OpenAPI-spec) werd nooit
# geprobeerd. Nu probeert de functie eerst de REST-idiomatische DELETE op de
# publish-collectie zelf.

@pytest.fixture
def unpub_env(monkeypatch):
    monkeypatch.setenv("UNPUBTEST_PUBLISH_URL", "https://unpubtest.example/api/v1/publish")
    monkeypatch.setenv("UNPUBTEST_PUBLISH_KEY", "test-key")
    yield {"id": "s-unpub", "name": "UnpubTest", "base_url": "https://unpubtest.example"}


def test_delete_op_de_publish_url_wordt_eerst_geprobeerd(unpub_env, monkeypatch):
    import asyncio
    from backend.domains.publish import content_pipeline

    calls = []

    def fake_delete(url, headers=None, timeout=None, follow_redirects=None):
        calls.append(("DELETE", url))
        class Resp:
            status_code = 200
            def json(self): return {"success": True}
        return Resp()

    def fake_post(*a, **k):
        calls.append(("POST", a, k))
        raise AssertionError("had de DELETE-route niet mogen overslaan")

    monkeypatch.setattr("httpx.delete", fake_delete)
    monkeypatch.setattr("httpx.post", fake_post)

    r = asyncio.run(content_pipeline.unpublish_from_project_site(
        unpub_env, "mijn-slug", reason="test"))
    assert r["success"] is True
    assert calls == [("DELETE", "https://unpubtest.example/api/v1/publish/mijn-slug")]


def test_valt_terug_op_post_unpublish_als_delete_404_geeft(unpub_env, monkeypatch):
    import asyncio
    from backend.domains.publish import content_pipeline

    def fake_delete(url, headers=None, timeout=None, follow_redirects=None):
        class Resp:
            status_code = 404
            text = '{"detail":"Not Found"}'
        return Resp()

    def fake_post(url, json=None, headers=None, timeout=None, follow_redirects=None):
        class Resp:
            status_code = 200
            def json(self): return {"success": True}
        return Resp()

    monkeypatch.setattr("httpx.delete", fake_delete)
    monkeypatch.setattr("httpx.post", fake_post)

    r = asyncio.run(content_pipeline.unpublish_from_project_site(
        unpub_env, "mijn-slug", reason="test"))
    assert r["success"] is True
