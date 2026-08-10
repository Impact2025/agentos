"""Managementinformatie over de Beursmeester — de cijfers waarop je stuurt.

`portfolio.py` beantwoordt "wat heb ik?", `risk.py` "mag dit?". Dit bestand
beantwoordt de derde vraag, die tot nu toe nergens stond: **werkt het, en waar
gaat het mis?** Een rendementspercentage alleen zegt dat niet. Twee
portefeuilles op +3% kunnen een trefpercentage van 70% met kleine winsten en
één grote verliezer hebben, of andersom — en dat verschil bepaalt of je
doorgaat of stopt.

Vier ontwerpkeuzes, alle vier dezelfde als elders in dit domein:

(a) **Elk cijfer draagt zijn n mee.** Een trefpercentage van 100% over twee
    afgesloten posities is geen prestatie maar een steekproef van twee. Daarom
    geeft elke statistiek een `zeggingskracht` terug (`geen`/`indicatief`/
    `betekenisvol`) en toont de UI die naast het getal. Een dashboard dat 100%
    laat zien zonder de n erbij, is precies hoe je een strategie opschaalt die
    nog niets heeft bewezen.

(b) **Onmeetbaar is niet nul.** Ontbreekt de wisselkoers van de sluitdag, dan
    valt die positie buiten de euro-statistiek en wordt hij apart geteld — hij
    wordt niet stilzwijgend op 0 gezet. Zelfde regel als in `portfolio.snapshot`.

(c) **De NAV-reeks heeft gaten by design.** `leg_nav_vast` slaat dagen met een
    onvolledige NAV over. Volatiliteit en maximale terugval berekend over een
    reeks met gaten zijn stelselmatig te gúnstig: precies de dagen die
    ontbreken zijn de dagen waarop iets niet klopte. `koerslijn()` telt die
    gaten daarom en meldt ze; boven een handvol gaten staat er bij de risicomaten
    "onbetrouwbaar" in plaats van een getal dat er goed uitziet.

(d) **Percentages zijn valuta-vrij, euro's niet.** Het koersresultaat van een
    positie in dollars is in procenten hetzelfde in beide valuta; in euro's
    niet. Vandaar dat `resultaat_pct` en de R-veelvoud op de kale koersbeweging
    rusten (exact), en `resultaat_eur` op de wisselkoers van de sluitdag plus de
    werkelijke kosten (bij meerdere deelverkopen een benadering — dat staat dan
    ook in het veld `fx_benadering`).
"""
from __future__ import annotations

import logging
import math
from datetime import date, datetime
from typing import Any, Dict, List, Optional

from ...shared.config import (
    INVEST_MAX_CLASS_PCT, INVEST_MAX_CRYPTO_PCT, INVEST_MAX_DRAWDOWN_PCT,
    INVEST_MAX_POSITION_PCT, INVEST_RISK_PER_TRADE,
)
from ...shared.database import get_conn
from ...shared.learning import track_record
from . import history, portfolio, risk, service, universe

logger = logging.getLogger(__name__)

# Onder deze aantallen is een statistiek een anekdote. De grenzen zijn niet
# heilig, maar ze moeten érgens staan: zonder drempel leest "trefpercentage 100%"
# na twee trades als bewijs.
_N_INDICATIEF = 5
_N_BETEKENISVOL = 20

# Handelsdagen per jaar — voor het annualiseren van dagvolatiliteit.
_HANDELSDAGEN = 252


def _zeggingskracht(n: int) -> str:
    if n < _N_INDICATIEF:
        return "geen"
    if n < _N_BETEKENISVOL:
        return "indicatief"
    return "betekenisvol"


def _dagen_tussen(a: str, b: str) -> Optional[int]:
    try:
        return (date.fromisoformat(b) - date.fromisoformat(a)).days
    except (ValueError, TypeError):
        return None


# ── Afgesloten posities: het trackrecord ───────────────────────────────────

def _trades_van_positie(positie_id: str) -> List[Dict[str, Any]]:
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM invest_trades WHERE position_id = ? ORDER BY executed_on",
            (positie_id,),
        ).fetchall()]


