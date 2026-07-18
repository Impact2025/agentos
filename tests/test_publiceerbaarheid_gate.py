"""Publiceerbaarheidsgate + eerlijke publicatiestatus.

Achtergrond (17-07-2026): op bijeen.app stonden twee interne werkstukken als
blog live ("Plan: Directe antwoorden toevoegen aan alle 28 pagina's", score 85;
"Rapport: Status aanpassingen templates en one-pager"). De kwaliteitsgate meet
schrijfkwaliteit en de relevantie-gate meet het onderwerp — geen van beide
vraagt of iets voor bezoekers bedoeld is. Daarnaast stonden jobs op 'published'
terwijl er niets online stond (404, of ontbrekende publish-credentials).
"""
import pytest

from backend.domains.publish import content_pipeline as cp


class TestInterneDocumentenHerkennen:
    @pytest.mark.parametrize("titel", [
        "Plan: Directe antwoorden toevoegen aan alle 28 pagina's — Bijeen",
        "Rapport: Status aanpassingen templates en one-pager",
        "Eindredactie: Content Redactie en Consistentiecheck",
        "Analyse: concurrentie Q3",
        "Memo: aanpak linkbuilding",
    ])
    def test_intern_werkstuk_wordt_geweigerd(self, titel):
        assert cp.is_internal_document(titel, "<p>Wat tekst.</p>") is not None

    @pytest.mark.parametrize("titel", [
        "SROI berekenen per evenement: een praktisch stappenplan",
        "Welzijnsevenement organiseren: waarom lege gebaren niet werken",
        "Levensverhaal vastleggen voor kleinkinderen: 7 praktische manieren",
        "Planningsmodule specifiek voor sport- en buurtverenigingen",
    ])
    def test_echt_artikel_mag_door(self, titel):
        assert cp.is_internal_document(titel, "<p>Een artikel voor bezoekers.</p>") is None

    def test_werkproces_tekst_wordt_geweigerd_ondanks_neutrale_titel(self):
        html = ("<p>We zetten dit in de wachtrij van Agent OS zodat de agent "
                "het oppakt. De SEO-score bepaalt of het live gaat.</p>")
        assert cp.is_internal_document("Vindbaarheid verbeteren", html) is not None

    def test_losse_term_maakt_een_artikel_nog_niet_intern(self):
        # Eén toevallige hit mag een echt artikel niet blokkeren.
        html = "<p>Plan je sprint goed in, dan verloopt je evenement soepel.</p>"
        assert cp.is_internal_document("Evenement plannen in 5 stappen", html) is None


class TestLiveControle:
    @pytest.mark.asyncio
    async def test_404_meldt_fout(self, monkeypatch):
        monkeypatch.setattr(cp, "httpx", _fake_httpx(404), raising=False)
        reden = await cp._verify_live("https://bijeen.app/blog/bestaat-niet")
        assert reden is not None and "404" in reden

    @pytest.mark.asyncio
    async def test_200_is_stil(self, monkeypatch):
        monkeypatch.setattr(cp, "httpx", _fake_httpx(200), raising=False)
        assert await cp._verify_live("https://bijeen.app/blog/bestaat-wel") is None

    @pytest.mark.asyncio
    async def test_netwerkfout_keurt_publicatie_niet_af(self, monkeypatch):
        # Een onbeslist antwoord is geen bewijs van mislukking.
        monkeypatch.setattr(cp, "httpx", _fake_httpx(exc=RuntimeError("timeout")),
                            raising=False)
        assert await cp._verify_live("https://bijeen.app/blog/x") is None


def _fake_httpx(status: int = 200, exc: Exception | None = None):
    """Minimale httpx-dubbel: AsyncClient als context manager met .get()."""
    class _Resp:
        status_code = status

    class _Client:
        def __init__(self, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, **kw):
            if exc:
                raise exc
            return _Resp()

    class _Mod:
        AsyncClient = _Client

    return _Mod()
