"""De portefeuille — posities, grootboek, waarde en de benchmark ernaast.

Vóór dit bestand bestond de €10.000 alleen als zin in een promptregel. Elke
ochtend werd hij opnieuw verzonnen: het rapport van gisteren wist niet wat het
gisteren adviseerde, dus niemand kon zeggen of het werkte. Hier krijgt hij een
rekening met een saldo.

Drie ontwerpkeuzes die het verschil maken tussen meten en doen alsof:

(a) **Gesloten posities blijven staan.** De verleiding om alleen de open
    posities te tonen is precies hoe een trackrecord zichzelf mooi rekent: de
    winnaars staan er nog, de verliezers zijn "geen positie meer". Realized én
    unrealized tellen mee, altijd, inclusief kosten.

(b) **Valuta wordt echt omgerekend, of helemaal niet.** Een positie in AAPL
    staat in dollars. Die naast een EUR-saldo optellen alsof het hetzelfde is,
    geeft een NAV die er precies zo geloofwaardig uitziet als een juiste — de
    gevaarlijkste soort fout. Ontbreekt de wisselkoers, dan is de positie
    `onwaardeerbaar` en weigert `rendement()` een percentage te noemen. Geen
    getal is beter dan een verkeerd getal.

(c) **De benchmark ligt vast bij de start.** `benchmark_start_price` wordt
    éénmalig geschreven. Zou je hem elke keer opnieuw bepalen, dan is "we
    verslaan de index" achteraf naar elke gewenste uitkomst te rekenen.
"""
from __future__ import annotations

import logging
import uuid
from datetime import date, datetime
from typing import Any, Dict, List, Optional

from ...shared.config import INVEST_START_CAPITAL
from ...shared.database import get_conn
from . import history, universe

logger = logging.getLogger(__name__)

STANDAARD_ID = "hoofdportefeuille"


def _nu() -> str:
    return datetime.now().isoformat(timespec="seconds")


# ── Aanmaken & ophalen ─────────────────────────────────────────────────────

def ensure_portfolio(portfolio_id: str = STANDAARD_ID) -> Dict[str, Any]:
    """Haal de portefeuille op, of maak hem aan met het startkapitaal.

    De benchmark-startkoers wordt hier vastgelegd. Is die (nog) niet bekend
    omdat de koershistorie leeg is, dan blijft hij NULL en vult
    `_zet_benchmark_start` hem bij de eerste sync alsnog — met de koers van de
    startdatum, niet die van vandaag.
    """
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM invest_portfolio WHERE id = ?", (portfolio_id,)).fetchone()
        if row:
            pf = dict(row)
        else:
            vandaag = date.today().isoformat()
            conn.execute(
                "INSERT INTO invest_portfolio (id, name, mode, base_currency, start_capital, cash, "
                "benchmark_symbol, benchmark_start_price, started_on, created_at) "
                "VALUES (?, ?, 'paper', 'EUR', ?, ?, ?, NULL, ?, ?)",
                (portfolio_id, "Beursmeester", INVEST_START_CAPITAL, INVEST_START_CAPITAL,
                 universe.BENCHMARK, vandaag, _nu()),
            )
            logger.info("[invest] portefeuille aangemaakt met %.2f startkapitaal", INVEST_START_CAPITAL)
            pf = dict(conn.execute(
                "SELECT * FROM invest_portfolio WHERE id = ?", (portfolio_id,)
            ).fetchone())
    if pf.get("benchmark_start_price") is None:
        _zet_benchmark_start(pf)
        pf = get(portfolio_id) or pf
    return pf


def _zet_benchmark_start(pf: Dict[str, Any]) -> None:
    """Leg de benchmarkkoers van de startdatum vast — eenmalig, en met de koers
    van tóen. Lukt het niet, dan blijft het veld leeg en zegt `rendement()`
    eerlijk dat er nog geen vergelijking is."""
    koers = history.slot_op_of_voor(pf["benchmark_symbol"], pf["started_on"])
    if not koers:
        return
    with get_conn() as conn:
        conn.execute(
            "UPDATE invest_portfolio SET benchmark_start_price = ? WHERE id = ? "
            "AND benchmark_start_price IS NULL",
            (koers[1], pf["id"]),
        )


