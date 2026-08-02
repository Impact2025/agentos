"""Zoekwoord-dedupe: één zoekwoord per site, één artikel.

Achtergrond (23-24 juli 2026): voor het zoekwoord 'beste partners voor
AI-oplossingen in het sociale domein in Nederland?' stond precies één rij in
`opportunities`, maar liepen er twee content-jobs — '9 beste partners voor
AI-oplossingen in het sociale domein in Nederland' (aangemaakt 09:45) en 'Zeven
AI-partners die bewezen hebben in het sociaal domein te werken' (10:22). Beide
zijn op 24 juli goedgekeurd en staan sindsdien live op weareimpact.nl, waar ze
elkaar op hetzelfde zoekwoord kannibaliseren.

De dedupe in `create_job` keek alleen naar (site_id, slug). Een andere titel
geeft een andere slug, dus beide gingen erdoor. `select_topic` zet een kans wél
op 'in_progress', maar dekt daarmee alleen zijn eigen route af: Iris'
content_run en de goal-publisher komen daar niet langs. `create_job` is de enige
trechter die álle routes passeren.
"""
import pytest

from backend.domains.publish import content_pipeline as cp
from backend.domains.seo import sites as sites_service


@pytest.fixture
def site():
    s = sites_service.create_site(
        {"name": "KannibalisatieTest", "base_url": "https://voorbeeld.nl"}
    )
    yield sites_service.get_site(s["id"])
    sites_service.delete_site(s["id"])


def _maak(site_id, titel, keyword, slug, status="pending_review"):
    return cp.create_job(
        site_id, titel, keyword, "waarom", "<h1>x</h1>", 85, {}, None, slug,
        status=status,
    )


class TestZoekwoordNormalisatie:
    @pytest.mark.parametrize("a,b", [
        ("Beste partners voor AI-oplossingen?", "beste partners voor ai-oplossingen"),
        ("  digitale   erfenis  ", "digitale erfenis"),
        ("Levensverhaal vastleggen?", "levensverhaal  vastleggen"),
    ])
    def test_varianten_zijn_hetzelfde_zoekwoord(self, a, b):
        assert cp._keyword_key(a) == cp._keyword_key(b)

    def test_echt_verschillende_zoekwoorden_blijven_verschillend(self):
        assert cp._keyword_key("levensverhaal vastleggen") != cp._keyword_key(
            "levensverhaal laten schrijven kosten"
        )

    def test_leeg_zoekwoord_geeft_lege_sleutel(self):
        assert cp._keyword_key("") == ""
        assert cp._keyword_key(None) == ""


class TestDedupeOpZoekwoord:
    def test_de_echte_ai_partners_situatie(self, site):
        """Het incident van 23 juli, exact nagebouwd."""
        eerste = _maak(
            site["id"],
            "9 beste partners voor AI-oplossingen in het sociale domein in Nederland",
            "beste partners voor ai-oplossingen in het sociale domein in nederland?",
            "9-beste-partners-voor-ai-oplossingen-in-het-sociale-domein-i",
        )
        tweede = _maak(
            site["id"],
            "Zeven AI-partners die bewezen hebben in het sociaal domein te werken",
            "Beste partners voor AI-oplossingen in het sociale domein in Nederland",
            "zeven-ai-partners-die-bewezen-hebben-in-het-sociaal-domein-t",
        )
        assert tweede == eerste, "tweede titel hoort dezelfde job bij te werken"
        assert len(cp.list_jobs(site_id=site["id"])) == 1

        # De rij is bijgewerkt naar de nieuwste titel en slug, niet blijven staan
        # op de oude — anders publiceer je straks onder een slug die niet bij de
        # tekst hoort.
        job = cp.get_job(eerste)
        assert job["title"].startswith("Zeven AI-partners")
        assert job["slug"] == "zeven-ai-partners-die-bewezen-hebben-in-het-sociaal-domein-t"

    def test_slug_dedupe_blijft_werken(self, site):
        eerste = _maak(site["id"], "Titel A", "kw een", "zelfde-slug")
        tweede = _maak(site["id"], "Titel B", "heel ander zoekwoord", "zelfde-slug")
        assert tweede == eerste

    def test_ander_zoekwoord_krijgt_een_eigen_artikel(self, site):
        """Een echte zoekintentie-variant verdient een eigen pagina."""
        eerste = _maak(site["id"], "Levensverhaal vastleggen",
                       "levensverhaal vastleggen", "levensverhaal-vastleggen")
        tweede = _maak(site["id"], "Wat kost een levensverhaal laten schrijven?",
                       "levensverhaal laten schrijven kosten",
                       "levensverhaal-laten-schrijven-kosten")
        assert tweede != eerste
        assert len(cp.list_jobs(site_id=site["id"])) == 2

    def test_andere_site_mag_hetzelfde_zoekwoord(self, site):
        ander = sites_service.create_site(
            {"name": "AndereSite", "base_url": "https://anders.nl"}
        )
        try:
            een = _maak(site["id"], "Titel", "digitale erfenis", "digitale-erfenis")
            twee = _maak(ander["id"], "Titel", "digitale erfenis", "digitale-erfenis")
            assert een != twee
        finally:
            sites_service.delete_site(ander["id"])

    def test_afgewezen_artikel_geeft_het_zoekwoord_vrij(self, site):
        """'rejected' is een bewuste afschrijving — dan mag het opnieuw."""
        eerste = _maak(site["id"], "Titel A", "digitale erfenis", "slug-a",
                       status="rejected")
        tweede = _maak(site["id"], "Titel B", "digitale erfenis", "slug-b")
        assert tweede != eerste

    def test_gepubliceerd_artikel_houdt_het_zoekwoord_bezet(self, site):
        """Precies de fout die live ging: naast een live artikel geen tweede."""
        eerste = _maak(site["id"], "Titel A", "digitale erfenis", "slug-a",
                       status="published")
        tweede = _maak(site["id"], "Titel B", "digitale erfenis", "slug-b")
        assert tweede == eerste
        # Een bestaande publicatie mag niet terugvallen naar pending_review.
        assert cp.get_job(eerste)["status"] == "published"

    def test_leeg_zoekwoord_dedupet_niet(self, site):
        """38 oudere jobs hebben een leeg keyword-veld; die mogen elkaar niet
        opslokken tot één rij."""
        een = _maak(site["id"], "Titel A", "", "slug-a")
        twee = _maak(site["id"], "Titel B", "", "slug-b")
        assert een != twee

    def test_dedupe_uit_maakt_altijd_een_nieuwe_rij(self, site):
        een = _maak(site["id"], "Titel A", "digitale erfenis", "slug-a")
        twee = cp.create_job(
            site["id"], "Titel B", "digitale erfenis", "waarom", "<h1>x</h1>",
            85, {}, None, "slug-b", dedupe=False,
        )
        assert een != twee
