"""Merkvermelding-watch (`brand_mention`) — de PR-tegenhanger van `competitor`.

Aanleiding (15 aug 2026): het Search Engine Land-artikel over off-page-SEO in
het AI-zoektijdperk stelt dat ongekoppelde merkvermeldingen (bijv. een
naamsvermelding in een vakblad zónder hyperlink) bij AI-zoekmachines vaak
evenveel autoriteit opbouwen als een gewone backlink. De Mission Radar had al
een `competitor`-watch (site: concurrent.nl) maar niets dat de eigen merknaam
buiten het eigen domein volgt.

Twee dingen moesten expliciet vastgelegd worden, precies omdat ze de bekende
faalpatronen van dit domein zijn:

  * de zoekopdracht mag GEEN `-site:`-uitsluiting in de querytekst bevatten —
    die Tavily-syntax geeft op de DuckDuckGo/Brave-terugval nul resultaten
    (dezelfde les als `linkbuilding/prospector.simplify_query`); de eigen-site-
    uitsluiting loopt via `quality.py`'s bestaande `eigen-site`-regel, niet via
    zoeksyntax;
  * een merkvermelding is PR-bewijs ('iemand noemde ons'), geen content-
    onderwerp — hij mag niet de trend-brug (`seo/trends.py`) of de auto-AEO-
    aanval in rollen, anders wordt een positieve persvermelding een
    artikelopdracht met de eigen merknaam als zoekwoord.
"""
import uuid

import pytest

from backend.domains.radar import service as radar_service
from backend.shared.database import get_conn


class TestWatchType:

    def test_brand_mention_is_een_geldig_watch_type(self):
        svc = radar_service.get_service()
        item = svc.add_watch("merktest", "WeAreImpact", "brand_mention", "WeAreImpact")
        try:
            assert item["type"] == "brand_mention"
        finally:
            svc.delete_watch(item["id"])

    def test_onbekend_type_blijft_geweigerd(self):
        svc = radar_service.get_service()
        with pytest.raises(ValueError):
            svc.add_watch("merktest", "Onzin", "geen-bestaand-type", "x")


class TestQuerybouw:

    def test_geen_site_uitsluiting_in_de_querytekst(self, monkeypatch):
        """`-site:` breekt de DuckDuckGo/Brave-terugval — de eigen-site-
        uitsluiting hoort via `quality.py` te lopen, niet via zoeksyntax."""
        svc = radar_service.get_service()
        gezien = {}

        def _fake_tavily_search(query, days=14, max_results=6):
            gezien["query"] = query
            gezien["days"] = days
            return []

        monkeypatch.setattr(svc, "_tavily_search", _fake_tavily_search)
        svc._gather({"type": "brand_mention", "value": "WeAreImpact"})
        assert "-site:" not in gezien["query"]
        assert "WeAreImpact" in gezien["query"]
        # Een merkvermelding veroudert niet in 14 dagen zoals nieuws — ruimer
        # venster dan de standaard keyword-watch.
        assert gezien["days"] > 14


class TestGeenContentIdee:
    """Een merkvermelding is bewijs, geen onderwerp — de twee plekken waar de
    radar signalen automatisch ombouwt tot content-werk moeten hem overslaan."""

    def _watch(self, wtype, value="WeAreImpact"):
        wid = f"w-{uuid.uuid4().hex[:8]}"
        with get_conn() as c:
            c.execute(
                "INSERT INTO radar_watchlist (id, project, label, type, value, active, "
                "last_scanned_at, created_at) VALUES (?, 'merktest', ?, ?, ?, 1, '', "
                "datetime('now'))",
                (wid, value, wtype, value))
        return wid

    def _signal(self, watch_id, score=90, match=90, title=None):
        sig_id = str(uuid.uuid4())
        now = "2026-08-15T09:00:00+00:00"
        with get_conn() as c:
            c.execute(
                """INSERT INTO radar_signals
                   (id, watch_id, project, keyword, title, url, source, signal_score,
                    ai_hook, ai_angle, ai_match_score, status, scanned_at,
                    created_at, updated_at, filter_reason)
                   VALUES (?, ?, 'merktest', 'WeAreImpact', ?, ?, 'news', ?, 'hook',
                           'angle', ?, 'new', ?, ?, ?, '')""",
                (sig_id, watch_id,
                 title or "WeAreImpact genoemd als koploper AI in sociaal domein",
                 f"https://vakblad.nl/artikelen/{sig_id}", score, match, now, now, now))
        return sig_id

    def teardown_method(self):
        with get_conn() as c:
            c.execute("DELETE FROM radar_signals WHERE project = 'merktest'")
            c.execute("DELETE FROM radar_watchlist WHERE project = 'merktest'")

    def test_auto_aeo_slaat_merkvermeldingen_over(self, monkeypatch):
        monkeypatch.setattr(radar_service, "AEO_AUTO_ATTACK", True)
        wid = self._watch("brand_mention")
        self._signal(wid)

        svc = radar_service.get_service()
        aangevallen = []
        monkeypatch.setattr(svc, "aeo_attack", lambda sid, channels=None: aangevallen.append(sid))
        svc._auto_aeo_top_signals()
        assert aangevallen == []

    def test_auto_aeo_pakt_gewone_keyword_signalen_wel(self, monkeypatch):
        """Contrast: een gewoon keyword-signaal met dezelfde score/match mag
        wél worden opgepakt — de uitsluiting geldt specifiek voor
        `brand_mention`, niet voor alle signalen."""
        monkeypatch.setattr(radar_service, "AEO_AUTO_ATTACK", True)
        wid = self._watch("keyword", "een trending onderwerp")
        self._signal(wid, title="Een trending onderwerp in het sociaal domein")

        svc = radar_service.get_service()
        aangevallen = []
        monkeypatch.setattr(svc, "aeo_attack", lambda sid, channels=None: aangevallen.append(sid))
        svc._auto_aeo_top_signals()
        assert len(aangevallen) == 1
