"""De Kansen-lijst mag alleen kansen tonen die écht een kans zijn.

Aanleiding (2 aug 2026): het Kansen-paneel van WeAreImpact bood 11 "nieuwe"
kansen aan. Precies één was nieuw. Twee waren al gedaan — 'consultant sociaal
domein' lag sinds de dag ervoor in de Wachtrij, en 'programma manager digitale
transformatie' stond live als 'programmamanager digitale transformatie' (één
spatie verschil). Vier kannibaliseerden een bestaand blog, vier waren ruis
(concurrent-domein, Duitstalige query, varianten van weggeklikte kansen).
"Schrijf alle 11" had dus tien artikelen geproduceerd die de site schaden.

Elke test hieronder is één van de gaten waardoor dat kon gebeuren.
"""
import json
import uuid

import pytest

from backend.domains.publish import content_pipeline as cp
from backend.domains.seo import engine
from backend.domains.seo import opportunity_quality as quality
from backend.domains.seo import sites as sites_service
from backend.shared.database import get_conn


@pytest.fixture(autouse=True)
def _schone_cache():
    quality.invalidate()
    yield
    quality.invalidate()


@pytest.fixture
def site(monkeypatch):
    # Geen netwerk in de tests: de sitemap-bron wordt per test gevuld.
    monkeypatch.setattr(quality, "_external_coverage", lambda s: [])
    s = sites_service.create_site(
        {"name": "KansenTest", "base_url": "https://voorbeeld.nl"})
    yield sites_service.get_site(s["id"])
    with get_conn() as conn:
        conn.execute("DELETE FROM opportunities WHERE site_id = ?", (s["id"],))
        conn.execute("DELETE FROM content_jobs WHERE site_id = ?", (s["id"],))
    sites_service.delete_site(s["id"])


@pytest.fixture
def gsc(site):
    """Zet een GSC-waarneming klaar: deze pagina vertoont op dit zoekwoord."""
    gemaakt = []

    def _zet(site_id, page_url, top_query, position=15.0, impressions=40):
        with get_conn() as conn:
            conn.execute(
                "INSERT INTO gsc_history (id, site_id, scope, page_url, date, "
                "clicks, impressions, ctr, position, top_query, created_at) "
                "VALUES (?, ?, 'page', ?, '2026-08-03', 0, ?, 0.0, ?, ?, "
                "datetime('now'))",
                (str(uuid.uuid4()), site_id, page_url, impressions, position,
                 top_query),
            )
        gemaakt.append(site_id)
        quality.invalidate(site_id)

    yield _zet
    with get_conn() as conn:
        for sid in gemaakt:
            conn.execute("DELETE FROM gsc_history WHERE site_id = ?", (sid,))


def _kans(site_id: str, query: str, status: str = "new",
          impressions: int = 100, position: float = 12.0) -> str:
    oid = str(uuid.uuid4())
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO opportunities (id, site_id, query, clicks, impressions, "
            "ctr, position, opportunity_score, status, scanned_at) "
            "VALUES (?, ?, ?, 0, ?, 0.0, ?, 50.0, ?, '2026-08-01')",
            (oid, site_id, query, impressions, position, status),
        )
    return oid


def _open_kansen(site_id):
    return engine.list_opportunities_truth(site_id=site_id, status="open")


def _uitgefilterd(site_id):
    return engine.list_opportunities_truth(site_id=site_id, status="uitgefilterd")


def _queries(kansen):
    return {k["query"] for k in kansen}


