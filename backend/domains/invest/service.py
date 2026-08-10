"""De dagelijkse ronde en de review-gate.

Volgorde van de ronde is niet vrij te kiezen:

  1. koersen ophalen — alles hierna rust erop
  2. stops en koersdoelen afhandelen — een positie die gisteren geraakt werd,
     hoort niet mee te tellen in de NAV van vandaag
  3. NAV vastleggen en de grenzen toetsen
  4. openstaande voorspellingen afrekenen — vóór het nadenken, zodat de
     analist zijn eigen trefkans van gisteren meekrijgt
  5. analyseren (Claude Code), risicotoets, voorstellen in de gate

Stap 4 vóór stap 5 is de hele leer-lus. Andersom zou de agent adviseren zonder
te weten hoe zijn vorige advies afliep, en dat is precies het systeem dat hier
werd vervangen.

De gate: `keur_goed` is de énige weg naar een order, en hij wordt alleen
aangeroepen vanuit een menselijke klik (Actiecentrum of API). Niets in dit
bestand voert uit zonder die klik.
"""
from __future__ import annotations

import logging
import uuid
from datetime import date, datetime
from typing import Any, Dict, List, Optional

from ...shared.database import get_conn
from ...shared.failures import describe_exception, note_success, should_escalate
from ...shared.learning import evaluate_due, record_prediction, track_record, upsert_lesson
from ...shared.outcomes import log_outcome
from . import analyst, broker, features, history, portfolio, risk, universe

logger = logging.getLogger(__name__)

AGENT = "invest"
PROJECT = "Beursmeester"


def _nu() -> str:
    return datetime.now().isoformat(timespec="seconds")


# ── De leer-lus ────────────────────────────────────────────────────────────

def _resolver(metric: str, context: str) -> Optional[float]:
    """Reken een voorspelling af tegen de échte koers.

    `context` is het symbool. Geen koers = None, niet 0: een voorspelling die
    niet eerlijk te meten is, hoort 'unclear' te worden en niet als misser te
    tellen.
    """
    if metric != "close" or not context:
        return None
    laatste = history.laatste_slot(context)
    return laatste[1] if laatste else None


def reken_voorspellingen_af() -> Dict[str, Any]:
    return evaluate_due(AGENT, _resolver)


# ── Voorstellen ────────────────────────────────────────────────────────────

def _bewaar_voorstel(portfolio_id: str, run_id: str, v: Dict[str, Any],
                     oordeel: Dict[str, Any], denkwerk: str, les_id: str) -> str:
    """Leg één voorstel vast, mét de voorspelling die eraan hangt.

    De baseline van die voorspelling komt uit `market_history` en nooit uit de
    tekst van het model. Zou je het model zijn eigen ijkpunt laten kiezen, dan
    kan hij achteraf niet ongelijk krijgen — en dat is geen leerlus maar een
    complimentenmachine.
    """
    pid = str(uuid.uuid4())
    voorspelling_id = ""
    if oordeel["toegestaan"]:
        voorspelling_id = record_prediction(
            AGENT,
            metric="close",
            context=v["symbol"],
            direction="up" if v["side"] == "buy" else "down",
            baseline=v["ref_price"],
            statement=(f"{v['symbol']} staat over {v['horizon_days']} dagen "
                       f"{'hoger' if v['side'] == 'buy' else 'lager'} dan {v['ref_price']:g}"),
            horizon_days=v["horizon_days"],
            # Ruis van een halve procent: kleiner is koersgeruis, geen richting.
            noise=abs(v["ref_price"]) * 0.005,
            lesson_id=les_id,
        ) or ""

    with get_conn() as conn:
        conn.execute(
            "INSERT INTO invest_proposals (id, portfolio_id, run_id, symbol, asset_class, side, "
            "ref_price, ref_date, stop, target, horizon_days, size_pct, qty, thesis, invalidation, "
            "confidence, backtest_ref, denkwerk, risk_note, prediction_id, status, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (pid, portfolio_id, run_id, v["symbol"], v["asset_class"], v["side"],
             v["ref_price"], v["ref_date"], v["stop"], v["target"], v["horizon_days"],
             0.0, oordeel["qty"], v["thesis"], v["invalidation"], v["confidence"],
             v["backtest_ref"], denkwerk, oordeel["reden"], voorspelling_id,
             "pending_review" if oordeel["toegestaan"] else "geblokkeerd", _nu()),
        )
    return pid


def open_voorstellen(portfolio_id: str = portfolio.STANDAARD_ID) -> List[Dict[str, Any]]:
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM invest_proposals WHERE portfolio_id = ? AND status = 'pending_review' "
            "ORDER BY created_at DESC", (portfolio_id,),
        ).fetchall()]