def get(portfolio_id: str = STANDAARD_ID) -> Optional[Dict[str, Any]]:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM invest_portfolio WHERE id = ?", (portfolio_id,)).fetchone()
    return dict(row) if row else None


def posities(portfolio_id: str = STANDAARD_ID, *, alleen_open: bool = True) -> List[Dict[str, Any]]:
    q = "SELECT * FROM invest_positions WHERE portfolio_id = ?"
    params: List[Any] = [portfolio_id]
    if alleen_open:
        q += " AND status = 'open'"
    q += " ORDER BY opened_on DESC"
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(q, params).fetchall()]


def positie(portfolio_id: str, symbol: str) -> Optional[Dict[str, Any]]:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM invest_positions WHERE portfolio_id = ? AND symbol = ? AND status = 'open'",
            (portfolio_id, symbol),
        ).fetchone()
    return dict(row) if row else None


def trades(portfolio_id: str = STANDAARD_ID, limiet: int = 100) -> List[Dict[str, Any]]:
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM invest_trades WHERE portfolio_id = ? ORDER BY executed_on DESC, created_at DESC LIMIT ?",
            (portfolio_id, limiet),
        ).fetchall()]


# ── Valuta ─────────────────────────────────────────────────────────────────

def wisselkoers_eur(valuta: str, dag: Optional[str] = None) -> Optional[float]:
    """Hoeveel euro is één eenheid `valuta` waard? None = onbekend.

    Alleen EUR en USD zijn nu nodig; een onbekende valuta geeft bewust None in
    plaats van 1.0. Stilzwijgend 1.0 aannemen is exact de fout die een NAV
    geloofwaardig-maar-verkeerd maakt.
    """
    valuta = (valuta or "").upper()
    if valuta in ("EUR", ""):
        return 1.0
    if valuta == "USD":
        koers = history.slot_op_of_voor("EURUSD=X", dag) if dag else history.laatste_slot("EURUSD=X")
        if not koers or not koers[1]:
            return None
        return 1.0 / koers[1]   # EURUSD=X is dollars per euro
    return None


def _valuta_van(symbol: str) -> str:
    inst = universe.instrument(symbol)
    return inst.valuta if inst else "EUR"


# ── Waardering ─────────────────────────────────────────────────────────────

def snapshot(portfolio_id: str = STANDAARD_ID, dag: Optional[str] = None) -> Dict[str, Any]:
    """De stand van de portefeuille: per positie een waarde, plus de NAV.

    `onwaardeerbaar` bevat elke positie waarvan de koers of de wisselkoers
    ontbreekt. Zolang die lijst niet leeg is, is de NAV een ondergrens en geen
    feit — en zeggen alle afnemers dat er ook bij.
    """
    pf = ensure_portfolio(portfolio_id)
    regels: List[Dict[str, Any]] = []
    onwaardeerbaar: List[Dict[str, str]] = []
    waarde = 0.0

    for p in posities(portfolio_id):
        koers = history.slot_op_of_voor(p["symbol"], dag) if dag else history.laatste_slot(p["symbol"])
        fx = wisselkoers_eur(_valuta_van(p["symbol"]), dag)
        if not koers or fx is None:
            onwaardeerbaar.append({
                "symbol": p["symbol"],
                "reden": "geen koers in de historie" if not koers else
                         f"geen wisselkoers voor {_valuta_van(p['symbol'])}",
            })
            regels.append({**p, "koers": None, "waarde": None, "pnl": None, "pnl_pct": None})
            continue
        markt = koers[1] * fx
        kost = p["avg_price"] * fx
        pos_waarde = markt * p["qty"]
        waarde += pos_waarde
        regels.append({
            **p,
            "koers": round(koers[1], 4),
            "koers_dag": koers[0],
            "waarde": round(pos_waarde, 2),
            "pnl": round((markt - kost) * p["qty"], 2),
            "pnl_pct": round((markt - kost) / kost * 100, 2) if kost else None,
        })

    nav = pf["cash"] + waarde
    return {
        "portfolio_id": portfolio_id,
        "mode": pf["mode"],
        "peildatum": dag or (history.laatste_slot(universe.BENCHMARK) or ["", 0])[0],
        "cash": round(pf["cash"], 2),
        "positions_value": round(waarde, 2),
        "nav": round(nav, 2),
        "start_capital": pf["start_capital"],
        "posities": regels,
        "onwaardeerbaar": onwaardeerbaar,
        "volledig": not onwaardeerbaar,
        "halted_until": pf.get("halted_until") or "",
        "halt_reason": pf.get("halt_reason") or "",
    }