class TestNormaliseren:
    def test_samenstelling_met_spatie_is_hetzelfde_zoekwoord(self):
        """'programma manager' == 'programmamanager'. Nederlands schrijft
        samenstellingen aaneen, zoekers lang niet altijd, en GSC bewaart beide
        vormen als losse queries."""
        assert quality.squash("programma manager digitale transformatie") == \
            quality.squash("Programmamanager Digitale Transformatie")

    def test_accenten_tellen_niet_mee(self):
        """GSC levert 'ideeen' waar de site 'ideeën' schrijft."""
        assert quality.squash("cadeau ideeën") == quality.squash("cadeau ideeen")

    def test_functiewoorden_dragen_geen_onderwerp(self):
        assert quality.tokens("de rol van data in het sociaal domein") == \
            {"rol", "data", "sociaal", "domein"}

    def test_zoekintentie_woorden_blijven_staan(self):
        """'kosten' en 'beste' onderscheiden twee kansen van elkaar — die mogen
        nooit als stopwoord weggegooid worden."""
        assert "kosten" in quality.tokens("levensverhaal laten schrijven kosten")
        assert "beste" in quality.tokens("beste partners voor ai")


class TestAlGedaan:
    def test_concept_in_de_wachtrij_is_geen_nieuwe_kans(self, site):
        """Het kerngeval: 'consultant sociaal domein' lag al een dag klaar."""
        _kans(site["id"], "consultant sociaal domein")
        cp.create_job(site["id"], "Consultant sociaal domein: 7 signalen",
                      "consultant sociaal domein", "waarom", "<p>x</p>", 82,
                      {}, None, "consultant-sociaal-domein", status="pending_review")

        kansen = _open_kansen(site["id"])
        assert [k["status"] for k in kansen] == ["in_progress"]
        assert kansen[0]["filter_reason"] == "in-wachtrij"

    def test_concept_zonder_keyword_wordt_op_titel_herkend(self, site):
        """Jobs uit de goal-engine hebben een leeg keyword-veld. Die matchten
        per definitie nergens op — vandaar dat 'digitale transformatie sociaal
        domein' als nieuw werd aangeboden terwijl het concept er al lag."""
        _kans(site["id"], "digitale transformatie sociaal domein")
        cp.create_job(site["id"],
                      "Digitale transformatie in het sociaal domein: de rol van data",
                      "", "waarom", "<p>x</p>", 80, {}, None,
                      "digitale-transformatie-sociaal-domein", status="pending_review")

        assert _open_kansen(site["id"])[0]["status"] == "in_progress"

    def test_live_artikel_met_andere_spatiering(self, site):
        """'programma manager digitale transformatie' stond live als
        'programmamanager digitale transformatie'."""
        _kans(site["id"], "programma manager digitale transformatie")
        job_id = cp.create_job(site["id"], "Programmamanager digitale transformatie",
                               "programmamanager digitale transformatie", "waarom",
                               "<p>x</p>", 85, {}, None,
                               "programmamanager-digitale-transformatie",
                               status="published")
        with get_conn() as conn:
            conn.execute("UPDATE content_jobs SET publish_result = ? WHERE id = ?",
                         (json.dumps({"site": {"url": "https://voorbeeld.nl/p"}}), job_id))

        assert not _open_kansen(site["id"])  # niet langer open werk
        gepubliceerd = engine.list_opportunities_truth(site_id=site["id"],
                                                       include_filtered=True)
        assert gepubliceerd[0]["status"] == "published"

    def test_live_blog_buiten_agent_os_om_telt_mee(self, site, monkeypatch):
        """`published_pages` is voor de meeste sites leeg by design; de live
        sitemap is de énige bron die de échte blogs kent."""
        monkeypatch.setattr(
            quality, "_external_coverage",
            lambda s: [quality._record("al-live", "ai strategie en change management",
                                       "ai strategie en change management")],
        )
        _kans(site["id"], "ai strategie change management")
        assert not _open_kansen(site["id"])

    def test_andere_zoekintentie_is_wel_een_kans(self, site):
        """Bewust streng: 'kosten' erbij is een échte andere zoekintentie en
        verdient een eigen pagina. Over-filteren gooit goede kansen weg."""
        _kans(site["id"], "levensverhaal laten schrijven kosten")
        cp.create_job(site["id"], "Levensverhaal laten schrijven",
                      "levensverhaal laten schrijven", "waarom", "<p>x</p>", 85,
                      {}, None, "levensverhaal-laten-schrijven", status="published")

        assert _queries(_open_kansen(site["id"])) == \
            {"levensverhaal laten schrijven kosten"}


