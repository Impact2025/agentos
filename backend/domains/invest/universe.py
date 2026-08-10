"""Het handelsuniversum — welke symbolen dit domein kent, en wat hun kalender is.

Eén lijst, want elke andere module moet dezelfde vraag hetzelfde beantwoorden:
welke assetklasse is dit, handelt het in het weekend, en hoe oud mag een koers
zijn voordat hij niet meer als "actueel" telt. Dat laatste is geen detail: een
besluit op een koers van vrijdag is maandagochtend voor crypto drie dagen oud
en voor een ETF de meest recente die bestaat. Eén drempel voor allebei geeft
óf vals alarm óf een blinde vlek (zie invariant `koers_verouderd`).

De symbolen volgen de tickers die `finance/prompts.py` al noemt, zodat het
dagrapport en de portefeuille over dezelfde markt praten.
"""
from __future__ import annotations

from typing import Dict, List, NamedTuple, Optional

ETF = "etf"
AANDEEL = "aandeel"
CRYPTO = "crypto"
METAAL = "metaal"
INDEX = "index"       # niet verhandelbaar: alleen om het regime te lezen
MACRO = "macro"       # rente, dollar, volatiliteit — context, geen positie

# Klassen waarin een positie genomen mag worden. Een index is een thermometer,
# geen instrument; een voorstel op ^GSPC is een contractfout, geen kans.
VERHANDELBAAR = {ETF, AANDEEL, CRYPTO, METAAL}


class Instrument(NamedTuple):
    symbol: str
    naam: str
    asset_class: str
    valuta: str = "EUR"
    weekend: bool = False          # handelt 7 dagen per week (crypto)
    # Hoeveel kalenderdagen een slotkoers hoogstens oud mag zijn voordat een
    # besluit erop niet meer eerlijk is. Ruim genoeg voor een lang weekend.
    max_koers_leeftijd_dagen: int = 4


_UNIVERSUM: List[Instrument] = [
    # ── Kern-ETF's ────────────────────────────────────────────────────────
    Instrument("IWDA.AS", "iShares Core MSCI World", ETF, "EUR"),
    Instrument("VWRL.AS", "Vanguard FTSE All-World", ETF, "EUR"),
    Instrument("CSPX.AS", "iShares Core S&P 500", ETF, "EUR"),
    Instrument("EMIM.AS", "iShares Core MSCI EM IMI", ETF, "EUR"),
    # Op Xetra, niet op Euronext Amsterdam: 'IUIT.AS' bestaat niet bij Yahoo
    # (geverifieerd 2 aug 2026 — de sync meldde hem als enige van de 27 leeg).
    Instrument("QDVE.DE", "iShares S&P 500 Info Tech", ETF, "EUR"),

    # ── Edelmetalen (als ETF verhandelbaar; futures alleen als koersbron) ──
    Instrument("SGLD.AS", "Invesco Physical Gold", METAAL, "EUR"),
    Instrument("PHAG.AS", "WisdomTree Physical Silver", METAAL, "EUR"),
    Instrument("GC=F", "Goud future (koersbron)", MACRO, "USD"),
    Instrument("SI=F", "Zilver future (koersbron)", MACRO, "USD"),

    # ── Aandelen ──────────────────────────────────────────────────────────
    Instrument("ASML.AS", "ASML Holding", AANDEEL, "EUR"),
    Instrument("ADYEN.AS", "Adyen", AANDEEL, "EUR"),
    Instrument("INGA.AS", "ING Groep", AANDEEL, "EUR"),
    Instrument("SHELL.AS", "Shell", AANDEEL, "EUR"),
    Instrument("AAPL", "Apple", AANDEEL, "USD"),
    Instrument("MSFT", "Microsoft", AANDEEL, "USD"),
    Instrument("NVDA", "NVIDIA", AANDEEL, "USD"),
    Instrument("GOOGL", "Alphabet", AANDEEL, "USD"),

    # ── Crypto ────────────────────────────────────────────────────────────
    # Weekendhandel: een koers van zondag is hier gewoon actueel, en een koers
    # van vrijdag is op maandag écht oud. Vandaar de kortere houdbaarheid.
    Instrument("BTC-EUR", "Bitcoin", CRYPTO, "EUR", weekend=True,
               max_koers_leeftijd_dagen=2),
    Instrument("ETH-EUR", "Ethereum", CRYPTO, "EUR", weekend=True,
               max_koers_leeftijd_dagen=2),
    Instrument("SOL-EUR", "Solana", CRYPTO, "EUR", weekend=True,
               max_koers_leeftijd_dagen=2),

    # ── Regime & macro (nooit een positie, altijd context) ────────────────
    Instrument("^GSPC", "S&P 500", INDEX, "USD"),
    Instrument("^IXIC", "Nasdaq Composite", INDEX, "USD"),
    Instrument("^AEX", "AEX", INDEX, "EUR"),
    Instrument("^VIX", "Volatiliteit (VIX)", MACRO, "USD"),
    Instrument("^TNX", "US 10-jaars rente", MACRO, "USD"),
    Instrument("DX-Y.NYB", "Dollar-index (DXY)", MACRO, "USD"),
    Instrument("EURUSD=X", "EUR/USD", MACRO, "USD"),
]

_INDEX: Dict[str, Instrument] = {i.symbol: i for i in _UNIVERSUM}


def alle() -> List[Instrument]:
    return list(_UNIVERSUM)


def symbolen() -> List[str]:
    return [i.symbol for i in _UNIVERSUM]


def instrument(symbol: str) -> Optional[Instrument]:
    return _INDEX.get((symbol or "").strip())


def is_verhandelbaar(symbol: str) -> bool:
    inst = instrument(symbol)
    return bool(inst and inst.asset_class in VERHANDELBAAR)


def asset_class(symbol: str) -> str:
    inst = instrument(symbol)
    return inst.asset_class if inst else ""


def verhandelbare_symbolen() -> List[str]:
    return [i.symbol for i in _UNIVERSUM if i.asset_class in VERHANDELBAAR]


# De benchmark waartegen alles wordt afgerekend. Wereldwijd gespreid en
# kosteloos te repliceren: als de agent dit niet verslaat, had het geld beter
# hier gestaan. Dat is de hele meetlat.
BENCHMARK = "IWDA.AS"
