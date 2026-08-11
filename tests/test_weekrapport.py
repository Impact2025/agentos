"""Het weekrapport moet het systeem aansturen, niet alleen de mailbox vullen.

Aanleiding (4 aug 2026): het weekrapport berekende elke maandag per project de
zoekprestaties over 28 dagen tegen de 28 daarvóór, inclusief quick wins,
CTR-gaten en dalers — en stuurde dat naar de mail, naar Obsidian en naar een
chat-sessie. Drie plekken waar alleen een mens kijkt. Geen agent kon het lezen,
dus stuurde de rijkste analyse van het systeem nul beslissingen aan en leerde
Iris er niets van. De job meldde ondertussen elke week netjes 'ok'.

Wat deze tests vastleggen:

  * de bevindingen worden vastgelegd (en een herrun verdubbelt ze niet);
  * de trage horizon (28 vs. 28) staat náást de snelle (7 vs. 7) en vervangt
    hem niet — verschil tussen beide is een tijdschaal, geen tegenspraak;
  * "geen weekrapport" leest nooit als "een rustige week";
  * een structurele daling levert een knelpunt op mét knop, behalve wanneer de
    Wachtrij verstopt zit — dan is meer produceren schadelijk;
  * een kans die weken blijft liggen wordt gemeld: dat is de enige manier om te
    zien dat het rapport iets verandert.
"""
import json

import pytest

from backend.domains.analytics import insights
from backend.shared.database import get_conn


@pytest.fixture(autouse=True)
def _schoon():
    with get_conn() as conn:
        conn.execute("DELETE FROM weekly_insights")
    yield
    with get_conn() as conn:
        conn.execute("DELETE FROM weekly_insights")


def _analyse(site_id="site-1", naam="WeAreImpact", clicks=100, impressies=5000,
             positie=12.0, prev_clicks=150, prev_impressies=6000, prev_positie=9.0,
             quick_wins=None):
    return {
        "site_id": site_id, "name": naam, "property": f"sc-domain:{site_id}",
        "aggregate": {"clicks": clicks, "impressions": impressies,
                      "ctr": 2.0, "position": positie},
        "comparison": {
            "clicks": {"cur": clicks, "prev": prev_clicks},
            "impressions": {"cur": impressies, "prev": prev_impressies},
            "position": {"cur": positie, "prev": prev_positie},
        },
        "quick_wins": quick_wins if quick_wins is not None else [
            {"query": "ai transformatie", "position": 8.4, "impressions": 320, "clicks": 4}],
        "ctr_fix": [{"query": "digitale versnelling", "impressions": 900, "ctr": 0.4}],
        "risers": [], "fallers": [{"query": "ai partner", "pos_delta": -3.2}],
    }


def test_bevindingen_worden_vastgelegd_en_een_herrun_verdubbelt_niet():
    assert insights.store_week([_analyse()], "2026-W31") == 1
    assert insights.store_week([_analyse(clicks=110)], "2026-W31") == 1
    rijen = insights.portfolio("2026-W31")
    assert len(rijen) == 1
    assert rijen[0]["clicks"] == 110          # de herrun overschrijft, hij stapelt niet
    assert rijen[0]["quick_wins"][0]["query"] == "ai transformatie"


def test_analyse_zonder_site_id_wordt_niet_bewaard():
    """Zonder stabiele sleutel is 'dezelfde kans als vorige week' niet te
    bepalen, en dan is opslaan erger dan overslaan: het maakt van elke week een
    nieuwe kans en van de levensloop een leugen."""
    zonder = _analyse()
    zonder["site_id"] = ""
    assert insights.store_week([zonder], "2026-W31") == 0
    assert insights.portfolio("2026-W31") == []


def test_geen_weekrapport_is_geen_rustige_week():
    s = insights.summary()
    assert s["state"] == "geen"
    blok = insights.prompt_block()
    assert "nog nooit" in blok
    # Het blok mag Iris nooit de indruk geven dat er niets aan de hand is.
    assert "geen enkele conclusie" in blok


def test_deltas_rekenen_positie_als_winst_bij_stijging():
    insights.store_week([_analyse(positie=9.0, prev_positie=12.0,
                                  clicks=200, prev_clicks=100)], "2026-W31")
    r = insights.portfolio()[0]
    assert r["clicks_pct"] == 100.0
    assert r["position_delta"] == 3.0     # van 12 naar 9 = drie plaatsen winst


def test_structurele_daling_vergt_volume_en_positie():
    """Eén signaal is niet genoeg. Minder klikken kan seizoen zijn; een lagere
    gemiddelde positie kan een nieuw zoekwoord met veel impressies zijn."""
    alleen_volume = _analyse(site_id="s-a", naam="AlleenVolume",
                             clicks=50, prev_clicks=150,
                             positie=9.0, prev_positie=9.2)
    beide = _analyse(site_id="s-b", naam="Beide",
                     clicks=50, prev_clicks=150, positie=14.0, prev_positie=9.0)
    insights.store_week([alleen_volume, beide], "2026-W31")
    namen = [d["project"] for d in insights.structural_decliners()]
    assert namen == ["Beide"]


def test_zonder_vergelijkingsperiode_geen_oordeel():
    """Een verse site heeft geen vorige periode. 'Geen data' mag nooit als
    'gedaald' worden gelezen — dat is precies hoe een nieuw project een
    interventie krijgt die het niet nodig heeft."""
    vers = _analyse(prev_clicks=0, prev_impressies=0, prev_positie=0)
    insights.store_week([vers], "2026-W31")
    assert insights.structural_decliners() == []
    assert insights.portfolio()[0]["clicks_pct"] is None