class TestKannibalisatie:
    def test_volledig_gedekt_zoekwoord_kannibaliseert(self, site):
        _kans(site["id"], "kat adopteren")
        cp.create_job(site["id"], "Een kat adopteren uit het asiel: de complete gids",
                      "kat adopteren uit het asiel", "waarom", "<p>x</p>", 85,
                      {}, None, "kat-adopteren-asiel", status="published")

        assert not _open_kansen(site["id"])
        weg = _uitgefilterd(site["id"])
        assert weg[0]["filter_reason"] == "kannibaal"
        assert "asiel" in weg[0]["filter_detail"]

    def test_deels_overlappend_onderwerp_blijft_staan(self, site):
        """Twee gedeelde woorden van de vier is geen kannibalisatie."""
        _kans(site["id"], "hond adopteren tips")
        cp.create_job(site["id"], "Kat adopteren uit het asiel", "kat adopteren",
                      "waarom", "<p>x</p>", 85, {}, None, "kat", status="published")

        assert _queries(_open_kansen(site["id"])) == {"hond adopteren tips"}


class TestRuis:
    def test_domeinnaam_is_geen_contentvraag(self, site):
        """'nictiz.nl' — die zoeker wil een andere website."""
        _kans(site["id"], "nictiz.nl")
        assert not _open_kansen(site["id"])
        assert _uitgefilterd(site["id"])[0]["filter_reason"] == "navigatie"

    def test_eigen_domein_is_wel_een_kans(self, site):
        """Op je eigen merknaam hoor je juist op nummer 1 te staan."""
        _kans(site["id"], "voorbeeld.nl")
        assert _queries(_open_kansen(site["id"])) == {"voorbeeld.nl"}

    def test_anderstalige_query(self, site):
        _kans(site["id"], "ai strategie beratung")
        weg = _uitgefilterd(site["id"])
        assert weg[0]["filter_reason"] == "vreemde-taal"
        assert "beratung" in weg[0]["filter_detail"]

    def test_te_vaag_zoekwoord(self, site):
        _kans(site["id"], "ai")
        assert _uitgefilterd(site["id"])[0]["filter_reason"] == "te-vaag"

    def test_bewijs_staat_altijd_op_de_kaart(self, site):
        """Een filter dat je niet kunt controleren is niet te vertrouwen."""
        _kans(site["id"], "nictiz.nl")
        weg = _uitgefilterd(site["id"])[0]
        assert weg["filter_label"] and weg["filter_detail"]


class TestVraagHerkomst:
    def test_gsc_data_is_gemeten_vraag(self, site):
        _kans(site["id"], "digitale nalatenschap regelen", impressions=400,
              position=7.0)
        assert _open_kansen(site["id"])[0]["demand"] == "gemeten"

    def test_cold_start_zonder_impressies_is_speculatief(self, site):
        """Cold-start- en trendkansen stonden onder de kop 'Striking distance'
        alsof ze uit GSC kwamen. Ze hebben 0 impressies en positie 0."""
        _kans(site["id"], "digitale nalatenschap regelen", impressions=0,
              position=0.0)
        assert _open_kansen(site["id"])[0]["demand"] == "speculatief"

    def test_gemeten_vraag_staat_bovenaan(self, site):
        _kans(site["id"], "speculatieve kans hier", impressions=0, position=0.0)
        _kans(site["id"], "gemeten kans hier", impressions=300, position=8.0)
        assert _open_kansen(site["id"])[0]["query"] == "gemeten kans hier"


