"""Het dashboard mag niets beweren dat het niet waarmaakt.

Aanleiding (2 aug 2026): het WeAreImpact-dashboard was op vier punten
misleidend, en alle vier waren ze zelfstandig te verhelpen:

  1. "Gemiddelde positie 18,9 (-4,2)" las als vooruitgang, terwijl de laatste
     zeven GSC-dagen op 22,5 stonden en de klikken van 7 naar 3 zakten. Het
     28-daags aggregaat verbergt de verse terugval.
  2. Onder de diagnose "optimaliseer titel en meta description" stond een knop
     "Artikel schrijven" — een tweede pagina voor een zoekwoord waar er al één
     voor rankte, dus kannibalisatie als beloning voor het opvolgen van je
     eigen dashboard.
  3. De kansenlijst zette een bedacht zoekwoord (0 impressies, vaste score 60)
     boven een gemeten kans met 36 impressies op positie 13,8 (score 15). Twee
     onvergelijkbare schalen in één ORDER BY.
  4. "Beste volgende stap: schrijf 11 artikelen" terwijl er 53 concepten op
     goedkeuring wachtten.

Elke test hieronder pint er één vast.
"""
import pytest

from backend.domains.seo import potential
from backend.domains.seo import sites as sites_service
from backend.shared.database import get_conn


# ── 3. De kansen-score meet nu verwachte klikwinst ──────────────────────────

def test_gemeten_vraag_verslaat_speculatie_ongeacht_opgeslagen_score():
    """Een cold-start-gok met een hoge legacy-score mag nooit boven een
    gemeten kans staan. Dit was de inversie: nictiz.nl (0 impressies) op 67,
    'programma manager digitale transformatie' (36 impressies, pos 13,8) op 15."""
    kansen = [
        {"query": "nictiz.nl", "impressions": 0, "position": 0, "clicks": 0,
         "opportunity_score": 67.0},
        {"query": "programma manager digitale transformatie", "impressions": 36,
         "position": 13.8, "clicks": 0, "opportunity_score": 15.2},
    ]
    potential.annotate(kansen)
    assert kansen[0]["query"] == "programma manager digitale transformatie"
    assert kansen[0]["demand"] == "gemeten"
    assert kansen[1]["demand"] == "speculatief"


def test_speculatieve_kans_belooft_niets():
    """Zonder impressies is elke opbrengstvoorspelling verzonnen. Dan moet er
    None staan — niet 0, want 0 is een oordeel ('levert niets op') en dat
    weten we juist niet."""
    kans = {"query": "iets bedachts", "impressions": 0, "position": 0, "clicks": 0}
    assert potential.score(kans) is None
    assert "Geen gemeten vraag" in potential.describe(kans)


def test_klikwinst_is_uitgedrukt_in_klikken():
    """De score heeft een eenheid. 36 impressies van positie 13,8 naar 3 is
    het CTR-verschil tussen die posities, niet een abstract getal."""
    winst = potential.uplift_clicks(impressions=36, position=13.8)
    verwacht = 36 * (potential.expected_ctr(3.0) - potential.expected_ctr(13.8)) / 100
    assert winst == pytest.approx(round(verwacht, 1))
    assert 1 < winst < 10  # ordegrootte: een handvol klikken, geen honderden


def test_te_weinig_impressies_is_geen_meting():
    """Onder de drempel verschuift één toevallige klik de CTR met tientallen
    procenten. Dat is ruis, geen vraag."""
    assert potential.uplift_clicks(impressions=3, position=8.0) is None
    assert potential.is_measured({"impressions": 3, "position": 8.0}) is False


def test_benchmark_komt_uit_de_optimizer():
    """Eén benchmark voor het hele systeem. Twee zou betekenen dat het
    dashboard en de optimizer elkaar over dezelfde pagina tegenspreken."""
    from backend.domains.seo.optimizer import _expected_ctr
    for pos in (1, 3, 7, 12, 18, 45):
        assert potential.expected_ctr(pos) == _expected_ctr(pos)


# ── 2. De knop hoort bij de diagnose ────────────────────────────────────────

def test_rankende_pagina_krijgt_optimaliseer_knop():
    """De precieze situatie van 2 aug: 'code sociaal ondernemen', 29 impressies,
    0 klikken, positie 18 — met een pagina die er al voor rankt."""
    from backend.domains.projects.router import zero_click_advice
    d = zero_click_advice("code sociaal ondernemen", 18.0, 29, has_ranking_page=True)
    assert d["action"].startswith("optimize_page:")
    assert d["action_label"] == "Optimaliseer pagina"
    assert "snippet-probleem" in d["tekst"]
    assert "schrijf" not in d["tekst"].lower()


