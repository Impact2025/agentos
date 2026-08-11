"""De Mission Radar levert signalen, geen voorraad.

Aanleiding (gemeten 3 aug 2026, vier weken data): de radar had 2.865 signalen
verzameld, waarvan er 2.600 op 'new' stonden. De twaalf best scorende van eind
juli — score 70-80, relevantie 75-90, allemaal automatisch de vault in — waren
op één na geen artikel maar een vacature, een dienstpagina, een
overzichtspagina of ons eigen blog. De relevantie-rechter had gelijk dat ze
over het onderwerp gingen; "gaat hierover" is alleen niet hetzelfde als "hier
kun je iets mee".

Wat deze suite vastlegt:

  * de poort weert op vórm (URL-pad), niet op inhoud — dat is deterministisch
    en blijft dus werken als de gateway plat ligt;
  * hij laat door wat hij niet kan aanwijzen: onbekend is geen ruis;
  * niets verdwijnt stil — een geweerd signaal is opvraagbaar mét de reden;
  * de trend-brug bouwt het zoekwoord uit het signáál, niet uit de watchlist.
"""
import uuid

import pytest

from backend.domains.radar import quality
from backend.domains.seo import trends
from backend.shared.database import get_conn


EIGEN = {"weareimpact.nl", "ictusgo.nl"}


def _sig(url, titel="Een volwaardige artikeltitel over het onderwerp"):
    return {"url": url, "title": titel, "snippet": "x" * 120}


# ── 1. Weert de poort wat aantoonbaar geen signaal is? ─────────────────────

class TestWeertDeRuis:

    @pytest.mark.parametrize("url,reden", [
        # Alle gevallen hieronder zijn echte URL's uit de voorraad van eind juli
        # die met score 70-80 automatisch de vault in gingen.
        ("https://iroko.nl/vacatures/", "vacature"),
        ("https://www.publiekracht.nl/domeinen/digitale-transformatie", "aanbodpagina"),
        ("https://efficienterwerkenprogramma.nl/opleiding/hbo-verandermanagement", "aanbodpagina"),
        ("https://zenexmachina.com/what-we-do/lego-serious-play-workshop", "aanbodpagina"),
        ("https://platform-io.eu/projecten/sociaal-domein/", "aanbodpagina"),
        ("https://haute-equipe.nl/sociaal-domein/", "navigatie"),
        ("https://mintjesenco.nl/lego-serious-play/", "navigatie"),
        ("https://www.dierenbescherming.nl/", "navigatie"),
        ("https://en.wikipedia.org/wiki/TNO", "naslagwerk"),
    ])
    def test_bekende_ruis_wordt_geweerd(self, url, reden):
        oordeel = quality.assess(_sig(url), eigen=EIGEN)
        assert oordeel["filter_reason"] == reden, oordeel
        assert oordeel["filter_detail"], "een oordeel zonder bewijs is niet te controleren"

    def test_eigen_blog_is_nooit_een_signaal(self):
        """Dat de radar ons eigen artikel terugvindt, zegt alleen dat Google het
        heeft geïndexeerd.

        Deze toets stond bijna andersom in de code: de publicatie-marker '/blog/'
        werd éérst gecontroleerd, waardoor ons eigen blogartikel als trend door
        de poort glipte — een stuk dat dit systeem zelf had gepubliceerd.
        """
        oordeel = quality.assess(
            _sig("https://weareimpact.nl/blog/digitale-transformatie-sociaal-domein"),
            eigen=EIGEN)
        assert oordeel["filter_reason"] == "eigen-site"

    def test_titelloze_scrape_is_geen_signaal(self):
        """268 van 1.782 signalen heetten letterlijk 'Link to reddit.com'."""
        oordeel = quality.assess(
            {"url": "https://www.reddit.com/r/nederlands/comments/1eki3kc/iets",
             "title": "Link to reddit.com", "snippet": ""}, eigen=EIGEN)
        assert oordeel["filter_reason"] == "geen-inhoud"


# ── 2. Laat hij door wat hij niet kan aanwijzen? ───────────────────────────

class TestLaatDoorWatEchtIs:

    @pytest.mark.parametrize("url", [
        "https://www.vilans.nl/actueel/verhalen/5-belangrijkste-vormen-ai-zorg",
        "https://www.aivoorbedrijf.nl/dossiers/ai-strategie/change-management-ai/",
        "https://www.reddit.com/r/KPMG/comments/184juf6/move_to_netherlands/",
        "https://example.nl/2026/08/hoe-wij-de-wijk-veranderden",
        "https://onbekendecms.nl/iets/diep/weggestopt-artikel-over-van-alles",
    ])
    def test_publicaties_gaan_door(self, url):
        assert quality.assess(_sig(url), eigen=EIGEN)["filter_reason"] is None

    def test_artikel_over_diensten_blijft_een_artikel(self):
        """'/blog/onze-nieuwe-diensten' is een publicatie, geen dienstpagina.

        Daarom staat de publicatie-marker vóór de aanbod-filters: anders
        sneuvelt een artikel op een woord dat in zijn onderwerp zit.
        """
        oordeel = quality.assess(
            _sig("https://concurrent.nl/blog/onze-diensten-in-het-sociaal-domein"),
            eigen=EIGEN)
        assert oordeel["filter_reason"] is None

    def test_slug_met_een_toevallig_woord_sneuvelt_niet(self):
        """De laatste padsegment is proza, geen rubrieksnaam.

        Woord-voor-woord matchen op de slug zou 'hoe-je-echt-contact-maakt'
        weren op het woord 'contact' — een terecht signaal weggegooid op een
        toevalligheid.
        """
        oordeel = quality.assess(
            _sig("https://vakblad.nl/artikelen/hoe-je-echt-contact-maakt-met-je-team"),
            eigen=EIGEN)
        assert oordeel["filter_reason"] is None


