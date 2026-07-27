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
        # Agent-taaktitels. 'Schrijf meta-titel en -description voor pagina C'
        # haalde 82/100, passeerde élke gate en stond op 23-07-2026 als blog
        # live op steentjebijsteentje.nl (ontdekt 25-07-2026).
        "Schrijf meta-titel en -description voor pagina C",
        "Publiceer geüpdatete pagina A",
        "Optimaliseer interne links in pagina A en B",
        "Monitor zoekposities na publicatie",
        "Werk de striking distance zoekwoorden bij",
    ])
    def test_agent_taaktitel_wordt_geweigerd(self, titel):
        assert cp.is_internal_document(titel, "<p>Wat tekst.</p>") is not None

    @pytest.mark.parametrize("titel", [
        # Eén opdracht-werkwoord vooraan is te weinig bewijs: dit zijn gewone
        # artikeltitels en die moeten door de gate komen.
        "Schrijf je eigen liefdesbrief: 7 tips die wél werken",
        "Maak samen tijd vrij zonder agenda",
        "Voer dit gesprek voordat jullie gaan samenwonen",
    ])
    def test_imperatieve_artikeltitel_mag_door(self, titel):
        assert cp.is_internal_document(titel, "<p>Een artikel voor bezoekers.</p>") is None

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
    async def test_zachte_404_van_een_spa_wordt_ontmaskerd(self, monkeypatch):
        """Een single-page app geeft voor élke onbekende route dezelfde schil
        met status 200. Zo stond 'schrijf-meta-titel-en-description-voor-pagina-c'
        op 23-07-2026 als 'LIVE' in het logboek terwijl er niets rendde."""
        schil = "<html><body>" + ("x" * 5000) + "</body></html>"
        monkeypatch.setattr(cp, "httpx",
                            _fake_httpx(200, body=schil,
                                        probe_status=200, probe_body=schil),
                            raising=False)
        reden = await cp._verify_live("https://site.nl/blog/bestaat-niet-echt")
        assert reden is not None and "rendert niet" in reden

    @pytest.mark.asyncio
    async def test_echt_artikel_op_een_spa_blijft_goedgekeurd(self, monkeypatch):
        """Rendert het artikel wél, dan wijkt de pagina duidelijk af van de schil."""
        schil = "<html><body>" + ("x" * 5000) + "</body></html>"
        artikel = "<html><body>" + ("x" * 40000) + "</body></html>"
        monkeypatch.setattr(cp, "httpx",
                            _fake_httpx(200, body=artikel,
                                        probe_status=200, probe_body=schil),
                            raising=False)
        assert await cp._verify_live("https://site.nl/blog/echt-artikel") is None

    @pytest.mark.asyncio
    async def test_nette_404_maakt_de_200_geloofwaardig(self, monkeypatch):
        """Geeft de site wél nette 404's, dan is een 200 gewoon een echte pagina."""
        monkeypatch.setattr(cp, "httpx",
                            _fake_httpx(200, body="<html>artikel</html>",
                                        probe_status=404),
                            raising=False)
        assert await cp._verify_live("https://site.nl/blog/echt") is None

    @pytest.mark.asyncio
    async def test_netwerkfout_keurt_publicatie_niet_af(self, monkeypatch):
        # Een onbeslist antwoord is geen bewijs van mislukking.
        monkeypatch.setattr(cp, "httpx", _fake_httpx(exc=RuntimeError("timeout")),
                            raising=False)
        assert await cp._verify_live("https://bijeen.app/blog/x") is None


def _fake_httpx(status: int = 200, exc: Exception | None = None,
                body: str = "", probe_status: int = 404, probe_body: str = ""):
    """Minimale httpx-dubbel: AsyncClient als context manager met .get().

    De live-controle haalt twee URL's op: het artikel én een URL die
    gegarandeerd niet bestaat. `probe_*` beschrijft dat tweede antwoord.
    """
    class _Resp:
        def __init__(self, code, text):
            self.status_code = code
            self.text = text

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
            if "agentos-bestaat-niet-" in url:
                return _Resp(probe_status, probe_body)
            return _Resp(status, body or "<html>artikel</html>")

    class _Mod:
        AsyncClient = _Client

    return _Mod()