def test_zonder_rankende_pagina_mag_je_schrijven():
    from backend.domains.projects.router import zero_click_advice
    d = zero_click_advice("nieuw onderwerp", 15.0, 40, has_ranking_page=False)
    assert d["action"].startswith("write_article:")
    assert d["action_label"] == "Artikel schrijven"


def test_ver_buiten_klikbereik_is_een_rankingprobleem():
    """Op positie 45 repareert geen enkele meta description iets — dan is
    'optimaliseer de snippet' het verkeerde advies, ook mét rankende pagina."""
    from backend.domains.projects.router import zero_click_advice
    d = zero_click_advice("ver weg", 45.0, 80, has_ranking_page=True)
    assert not d["action"].startswith("optimize_page:")
    assert "te ver weg" in d["tekst"]


def test_ver_weg_maar_er_staat_al_een_pagina_schrijft_geen_tweede():
    """16 aug 2026. De derde tak keek helemaal niet naar `has_ranking_page` en
    beweerde onvoorwaardelijk "er is nog geen pagina die hierop mikt". Gemeten
    op WeAreImpact: dat stond op het scherm over 'impact strategy' (pos 78,2)
    terwijl /ai-strategie-consultant er met 45 impressies op rankte. De knop
    eronder zou een tweede pagina hebben geschreven — kannibalisatie."""
    from backend.domains.projects.router import zero_click_advice
    d = zero_click_advice("impact strategy", 78.2, 22, has_ranking_page=True,
                          page_url="https://weareimpact.nl/ai-strategie-consultant")
    assert not d["action"].startswith("write_article:")
    assert "nog geen pagina" not in d["tekst"]
    assert "ai-strategie-consultant" in d["tekst"]


def test_ver_weg_zonder_pagina_mag_wel_schrijven():
    """De keerzijde: zonder rankende pagina blijft schrijven het eerlijkste
    advies dat de data toelaat, ook ver buiten klikbereik."""
    from backend.domains.projects.router import zero_click_advice
    d = zero_click_advice("nieuw ding", 78.2, 22, has_ranking_page=False)
    assert d["action"].startswith("write_article:")


def test_geen_enkele_tak_belooft_een_lege_serp_bij_een_rankende_pagina():
    """De generieke regressie op deze klasse fout: zolang er een pagina rankt,
    mag geen enkele tak beweren dat die er niet is."""
    from backend.domains.projects.router import zero_click_advice
    for pos in (3.0, 12.0, 20.0, 25.0, 45.0, 78.2):
        d = zero_click_advice("q", pos, 50, has_ranking_page=True)
        assert "geen pagina" not in d["tekst"]
        assert not d["action"].startswith("write_article:")


def test_diagnose_en_knop_spreken_elkaar_nooit_tegen():
    """De regressietest op de oorspronkelijke fout: als de tekst over de
    snippet gaat, mag de knop nooit een artikel schrijven."""
    from backend.domains.projects.router import zero_click_advice
    for pos in (3.0, 12.0, 18.0, 20.0, 25.0, 45.0):
        for rankt in (True, False):
            d = zero_click_advice("q", pos, 50, has_ranking_page=rankt)
            zegt_optimaliseren = "snippet-probleem" in d["tekst"]
            doet_optimaliseren = d["action"].startswith("optimize_page:")
            assert zegt_optimaliseren == doet_optimaliseren


# ── 4. Doorvoer telt mee in het advies ──────────────────────────────────────

@pytest.fixture
def site():
    s = sites_service.create_site(
        {"name": "DashboardTest", "base_url": "https://dashboardtest.nl"})
    yield sites_service.get_site(s["id"])
    with get_conn() as conn:
        conn.execute("DELETE FROM content_jobs WHERE site_id = ?", (s["id"],))
    sites_service.delete_site(s["id"])


def _job(site_id: str, status: str) -> None:
    import uuid
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO content_jobs (id, site_id, title, keyword, status, created_at) "
            "VALUES (?, ?, ?, ?, ?, datetime('now'))",
            (str(uuid.uuid4()), site_id, "Concept", "kw", status),
        )


def test_wachtrijdruk_telt_alleen_wat_op_een_mens_wacht(site):
    from backend.domains.projects.router import _queue_pressure
    for _ in range(3):
        _job(site["id"], "pending_review")
    _job(site["id"], "needs_work")
    _job(site["id"], "published")   # klaar — telt niet als druk
    _job(site["id"], "rejected")    # afgewezen — telt niet als druk
    druk = _queue_pressure(site["id"])
    assert druk["pending_review"] == 3
    assert druk["needs_work"] == 1
    assert druk["totaal"] == 4