# ── 3. Verdwijnt er niets stil? ────────────────────────────────────────────

def test_partition_splitst_en_motiveert():
    door, weg = quality.partition([
        _sig("https://vakblad.nl/blog/een-echt-artikel-met-inhoud"),
        _sig("https://bedrijf.nl/vacatures/consultant"),
    ], eigen=EIGEN)
    assert len(door) == 1 and len(weg) == 1
    assert weg[0]["filter_reason"] == "vacature"
    assert weg[0]["filter_label"], "de UI toont het label, niet de sleutel"
    assert door[0]["filter_reason"] is None


# ── 4. De trend-brug ───────────────────────────────────────────────────────

class TestTrendBrug:

    @pytest.mark.parametrize("titel,verwacht", [
        ("5 belangrijkste vormen van AI in de zorg - Vilans",
         "5 belangrijkste vormen van AI in de zorg"),
        ("Adviesbureau Sociaal Domein | Publieke Sector | Haute Equipe",
         "Adviesbureau Sociaal Domein | Publieke Sector"),
        ("Ik wil vrijwilligerswerk doen bij een dierenasiel, heb ik daar enige ...",
         "Ik wil vrijwilligerswerk doen bij een dierenasiel, heb ik daar enige"),
    ])
    def test_merkstaart_en_afkapping_gaan_eraf(self, titel, verwacht):
        """De naam van een concurrent hoort niet ons zoekwoord te worden."""
        assert trends._signal_query({"title": titel, "keyword": "x"}) == verwacht

    def test_zoekwoord_komt_uit_het_signaal_niet_uit_de_watchlist(self):
        """De zwaarste fout van de radar, in één regel.

        `_signal_query` gaf `sig['keyword']` terug — de regel die Vincent zelf in
        de watchlist had gezet. Alle 38 kansen die de brug ooit maakte waren
        daardoor letterlijk een watchlist-regel, en omdat de dedupe op
        querytekst liep was elk woord na één conversie voor altijd verbruikt.
        Sinds 27 juli leverde de brug niets meer, en niets meldde dat.
        """
        sig = {"keyword": "hond adopteren",
               "title": "Hoe bereid je je voor op het adopteren van een asieldier?"}
        query = trends._signal_query(sig)
        assert query != sig["keyword"]
        assert "asieldier" in query

    def test_titelloze_bron_levert_geen_zoekwoord(self):
        assert trends._signal_query({"title": "Link to reddit.com"}) == ""

    def test_te_vage_titel_wordt_geweigerd(self):
        assert trends._bruikbare_query("Sociaal domein") is True
        assert trends._bruikbare_query("Nieuws") is False


# ── 5. De invarianten ──────────────────────────────────────────────────────

class TestInvarianten:

    def test_dode_bron_wordt_gemeld(self):
        from backend.domains.iris import integrity as ig
        from backend.domains.radar.models import ensure_schema
        ensure_schema()
        wid = f"w-{uuid.uuid4().hex[:8]}"
        with get_conn() as c:
            c.execute(
                "INSERT INTO radar_watchlist (id, project, label, type, value, active, "
                "created_at, scan_count, signal_count) "
                "VALUES (?, 'testproject', 'Dode feed', 'rss', ?, 1, datetime('now'), 9, 0)",
                (wid, f"https://dood.test/{wid}.xml"))
        try:
            b = [x for x in ig._check_radar_watch_dood() if wid in x.subject or "Dode feed" in x.detail]
            assert len(b) == 1
            assert "9×" in b[0].detail
        finally:
            with get_conn() as c:
                c.execute("DELETE FROM radar_watchlist WHERE id = ?", (wid,))

    def test_verse_bron_krijgt_het_voordeel_van_de_twijfel(self):
        """Twee scans zonder opbrengst is een momentopname, geen uitspraak."""
        from backend.domains.iris import integrity as ig
        from backend.domains.radar.models import ensure_schema
        ensure_schema()
        wid = f"w-{uuid.uuid4().hex[:8]}"
        with get_conn() as c:
            c.execute(
                "INSERT INTO radar_watchlist (id, project, label, type, value, active, "
                "created_at, scan_count, signal_count) "
                "VALUES (?, 'testproject', 'Verse feed', 'rss', ?, 1, datetime('now'), 2, 0)",
                (wid, f"https://vers.test/{wid}.xml"))
        try:
            assert [x for x in ig._check_radar_watch_dood() if "Verse feed" in x.detail] == []
        finally:
            with get_conn() as c:
                c.execute("DELETE FROM radar_watchlist WHERE id = ?", (wid,))
