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
        html = ("<p>We zetten dit in de wachtrij van Impact OS zodat de agent "
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


class TestGateOpHetLaagstePublicatiepunt:
    """De gate moet staan waar er gepubliceerd wordt, niet alleen op de nette route.

    Aanleiding (23 jul 2026): 'Schrijf meta-titel & -description voor Pagina 2'
    ging live op bewaardvoorjou.nl, terwijl approve_and_publish die titel toen al
    blokkeerde. De publicatie kwam van een eenmalig reparatiescript dat jobs met
    een ongeldige slug opnieuw uitrolde — en juist deze titel had een ongeldige
    slug (een '&'). Een gate op één route beschermt één route.
    """

    @pytest.mark.asyncio
    async def test_agent_taaktitel_bereikt_de_site_niet(self, monkeypatch):
        # Credentials bewust gezet: zonder de gate zou dit een echte poging worden.
        monkeypatch.setenv("TESTSITE_PUBLISH_URL", "https://testsite.nl/api/publish")
        monkeypatch.setenv("TESTSITE_PUBLISH_KEY", "geheim")

        geposte_urls = []

        class _Boom:
            AsyncClient = None

            def __getattr__(self, _name):  # pragma: no cover - mag nooit
                geposte_urls.append("aangeroepen")
                raise AssertionError("er mag geen HTTP-verkeer zijn")

        monkeypatch.setattr(cp, "httpx", _Boom(), raising=False)

        result = await cp._publish_to_project_site(
            {"name": "TestSite", "base_url": "https://testsite.nl"},
            "Schrijf meta-titel & -description voor Pagina 2",
            "<p>wat tekst</p>", "kw", "schrijf-meta-titel-description-voor-pagina-2", 85,
        )
        assert result["success"] is False
        assert "niet publiceerbaar" in result["error"]
        assert "pagina 2" in result["error"]
        assert not geposte_urls

    @pytest.mark.asyncio
    async def test_gewoon_artikel_wordt_niet_geblokkeerd(self, monkeypatch):
        """De gate mag geen echte artikelen tegenhouden: zonder credentials
        hoort hij door te lopen tot de bekende 'geen PUBLISH_URL'-melding."""
        monkeypatch.delenv("TESTSITE_PUBLISH_URL", raising=False)
        monkeypatch.delenv("TESTSITE_PUBLISH_KEY", raising=False)

        result = await cp._publish_to_project_site(
            {"name": "TestSite", "base_url": "https://testsite.nl"},
            "Digitale erfenis regelen: meer dan alleen wachtwoorden",
            "<p>Een echt artikel over nalatenschap.</p>", "digitale erfenis",
            "digitale-erfenis-regelen", 85,
        )
        assert result["success"] is False
        assert "PUBLISH_URL" in result["error"]
        assert "niet publiceerbaar" not in result["error"]


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
            if "impactos-bestaat-niet-" in url:
                return _Resp(probe_status, probe_body)
            return _Resp(status, body or "<html>artikel</html>")

    class _Mod:
        AsyncClient = _Client

    return _Mod()


# ── Test-artefacten en werktitels (1 aug 2026) ──────────────────────────────
# Bij het opruimen na vijf dagen offline bleken twee van de achttien mislukte
# publicaties nooit bedoeld voor een bezoeker: een end-to-end proefrit van het
# systeem zelf, en een bestandsnaam met versie-aanduiding. Beide haalden de
# kwaliteitsgate moeiteloos — het is technisch prima proza — en er was geen
# regel die zei dat ze geen artikel waren.

def test_test_artefact_wordt_geblokkeerd():
    reden = cp.is_internal_document("Impact OS end-to-end publicatietest")
    assert reden and "test-artefact" in reden


def test_redactionele_werktitel_wordt_geblokkeerd():
    reden = cp.is_internal_document(
        "Klantcases overzichtspagina Ictusgo – Definitieve versie "
        "(geredigeerd & SEO-geoptimaliseerd)")
    assert reden and "werktitel" in reden


def test_gewoon_artikel_met_het_woord_test_mag_wel():
    """Kaal 'test' als marker zou dit tegenhouden, en dit is een artikel."""
    assert cp.is_internal_document("Test je kennis van digitale nalatenschap") is None


def test_versienummer_in_een_productnaam_blokkeert_niet():
    assert cp.is_internal_document("Wat is er nieuw in iOS 26 voor mantelzorgers") is None


# ── Gemeten lekken uit de Wachtrij van 15 aug 2026 ─────────────────────────

class TestGemetenLekken15Aug:
    """Drie titels die als 'publiceerbaar' in de Wachtrij stonden.

    Alle drie kwamen uit de herschrijflus en alle drie hadden een nette score.
    Ze lekten langs een andere naad, en dat is de reden dat ze hier per stuk
    staan: een gate die één vorm kent, mist de volgende.
    """

    def test_placeholder_telt_ook_zonder_opdracht_werkwoord(self):
        """'pagina 3' is een verwijzing naar een werklijst, waar hij ook staat.

        De placeholder-toets zat achter de verb-first-poort, en deze titel
        opent met een zelfstandig naamwoord.
        """
        reden = cp.is_internal_document(
            "Meta-titel en -description schrijven voor pagina 3 van Bewaard voor Jou")
        assert reden and "placeholder" in reden

    def test_metadata_van_de_eigen_homepage_is_geen_artikel(self):
        """Opdracht-werkwoord + 'metadata'/'homepage' — het object stond niet
        in _TASK_TITLE_OBJECT, dus glipte hij langs elke toets."""
        assert cp.is_internal_document("Herschrijf homepage metadata")

    def test_plandocument_in_de_kop_wordt_geweigerd(self):
        """'implementatieplan' stond alleen als body-marker (3+ hits nodig)."""
        reden = cp.is_internal_document("SEO-optimalisatie van alle content – Implementatieplan")
        assert reden and "plandocument" in reden

    @pytest.mark.parametrize("kop", [
        "Zo weet ik of het liefde is",
        "7 signalen om de reputatie van een AI-adviseur te toetsen",
        "Vrijwilligers werven: checklist in 5 stappen",
        "Wat is een meta description en waarom telt hij?",
        "Netwerkbijeenkomst organiseren in 5 stappen",
        "De boodschap blijft hangen of verdwijnt",
    ])
    def test_echte_artikelkoppen_blijven_door(self, kop):
        """De prijs van strenger filteren mag geen echte kop zijn.

        'checklist' en 'stappen' zijn juist góéde artikelwoorden, en een
        artikel mág over een meta description gáán — jargon alleen is nooit
        genoeg, het is de opdrachtvorm eromheen die telt.
        """
        assert cp.is_internal_document(kop) is None