def voorstel(proposal_id: str) -> Optional[Dict[str, Any]]:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM invest_proposals WHERE id = ?", (proposal_id,)).fetchone()
    return dict(row) if row else None


def keur_goed(proposal_id: str) -> Dict[str, Any]:
    """De enige weg naar een order. Wordt uitsluitend door een mens aangeroepen.

    De risicotoets draait hier **opnieuw**. Tussen het voorstel en de klik kan
    een dag zitten: de koers is bewogen, een andere positie is bijgekomen, of
    de portefeuille staat inmiddels stil. Vertrouwen op het oordeel van gisteren
    is precies hoe een grens wordt overschreden zonder dat iemand hem heeft
    weggeklikt.
    """
    v = voorstel(proposal_id)
    if not v:
        return {"ok": False, "reden": "voorstel bestaat niet"}
    if v["status"] != "pending_review":
        return {"ok": False, "reden": f"voorstel staat op '{v['status']}', niet op review"}

    hertoets = risk.beoordeel({
        "symbol": v["symbol"], "side": v["side"], "stop": v["stop"],
        "ref_price": (history.laatste_slot(v["symbol"]) or [None, v["ref_price"]])[1],
    }, v["portfolio_id"])
    if not hertoets["toegestaan"]:
        with get_conn() as conn:
            conn.execute("UPDATE invest_proposals SET risk_note = ? WHERE id = ?",
                         (f"bij goedkeuring geweigerd: {hertoets['reden']}", proposal_id))
        return {"ok": False, "reden": f"risicotoets weigert nu: {hertoets['reden']}"}

    try:
        trade = broker.voer_uit(
            portfolio_id=v["portfolio_id"], symbol=v["symbol"], side=v["side"],
            qty=hertoets["qty"], besluit_dag=v["ref_date"], ref_price=v["ref_price"],
            reden="entry" if v["side"] == "buy" else "handmatig", proposal_id=proposal_id,
            stop=v["stop"], target=v["target"], horizon=v["horizon_days"], thesis=v["thesis"],
        )
    except broker.NietUitgevoerd as e:
        # Niet uitgevoerd is niet goedgekeurd. Het voorstel blijft staan zodat
        # de knop morgen opnieuw werkt — een order die stil verdampt, is hoe
        # je denkt een positie te hebben die er niet is.
        return {"ok": False, "reden": str(e), "opnieuw_proberen": True}

    with get_conn() as conn:
        conn.execute(
            "UPDATE invest_proposals SET status = 'filled', qty = ?, decided_at = ? WHERE id = ?",
            (hertoets["qty"], _nu(), proposal_id),
        )
    portfolio.leg_nav_vast(v["portfolio_id"])

    log_outcome(
        PROJECT, "order_uitgevoerd",
        f"{v['side'].upper()} {trade['qty']:g} {v['symbol']} @ {trade['price']:.4f} "
        f"({trade['fill_dag']}, kosten {trade['fee']:.2f})",
        artifact=v["backtest_ref"],
        next_step=f"Stop staat op {v['stop']:g}; die wordt dagelijks getoetst.",
    )
    return {"ok": True, "trade": trade}


def wijs_af(proposal_id: str, reden: str = "") -> Dict[str, Any]:
    """Afwijzen sluit óók de bijbehorende voorspelling.

    Een voorspelling laten doorlopen op een idee dat je hebt afgewezen, meet
    iets anders dan wat de agent daadwerkelijk deed — en vleit de trefkans in
    beide richtingen. De les blijft wél staan: die is los getoetst.
    """
    v = voorstel(proposal_id)
    if not v:
        return {"ok": False, "reden": "voorstel bestaat niet"}
    with get_conn() as conn:
        conn.execute(
            "UPDATE invest_proposals SET status = 'rejected', decided_at = ?, "
            "risk_note = COALESCE(NULLIF(?, ''), risk_note) WHERE id = ?",
            (_nu(), reden[:300], proposal_id),
        )
        if v["prediction_id"]:
            conn.execute(
                "UPDATE agent_predictions SET status = 'unclear', outcome_note = ?, "
                "evaluated_at = ? WHERE id = ? AND status = 'open'",
                ("voorstel afgewezen door een mens", _nu(), v["prediction_id"]),
            )
    return {"ok": True}


def verlopen_voorstellen_opruimen(dagen: int = 3) -> int:
    """Een voorstel dat dagen blijft liggen, rust op een koers die er niet meer
    is. Dat is geen inbox-item meer maar een verkeerd inbox-item."""
    grens = (date.today().toordinal() - dagen)
    verlopen = 0
    for v in open_voorstellen():
        if date.fromisoformat(v["ref_date"]).toordinal() < grens:
            wijs_af(v["id"], f"verlopen: de these rust op de koers van {v['ref_date']}")
            verlopen += 1
    return verlopen