def gesloten_resultaten(portfolio_id: str = portfolio.STANDAARD_ID) -> Dict[str, Any]:
    """Eén regel per afgesloten positie, mét kosten, R-veelvoud en looptijd.

    Verliezers blijven staan (zie `portfolio.py`, keuze a). Een positie waarvan
    het euro-resultaat niet eerlijk te bepalen is, komt terug in `onmeetbaar` —
    zichtbaar, niet weggelaten, want een weggelaten verliezer vleit precies
    zoals een verzwegen verliezer dat doet.
    """
    regels: List[Dict[str, Any]] = []
    onmeetbaar: List[Dict[str, str]] = []

    for p in portfolio.posities(portfolio_id, alleen_open=False):
        if p["status"] != "closed":
            continue
        trades = _trades_van_positie(p["id"])
        gekocht = sum(t["qty"] for t in trades if t["side"] == "buy")
        kosten = round(sum(t["fee"] or 0.0 for t in trades), 2)
        verkopen = [t for t in trades if t["side"] == "sell"]

        inleg = (p["avg_price"] or 0.0) * gekocht
        bruto = p["realized_pnl"] or 0.0
        valuta = universe.instrument(p["symbol"]).valuta if universe.instrument(p["symbol"]) else "EUR"
        fx = portfolio.wisselkoers_eur(valuta, p["closed_on"] or None)

        regel: Dict[str, Any] = {
            "symbol": p["symbol"],
            "asset_class": p["asset_class"] or universe.asset_class(p["symbol"]),
            "opened_on": p["opened_on"],
            "closed_on": p["closed_on"],
            "looptijd_dagen": _dagen_tussen(p["opened_on"], p["closed_on"] or ""),
            "close_reason": p["close_reason"] or "",
            "qty": gekocht,
            "avg_price": p["avg_price"],
            "stop": p["stop"],
            "kosten_eur": kosten,
            "thesis": (p["thesis"] or "")[:200],
            # Valuta-vrij en dus exact: de kale koersbeweging op de inleg.
            "resultaat_pct": round(bruto / inleg * 100, 2) if inleg else None,
            "fx_benadering": valuta != "EUR" and len(verkopen) > 1,
        }

        # R-veelvoud: hoeveel keer het vooraf geaccepteerde verlies is dit
        # geworden? Dit is het enige getal waarin een grote en een kleine
        # positie eerlijk naast elkaar staan. Zonder stop bestaat het niet —
        # en dan is dat het feit dat gerapporteerd hoort te worden, niet 0.
        if p["stop"] and p["avg_price"] and p["avg_price"] > p["stop"]:
            risico_per_stuk = p["avg_price"] - p["stop"]
            regel["r_multiple"] = round(bruto / (risico_per_stuk * gekocht), 2) if gekocht else None
        else:
            regel["r_multiple"] = None

        if fx is None:
            onmeetbaar.append({"symbol": p["symbol"],
                               "reden": f"geen wisselkoers voor {valuta} op {p['closed_on']}"})
            regel["resultaat_eur"] = None
        else:
            regel["resultaat_eur"] = round(bruto * fx - kosten, 2)
        regels.append(regel)

    regels.sort(key=lambda r: r["closed_on"] or "", reverse=True)
    return {"regels": regels, "onmeetbaar": onmeetbaar}


