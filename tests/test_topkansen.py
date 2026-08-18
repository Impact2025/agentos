"""Weinig kansen, maar échte kansen (16 aug 2026).

Aanleiding, gemeten op WeAreImpact: het Kansen-paneel bood 24 openstaande
kansen aan waarvan er **nul** één enkele impressie in Search Console had. De
Demand Engine leverde voor deze site geen gemeten vraag, dus was de hele
voorraad gevuld door de trend-brug — met koppen van andermans nieuwsberichten
('Inspiratiebijeenkomst Hybride Zorg en AI in de ggz', 'Programma MentalAIde
versnelt AI-gedreven innovaties voor de GGZ'). Eronder stond de knop "Schrijf 22
kansen".

Twee mechanismen repareren dat, en ze doen bewust verschillend werk:

  * `_lijkt_op_titel` beoordeelt de vórm — een kop herken je aan hoofdletters
    midden in de regel en aan een afsluitende punt. Deterministisch, geen LLM.
  * `_cap_speculatief` beoordeelt het aántal. Of een gok goed is kun je vooraf
    niet aan de tekst zien — dat is wat 'speculatief' betekent — maar je kunt
    wel bepalen hoeveel gokken je tegelijk betaalt.

Het tweede is het echte werk. Het eerste is een heuristiek en mag falen; de cap
niet.
"""
import pytest

from backend.domains.seo import opportunity_quality as oq


# ── Vorm: een kop is geen zoekopdracht ──────────────────────────────────────

@pytest.mark.parametrize("query", [
    # Hoofdletters midden in de regel: eigennamen van programma's en dossiers.
    "Online Dossier Digitale transformatie sociaal domein",
    "Programma MentalAIde versnelt AI-gedreven innovaties voor de GGZ",
    "Inspiratiebijeenkomst Hybride Zorg en AI in de ggz",
    # Afsluitende punt: overgenomen zin.
    "Datagedreven werken in zorg en welzijn.",
])
def test_koppen_van_nieuwsberichten_zijn_geen_zoekwoord(query):
    assert oq._lijkt_op_titel(query), f"{query!r} had als kop herkend moeten worden"


@pytest.mark.parametrize("query", [
    # Eén eigennaam is een plaatsnaam, geen kop — en dit is juist de longtail
    # waar het om gaat.
    "interim consultant sociaal domein Amsterdam",
    # 7 woorden: een longtail-vraag is lang. Daarom is de woordgrens bewust op
    # 8 blijven staan in plaats van mee te zakken naar 6.
    "hoe maak je een team digitaal handiger",
    "AI scan welzijnsorganisatie kosten baten",
    "welzijnsmedewerker sterker maken met technologie",
    # Afkortingen in hoofdletters mogen: die typen mensen echt zo.
    "AI in de GGZ",
    # Een vraagteken typen mensen wél.
    "wat kost een AI-scan?",
])
def test_echte_zoekopdrachten_blijven_staan(query):
    assert oq._lijkt_op_titel(query) is None, f"{query!r} had door moeten komen"


def test_een_hoofdletter_aan_het_begin_is_geen_kop():
    """De cold-start-generator kapitaliseert zelf ook; het eerste woord mag
    daarom nooit meetellen."""
    assert oq._lijkt_op_titel("Levensverhaal laten schrijven") is None


# ── Aantal: de cap op giswerk ───────────────────────────────────────────────

def _kans(query, *, impressions=0, position=0.0, score=60.0, status="new"):
    return {"query": query, "impressions": impressions, "position": position,
            "clicks": 0, "opportunity_score": score, "status": status}


def test_hooguit_drie_speculatieve_kansen_worden_aangeboden():
    kansen = [_kans(f"gok nummer {i}", score=100 - i) for i in range(10)]
    oq._cap_speculatief(oq_annotate_zonder_site(kansen))
    door = [k for k in kansen if not k.get("filter_reason")]
    assert len(door) == oq._TOP_SPECULATIEVE_KANSEN
    # En de bovenste drie zijn de best scorende, niet de eerste drie uit de rij.
    assert {k["query"] for k in door} == {"gok nummer 0", "gok nummer 1", "gok nummer 2"}


def test_gemeten_vraag_wordt_nooit_afgekapt():
    """De cap bijt op de overloop van giswerk. Gemeten kansen dragen bewijs en
    zijn schaars — die afkappen omdat er toevallig zes zijn, treft precies de
    enige categorie die zich heeft bewezen."""
    kansen = [_kans(f"gemeten {i}", impressions=200 + i, position=8.0) for i in range(6)]
    oq._cap_speculatief(oq_annotate_zonder_site(kansen))
    assert all(not k.get("filter_reason") for k in kansen)