# ── De ronde ───────────────────────────────────────────────────────────────

async def run_daily_cycle(portfolio_id: str = portfolio.STANDAARD_ID) -> Dict[str, Any]:
    """De dagelijkse Beursmeester-ronde. Draait als scheduler-job."""
    run_id = str(uuid.uuid4())
    gestart = datetime.now()
    pf = portfolio.ensure_portfolio(portfolio_id)
    verslag: Dict[str, Any] = {"run_id": run_id, "portfolio_id": portfolio_id}

    # 1. Koersen
    try:
        verslag["koersen"] = await history.sync()
    except Exception as e:
        _bewaar_run(run_id, portfolio_id, "geen", "error", "", 0, gestart,
                    fout=describe_exception(e))
        log_outcome(PROJECT, "beursronde",
                    f"De ronde is gestopt: koersen ophalen mislukte ({describe_exception(e)}).",
                    next_step="Zonder verse koersen zou elk voorstel op oude data rusten. "
                              "Controleer de datafeed en draai de ronde opnieuw.",
                    status="error")
        return {**verslag, "ok": False, "reden": "koersen ophalen mislukt"}

    # 2. Stops, koersdoelen en verstreken horizons
    verslag["stops"] = broker.controleer_stops(portfolio_id)
    for fout in verslag["stops"]["fouten"]:
        log_outcome(PROJECT, "stop_niet_uitgevoerd",
                    f"{fout['symbol']}: {fout['reden']} kon niet worden uitgevoerd — {fout['fout']}",
                    next_step="Controleer de positie handmatig; de bescherming werkt nu niet.",
                    status="error")

    # 3. NAV en grenzen
    portfolio.leg_nav_vast(portfolio_id)
    verslag["risico"] = risk.controleer_grenzen(portfolio_id)
    verslag["rendement"] = portfolio.rendement(portfolio_id)

    # 4. Afrekenen vóór nadenken
    verslag["voorspellingen"] = reken_voorspellingen_af()
    verslag["trefkans"] = track_record(AGENT)

    # 5. Verlopen voorstellen opruimen, dan analyseren
    verslag["verlopen"] = verlopen_voorstellen_opruimen()

    if not verslag["risico"]["mag_handelen"]:
        note = "; ".join(verslag["risico"]["redenen"])
        _bewaar_run(run_id, portfolio_id, "geen", "ok", "", 0, gestart, notitie=note)
        logger.info("[invest] geen analyse: %s", note)
        return {**verslag, "ok": True, "voorstellen": 0, "reden": note}

    analyse = await analyst.analyseer(portfolio_id)
    verslag["denkwerk"] = analyse["denkwerk"]
    verslag["werkmap"] = analyse["werkmap"]
    verslag["geweigerd"] = analyse.get("geweigerd", [])
    verslag["marktbeeld"] = analyse.get("marktbeeld", "")

    if not analyse["ok"]:
        _bewaar_run(run_id, portfolio_id, "geen", "error", analyse["werkmap"], 0, gestart,
                    fout=analyse.get("reden", ""))
        log_outcome(PROJECT, "beursronde",
                    f"Geen analyse mogelijk: {analyse.get('reden') or 'onbekende oorzaak'}",
                    artifact=analyse["werkmap"],
                    next_step="Controleer of de Claude Code-CLI bereikbaar is "
                              "(claude --version) en of het dagvenster niet op is.",
                    status="error")
        return {**verslag, "ok": False}

    # Een terugval-ronde is geen ronde. Zonder werkmap kan er niets worden
    # gebacktest, dus levert hij per definitie nul voorstellen op — en dat ziet
    # er in de cijfers precies zo uit als "de analist vond vandaag niets", wat
    # een heel ander bericht is. Tot 3 aug 2026 stond dit alleen als notitie in
    # `invest_runs`: de Pro-limiet was op ("You've hit your monthly spend
    # limit"), de ronde meldde `status='ok'` en nergens stond dat het denkwerk
    # niet had plaatsgevonden.
    if analyse["denkwerk"] == "terugval":
        _meld_terugval(analyse)
    else:
        note_success("invest_denkwerk")

    les_id = ""
    if analyse.get("les"):
        les_id = upsert_lesson(AGENT, analyse["les"], category="backtest",
                               evidence={"werkmap": analyse["werkmap"]}) or ""

    aangemaakt = 0
    geblokkeerd: List[Dict[str, str]] = []
    for v in analyse["voorstellen"]:
        oordeel = risk.beoordeel(v, portfolio_id)
        _bewaar_voorstel(portfolio_id, run_id, v, oordeel, analyse["denkwerk"], les_id)
        if oordeel["toegestaan"]:
            aangemaakt += 1
        else:
            geblokkeerd.append({"symbol": v["symbol"], "reden": oordeel["reden"]})

    verslag["voorstellen"] = aangemaakt
    verslag["geblokkeerd"] = geblokkeerd
    _bewaar_run(run_id, portfolio_id, analyse["denkwerk"], "ok", analyse["werkmap"],
                aangemaakt, gestart, notitie=analyse.get("reden", ""))

    # Eén uitkomstkaart per ronde, met het artefact erbij. Nul voorstellen is
    # géén fout: een analist die niets vindt en dat zegt, is beter dan een die
    # elke dag iets moet verzinnen.
    samenvatting = (
        f"{aangemaakt} voorstel(len) klaar voor review"
        + (f", {len(geblokkeerd)} geblokkeerd door de risicotoets" if geblokkeerd else "")
        + (f", {len(verslag['geweigerd'])} geweigerd bij validatie" if verslag["geweigerd"] else "")
        + f" · denkwerk: {analyse['denkwerk']}"
    )
    log_outcome(
        PROJECT, "beursronde", samenvatting,
        artifact=analyse["werkmap"],
        next_step=("Beoordeel de voorstellen in het Actiecentrum." if aangemaakt
                   else "Geen actie nodig."),
        status="ok",
    )
    return {**verslag, "ok": True}