def handelsstatistiek(portfolio_id: str = portfolio.STANDAARD_ID) -> Dict[str, Any]:
    """Het trackrecord in de vier cijfers die er toe doen.

    Trefpercentage alléén stuurt de verkeerde kant op: het is triviaal te
    verhogen door winst te vroeg te pakken en verlies te laten lopen. Daarom
    staat de payoff (gemiddelde winst ÷ gemiddeld verlies) er altijd naast, en
    is de verwachting per idee het cijfer waarop je stuurt — dat is het enige
    getal dat zegt of méér ideeën ook méér geld betekent.
    """
    gesloten = gesloten_resultaten(portfolio_id)
    meetbaar = [r for r in gesloten["regels"] if r["resultaat_eur"] is not None]
    n = len(meetbaar)

    basis: Dict[str, Any] = {
        "n": n,
        "n_onmeetbaar": len(gesloten["onmeetbaar"]),
        "zeggingskracht": _zeggingskracht(n),
        "onmeetbaar": gesloten["onmeetbaar"],
    }
    if not n:
        return {**basis, "toelichting": "Nog geen afgesloten posities — er valt niets af te rekenen."}

    winst = [r["resultaat_eur"] for r in meetbaar if r["resultaat_eur"] > 0]
    verlies = [r["resultaat_eur"] for r in meetbaar if r["resultaat_eur"] <= 0]
    som_winst = sum(winst)
    som_verlies = abs(sum(verlies))
    gem_winst = som_winst / len(winst) if winst else None
    gem_verlies = som_verlies / len(verlies) if verlies else None

    # Per sluitreden: dit legt bloot of de stops of de koersdoelen het werk
    # doen. Een portefeuille waarin álles op 'horizon' sluit, heeft geen thesis
    # maar een klok.
    per_reden: Dict[str, Dict[str, Any]] = {}
    for r in meetbaar:
        emmer = per_reden.setdefault(r["close_reason"] or "onbekend",
                                     {"n": 0, "resultaat_eur": 0.0})
        emmer["n"] += 1
        emmer["resultaat_eur"] = round(emmer["resultaat_eur"] + r["resultaat_eur"], 2)

    r_waarden = [r["r_multiple"] for r in meetbaar if r["r_multiple"] is not None]
    looptijden = [r["looptijd_dagen"] for r in meetbaar if r["looptijd_dagen"] is not None]

    return {
        **basis,
        "winnaars": len(winst),
        "verliezers": len(verlies),
        "trefpercentage": round(len(winst) / n * 100, 1),
        "resultaat_eur": round(som_winst - som_verlies, 2),
        "kosten_eur": round(sum(r["kosten_eur"] for r in meetbaar), 2),
        "gem_winst_eur": round(gem_winst, 2) if gem_winst is not None else None,
        "gem_verlies_eur": round(gem_verlies, 2) if gem_verlies is not None else None,
        # Payoff en profit factor bestaan alleen als er van béide soorten iets
        # is. Delen door nul geeft "oneindig", en dat leest als geniaal.
        "payoff": round(gem_winst / gem_verlies, 2) if (gem_winst and gem_verlies) else None,
        "profit_factor": round(som_winst / som_verlies, 2) if som_verlies else None,
        "verwachting_eur": round(sum(r["resultaat_eur"] for r in meetbaar) / n, 2),
        "verwachting_r": round(sum(r_waarden) / len(r_waarden), 2) if r_waarden else None,
        "n_zonder_stop": n - len(r_waarden),
        "beste": max(meetbaar, key=lambda r: r["resultaat_eur"]),
        "slechtste": min(meetbaar, key=lambda r: r["resultaat_eur"]),
        "gem_looptijd_dagen": round(sum(looptijden) / len(looptijden), 1) if looptijden else None,
        "per_reden": per_reden,
    }


# ── Open posities: waar staat het geld, en wat kan het kosten? ─────────────

def open_risico(portfolio_id: str = portfolio.STANDAARD_ID) -> Dict[str, Any]:
    """Wat verlies je als élke stop vandaag wordt geraakt?

    Dit is het cijfer dat in een dashboard hoort en er zelden staat. De waarde
    van de portefeuille zegt niets over de kwetsbaarheid ervan: vier posities
    die elk 1% risico dragen zijn samen 4% — en dat is het bedrag waarop je een
    slechte week beoordeelt, niet de NAV.

    Een positie zónder stop draagt geen bekend risico maar een onbekend risico;
    die wordt apart geteld en nooit als 0 meegerekend (invariant
    `positie_zonder_stop` meldt hem los).
    """
    snap = portfolio.snapshot(portfolio_id)
    nav = snap["nav"]
    regels: List[Dict[str, Any]] = []
    totaal = 0.0
    zonder_stop: List[str] = []
    onbekend: List[str] = []

    for p in snap["posities"]:
        valuta = universe.instrument(p["symbol"]).valuta if universe.instrument(p["symbol"]) else "EUR"
        fx = portfolio.wisselkoers_eur(valuta)
        koers = p.get("koers")
        regel: Dict[str, Any] = {
            "symbol": p["symbol"],
            "asset_class": p.get("asset_class") or universe.asset_class(p["symbol"]),
            "qty": p["qty"],
            "koers": koers,
            "stop": p.get("stop"),
            "target": p.get("target"),
            "waarde": p.get("waarde"),
            "pnl_pct": p.get("pnl_pct"),
            "opened_on": p.get("opened_on"),
            "horizon_days": p.get("horizon_days"),
            "thesis": (p.get("thesis") or "")[:200],
        }
        # Dagen open tegen de horizon: een positie die zijn horizon voorbij is,
        # wordt door `controleer_stops` gesloten. Staat hij er tóch nog, dan is
        # dat een storing en geen keuze — daarom zichtbaar.
        dagen_open = _dagen_tussen(p.get("opened_on") or "", date.today().isoformat())
        regel["dagen_open"] = dagen_open
        regel["horizon_verstreken"] = bool(
            dagen_open is not None and p.get("horizon_days") and dagen_open > p["horizon_days"]
        )

        if not p.get("stop"):
            zonder_stop.append(p["symbol"])
            regel["risico_eur"] = None
        elif koers is None or fx is None:
            onbekend.append(p["symbol"])
            regel["risico_eur"] = None
        else:
            # Staat de koers al ónder de stop, dan is het risico wat er nu nog
            # op tafel ligt; negatief zou betekenen dat sluiten winst oplevert.
            risico = max(0.0, (koers - p["stop"]) * p["qty"] * fx)
            regel["risico_eur"] = round(risico, 2)
            regel["afstand_stop_pct"] = round((koers - p["stop"]) / koers * 100, 2)
            if p.get("target"):
                regel["afstand_target_pct"] = round((p["target"] - koers) / koers * 100, 2)
                # Wat je kunt winnen tegen wat je kunt verliezen, vanaf hier.
                if koers > p["stop"]:
                    regel["rr_resterend"] = round((p["target"] - koers) / (koers - p["stop"]), 2)
            totaal += risico
        regels.append(regel)

    return {
        "posities": regels,
        "risico_eur": round(totaal, 2),
        "risico_pct_nav": round(totaal / nav * 100, 2) if nav else None,
        "zonder_stop": zonder_stop,
        "risico_onbekend": onbekend,
        # De grens per idee staat in de config; hier tel je op wat er in totaal
        # openstaat. Zonder die vergelijking is "4,1%" een getal zonder maat.
        "grens_per_idee_pct": round(INVEST_RISK_PER_TRADE * 100, 2),
        "volledig": not zonder_stop and not onbekend,
    }