def rendement(portfolio_id: str = STANDAARD_ID) -> Dict[str, Any]:
    """Rendement van de portefeuille náást dat van de benchmark.

    Zonder die tweede kolom is een rendementscijfer betekenisloos: +4% in een
    markt die +9% deed is verlies. Ontbreekt de benchmark-startkoers of is de
    NAV incompleet, dan staat er `None` en waaróm — nooit een cijfer dat
    toevallig een kant op wijst.
    """
    pf = ensure_portfolio(portfolio_id)
    snap = snapshot(portfolio_id)

    eigen = None
    if snap["volledig"] and pf["start_capital"]:
        eigen = round((snap["nav"] - pf["start_capital"]) / pf["start_capital"] * 100, 2)

    bench = None
    bench_nu = history.laatste_slot(pf["benchmark_symbol"])
    if pf.get("benchmark_start_price") and bench_nu:
        bench = round((bench_nu[1] - pf["benchmark_start_price"]) / pf["benchmark_start_price"] * 100, 2)

    reden = ""
    if eigen is None:
        reden = ("De NAV is onvolledig: " +
                 ", ".join(f"{o['symbol']} ({o['reden']})" for o in snap["onwaardeerbaar"]))
    elif bench is None:
        reden = "Geen vastgelegde startkoers voor de benchmark — vergelijking nog niet mogelijk."

    return {
        "nav": snap["nav"] if snap["volledig"] else None,
        "start_capital": pf["start_capital"],
        "rendement_pct": eigen,
        "benchmark_symbol": pf["benchmark_symbol"],
        "benchmark_pct": bench,
        "alpha_pct": round(eigen - bench, 2) if (eigen is not None and bench is not None) else None,
        "sinds": pf["started_on"],
        "onvolledig_reden": reden,
        "gesloten_posities": len(posities(portfolio_id, alleen_open=False)) - len(posities(portfolio_id)),
    }


