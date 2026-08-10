"""Marktkenmerken — deterministisch, zonder LLM.

Dezelfde reden als bij `seo/opportunity_quality.py`: een systeem dat pas een
mening heeft als de gateway antwoordt, valt stil op precies de dag dat je het
nodig hebt. De cijfers hieronder — trend, momentum, volatiliteit, regime —
staan er ook als er nergens een model bereikbaar is. Claude krijgt ze áls
invoer; hij rekent ze niet zelf uit, want dan zou dezelfde vraag twee
antwoorden kunnen hebben.

Alles rekent op de opgeslagen reeks in `market_history`, in puur Python: geen
pandas/numpy nodig, en daardoor ook testbaar zonder netwerk.

`None` betekent overal "te weinig historie om dit eerlijk te zeggen". Nooit 0.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from . import history, universe


def _sluitingen(reeks: List[Dict[str, Any]]) -> List[float]:
    return [r["close"] for r in reeks if r.get("close") is not None]


def sma(reeks: List[Dict[str, Any]], n: int) -> Optional[float]:
    c = _sluitingen(reeks)
    if len(c) < n:
        return None
    return round(sum(c[-n:]) / n, 4)


def rsi(reeks: List[Dict[str, Any]], n: int = 14) -> Optional[float]:
    """Wilder's RSI. Onder ~30 oververkocht, boven ~70 overgekocht — bedoeld
    als context bij een these, niet als signaal op zichzelf."""
    c = _sluitingen(reeks)
    if len(c) < n + 1:
        return None
    winst = verlies = 0.0
    for i in range(1, n + 1):
        delta = c[i] - c[i - 1]
        winst += max(delta, 0.0)
        verlies += max(-delta, 0.0)
    gem_w, gem_v = winst / n, verlies / n
    for i in range(n + 1, len(c)):
        delta = c[i] - c[i - 1]
        gem_w = (gem_w * (n - 1) + max(delta, 0.0)) / n
        gem_v = (gem_v * (n - 1) + max(-delta, 0.0)) / n
    if gem_v == 0:
        return 100.0
    rs = gem_w / gem_v
    return round(100 - (100 / (1 + rs)), 1)


def atr(reeks: List[Dict[str, Any]], n: int = 14) -> Optional[float]:
    """Average True Range — de dagelijkse ademhaling van een instrument.

    Dit is het getal waar de positiegrootte op rust: een stop op 2×ATR onder
    de instap betekent iets anders voor Bitcoin dan voor een obligatie-ETF.
    Zonder ATR wordt 'grootte' een gevoel, en dan bepaalt de analist zijn eigen
    risico.
    """
    if len(reeks) < n + 1:
        return None
    trs: List[float] = []
    for i in range(1, len(reeks)):
        hoog, laag = reeks[i].get("high"), reeks[i].get("low")
        vorige_slot = reeks[i - 1].get("close")
        if hoog is None or laag is None or vorige_slot is None:
            continue
        trs.append(max(hoog - laag, abs(hoog - vorige_slot), abs(laag - vorige_slot)))
    if len(trs) < n:
        return None
    return round(sum(trs[-n:]) / n, 4)


def rendement_pct(reeks: List[Dict[str, Any]], dagen: int) -> Optional[float]:
    c = _sluitingen(reeks)
    if len(c) < dagen + 1 or not c[-(dagen + 1)]:
        return None
    return round((c[-1] - c[-(dagen + 1)]) / c[-(dagen + 1)] * 100, 2)


def afstand_tot_top(reeks: List[Dict[str, Any]], dagen: int = 252) -> Optional[float]:
    c = _sluitingen(reeks)[-dagen:]
    if len(c) < 20:
        return None
    top = max(c)
    if not top:
        return None
    return round((c[-1] - top) / top * 100, 2)


def correlatie(a: List[float], b: List[float]) -> Optional[float]:
    """Pearson over dagrendementen. None bij te weinig overlap."""
    n = min(len(a), len(b))
    if n < 30:
        return None
    ra = [(a[i] - a[i - 1]) / a[i - 1] for i in range(1, n) if a[i - 1]]
    rb = [(b[i] - b[i - 1]) / b[i - 1] for i in range(1, n) if b[i - 1]]
    m = min(len(ra), len(rb))
    if m < 20:
        return None
    ra, rb = ra[-m:], rb[-m:]
    ga, gb = sum(ra) / m, sum(rb) / m
    teller = sum((ra[i] - ga) * (rb[i] - gb) for i in range(m))
    noemer = (sum((x - ga) ** 2 for x in ra) ** 0.5) * (sum((x - gb) ** 2 for x in rb) ** 0.5)
    if not noemer:
        return None
    return round(teller / noemer, 2)


def kenmerken(symbol: str) -> Dict[str, Any]:
    """Het volledige kenmerkenblok van één instrument."""
    reeks = history.reeks(symbol, dagen=400)
    laatste = reeks[-1] if reeks else None
    return {
        "symbol": symbol,
        "naam": (universe.instrument(symbol).naam if universe.instrument(symbol) else symbol),
        "asset_class": universe.asset_class(symbol),
        "koers": laatste["close"] if laatste else None,
        "koers_dag": laatste["date"] if laatste else None,
        "verouderd": history.is_verouderd(symbol),
        "sma20": sma(reeks, 20),
        "sma50": sma(reeks, 50),
        "sma200": sma(reeks, 200),
        "rsi14": rsi(reeks),
        "atr14": atr(reeks),
        "atr_pct": (round(atr(reeks) / laatste["close"] * 100, 2)
                    if atr(reeks) and laatste and laatste["close"] else None),
        "rendement_5d": rendement_pct(reeks, 5),
        "rendement_21d": rendement_pct(reeks, 21),
        "rendement_126d": rendement_pct(reeks, 126),
        "afstand_tot_52w_top": afstand_tot_top(reeks),
        "boven_sma200": (None if not (laatste and sma(reeks, 200))
                         else laatste["close"] > sma(reeks, 200)),
        "dagen_historie": len(reeks),
    }


def regime() -> Dict[str, Any]:
    """Het marktklimaat in één deterministisch oordeel.

    Drie ingrediënten met elk een duidelijke richting: de trend van de brede
    index (boven of onder het 200-daags gemiddelde), de volatiliteit (VIX) en
    de rente-richting. Een LLM mag hier nuance aan toevoegen, maar het *label*
    komt hiervandaan — anders verschuift de definitie van 'risk-off' met de
    stemming van het model.
    """
    spx = kenmerken("^GSPC")
    vix = history.laatste_slot("^VIX")
    tnx = kenmerken("^TNX")

    punten = 0
    redenen: List[str] = []

    if spx["boven_sma200"] is True:
        punten += 1
        redenen.append("S&P 500 boven het 200-daags gemiddelde")
    elif spx["boven_sma200"] is False:
        punten -= 1
        redenen.append("S&P 500 onder het 200-daags gemiddelde")

    if vix:
        if vix[1] < 18:
            punten += 1
            redenen.append(f"VIX rustig ({vix[1]:.1f})")
        elif vix[1] > 25:
            punten -= 1
            redenen.append(f"VIX verhoogd ({vix[1]:.1f})")

    if tnx["rendement_21d"] is not None:
        if tnx["rendement_21d"] > 5:
            punten -= 1
            redenen.append(f"rente sterk opgelopen ({tnx['rendement_21d']:+.1f}% in een maand)")
        elif tnx["rendement_21d"] < -5:
            punten += 1
            redenen.append(f"rente gedaald ({tnx['rendement_21d']:+.1f}% in een maand)")

    label = "risk-on" if punten >= 2 else ("risk-off" if punten <= -1 else "neutraal")
    # Eerlijk zijn over blindheid: zonder de onderliggende reeksen is dit geen
    # 'neutraal' maar een 'onbekend'. Die twee door elkaar halen is hoe een
    # lege datafeed als rustige markt op het scherm komt.
    if spx["boven_sma200"] is None and not vix:
        label = "onbekend"
        redenen = ["te weinig koershistorie om het regime te bepalen"]

    return {"regime": label, "score": punten, "redenen": redenen,
            "vix": vix[1] if vix else None}


def marktbeeld() -> Dict[str, Any]:
    """Alles wat de analist als feitenbasis krijgt: regime, per instrument de
    kenmerken, en de correlaties die er in dit universum toe doen."""
    per_symbool = {s: kenmerken(s) for s in universe.symbolen()}

    def _c(a: str, b: str) -> Optional[float]:
        ra = [r["close"] for r in history.reeks(a, 120)]
        rb = [r["close"] for r in history.reeks(b, 120)]
        return correlatie(ra, rb)

    return {
        "regime": regime(),
        "instrumenten": per_symbool,
        "correlaties": {
            "btc_vs_spx": _c("BTC-EUR", "^GSPC"),
            "goud_vs_dxy": _c("GC=F", "DX-Y.NYB"),
            "goud_vs_rente": _c("GC=F", "^TNX"),
            "btc_vs_goud": _c("BTC-EUR", "GC=F"),
        },
        "verouderd": history.verouderde_symbolen(),
        "dekking": history.dekking(),
    }