def blootstelling(portfolio_id: str = portfolio.STANDAARD_ID) -> Dict[str, Any]:
    """Waar staat het geld, en hoeveel ruimte is er nog binnen de klemmen?

    De klemmen uit `risk.py` zijn onzichtbaar tot ze een voorstel blokkeren.
    Dan is het te laat om te begrijpen waaróm — je ziet alleen "geweigerd".
    Hier staan ze vóóraf, met de benutting erbij.
    """
    snap = portfolio.snapshot(portfolio_id)
    nav = snap["nav"]
    per_klasse: Dict[str, float] = {}
    grootste = None

    for p in snap["posities"]:
        if p.get("waarde") is None:
            continue
        klasse = p.get("asset_class") or universe.asset_class(p["symbol"])
        per_klasse[klasse] = per_klasse.get(klasse, 0.0) + p["waarde"]
        if grootste is None or p["waarde"] > grootste["waarde"]:
            grootste = {"symbol": p["symbol"], "waarde": p["waarde"]}

    klassen = []
    for klasse, waarde in sorted(per_klasse.items(), key=lambda kv: -kv[1]):
        grens = INVEST_MAX_CRYPTO_PCT if klasse == universe.CRYPTO else INVEST_MAX_CLASS_PCT
        klassen.append({
            "klasse": klasse,
            "waarde_eur": round(waarde, 2),
            "pct_nav": round(waarde / nav * 100, 2) if nav else None,
            "grens_pct": round(grens * 100, 1),
            "benutting_pct": round(waarde / (nav * grens) * 100, 1) if nav and grens else None,
        })

    return {
        "nav": nav if snap["volledig"] else None,
        "cash_eur": snap["cash"],
        "cash_pct": round(snap["cash"] / nav * 100, 2) if nav else None,
        "belegd_pct": round(snap["positions_value"] / nav * 100, 2) if nav else None,
        "klassen": klassen,
        "grootste_positie": ({**grootste, "pct_nav": round(grootste["waarde"] / nav * 100, 2)}
                             if grootste and nav else None),
        "grens_positie_pct": round(INVEST_MAX_POSITION_PCT * 100, 1),
        "volledig": snap["volledig"],
        "onwaardeerbaar": snap["onwaardeerbaar"],
    }


# ── De koerslijn en de risicomaten eronder ─────────────────────────────────