def leg_nav_vast(portfolio_id: str = STANDAARD_ID, dag: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Schrijf één punt op de koerslijn. Een onvolledige NAV wordt NIET
    vastgelegd: één verkeerd punt vervuilt de hele reeks, en die reeks is
    later het bewijsmateriaal."""
    dag = dag or date.today().isoformat()
    snap = snapshot(portfolio_id)
    if not snap["volledig"]:
        logger.warning("[invest] NAV van %s niet vastgelegd — onwaardeerbare posities: %s",
                       dag, [o["symbol"] for o in snap["onwaardeerbaar"]])
        return None
    pf = ensure_portfolio(portfolio_id)
    bench = history.slot_op_of_voor(pf["benchmark_symbol"], dag)
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO invest_nav (portfolio_id, date, nav, cash, positions_value, benchmark_price) "
            "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(portfolio_id, date) DO UPDATE SET "
            "nav=excluded.nav, cash=excluded.cash, positions_value=excluded.positions_value, "
            "benchmark_price=excluded.benchmark_price",
            (portfolio_id, dag, snap["nav"], snap["cash"], snap["positions_value"],
             bench[1] if bench else None),
        )
    return snap


def nav_reeks(portfolio_id: str = STANDAARD_ID, dagen: int = 180) -> List[Dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM invest_nav WHERE portfolio_id = ? ORDER BY date DESC LIMIT ?",
            (portfolio_id, dagen),
        ).fetchall()
    return [dict(r) for r in reversed(rows)]


def drawdown(portfolio_id: str = STANDAARD_ID) -> Optional[float]:
    """Huidige terugval vanaf de hoogste NAV ooit, in procenten (positief =
    verlies). None zolang er geen reeks is — niet 0, want 0 zou "alles in orde"
    betekenen terwijl we het niet weten."""
    reeks = nav_reeks(portfolio_id, dagen=3650)
    if not reeks:
        return None
    top = max(r["nav"] for r in reeks)
    nu = reeks[-1]["nav"]
    if not top:
        return None
    return round(max(0.0, (top - nu) / top * 100), 2)


def dagresultaat(portfolio_id: str = STANDAARD_ID) -> Optional[float]:
    """Resultaat van vandaag t.o.v. het vorige NAV-punt, in procenten."""
    reeks = nav_reeks(portfolio_id, dagen=2)
    if len(reeks) < 2 or not reeks[0]["nav"]:
        return None
    return round((reeks[-1]["nav"] - reeks[0]["nav"]) / reeks[0]["nav"] * 100, 2)


# ── Mutaties (alleen aangeroepen door de broker) ───────────────────────────

def _muteer_cash(conn, portfolio_id: str, delta: float) -> None:
    conn.execute("UPDATE invest_portfolio SET cash = cash + ? WHERE id = ?", (delta, portfolio_id))


def open_of_vergroot(conn, portfolio_id: str, *, symbol: str, qty: float, prijs: float,
                     stop: Optional[float], target: Optional[float], horizon: int,
                     thesis: str, proposal_id: str, dag: str) -> str:
    """Nieuwe positie, of bijkopen in een bestaande (gewogen kostprijs)."""
    bestaand = positie(portfolio_id, symbol)
    if bestaand:
        totaal_qty = bestaand["qty"] + qty
        nieuwe_prijs = ((bestaand["avg_price"] * bestaand["qty"]) + (prijs * qty)) / totaal_qty
        conn.execute(
            "UPDATE invest_positions SET qty = ?, avg_price = ?, stop = COALESCE(?, stop), "
            "target = COALESCE(?, target) WHERE id = ?",
            (totaal_qty, nieuwe_prijs, stop, target, bestaand["id"]),
        )
        return bestaand["id"]
    pid = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO invest_positions (id, portfolio_id, symbol, asset_class, qty, avg_price, "
        "stop, target, horizon_days, thesis, proposal_id, status, opened_on) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', ?)",
        (pid, portfolio_id, symbol, universe.asset_class(symbol), qty, prijs, stop, target,
         horizon, thesis[:600], proposal_id, dag),
    )
    return pid


def verklein_of_sluit(conn, portfolio_id: str, *, symbol: str, qty: float, prijs: float,
                      reden: str, dag: str) -> Optional[str]:
    """Verkoop uit een positie. Sluit hem als de hele hoeveelheid weggaat,
    inclusief het gerealiseerde resultaat — dat blijft staan, ook als het een
    verlies is."""
    pos = positie(portfolio_id, symbol)
    if not pos:
        return None
    verkocht = min(qty, pos["qty"])
    gerealiseerd = (prijs - pos["avg_price"]) * verkocht
    rest = pos["qty"] - verkocht
    if rest <= 1e-9:
        conn.execute(
            "UPDATE invest_positions SET qty = 0, status = 'closed', closed_on = ?, "
            "close_reason = ?, realized_pnl = realized_pnl + ? WHERE id = ?",
            (dag, reden, gerealiseerd, pos["id"]),
        )
    else:
        conn.execute(
            "UPDATE invest_positions SET qty = ?, realized_pnl = realized_pnl + ? WHERE id = ?",
            (rest, gerealiseerd, pos["id"]),
        )
    return pos["id"]