def test_de_cap_legt_geen_tweede_reden_over_een_bestaande():
    """Een kans die al is verklaard ('kannibaal') houdt die verklaring — er een
    tweede overheen leggen maakt het bewijs onleesbaar."""
    kansen = [_kans(f"gok {i}", score=100 - i) for i in range(6)]
    kansen[0]["filter_reason"] = "kannibaal"
    kansen[0]["filter_label"] = "Kannibaliseert bestaande content"
    oq._cap_speculatief(oq_annotate_zonder_site(kansen))
    assert kansen[0]["filter_reason"] == "kannibaal"
    # De cap telt hem ook niet mee als een van de drie aangeboden kansen.
    door = [k for k in kansen if not k.get("filter_reason")]
    assert len(door) == oq._TOP_SPECULATIEVE_KANSEN


def test_lopend_werk_valt_buiten_de_cap():
    """De cap gaat over aanbod. Wat al in behandeling is, is geen aanbod."""
    kansen = [_kans(f"loopt {i}", status="in_progress") for i in range(8)]
    oq._cap_speculatief(oq_annotate_zonder_site(kansen))
    assert all(not k.get("filter_reason") for k in kansen)


def test_afgekapte_kans_draagt_zijn_bewijs():
    """Niets verdwijnt stil: de reden moet leesbaar zijn en het aantal noemen,
    want dat pad ('Toch oppakken') is hier load-bearing."""
    kansen = [_kans(f"gok {i}", score=100 - i) for i in range(6)]
    oq._cap_speculatief(oq_annotate_zonder_site(kansen))
    weg = [k for k in kansen if k.get("filter_reason") == "geen-topkans"]
    assert weg, "er had iets afgekapt moeten worden"
    for k in weg:
        assert k["filter_label"] == oq.REASON_LABELS["geen-topkans"]
        assert str(oq._TOP_SPECULATIEVE_KANSEN) in k["filter_detail"]


def oq_annotate_zonder_site(kansen):
    """Alleen de potential-annotatie + sortering, zonder site-dekking op te
    halen: `_cap_speculatief` verwacht een lijst die al gesorteerd is en waarop
    `demand` staat."""
    from backend.domains.seo import potential
    return potential.annotate(kansen)


# ── De suggestie-engine mag niet omvallen op een pijler zonder agent ────────

def test_pijler_zonder_score_laat_de_suggestie_engine_niet_omvallen(monkeypatch):
    """16 aug 2026: `metrics.project_scores` kreeg een vijfde pijler `geo` die
    op een site zonder GEO-scan `score: None` draagt. `.get("score", 0)` vangt
    een ontbrekende sleutel af, geen aanwezige None — de sortering viel om met
    "'<' not supported between 'NoneType' and 'int'" en nam de scheduler-job
    iris_auto_deploy een etmaal mee."""
    from backend.domains.agentctl import suggest as s
    monkeypatch.setattr(s.iris_metrics, "project_scores", lambda: [{
        "project": "Testsite", "grade": 4.0,
        "pillars": {
            "content": {"score": 3.0}, "seo": {"score": 12.0},
            "uitvoering": {"score": 18.0}, "hygiene": {"score": 20.0},
            "geo": {"score": None},
        },
    }])
    out = s.suggest()
    assert not out.get("error")
    assert out["count"] == 1
    assert out["suggestions"][0]["pillar_key"] == "content"


def test_een_lage_geo_score_kaapt_de_suggestie_niet(monkeypatch):
    """De stille helft van dezelfde fout: `geo` staat op schaal 0-100 tussen
    pijlers van 0-25. Een site met GEO 8 zou 'geo' als zwakste aanwijzen, in
    `_PILLAR_AGENT` niets vinden en via `continue` géén suggestie opleveren —
    zonder dat er iets gooit."""
    from backend.domains.agentctl import suggest as s
    monkeypatch.setattr(s.iris_metrics, "project_scores", lambda: [{
        "project": "Testsite", "grade": 4.0,
        "pillars": {
            "content": {"score": 9.0}, "seo": {"score": 12.0},
            "uitvoering": {"score": 18.0}, "hygiene": {"score": 20.0},
            "geo": {"score": 8},
        },
    }])
    out = s.suggest()
    assert out["count"] == 1
    assert out["suggestions"][0]["pillar_key"] == "content"


def test_elke_pijler_heeft_een_agent_of_is_expliciet_informatief():
    """De invariant in codevorm: een nieuwe pijler moet een besluit afdwingen,
    niet stil gedrag veranderen."""
    from backend.domains.agentctl.suggest import _PILLAR_AGENT, _INFORMATIEVE_PIJLERS
    assert not (set(_PILLAR_AGENT) & set(_INFORMATIEVE_PIJLERS)), \
        "een pijler kan niet tegelijk werk en louter inzicht zijn"
