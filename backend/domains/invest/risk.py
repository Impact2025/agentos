"""Risicobeheer — deterministisch, vóór de gate, en het wint van de analist.

De analist bedenkt wát; deze module bepaalt hoevéél, en of het überhaupt mag.
Die scheiding is het punt. Laat je een taalmodel zijn eigen positiegrootte
kiezen, dan bepaalt zijn stemming je risico, en één overtuigend verhaal kost
een kwart van de portefeuille. Hier staat geen mening: de grootte volgt uit de
afstand tot de stop, en de grenzen staan in `shared/config.py`.

Elke weigering komt mét reden terug en wordt vastgelegd. Een voorstel dat
stilletjes verdwijnt, leert je niets over of je klemmen te strak staan — en dan
zet je ze op gevoel bij, wat precies is wat deze module moet voorkomen.

De klemmen, van zwaar naar licht:
  - noodrem (`INVEST_KILL_SWITCH`) en handelsstop na te grote terugval
  - dagverlies boven de grens → vandaag geen nieuwe posities
  - afkoelperiode na een uitgestopte positie in hetzelfde instrument
  - maximum per positie, per assetklasse, en apart voor crypto
  - genoeg cash, inclusief kosten
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

from ...shared.config import (
    INVEST_COOLDOWN_DAYS, INVEST_FEE_FIXED, INVEST_FEE_PCT, INVEST_KILL_SWITCH,
    INVEST_MAX_CLASS_PCT, INVEST_MAX_CRYPTO_PCT, INVEST_MAX_DAY_LOSS_PCT,
    INVEST_MAX_DRAWDOWN_PCT, INVEST_MAX_POSITION_PCT, INVEST_RISK_PER_TRADE,
)
from ...shared.database import get_conn
from ...shared.outcomes import log_outcome
from . import portfolio, universe

logger = logging.getLogger(__name__)


class Oordeel(dict):
    """{toegestaan, qty, waarde_eur, reden} — een dict zodat hij rechtstreeks
    in de API en de voorstellen-tabel past."""


def _afgekeurd(reden: str) -> Oordeel:
    return Oordeel(toegestaan=False, qty=0.0, waarde_eur=0.0, reden=reden)


# ── Portefeuille-brede remmen ──────────────────────────────────────────────

def portefeuille_status(portfolio_id: str = portfolio.STANDAARD_ID) -> Dict[str, Any]:
    """Mag er vandaag überhaupt gehandeld worden? Los van elk voorstel, want
    dit is een eigenschap van de portefeuille en niet van het idee."""
    pf = portfolio.ensure_portfolio(portfolio_id)
    redenen: List[str] = []

    if INVEST_KILL_SWITCH:
        redenen.append("INVEST_KILL_SWITCH staat aan")

    halt = (pf.get("halted_until") or "").strip()
    if halt and halt >= date.today().isoformat():
        redenen.append(f"handelsstop tot {halt}: {pf.get('halt_reason') or 'geen reden vastgelegd'}")

    dd = portfolio.drawdown(portfolio_id)
    if dd is not None and dd >= INVEST_MAX_DRAWDOWN_PCT * 100:
        redenen.append(f"terugval van {dd:.1f}% vanaf de top (grens {INVEST_MAX_DRAWDOWN_PCT * 100:.0f}%)")

    dag = portfolio.dagresultaat(portfolio_id)
    if dag is not None and dag <= -INVEST_MAX_DAY_LOSS_PCT * 100:
        redenen.append(f"dagverlies van {dag:.1f}% (grens -{INVEST_MAX_DAY_LOSS_PCT * 100:.0f}%)")

    return {
        "mag_handelen": not redenen,
        "redenen": redenen,
        "drawdown_pct": dd,
        "dagresultaat_pct": dag,
        "kill_switch": INVEST_KILL_SWITCH,
    }


def zet_handelsstop(portfolio_id: str, dagen: int, reden: str) -> None:
    """Zet de portefeuille stil. Wordt aangeroepen als een grens is geraakt —
    en logt een kaart, want een handelsstop die niemand ziet, is er geen."""
    tot = (date.today() + timedelta(days=max(1, dagen))).isoformat()
    with get_conn() as conn:
        conn.execute(
            "UPDATE invest_portfolio SET halted_until = ?, halt_reason = ? WHERE id = ?",
            (tot, reden[:300], portfolio_id),
        )
    log_outcome(
        "Beursmeester", "handelsstop",
        f"Handel gepauzeerd tot {tot}: {reden}",
        next_step="Kijk of de oorzaak structureel is voordat je hervat. Hervatten kan via "
                  "POST /api/invest/resume.",
        status="error",
    )
    logger.warning("[invest] handelsstop tot %s — %s", tot, reden)


def hervat(portfolio_id: str = portfolio.STANDAARD_ID) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE invest_portfolio SET halted_until = '', halt_reason = '' WHERE id = ?",
            (portfolio_id,),
        )


# ── Per voorstel ───────────────────────────────────────────────────────────

def _afkoeling_actief(portfolio_id: str, symbol: str) -> Optional[str]:
    """Is er kort geleden een positie in dit instrument uitgestopt?

    Direct terugkopen na een stop is de klassieke manier om één verkeerde
    these twee keer te betalen. De afkoelperiode dwingt dat er iets nieuws moet
    zijn gebeurd voordat het idee terugkomt.
    """
    with get_conn() as conn:
        row = conn.execute(
            "SELECT closed_on FROM invest_positions WHERE portfolio_id = ? AND symbol = ? "
            "AND status = 'closed' AND close_reason = 'stop' ORDER BY closed_on DESC LIMIT 1",
            (portfolio_id, symbol),
        ).fetchone()
    if not row or not row["closed_on"]:
        return None
    tot = date.fromisoformat(row["closed_on"]) + timedelta(days=INVEST_COOLDOWN_DAYS)
    if date.today() <= tot:
        return f"uitgestopt op {row['closed_on']}; afkoelperiode loopt tot {tot.isoformat()}"
    return None


def _blootstelling(portfolio_id: str, snap: Dict[str, Any]) -> Dict[str, float]:
    """Huidige waarde per assetklasse in euro."""
    per_klasse: Dict[str, float] = {}
    for p in snap["posities"]:
        if p.get("waarde") is None:
            continue
        klasse = p.get("asset_class") or universe.asset_class(p["symbol"])
        per_klasse[klasse] = per_klasse.get(klasse, 0.0) + p["waarde"]
    return per_klasse


def beoordeel(voorstel: Dict[str, Any],
              portfolio_id: str = portfolio.STANDAARD_ID) -> Oordeel:
    """Mag dit voorstel door, en zo ja: met hoeveel stuks?

    De positiegrootte volgt uit één regel: verlies bij het raken van de stop is
    hoogstens `INVEST_RISK_PER_TRADE` van de NAV. Daarom is een stop verplicht —
    zonder stop bestaat er geen grootte, alleen een gok.
    """
    status = portefeuille_status(portfolio_id)
    if not status["mag_handelen"]:
        return _afgekeurd("; ".join(status["redenen"]))

    symbol = voorstel["symbol"]
    side = voorstel.get("side", "buy")
    snap = portfolio.snapshot(portfolio_id)

    if not snap["volledig"]:
        # Zonder betrouwbare NAV is elke procentuele grens een slag in de
        # lucht. Liever niets doen dan sizen op een verzonnen noemer.
        return _afgekeurd(
            "de NAV is onvolledig ("
            + ", ".join(f"{o['symbol']}: {o['reden']}" for o in snap["onwaardeerbaar"])
            + ") — grenzen zijn dan niet te toetsen"
        )

    if side == "sell":
        pos = portfolio.positie(portfolio_id, symbol)
        if not pos:
            return _afgekeurd("geen open positie om te verkopen")
        return Oordeel(toegestaan=True, qty=pos["qty"],
                       waarde_eur=pos.get("qty", 0) * (voorstel.get("ref_price") or 0),
                       reden="volledige positie sluiten")

    afkoeling = _afkoeling_actief(portfolio_id, symbol)
    if afkoeling:
        return _afgekeurd(afkoeling)

    stop = voorstel.get("stop")
    ref = voorstel.get("ref_price")
    if not stop or not ref or stop >= ref:
        return _afgekeurd("geen bruikbare stop onder de koers")

    valuta = universe.instrument(symbol).valuta if universe.instrument(symbol) else "EUR"
    fx = portfolio.wisselkoers_eur(valuta)
    if fx is None:
        return _afgekeurd(f"geen wisselkoers voor {valuta}")

    nav = snap["nav"]
    risico_eur = nav * INVEST_RISK_PER_TRADE
    verlies_per_stuk_eur = (ref - stop) * fx
    if verlies_per_stuk_eur <= 0:
        return _afgekeurd("stopafstand is nul of negatief")

    qty = risico_eur / verlies_per_stuk_eur
    waarde_eur = qty * ref * fx
    beperkingen: List[str] = []

    # Maximum per positie
    max_positie = nav * INVEST_MAX_POSITION_PCT
    if waarde_eur > max_positie:
        qty = max_positie / (ref * fx)
        waarde_eur = max_positie
        beperkingen.append(f"afgetopt op {INVEST_MAX_POSITION_PCT * 100:.0f}% van de NAV")

    # Maximum per assetklasse (crypto heeft een eigen, strakkere grens)
    klasse = universe.asset_class(symbol)
    grens_pct = INVEST_MAX_CRYPTO_PCT if klasse == universe.CRYPTO else INVEST_MAX_CLASS_PCT
    huidig = _blootstelling(portfolio_id, snap).get(klasse, 0.0)
    ruimte = nav * grens_pct - huidig
    if ruimte <= 0:
        return _afgekeurd(
            f"de klasse '{klasse}' zit al op {huidig / nav * 100:.0f}% van de NAV "
            f"(grens {grens_pct * 100:.0f}%)"
        )
    if waarde_eur > ruimte:
        qty = ruimte / (ref * fx)
        waarde_eur = ruimte
        beperkingen.append(f"beperkt door de klassegrens '{klasse}' ({grens_pct * 100:.0f}%)")

    # Cash, inclusief kosten
    kosten = INVEST_FEE_FIXED + waarde_eur * INVEST_FEE_PCT
    if waarde_eur + kosten > snap["cash"]:
        beschikbaar = snap["cash"] - INVEST_FEE_FIXED
        if beschikbaar <= 0:
            return _afgekeurd(f"onvoldoende cash ({snap['cash']:.2f})")
        qty = beschikbaar / (ref * fx * (1 + INVEST_FEE_PCT))
        waarde_eur = qty * ref * fx
        beperkingen.append("beperkt door de beschikbare cash")

    # Een positie die na alle klemmen niets meer voorstelt, is alleen nog
    # transactiekosten. Die niet nemen is de juiste uitkomst.
    if waarde_eur < max(50.0, INVEST_FEE_FIXED * 20):
        return _afgekeurd(
            f"resterende ruimte is te klein ({waarde_eur:.2f}) — de kosten zouden "
            "het rendement opeten"
        )

    # Hele stukken voor aandelen/ETF's, fracties voor crypto.
    if klasse != universe.CRYPTO:
        qty = float(int(qty))
        if qty < 1:
            return _afgekeurd("minder dan één stuk past binnen de risicogrens")
        waarde_eur = qty * ref * fx
    else:
        qty = round(qty, 6)

    reden = (f"risico {INVEST_RISK_PER_TRADE * 100:.0f}% van NAV ({risico_eur:.0f} EUR) bij een "
             f"stopafstand van {(ref - stop) / ref * 100:.1f}%")
    if beperkingen:
        reden += "; " + ", ".join(beperkingen)

    return Oordeel(toegestaan=True, qty=qty, waarde_eur=round(waarde_eur, 2), reden=reden)


def controleer_grenzen(portfolio_id: str = portfolio.STANDAARD_ID) -> Dict[str, Any]:
    """Draai na elke NAV-update: raakt de portefeuille een harde grens, dan
    gaat de handel stil. Dit is de enige plek die dat automatisch doet, zodat
    er één antwoord bestaat op 'wanneer stoppen we'."""
    status = portefeuille_status(portfolio_id)
    pf = portfolio.ensure_portfolio(portfolio_id)
    al_stil = (pf.get("halted_until") or "") >= date.today().isoformat()

    dd = status["drawdown_pct"]
    if dd is not None and dd >= INVEST_MAX_DRAWDOWN_PCT * 100 and not al_stil:
        zet_handelsstop(
            portfolio_id, 30,
            f"terugval van {dd:.1f}% vanaf de top overschrijdt de grens van "
            f"{INVEST_MAX_DRAWDOWN_PCT * 100:.0f}%",
        )
    return status