class TestGateGeldtOveral:
    def test_autonome_contentmotor_slaat_ruis_over(self, site):
        """Anders schrijft de motor 's nachts alsnog wat Vincent overdag met
        reden weggefilterd ziet — dan is het filter alleen cosmetiek."""
        # De ruis staat bovenaan (hoogste score) — precies het geval waarin de
        # motor hem als eerste zou oppakken.
        _kans(site["id"], "nictiz.nl", impressions=900, position=6.0)
        _kans(site["id"], "digitale nalatenschap regelen")

        gekozen = cp.select_topic(site)
        assert gekozen["query"] == "digitale nalatenschap regelen"
        with get_conn() as conn:
            ruis = conn.execute(
                "SELECT status FROM opportunities WHERE query = 'nictiz.nl'"
            ).fetchone()["status"]
        assert ruis == "dismissed"

    def test_motor_pakt_geen_zoekwoord_dat_al_in_de_wachtrij_ligt(self, site):
        _kans(site["id"], "consultant sociaal domein")
        cp.create_job(site["id"], "Consultant sociaal domein",
                      "consultant sociaal domein", "waarom", "<p>x</p>", 82,
                      {}, None, "consultant", status="pending_review")

        assert cp.select_topic(site) is None
        with get_conn() as conn:
            status = conn.execute(
                "SELECT status FROM opportunities WHERE query = 'consultant sociaal domein'"
            ).fetchone()["status"]
        # Niet dismissen: het artikel is onderweg. Wordt het afgewezen, dan geeft
        # `reconcile_opportunities` het zoekwoord vanzelf weer vrij.
        assert status == "new"

    def test_kapotte_gate_trekt_de_lijst_niet_leeg(self, site, monkeypatch):
        """"Geen kansen" leest als "niets te doen" — een gevaarlijker leugen dan
        een dubbele kans."""
        _kans(site["id"], "digitale nalatenschap regelen")

        def _stuk(*a, **kw):
            raise RuntimeError("sitemap onbereikbaar")
        monkeypatch.setattr(quality, "annotate", _stuk)

        assert len(_open_kansen(site["id"])) == 1


class TestWoordvormen:
    """Nederlands vervoegt en plakt samen; een gate die dat niet volgt is in
    het Nederlands geen gate.

    Aanleiding (3 aug 2026): Bewaard voor Jou kreeg acht 'nieuwe' kansen terwijl
    er 102 pagina's live stonden. De vergelijking liep op exacte tokens, dus
    'ouders' dekte 'ouder' niet en 'voetbalskills' dekte 'voetbal' niet.
    """

    def test_meervoud_dekt_enkelvoud(self, site):
        _kans(site["id"], "voetbalontwikkeling kind zien ouders")
        cp.create_job(site["id"], "Ouder inzicht in voetbalontwikkeling kind zonder druk",
                      "", "waarom", "<p>x</p>", 85, {}, None, "ouder-inzicht",
                      status="published")

        assert not _open_kansen(site["id"])

    def test_samenstelling_dekt_grondwoord(self, site):
        _kans(site["id"], "voetbalskills kind bijhouden app")
        cp.create_job(site["id"], "Voetbal vaardigheden bijhouden met app voor coaches",
                      "", "waarom", "<p>x</p>", 85, {}, None, "voetbal-bijhouden-app",
                      status="published")

        assert not _open_kansen(site["id"])

    def test_gedeeld_beginwoord_is_geen_zelfde_onderwerp(self, site):
        """'levensboek' en 'levensverhaal' delen zes letters en zijn écht twee
        onderwerpen. Precies dit paar bepaalt de stam-drempel."""
        assert not quality._same_word("levensboek", "levensverhaal")
        _kans(site["id"], "levensboek maken stappen")
        cp.create_job(site["id"], "Levensverhaal vastleggen: de complete gids",
                      "levensverhaal vastleggen", "waarom", "<p>x</p>", 85,
                      {}, None, "levensverhaal", status="published")

        assert _queries(_open_kansen(site["id"])) == {"levensboek maken stappen"}