def _handelsdagen_tussen(vanaf: str, tot: str) -> List[str]:
    """De dagen waarop de benchmark handelde. Dát is de kalender van deze
    portefeuille — niet de kalenderdagen, want in het weekend hoort er geen
    NAV-punt te zijn en een ontbrekend weekendpunt is dus geen gat."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT date FROM market_history WHERE symbol = ? AND date >= ? AND date <= ? "
            "ORDER BY date", (universe.BENCHMARK, vanaf, tot),
        ).fetchall()
    return [r["date"] for r in rows]


def koerslijn(portfolio_id: str = portfolio.STANDAARD_ID, dagen: int = 365) -> Dict[str, Any]:
    """De NAV náást de benchmark, beide herschaald naar 100 op de eerste dag.

    Herschalen is geen cosmetica: een NAV van €10.000 en een ETF-koers van €95
    in één grafiek zetten vraagt om twee assen, en twee assen kun je zó kiezen
    dat elke lijn wint. Op 100 beginnen is de enige weergave waarin het
    verschil zichtbaar is zonder dat de schaal een mening heeft.
    """
    reeks = portfolio.nav_reeks(portfolio_id, dagen=dagen)
    if not reeks:
        return {"punten": [], "gaten": None, "risico": {"reden": "nog geen NAV-reeks"}}

    nav0 = reeks[0]["nav"]
    bench0 = next((r["benchmark_price"] for r in reeks if r.get("benchmark_price")), None)
    punten = []
    for r in reeks:
        punten.append({
            "date": r["date"],
            "nav": round(r["nav"], 2),
            "nav_index": round(r["nav"] / nav0 * 100, 2) if nav0 else None,
            "bench_index": (round(r["benchmark_price"] / bench0 * 100, 2)
                            if bench0 and r.get("benchmark_price") else None),
        })

    handelsdagen = _handelsdagen_tussen(reeks[0]["date"], reeks[-1]["date"])
    aanwezig = {r["date"] for r in reeks}
    gaten = [d for d in handelsdagen if d not in aanwezig]

    return {
        "punten": punten,
        "vanaf": reeks[0]["date"],
        "tot": reeks[-1]["date"],
        "gaten": len(gaten),
        "gaten_dagen": gaten[-10:],
        "risico": _risicomaten([r["nav"] for r in reeks], len(gaten)),
    }


def _risicomaten(navs: List[float], gaten: int) -> Dict[str, Any]:
    """Volatiliteit, maximale terugval en rendement-per-risico.

    Bewust géén getal bij te weinig punten of te veel gaten. Een volatiliteit
    over acht dagen is ruis met een decimaal, en een maximale terugval over een
    reeks waarin de slechte dagen ontbreken (zie de moduledocstring) is
    stelselmatig te mooi. Liever een reden dan een cijfer.
    """
    if len(navs) < 20:
        return {"reden": f"te weinig meetpunten ({len(navs)}) — minimaal 20 nodig"}
    if gaten > max(3, len(navs) * 0.1):
        return {"reden": f"{gaten} ontbrekende handelsdagen in de reeks — "
                         "risicomaten zouden te gunstig uitvallen"}

    rendementen = [(navs[i] - navs[i - 1]) / navs[i - 1]
                   for i in range(1, len(navs)) if navs[i - 1]]
    if not rendementen:
        return {"reden": "geen bruikbare dagrendementen"}

    gem = sum(rendementen) / len(rendementen)
    var = sum((r - gem) ** 2 for r in rendementen) / (len(rendementen) - 1) if len(rendementen) > 1 else 0.0
    dagvol = math.sqrt(var)
    vol = dagvol * math.sqrt(_HANDELSDAGEN) * 100

    top = navs[0]
    max_dd = 0.0
    for n in navs:
        top = max(top, n)
        max_dd = max(max_dd, (top - n) / top * 100 if top else 0.0)

    return {
        "volatiliteit_pct": round(vol, 2),
        "max_drawdown_pct": round(max_dd, 2),
        # Rendement per eenheid risico, met een risicovrije voet van 0. Dat
        # laatste staat er expliciet bij: bij een positieve rente vleit een
        # Sharpe zonder voet het resultaat.
        "sharpe": (round(gem * _HANDELSDAGEN / (dagvol * math.sqrt(_HANDELSDAGEN)), 2)
                   if dagvol else None),
        "sharpe_voet": "risicovrije voet 0%",
        "meetpunten": len(navs),
        "grens_drawdown_pct": round(INVEST_MAX_DRAWDOWN_PCT * 100, 1),
    }


# ── De machine zelf: rondes, voorstellen, denkwerk ─────────────────────────

def voorstel_trechter(portfolio_id: str = portfolio.STANDAARD_ID,
                      dagen: int = 90) -> Dict[str, Any]:
    """Van idee naar order, met de uitval per stap.

    Dezelfde formule als bij de acquisitie (`prospecting/funnel.py`): zonder de
    verhouding tussen invoer en uitvoer weet je niet of de agent te weinig
    ideeën heeft of te strakke klemmen — en dat zijn tegengestelde ingrepen.
    """
    grens = f"-{max(1, dagen)} days"
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT status, COUNT(*) AS n FROM invest_proposals WHERE portfolio_id = ? "
            "AND created_at >= datetime('now', ?) GROUP BY status",
            (portfolio_id, grens),
        ).fetchall()
        redenen = conn.execute(
            "SELECT risk_note, COUNT(*) AS n FROM invest_proposals WHERE portfolio_id = ? "
            "AND status = 'geblokkeerd' AND created_at >= datetime('now', ?) "
            "GROUP BY risk_note ORDER BY n DESC LIMIT 6",
            (portfolio_id, grens),
        ).fetchall()
    tel = {r["status"]: r["n"] for r in rows}
    voorgesteld = sum(tel.values())
    gevuld = tel.get("filled", 0)
    return {
        "dagen": dagen,
        "voorgesteld": voorgesteld,
        "in_review": tel.get("pending_review", 0),
        "geblokkeerd": tel.get("geblokkeerd", 0),
        "afgewezen": tel.get("rejected", 0),
        "uitgevoerd": gevuld,
        "conversie_pct": round(gevuld / voorgesteld * 100, 1) if voorgesteld else None,
        "blokkade_redenen": [{"reden": (r["risk_note"] or "onbekend")[:160], "n": r["n"]}
                             for r in redenen],
    }


def ronde_historie(portfolio_id: str = portfolio.STANDAARD_ID,
                   limiet: int = 14) -> Dict[str, Any]:
    """De laatste rondes, mét de herkomst van het denkwerk.

    Een ronde op de terugval kan niets backtesten en levert per definitie nul
    voorstellen op. In de cijfers ziet dat er precies zo uit als "de analist
    vond vandaag niets" — een heel ander bericht. Daarom staat de verhouding
    hier apart, en niet alleen de laatste ronde.
    """
    with get_conn() as conn:
        rijen = [dict(r) for r in conn.execute(
            "SELECT * FROM invest_runs WHERE portfolio_id = ? ORDER BY created_at DESC LIMIT ?",
            (portfolio_id, limiet),
        ).fetchall()]
        verdeling = {r["denkwerk"] or "onbekend": r["n"] for r in conn.execute(
            "SELECT denkwerk, COUNT(*) AS n FROM invest_runs WHERE portfolio_id = ? "
            "AND created_at >= datetime('now', '-30 days') GROUP BY denkwerk",
            (portfolio_id,),
        ).fetchall()}

    laatste = rijen[0] if rijen else None
    dagen_geleden = _dagen_tussen(laatste["run_date"], date.today().isoformat()) if laatste else None
    echt = verdeling.get("claude_code", 0)
    totaal = sum(verdeling.values())
    return {
        "rondes": rijen,
        "denkwerk_30d": verdeling,
        "echt_denkwerk_pct": round(echt / totaal * 100, 1) if totaal else None,
        "laatste_run_dagen_geleden": dagen_geleden,
        # Twee dagen zonder ronde is een weekend; meer is een storing die niet
        # als "rustig" mag lezen.
        "achterstallig": bool(dagen_geleden is not None and dagen_geleden > 2),
        "fouten": [r for r in rijen if r["status"] == "error"],
    }


# ── Alles bij elkaar ───────────────────────────────────────────────────────

def management_rapport(portfolio_id: str = portfolio.STANDAARD_ID) -> Dict[str, Any]:
    """Het volledige beeld voor het dashboard. Eén call, want een dashboard dat
    zeven endpoints los ophaalt, toont zeven momenten door elkaar."""
    snap = portfolio.snapshot(portfolio_id)
    rend = portfolio.rendement(portfolio_id)
    stat = handelsstatistiek(portfolio_id)
    lijn = koerslijn(portfolio_id)
    risico_open = open_risico(portfolio_id)
    expo = blootstelling(portfolio_id)
    rondes = ronde_historie(portfolio_id)

    return {
        "gegenereerd_op": datetime.now().isoformat(timespec="seconds"),
        "portefeuille": snap,
        "rendement": rend,
        "risico": risk.portefeuille_status(portfolio_id),
        "open_risico": risico_open,
        "blootstelling": expo,
        "koerslijn": lijn,
        "trackrecord": stat,
        "gesloten": gesloten_resultaten(portfolio_id)["regels"][:25],
        "trades": portfolio.trades(portfolio_id, limiet=25),
        "voorstellen": service.open_voorstellen(portfolio_id),
        "trechter": voorstel_trechter(portfolio_id),
        "rondes": rondes,
        "trefkans": track_record(service.AGENT),
        "dekking": history.dekking(),
        "verouderd": history.verouderde_symbolen(),
        "aandachtspunten": _aandachtspunten(snap, rend, risico_open, expo, rondes),
    }


def _aandachtspunten(snap: Dict[str, Any], rend: Dict[str, Any],
                     open_r: Dict[str, Any], expo: Dict[str, Any],
                     rondes: Dict[str, Any]) -> List[Dict[str, str]]:
    """Wat vraagt aandacht — deterministisch, zonder LLM.

    Zelfde afweging als bij `bridge/build_pulse` en de kansen-gate: een oordeel
    dat een gateway nodig heeft, valt stil op de dag dat je het nodig hebt. En
    zonder deze lijst moet een mens elf tabellen lezen om te zien of er iets
    aan de hand is — dan wordt er niet gekeken.
    """
    punten: List[Dict[str, str]] = []

    if not snap["volledig"]:
        punten.append({"ernst": "blokkerend", "tekst":
                       "De NAV is onvolledig: " + ", ".join(
                           f"{o['symbol']} ({o['reden']})" for o in snap["onwaardeerbaar"]),
                       "waarom": "Zonder volledige NAV weigert de risicotoets te sizen — "
                                 "er komen dus geen voorstellen door."})
    if open_r["zonder_stop"]:
        punten.append({"ernst": "blokkerend",
                       "tekst": "Positie zonder stop: " + ", ".join(open_r["zonder_stop"]),
                       "waarom": "Het maximale verlies op die positie is onbekend; "
                                 "de dagelijkse stopcontrole beschermt hem niet."})
    if rondes["achterstallig"]:
        punten.append({"ernst": "stil", "tekst":
                       f"De laatste ronde was {rondes['laatste_run_dagen_geleden']} dagen geleden.",
                       "waarom": "Stops en koersdoelen worden in de ronde getoetst; "
                                 "zonder ronde staan open posities onbewaakt."})
    if rondes.get("echt_denkwerk_pct") is not None and rondes["echt_denkwerk_pct"] < 50:
        punten.append({"ernst": "stil", "tekst":
                       f"Slechts {rondes['echt_denkwerk_pct']}% van de rondes van de laatste "
                       "30 dagen deed écht denkwerk (de rest viel terug op de gateway).",
                       "waarom": "Een terugval-ronde kan niets backtesten en levert per "
                                 "definitie geen voorstellen op."})
    for r in open_r["posities"]:
        if r.get("horizon_verstreken"):
            punten.append({"ernst": "stil", "tekst":
                           f"{r['symbol']} staat {r['dagen_open']} dagen open bij een horizon "
                           f"van {r['horizon_days']} dagen.",
                           "waarom": "De ronde hoort hem te sluiten; dat hij er nog staat "
                                     "betekent dat die stap niet is uitgevoerd."})
    if rend.get("alpha_pct") is not None and rend["alpha_pct"] < 0:
        punten.append({"ernst": "hygiene", "tekst":
                       f"De portefeuille loopt {abs(rend['alpha_pct'])}% achter op "
                       f"{rend['benchmark_symbol']}.",
                       "waarom": "Dat is de hele meetlat: blijft dit staan, dan had het geld "
                                 "beter in de index gestaan."})
    if expo.get("cash_pct") is not None and expo["cash_pct"] > 90:
        punten.append({"ernst": "hygiene",
                       "tekst": f"{expo['cash_pct']}% staat in cash.",
                       "waarom": "Niet fout, wel een keuze: er wordt op dit moment nauwelijks "
                                 "risico genomen, dus ook geen rendement gemaakt."})

    volgorde = {"blokkerend": 0, "stil": 1, "hygiene": 2}
    punten.sort(key=lambda p: volgorde.get(p["ernst"], 3))
    return punten