def test_iris_schrijft_niet_bij_op_een_volle_wachtrij(site, monkeypatch):
    """De harde regel achter 'elke actie levert iets op': een artikel dat op
    een stapel van tien belandt, levert geen klik op maar kost wel geld."""
    import asyncio
    from backend.domains.iris import actions

    for _ in range(actions.QUEUE_JAM):
        _job(site["id"], "pending_review")

    gedraaid = []
    monkeypatch.setattr(
        "backend.domains.publish.content_pipeline.run_content_batch",
        lambda *a, **k: gedraaid.append(1) or [],
    )
    uitkomst = asyncio.run(actions.content_run(site["name"], 1, "test"))
    assert gedraaid == [], "contentmotor had niet mogen draaien"
    assert "NIET gestart" in uitkomst
    assert str(actions.QUEUE_JAM) in uitkomst


def test_iris_meldt_doorvoer_als_eigen_knelpunt():
    """Bij een grote stapel is 'keur goed' te zwak: Iris moet expliciet lezen
    dat méér produceren schadelijk is, anders blijft ze content_run
    voorstellen."""
    from backend.domains.iris.metrics import bottlenecks
    snap = {"projects": [], "global": {"pending_review_total": 53}}
    issues = {b["issue"] for b in bottlenecks(snap)}
    assert "doorvoer" in issues
    doorvoer = next(b for b in bottlenecks(snap) if b["issue"] == "doorvoer")
    assert "content_run" in doorvoer["waarom"]


# ── 5. "Beste volgende stap" mag niet gokken of een pagina al rankt ─────────
# Aanleiding (13 aug 2026, Bewaard voor Jou): _gsc_facts haalde de
# pagina/zoekwoord-dimensie op met een eigen live GSC-call en een stille
# `except Exception: page_queries = []` eronder. Toen die call leeg terugkwam,
# adviseerde het dashboard "schrijf een artikel" voor 'voorbeeld korte
# biografie schrijven' terwijl /kennisbank/memoires-schrijven-voorbeelden-en-
# tips er al 69 impressies/dag op haalde (positie 17,8) — precies de fout die
# `zero_click_advice` claimt te voorkomen. De bron is nu `gsc_history`,
# dezelfde tabel als de Kansen-lijst gebruikt voor 'rankt-al'.

def _gsc_page_row(site_id: str, url: str, top_query: str,
                  impressions: int, position: float) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO gsc_history (id, site_id, scope, page_url, date, "
            "clicks, impressions, ctr, position, top_query, created_at) "
            "VALUES (?, ?, 'page', ?, date('now'), 1, ?, 1.0, ?, ?, datetime('now'))",
            (f"test-{url}", site_id, url, impressions, position, top_query),
        )


def test_ranking_page_komt_uit_gsc_history_niet_uit_een_losse_live_call(site):
    from backend.domains.projects.router import _ranking_page_from_history

    _gsc_page_row(site["id"], "https://dashboardtest.nl/artikel-a",
                  "voorbeeld korte biografie schrijven", 69, 17.8)
    ranking = _ranking_page_from_history(site["id"])
    assert "voorbeeld korte biografie schrijven" in ranking
    assert ranking["voorbeeld korte biografie schrijven"]["url"] == \
        "https://dashboardtest.nl/artikel-a"
    with get_conn() as conn:
        conn.execute("DELETE FROM gsc_history WHERE site_id = ?", (site["id"],))


def test_ranking_page_overleeft_een_kapotte_live_gsc_call(site, monkeypatch):
    """De oude fout: een falende live GSC-call degradeerde stil naar 'geen
    pagina rankt hier', wat het advies liet zeggen 'schrijf er een' voor een
    zoekwoord dat allang een pagina had. gsc_history is lokaal en hoeft de
    live API niet aan te roepen, dus die faalmodus kan hier niet meer."""
    from backend.domains.seo import gsc as gsc_module

    def kapot(*a, **k):
        raise RuntimeError("GSC quota exceeded")

    monkeypatch.setattr(gsc_module, "fetch_page_query_performance", kapot)

    _gsc_page_row(site["id"], "https://dashboardtest.nl/artikel-b",
                  "digitale erfenis", 40, 9.0)
    from backend.domains.projects.router import _ranking_page_from_history
    ranking = _ranking_page_from_history(site["id"])
    assert "digitale erfenis" in ranking
    with get_conn() as conn:
        conn.execute("DELETE FROM gsc_history WHERE site_id = ?", (site["id"],))