def test_prompt_blok_noemt_de_horizon_en_de_kansen():
    insights.store_week([_analyse()], "2026-W31")
    blok = insights.prompt_block()
    assert "28 dagen" in blok
    assert "ai transformatie" in blok
    assert "CTR" in blok


def test_kans_die_weken_blijft_liggen_wordt_gemeld():
    kans = [{"query": "ai transformatie", "position": 8.4, "impressions": 320}]
    for week in ("2026-W29", "2026-W30", "2026-W31"):
        insights.store_week([_analyse(quick_wins=kans)], week)
    blijvers = insights.stale_quick_wins()
    assert len(blijvers) == 1
    assert blijvers[0]["weken"] == 3


def test_kans_die_beweegt_telt_niet_als_blijver():
    """Als de positie duidelijk verbetert, wórdt er aan gewerkt — dan is
    herhaling in het rapport geen verwaarlozing maar voortgang."""
    for week, pos in (("2026-W29", 12.0), ("2026-W30", 10.0), ("2026-W31", 7.0)):
        insights.store_week([_analyse(quick_wins=[
            {"query": "ai transformatie", "position": pos, "impressions": 320}])], week)
    assert insights.stale_quick_wins() == []


def test_te_weinig_historie_beweert_niets():
    kans = [{"query": "ai transformatie", "position": 8.4, "impressions": 320}]
    for week in ("2026-W30", "2026-W31"):
        insights.store_week([_analyse(quick_wins=kans)], week)
    assert insights.stale_quick_wins() == []


# ── Iris ───────────────────────────────────────────────────────────────────

def _snap(pending=0):
    from backend.domains.iris import metrics
    insights.store_week([_analyse(clicks=50, prev_clicks=150,
                                  positie=14.0, prev_positie=9.0)], "2026-W31")
    glob = {
        "errors_24h": [], "delivered_24h": 0, "pending_review_total": pending,
        "scheduler_failures": [], "downtime_gaps": [], "funnel": {},
        "inputs_7d": {}, "linkbuilding": {},
        "weekrapport": insights.summary(),
    }
    return metrics, {"projects": [], "global": glob}


def test_structurele_daling_wordt_een_knelpunt_met_knop():
    metrics, snap = _snap()
    knel = [b for b in metrics.bottlenecks(snap) if b["issue"] == "structurele_daling"]
    assert len(knel) == 1
    assert knel[0]["suggestion"]["type"] == "seo_refresh"
    assert knel[0]["suggestion"]["target"] == "site-1"


def test_geen_knop_bij_verstopte_wachtrij():
    """Doorvoer boven productie: bij een stapel van 20+ concepten maakt een
    seo_refresh de stapel groter zonder één klik op te leveren. De diagnose
    blijft staan, de knop verdwijnt."""
    metrics, snap = _snap(pending=25)
    knel = [b for b in metrics.bottlenecks(snap) if b["issue"] == "structurele_daling"]
    assert len(knel) == 1
    assert "suggestion" not in knel[0]


def test_iris_prompt_bevat_het_weekbeeld():
    from backend.domains.iris import service
    insights.store_week([_analyse()], "2026-W31")
    blok = service._weekrapport_blok()
    assert "2026-W31" in blok
    assert "WeAreImpact" in blok


def test_invariant_meldt_een_rapport_dat_niets_vastlegt(monkeypatch):
    """De invariant-vorm van de fout die dit mechanisme veroorzaakte: de job
    meldt 'ok', maar er staat geen weekbeeld in de database."""
    from backend.domains.iris import integrity
    from backend.domains.seo import gsc as gsc_api

    monkeypatch.setattr(gsc_api, "is_configured", lambda: True)
    with get_conn() as conn:
        conn.execute("DELETE FROM scheduler_runs WHERE job_id = 'weekly_ga_report'")
        conn.execute(
            "INSERT INTO scheduler_runs (job_id, status, last_run_at, last_ok_at) "
            "VALUES (?,?,?,?)",
            ("weekly_ga_report", "ok", "2026-08-03T08:00:00", "2026-08-03T08:00:00"),
        )
    try:
        bevindingen = integrity._check_weekrapport_niet_vastgelegd()
        assert len(bevindingen) == 1
        assert "weekly_insights" in bevindingen[0].detail

        # Zodra het weekbeeld er wél is, sluit de bevinding zichzelf.
        insights.store_week([_analyse()], "2026-W32")
        assert integrity._check_weekrapport_niet_vastgelegd() == []
    finally:
        with get_conn() as conn:
            conn.execute("DELETE FROM scheduler_runs WHERE job_id = 'weekly_ga_report'")


def test_invariant_zwijgt_zonder_gsc_koppeling(monkeypatch):
    from backend.domains.iris import integrity
    from backend.domains.seo import gsc as gsc_api
    monkeypatch.setattr(gsc_api, "is_configured", lambda: False)
    assert integrity._check_weekrapport_niet_vastgelegd() == []


def test_opslag_bewaart_de_ruwe_bevindingen_als_json():
    """De bevindingen moeten leesbaar terugkomen zonder herberekening: de
    28-daagse meting van vorige week is niet opnieuw op te halen uit GSC."""
    insights.store_week([_analyse()], "2026-W31")
    with get_conn() as conn:
        rij = conn.execute("SELECT ctr_fix FROM weekly_insights").fetchone()
    assert json.loads(rij["ctr_fix"])[0]["query"] == "digitale versnelling"
