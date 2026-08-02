"""Leadvalidatie: een zoekresultaat is niet vanzelf een organisatie.

Achtergrond (27 juli 2026): van de 165 leads stonden er 100 op 'lost'. `org_name`
werd letterlijk gevuld met de paginatitel uit de zoekprovider, dus de voorraad
bestond voor een groot deel uit artikelen, vacatures en portals — 'Top AI
Consulting Companies in the Netherlands', '[PDF] Haalbaarheidsonderzoek Sociale
Kaart', 'De rol van AI in de gezondheidszorg | Parseur®'.

Elke zulke rij kostte een scrape én een LLM-analyse, en verpestte daarna de
conversiecijfers van de acquisitieformule: die meet dan de kwaliteit van de
zoekresultaten in plaats van de kwaliteit van de verkoop.

De testgevallen hieronder komen uit de echte leads-tabel.
"""
import pytest

from backend.domains.prospecting import validate


class TestArtikelenWordenGeweerd:
    @pytest.mark.parametrize("titel,url", [
        ("Top AI Consulting Companies in the Netherlands", "https://example.com/"),
        ("Best AI Providers in the Netherlands 2025 | Complete Guide | Serviceform",
         "https://serviceform.com/"),
        ("[PDF] Haalbaarheidsonderzoek Sociale Kaart - Open Research Amsterdam",
         "https://openresearch.amsterdam/rapport.pdf"),
        ("De rol van AI in de gezondheidszorg | Parseur®", "https://parseur.com/"),
        ("De opkomst van generatieve AI in de zorg - TeamTelefoon",
         "https://teamtelefoon.nl/blog/generatieve-ai"),
        ("Wat is AI-geletterdheid en waarom is het belangrijk?", "https://example.nl/"),
        ("7 tips voor een succesvolle AI-implementatie", "https://example.nl/"),
        ("AI-Coaching: stilte voor de psychologische storm?", "https://example.nl/"),
        ("Strategie voor het implementeren van AI in je bedrijf en organisatie vandaag",
         "https://example.nl/"),
    ])
    def test_artikel_is_geen_lead(self, titel, url):
        geschikt, reden = validate.looks_like_organisation(titel, url)
        assert not geschikt, f"had geweigerd moeten worden: {titel}"
        assert reden

    @pytest.mark.parametrize("titel,url,snippet", [
        ("Projectleider AI op de poli in Utrecht (UMC Utrecht)",
         "https://medischcontact.nl/vacatures/projectleider", ""),
        ("Servicemanager digitalisering zorg en welzijn - Stichting Sigra",
         "https://boomingjobs.nl/vacature/123", ""),
        ("Directeur zorg a.i. - Leeuwendaal", "https://leeuwendaal.nl/",
         "Voor deze functie zoeken wij een ervaren directeur, 36 fte."),
    ])
    def test_vacature_is_geen_lead(self, titel, url, snippet):
        geschikt, reden = validate.looks_like_organisation(titel, url, snippet)
        assert not geschikt
        assert reden

    @pytest.mark.parametrize("url", [
        "https://nl.wikipedia.org/wiki/Kunstmatige_intelligentie",
        "https://www.linkedin.com/pulse/ai-in-de-zorg",
        "https://www.indeed.nl/bedrijf/acme",
        "https://www.youtube.com/watch?v=abc",
    ])
    def test_portal_is_geen_lead(self, url):
        geschikt, reden = validate.looks_like_organisation("Een prima titel", url)
        assert not geschikt
        assert "portal" in reden or "aggregator" in reden


class TestEchteOrganisatiesKomenErDoor:
    @pytest.mark.parametrize("titel,url", [
        ("AI-Strategie & Implementatie | NEXTRIQ", "https://nextriq.nl/"),
        ("Mensgericht digitaliseren voor social en non-profit - digiraf",
         "https://digiraf.nl/"),
        ("Devoteam Nederland", "https://devoteam.com/nl/"),
        ("Zorggroep Almere", "https://zorggroep-almere.nl/"),
        ("Acme Consultancy B.V.", "https://acme.nl/"),
    ])
    def test_organisatie_wordt_toegelaten(self, titel, url):
        geschikt, reden = validate.looks_like_organisation(titel, url)
        assert geschikt, f"ten onrechte geweigerd ({reden}): {titel}"

    def test_lange_naam_met_rechtsvorm_mag_door(self):
        """Een lange naam is verdacht, tenzij er een juridische vorm in staat."""
        geschikt, _ = validate.looks_like_organisation(
            "Stichting Samenwerkende Zorginstellingen Noord Holland Noord",
            "https://sszn.nl/")
        assert geschikt


class TestNaamOpschonen:
    @pytest.mark.parametrize("titel,url,verwacht", [
        ("AI-Strategie & Implementatie | NEXTRIQ", "https://nextriq.nl/", "NEXTRIQ"),
        ("Mensgericht digitaliseren voor social en non-profit - digiraf",
         "https://digiraf.nl/", "digiraf"),
        ("De rol van AI | Parseur®", "https://parseur.com/", "Parseur"),
        ("Zorggroep Almere", "https://zorggroep-almere.nl/", "Zorggroep Almere"),
    ])
    def test_merknaam_uit_paginatitel(self, titel, url, verwacht):
        assert validate.clean_org_name(titel, url) == verwacht

    def test_lege_titel_valt_terug_op_domein(self):
        assert validate.clean_org_name("", "https://www.digiraf.nl/over-ons") == "digiraf"

    def test_hele_zin_valt_terug_op_domein(self):
        naam = validate.clean_org_name(
            "Wij helpen organisaties in het sociaal domein met slimme digitale oplossingen",
            "https://voorbeeld.nl/")
        assert naam == "voorbeeld"

    def test_home_wordt_overgeslagen_als_merknaam(self):
        assert validate.clean_org_name("Acme Zorg | Home", "https://acme.nl/") == "Acme Zorg"


class TestBruikbaarContact:
    def test_zonder_contact_is_ruis(self):
        assert validate.usable_contact({"org_name": "Acme"}) is None
        assert validate.usable_contact({"email": "", "phone": "", "kvk_number": ""}) is None

    @pytest.mark.parametrize("veld", ["email", "phone", "kvk_number"])
    def test_met_contact_is_voorraad(self, veld):
        assert validate.usable_contact({veld: "iets"}) == veld
