"""De uitvoerlaag — waar een goedgekeurd voorstel een fill wordt.

Eén interface, drie standen (`invest_portfolio.mode`):

  - `paper`        — intern grootboek, fills uit de échte koershistorie
  - `alpaca_paper` — echte broker-API, nul geld (nog niet gebouwd)
  - `live`         — echt geld, altijd per order door een mens goedgekeurd

De twee regels die dit bestand eerlijk houden:

**Een fill gebeurt op de vólgende koers.** Een besluit dat vandaag op de
slotkoers is genomen, wordt op zijn vroegst morgen uitgevoerd. Vullen op de
prijs die het model zag, is gratis vooruitkijken; een strategie die zichzelf zo
waardeert verslaat elke index en levert in het echt niets op. Bestaat die
volgende koers nog niet, dan is de order simpelweg nog niet uitgevoerd
(`NietUitgevoerd`) — geen aanname, geen schatting.

**Kosten en slippage zijn niet optioneel.** Ze staan in `shared/config.py` en
horen eerder te hoog dan te laag: een papieren rendement dat kosten weglaat is
geen voorspelling van het echte, maar een reclamefolder.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Any, Dict, Optional

from ...shared.config import (
    INVEST_FEE_FIXED, INVEST_FEE_PCT, INVEST_KILL_SWITCH,
    INVEST_SLIPPAGE_BPS, INVEST_SLIPPAGE_BPS_CRYPTO,
)
from ...shared.database import get_conn
from . import history, portfolio, universe

logger = logging.getLogger(__name__)


class NietUitgevoerd(RuntimeError):
    """De order kón niet worden uitgevoerd. Bewust een exception en geen
    stille 'ok': een order die niet is uitgevoerd mag nergens als uitvoering
    tellen (CLAUDE.md — activiteit is geen effect)."""


def _slippage_bps(symbol: str) -> float:
    return (INVEST_SLIPPAGE_BPS_CRYPTO
            if universe.asset_class(symbol) == universe.CRYPTO
            else INVEST_SLIPPAGE_BPS)


def _fill_prijs(symbol: str, ruwe_prijs: float, side: str) -> float:
    """Slippage werkt altijd tegen je: kopen doe je iets duurder, verkopen
    iets goedkoper. Andersom modelleren is jezelf voordeel toerekenen."""
    bps = _slippage_bps(symbol) / 10_000.0
    return ruwe_prijs * (1 + bps) if side == "buy" else ruwe_prijs * (1 - bps)


def _kosten(waarde: float) -> float:
    return round(INVEST_FEE_FIXED + abs(waarde) * INVEST_FEE_PCT, 2)


def voer_uit(
    *,
    portfolio_id: str,
    symbol: str,
    side: str,
    qty: float,
    besluit_dag: str,
    ref_price: float,
    reden: str = "entry",
    proposal_id: str = "",
    stop: Optional[float] = None,
    target: Optional[float] = None,
    horizon: int = 14,
    thesis: str = "",
) -> Dict[str, Any]:
    """Voer één order uit tegen de eerstvolgende beschikbare slotkoers.

    `besluit_dag` is de handelsdag waarop de these rust; de fill komt van de
    dag daarná. Retourneert de trade; gooit `NietUitgevoerd` als dat (nog)
    niet kan.
    """
    if INVEST_KILL_SWITCH:
        raise NietUitgevoerd("INVEST_KILL_SWITCH staat aan — er wordt niets uitgevoerd.")

    pf = portfolio.ensure_portfolio(portfolio_id)
    if pf["mode"] != "paper":
        raise NietUitgevoerd(
            f"Portefeuille staat in stand '{pf['mode']}', maar alleen 'paper' is gebouwd. "
            "Zie invest/broker.py."
        )
    if side not in ("buy", "sell"):
        raise NietUitgevoerd(f"Onbekende orderkant '{side}'.")
    if qty <= 0:
        raise NietUitgevoerd("Ordergrootte is nul.")
    if not universe.is_verhandelbaar(symbol):
        raise NietUitgevoerd(
            f"'{symbol}' is geen verhandelbaar instrument (index of macro-reeks)."
        )

    volgende = history.close_na(symbol, besluit_dag)
    if not volgende:
        raise NietUitgevoerd(
            f"Er is nog geen koers ná {besluit_dag} voor {symbol}; de order wacht op de "
            "volgende handelsdag."
        )
    fill_dag, ruwe = volgende
    prijs = _fill_prijs(symbol, ruwe, side)

    fx = portfolio.wisselkoers_eur(universe.instrument(symbol).valuta, fill_dag)
    if fx is None:
        raise NietUitgevoerd(
            f"Geen wisselkoers voor {universe.instrument(symbol).valuta} op {fill_dag}; "
            "zonder omrekening is de kaspositie niet correct bij te werken."
        )
    waarde_eur = prijs * qty * fx
    fee = _kosten(waarde_eur)

    if side == "buy" and pf["cash"] < waarde_eur + fee:
        raise NietUitgevoerd(
            f"Onvoldoende cash: {pf['cash']:.2f} beschikbaar, {waarde_eur + fee:.2f} nodig."
        )
    if side == "sell":
        bestaand = portfolio.positie(portfolio_id, symbol)
        if not bestaand or bestaand["qty"] + 1e-9 < qty:
            raise NietUitgevoerd(
                f"Geen (voldoende) positie in {symbol} om te verkopen — "
                "shorten is in dit domein niet toegestaan."
            )

    trade_id = str(uuid.uuid4())
    with get_conn() as conn:
        if side == "buy":
            positie_id = portfolio.open_of_vergroot(
                conn, portfolio_id, symbol=symbol, qty=qty, prijs=prijs, stop=stop,
                target=target, horizon=horizon, thesis=thesis, proposal_id=proposal_id,
                dag=fill_dag,
            )
            portfolio._muteer_cash(conn, portfolio_id, -(waarde_eur + fee))
        else:
            positie_id = portfolio.verklein_of_sluit(
                conn, portfolio_id, symbol=symbol, qty=qty, prijs=prijs,
                reden=reden, dag=fill_dag,
            ) or ""
            portfolio._muteer_cash(conn, portfolio_id, waarde_eur - fee)

        conn.execute(
            "INSERT INTO invest_trades (id, portfolio_id, position_id, proposal_id, symbol, side, "
            "qty, price, ref_price, fee, reason, executed_on, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (trade_id, portfolio_id, positie_id, proposal_id, symbol, side, qty, prijs,
             ref_price, fee, reden, fill_dag, datetime.now().isoformat(timespec="seconds")),
        )

    logger.info("[invest] %s %.4f %s @ %.4f (%s) fee %.2f", side, qty, symbol, prijs, fill_dag, fee)
    return {
        "id": trade_id,
        "symbol": symbol,
        "side": side,
        "qty": qty,
        "price": round(prijs, 4),
        "ref_price": ref_price,
        "fill_dag": fill_dag,
        "fee": fee,
        "waarde_eur": round(waarde_eur, 2),
        "slippage_bps": _slippage_bps(symbol),
    }


def controleer_stops(portfolio_id: str = portfolio.STANDAARD_ID) -> Dict[str, Any]:
    """Sluit posities waarvan de stop of het koersdoel is geraakt, of waarvan
    de horizon verstreek.

    Dit is de tegenhanger van "we kijken later wel": een stop die alleen op
    papier staat, beschermt niets. De toets gebruikt de dag-*low* en -*high*,
    niet de slotkoers — een stop die intraday geraakt werd is geraakt, ook als
    de koers herstelde. Andersom rekenen zou het risico stelselmatig te laag
    voorstellen.
    """
    gesloten: list = []
    fouten: list = []
    for pos in portfolio.posities(portfolio_id):
        reeks = history.reeks(pos["symbol"], dagen=3)
        vandaag = reeks[-1] if reeks else None
        if not vandaag:
            continue
        reden = ""
        if pos["stop"] and vandaag["low"] is not None and vandaag["low"] <= pos["stop"]:
            reden = "stop"
        elif pos["target"] and vandaag["high"] is not None and vandaag["high"] >= pos["target"]:
            reden = "target"
        elif pos["horizon_days"]:
            from datetime import date, timedelta
            verloopt = date.fromisoformat(pos["opened_on"]) + timedelta(days=pos["horizon_days"])
            if date.today() > verloopt:
                reden = "horizon"
        if not reden:
            continue
        try:
            trade = voer_uit(
                portfolio_id=portfolio_id, symbol=pos["symbol"], side="sell",
                qty=pos["qty"], besluit_dag=reeks[-2]["date"] if len(reeks) > 1 else vandaag["date"],
                ref_price=pos["stop"] if reden == "stop" else (pos["target"] or vandaag["close"]),
                reden=reden, proposal_id=pos["proposal_id"],
            )
            gesloten.append({**trade, "reden": reden})
        except NietUitgevoerd as e:
            # Luid, niet stil: een stop die niet kon worden uitgevoerd is
            # precies het moment waarop iemand het moet weten.
            fouten.append({"symbol": pos["symbol"], "reden": reden, "fout": str(e)})
            logger.warning("[invest] stop/target %s op %s niet uitgevoerd: %s",
                           reden, pos["symbol"], e)
    return {"gesloten": gesloten, "fouten": fouten}
