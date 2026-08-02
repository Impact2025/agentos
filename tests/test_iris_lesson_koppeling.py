"""Iris' leerlus: voorspellingen moeten aan lessen gekoppeld raken.

Achtergrond (27 juli 2026): er stonden 51 actieve lessen, maar in totaal waren
er ooit 2 voorspellingen aan een les gekoppeld. Daardoor won of verloor er nooit
een les vertrouwen en stond `confidence` overal nog op de startwaarde 0,50 — de
leerlus was gebouwd, maar draaide leeg.

Oorzaak: `lesson_ids.get(lesson_text, "")` is een exacte stringvergelijking. Die
eist dat het model de lestekst woordelijk herhaalt in het veld `les` van zijn
voorspelling; in de praktijk parafraseert het altijd.
"""
import uuid

import pytest

from backend.domains.iris import predictions, service
from backend.shared.database import get_conn


@pytest.fixture
def les():
    """Een actieve les in de database, zoals een eerdere briefing hem achterliet."""
    lid = str(uuid.uuid4())
    tekst = ("Artikelen met een concrete casestudy halen sneller clicks dan "
             "algemene overzichtsartikelen")
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO iris_lessons (id, lesson, category, source, created_at, "
            "updated_at) VALUES (?, ?, 'content', 'test', '2026-07-20', '2026-07-20')",
            (lid, tekst),
        )
    yield lid, tekst
    with get_conn() as conn:
        conn.execute("DELETE FROM iris_lessons WHERE id = ?", (lid,))


class TestLesMatchen:
    def test_exacte_tekst_matcht(self, les):
        lid, tekst = les
        assert service._match_lesson(tekst.lower(), {tekst.lower(): lid}) == lid

    def test_parafrase_matcht_ook(self, les):
        """Het echte geval: het model herhaalt de les niet woordelijk."""
        lid, _ = les
        parafrase = ("artikelen met concrete casestudy halen sneller clicks dan "
                     "algemene overzichtsartikelen over het onderwerp")
        assert service._match_lesson(parafrase, {}) == lid

    def test_oudere_les_wordt_ook_gevonden(self, les):
        """lesson_ids bevat alleen de lessen van vandaag; Iris verwijst
        regelmatig naar een les van vorige week."""
        lid, tekst = les
        assert service._match_lesson(tekst.lower(), {}) == lid

    def test_ongerelateerde_les_matcht_niet(self, les):
        """Liever geen koppeling dan een verkeerde: een les die krediet krijgt
        voor andermans voorspelling maakt het vertrouwenscijfer waardeloos."""
        assert service._match_lesson(
            "outreach-mails op dinsdagochtend krijgen meer antwoord", {}
        ) == ""

    def test_lege_tekst_geeft_geen_koppeling(self, les):
        assert service._match_lesson("", {}) == ""
        assert service._match_lesson("   ", {}) == ""

    def test_alleen_vulwoorden_koppelt_niet(self, les):
        """Zonder stopwoordfilter matcht 'meer wordt beter' op zo'n beetje alles."""
        assert service._match_lesson("meer wordt beter dus altijd", {}) == ""


class TestOordeelVersusBoekhouding:
    """'Nooit gemeten' is geen 'gemeten zonder uitsluitsel'.

    Op 27 juli stonden 12 uitkomsten als 'unclear' geboekt; 6 daarvan waren puur
    opruimwerk van dubbele voorspellingen. Op één hoop laat dat de leerlus
    slechter lijken dan hij is.
    """

    def test_nauwelijks_bewogen_is_unclear(self):
        status, _ = predictions._judge("clicks", "up", 8.0, 12.0, 8.0)
        assert status == "unclear"

    def test_juiste_kant_zonder_doel_is_unclear(self):
        status, note = predictions._judge("clicks", "up", 8.0, 12.0, 11.0)
        assert status == "unclear"
        assert "juiste kant" in note

    def test_doel_gehaald_is_correct(self):
        status, _ = predictions._judge("clicks", "up", 8.0, 12.0, 13.0)
        assert status == "correct"

    def test_verkeerde_kant_is_wrong(self):
        status, _ = predictions._judge("clicks", "up", 8.0, 12.0, 3.0)
        assert status == "wrong"

    def test_positie_lager_is_beter(self):
        # direction 'up' betekent voor positie: omhoog in de ranglijst = lager getal.
        status, _ = predictions._judge("position", "up", 40.0, 32.0, 30.0)
        assert status == "correct"
        status, _ = predictions._judge("position", "up", 40.0, 32.0, 48.0)
        assert status == "wrong"