class TestEenOnbedektWoord:
    """De docstring van `_CANNIBAL_OVERLAP` beloofde sinds 2 aug 2026 "alle
    inhoudswoorden op één na", maar de code eiste 0,99 — oftewel: geen enkel
    woord onbedekt. Daardoor glipte 'outdoor teambuilding schiphol omgeving'
    langs het live artikel 'Outdoor teambuilding Schiphol regio'.
    """

    def test_synoniem_als_restwoord_is_kannibalisatie(self, site):
        _kans(site["id"], "outdoor teambuilding schiphol omgeving")
        cp.create_job(site["id"], "Outdoor teambuilding Schiphol regio: 7 GPS-tochten",
                      "", "waarom", "<p>x</p>", 85, {}, None, "outdoor-schiphol",
                      status="published")

        weg = _uitgefilterd(site["id"])
        assert weg and weg[0]["filter_reason"] == "kannibaal"

    def test_prijsvraag_als_restwoord_blijft_een_eigen_kans(self):
        """'kosten' verlegt de zoekintentie: die zoeker wil een prijs, niet nóg
        een uitleg. Dit is het onderscheid met 'omgeving' hierboven."""
        artikel = quality.tokens("levensverhaal laten schrijven")
        assert quality._kannibaliseert(
            quality.tokens("levensverhaal laten schrijven online"), artikel)
        assert not quality._kannibaliseert(
            quality.tokens("levensverhaal laten schrijven kosten"), artikel)

    def test_twee_woorden_krijgt_de_uitzondering_niet(self):
        """Bij twee woorden is één onbedekt woord de helft van de vraag."""
        assert not quality._kannibaliseert(
            quality.tokens("organisatiebijdrage meten"), quality.tokens("impact meten"))
        # Volledig gedekt blijft ook bij twee woorden kannibalisatie.
        assert quality._kannibaliseert(
            quality.tokens("kat adopteren"),
            quality.tokens("kat adopteren uit het asiel"))


class TestGscDekking:
    """De gate moet de buitenwereld raadplegen, niet alleen de eigen
    administratie — de les van `afgewezen_maar_live`, hier andersom toegepast.

    `content_jobs` en de sitemap zijn beweringen van het systeem over zichzelf.
    Een pagina die bij Google vertoningen krijgt, bestáát; het zoekwoord waarop
    hij vertoont is het hardste bewijs van waar hij al voor meedoet.
    """

    def test_pagina_die_al_rankt_maakt_er_geen_tweede(self, site, gsc):
        gsc(site["id"], "https://voorbeeld.nl/blog/reminiscentie-in-de-zorg",
            "reminiscentie in de zorg", position=27.5)
        _kans(site["id"], "reminiscentie in de zorg")

        weg = _uitgefilterd(site["id"])
        assert weg and weg[0]["filter_reason"] == "rankt-al"
        # Het getal moet mee: het verschil tussen "optimaliseer die pagina" en
        # "die staat hopeloos ver weg" zit in de positie, niet in het feit.
        assert "27,5" in weg[0]["filter_detail"]
        assert weg[0]["filter_source"] == "gsc"

    def test_ander_zoekwoord_op_dezelfde_site_blijft_een_kans(self, site, gsc):
        gsc(site["id"], "https://voorbeeld.nl/blog/hond-adopteren",
            "hond adopteren asiel", position=12.0)
        _kans(site["id"], "kat herplaatsen particulier")

        assert _queries(_open_kansen(site["id"])) == {"kat herplaatsen particulier"}

    def test_zonder_gsc_historie_blijft_de_gate_werken(self, site):
        """Een verse installatie heeft geen `gsc_history`-rijen. Dan valt de
        gate terug op de tekstbronnen — nooit op een lege lijst."""
        assert quality._gsc_coverage(site["id"]) == []
        _kans(site["id"], "digitale nalatenschap regelen")

        assert len(_open_kansen(site["id"])) == 1

    def test_kapotte_gsc_bron_legt_de_lijst_niet_plat(self, site, monkeypatch):
        """De tabel kán ontbreken. Dan zwijgt deze bron en doen de andere twee
        hun werk — een kansenlijst mag nooit op een SQL-fout stukvallen."""
        class _Stuk:
            def __enter__(self, *a):
                raise RuntimeError("no such table: gsc_history")

            def __exit__(self, *a):
                return False
        monkeypatch.setattr(quality, "get_conn", _Stuk)

        assert quality._gsc_coverage(site["id"]) == []