def _meld_terugval(analyse: Dict[str, Any]) -> None:
    """Meld dat het echte denkwerk niet heeft plaatsgevonden.

    Twee oorzaken met twee verschillende knoppen eronder, en dat onderscheid is
    het hele punt (zie `shared/failures.py`): een bereikte abonnementslimiet is
    mens-alleen — opnieuw proberen doet niets, alleen wachten of de limiet
    verhogen helpt — terwijl een netwerkblip of een gemiste CLI vanzelf
    overgaat en pas na een reeks een inbox-item verdient.
    """
    reden = (analyse.get("reden") or "onbekende oorzaak").strip()
    sleutel = "invest_denkwerk"
    if analyse.get("limiet"):
        vandaag = date.today().isoformat()
        with get_conn() as conn:
            al_gemeld = conn.execute(
                "SELECT 1 FROM activity_log WHERE action = 'denkwerk_uitgevallen' "
                "AND substr(created_at, 1, 10) = ? LIMIT 1", (vandaag,),
            ).fetchone()
        if al_gemeld:
            return
        log_outcome(
            PROJECT, "denkwerk_uitgevallen",
            f"De beursronde draaide zónder analyse: {reden}",
            artifact=analyse.get("werkmap", ""),
            next_step="Verhoog de limiet op claude.ai of wacht tot het venster ververst. "
                      "Tot die tijd komen er geen voorstellen — er kan niets gebacktest worden.",
            status="error",
        )
        return
    if should_escalate(sleutel, RuntimeError(reden)):
        log_outcome(
            PROJECT, "denkwerk_uitgevallen",
            f"De beursronde valt herhaaldelijk terug op de gateway: {reden}",
            artifact=analyse.get("werkmap", ""),
            next_step="Controleer of de Claude Code-CLI bereikbaar is (claude --version) "
                      "en of CLAUDE_CODE_BIN in .env naar het juiste pad wijst.",
            status="error",
        )


def _bewaar_run(run_id: str, portfolio_id: str, denkwerk: str, status: str, werkmap: str,
                voorstellen: int, gestart: datetime, notitie: str = "", fout: str = "") -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO invest_runs (id, portfolio_id, run_date, denkwerk, status, workspace, "
            "proposals, duration_ms, note, error, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (run_id, portfolio_id, date.today().isoformat(), denkwerk, status, werkmap,
             voorstellen, int((datetime.now() - gestart).total_seconds() * 1000),
             notitie[:400], fout[:400], _nu()),
        )


def laatste_run() -> Optional[Dict[str, Any]]:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM invest_runs ORDER BY created_at DESC LIMIT 1").fetchone()
    return dict(row) if row else None


# ── Overzicht voor UI en briefing ──────────────────────────────────────────

def overzicht(portfolio_id: str = portfolio.STANDAARD_ID) -> Dict[str, Any]:
    snap = portfolio.snapshot(portfolio_id)
    return {
        "portefeuille": snap,
        "rendement": portfolio.rendement(portfolio_id),
        "risico": risk.portefeuille_status(portfolio_id),
        "voorstellen": open_voorstellen(portfolio_id),
        "trefkans": track_record(AGENT),
        "regime": features.regime(),
        "laatste_run": laatste_run(),
        "dekking": history.dekking(),
        "verouderd": history.verouderde_symbolen(),
    }
