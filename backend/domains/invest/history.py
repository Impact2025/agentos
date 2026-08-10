"""Koershistorie — de reeks, niet de stand.

`tools/market_data.py` haalt op wat een koers *nu* is en gooit het daarna weg.
Daarmee kun je adviseren, maar niet afrekenen: "koop goud op 2.340" is over een
maand niet te toetsen, een backtest is onmogelijk, en je ziet niet dat je
datafeed al een week stilstaat. Dit bestand is voor de beurs wat
`seo/history.py` voor GSC is: de opgeslagen reeks waar elke meting op rust.

Drie dingen die niet vanzelf spreken:

(a) **Een fill gebeurt op de vólgende koers, niet op de koers die het model
    zag.** `close_na()` bestaat daarvoor. Vult een papieren portefeuille op de
    prijs uit de analyse, dan rekent hij zichzelf systematisch rijk — precies
    het soort stille vertekening waar dit hele project op gebouwd is om te
    vangen.

(b) **Eén symbool dat faalt is geen storing; alle symbolen die falen wél.**
    Een enkele ticker die tijdelijk niets teruggeeft escaleert via
    `shared/failures.py` pas na een reeks; valt de hele feed weg, dan meteen
    een kaart. Een non-200 stil inslikken is verboden — zo bleef een dood
    Meta-token twaalf dagen onzichtbaar.

(c) **Geen historie geeft None, nooit 0.** 0 is een oordeel ("waardeloos"),
    None is de waarheid ("we weten het niet"). Elke rekenfunctie hieronder
    houdt zich daaraan.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from ...shared.database import get_conn
from ...shared.failures import describe_exception, note_failure, note_success, should_escalate
from ...shared.outcomes import log_outcome
from . import universe

logger = logging.getLogger(__name__)

# Eerste sync haalt twee jaar op: genoeg voor een backtest over een hele
# marktcyclus én voor 200-daags gemiddelde vanaf dag één. Daarna volstaat een
# kort venster; de upsert dicht gaten vanzelf.
_BACKFILL_PERIODE = "2y"
_DAGELIJKS_VENSTER = "1mo"

# Hoeveel symbolen tegelijk. yfinance is synchroon en rate-limit-gevoelig;
# meer parallel levert vooral lege antwoorden op.
_PARALLEL = 4


def _vandaag() -> str:
    return date.today().isoformat()


# ── Ophalen ────────────────────────────────────────────────────────────────

def _haal_symbool(symbol: str, periode: str) -> List[Tuple[str, float, float, float, float, float, str]]:
    """Haal dagkoersen op voor één symbool. Gooit door bij een echte fout —
    de aanroeper classificeert en telt de reeks."""
    import yfinance as yf

    ticker = yf.Ticker(symbol)
    frame = ticker.history(period=periode, interval="1d", auto_adjust=False)
    if frame is None or frame.empty:
        raise RuntimeError(f"lege koersreeks voor '{symbol}'")

    valuta = ""
    try:
        # `.info` is traag en soms leeg; de valuta is prettig maar niet kritiek,
        # dus een mislukking hier mag de hele rij niet kosten.
        valuta = (ticker.fast_info.get("currency") or "") if hasattr(ticker, "fast_info") else ""
    except Exception:
        valuta = ""
    if not valuta:
        inst = universe.instrument(symbol)
        valuta = inst.valuta if inst else ""

    vandaag = date.today().isoformat()
    rijen = []
    for idx, row in frame.iterrows():
        try:
            dag = idx.date().isoformat()
        except AttributeError:
            dag = str(idx)[:10]
        # Alleen vóltooide handelsdagen. yfinance geeft tijdens beursuren een
        # bar voor vandaag terug met de koers van dít moment in de kolom
        # `Close`; die rij ziet er in de tabel precies zo uit als een echte
        # slotkoers en is het niet. Alles hieronder rekent op slotkoersen: de
        # 200-daagse, de ATR, de fill van `close_na`, de baseline van elke
        # voorspelling. Eén halve dag ertussen maakt van al die getallen een
        # meting op iets anders dan waarop ze zijn gedefinieerd — en er is
        # niets dat dat later nog aan de rij kan zien. (Ontdekt 4 aug 2026:
        # de ochtendsync schreef een bar van 09:20 weg als dag-slot.)
        if dag >= vandaag:
            continue
        slot = row.get("Close")
        if slot is None or slot != slot:  # NaN
            continue
        rijen.append((
            dag,
            float(row.get("Open") or slot),
            float(row.get("High") or slot),
            float(row.get("Low") or slot),
            float(slot),
            float(row.get("Volume") or 0),
            valuta,
        ))
    if not rijen:
        raise RuntimeError(f"geen bruikbare slotkoersen voor '{symbol}'")
    return rijen


def _bewaar(symbol: str, rijen: List[Tuple[str, float, float, float, float, float, str]]) -> int:
    """Idempotente upsert. Twee keer dezelfde dag mag nooit twee rijen geven."""
    nu = datetime.now().isoformat(timespec="seconds")
    with get_conn() as conn:
        conn.executemany(
            "INSERT INTO market_history (symbol, date, open, high, low, close, volume, currency, fetched_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(symbol, date) DO UPDATE SET "
            "open=excluded.open, high=excluded.high, low=excluded.low, close=excluded.close, "
            "volume=excluded.volume, currency=excluded.currency, fetched_at=excluded.fetched_at",
            [(symbol, r[0], r[1], r[2], r[3], r[4], r[5], r[6], nu) for r in rijen],
        )
    return len(rijen)


def heeft_historie(symbol: str) -> bool:
    with get_conn() as conn:
        return conn.execute(
            "SELECT 1 FROM market_history WHERE symbol = ? LIMIT 1", (symbol,)
        ).fetchone() is not None


async def sync(symbols: Optional[List[str]] = None) -> Dict[str, Any]:
    """Ververs de koershistorie. Nieuwe symbolen krijgen een backfill, bekende
    een kort venster. Retourneert een verslag; faalt de hele feed, dan komt er
    een kaart in het Actiecentrum."""
    symbols = symbols or universe.symbolen()
    gelukt: Dict[str, int] = {}
    mislukt: Dict[str, str] = {}

    semafoor = asyncio.Semaphore(_PARALLEL)

    async def _een(sym: str) -> None:
        periode = _DAGELIJKS_VENSTER if heeft_historie(sym) else _BACKFILL_PERIODE
        async with semafoor:
            try:
                rijen = await asyncio.to_thread(_haal_symbool, sym, periode)
                gelukt[sym] = await asyncio.to_thread(_bewaar, sym, rijen)
                note_success(f"invest_history:{sym}")
            except Exception as e:
                melding = describe_exception(e)
                mislukt[sym] = melding
                if should_escalate(f"invest_history:{sym}", e):
                    log_outcome(
                        "Beursmeester", "koershistorie",
                        f"Koersen van '{sym}' zijn al meerdere runs niet op te halen: {melding}",
                        next_step=f"Controleer of het ticker-symbool '{sym}' nog klopt op Yahoo Finance, "
                                  "of haal het uit het universum (domains/invest/universe.py).",
                        status="error",
                    )

    await asyncio.gather(*[_een(s) for s in symbols])

    verslag = {
        "symbolen": len(symbols),
        "gelukt": len(gelukt),
        "mislukt": len(mislukt),
        "rijen": sum(gelukt.values()),
        "fouten": mislukt,
    }

    # Eén ticker die hapert is ruis; de hele feed die wegvalt is een storing
    # waarop élk besluit van vandaag zou rusten. Die mag niet stil blijven.
    if symbols and not gelukt:
        eerste = next(iter(mislukt.values()), "onbekende oorzaak")
        log_outcome(
            "Beursmeester", "koershistorie",
            f"Geen enkel symbool leverde koersen op ({len(symbols)} geprobeerd). Eerste fout: {eerste}",
            next_step="Controleer de internetverbinding en of 'yfinance' nog werkt "
                      "(.venv/Scripts/python.exe -c \"import yfinance\"). Zolang dit staat, "
                      "rust elk beleggingsbesluit op verouderde koersen.",
            status="error",
        )
        logger.error("[invest] koershistorie-sync leverde niets op: %s", eerste)
    else:
        logger.info("[invest] koershistorie: %d/%d symbolen, %d dagrijen",
                    len(gelukt), len(symbols), verslag["rijen"])
    return verslag


# ── Lezen ──────────────────────────────────────────────────────────────────

def laatste_slot(symbol: str) -> Optional[Tuple[str, float]]:
    """(handelsdag, slotkoers) van de meest recente dag, of None."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT date, close FROM market_history WHERE symbol = ? ORDER BY date DESC LIMIT 1",
            (symbol,),
        ).fetchone()
    return (row["date"], float(row["close"])) if row else None


def slot_op_of_voor(symbol: str, dag: str) -> Optional[Tuple[str, float]]:
    """De laatst bekende slotkoers op of vóór `dag`. Vangt weekenden en
    beursvakanties af zonder aan te nemen dat elke dag een handelsdag is."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT date, close FROM market_history WHERE symbol = ? AND date <= ? "
            "ORDER BY date DESC LIMIT 1",
            (symbol, dag),
        ).fetchone()
    return (row["date"], float(row["close"])) if row else None


def close_na(symbol: str, dag: str) -> Optional[Tuple[str, float]]:
    """De eerste slotkoers ná `dag` — de eerlijke fill-prijs voor een besluit
    dat op `dag` is genomen. None zolang die dag nog niet bestaat: dan is de
    order simpelweg nog niet uitgevoerd, en dat is de waarheid."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT date, close FROM market_history WHERE symbol = ? AND date > ? "
            "ORDER BY date ASC LIMIT 1",
            (symbol, dag),
        ).fetchone()
    return (row["date"], float(row["close"])) if row else None


def reeks(symbol: str, dagen: int = 250) -> List[Dict[str, Any]]:
    """Chronologische reeks (oud → nieuw) van de laatste `dagen` handelsdagen."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT date, open, high, low, close, volume FROM market_history "
            "WHERE symbol = ? ORDER BY date DESC LIMIT ?",
            (symbol, max(1, dagen)),
        ).fetchall()
    return [dict(r) for r in reversed(rows)]


def rendement(symbol: str, dagen: int) -> Optional[float]:
    """Rendement in procenten over de laatste `dagen` handelsdagen, of None
    als er te weinig historie is om het eerlijk te zeggen."""
    r = reeks(symbol, dagen + 1)
    if len(r) < 2:
        return None
    start, eind = r[0]["close"], r[-1]["close"]
    if not start:
        return None
    return round((eind - start) / start * 100, 2)


def is_verouderd(symbol: str, vandaag: Optional[str] = None) -> bool:
    """Is de nieuwste koers te oud om een besluit op te baseren?

    De drempel komt per instrument uit `universe`: crypto handelt in het
    weekend, een ETF niet. Eén drempel voor allebei geeft óf vals alarm op
    maandagochtend óf een blinde vlek voor een stilgevallen cryptofeed.
    """
    laatste = laatste_slot(symbol)
    if not laatste:
        return True
    inst = universe.instrument(symbol)
    grens = inst.max_koers_leeftijd_dagen if inst else 4
    peil = date.fromisoformat(vandaag) if vandaag else date.today()
    return (peil - date.fromisoformat(laatste[0])).days > grens


def verouderde_symbolen(symbols: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    """Alle symbolen waarvan de koers over datum is — grondstof voor de
    invariant `koers_verouderd` én voor de rem op de dagelijkse ronde."""
    uit = []
    for sym in (symbols or universe.verhandelbare_symbolen()):
        if is_verouderd(sym):
            laatste = laatste_slot(sym)
            uit.append({
                "symbol": sym,
                "laatste_dag": laatste[0] if laatste else "",
                "dagen_oud": (date.today() - date.fromisoformat(laatste[0])).days if laatste else None,
            })
    return uit


def dekking() -> Dict[str, Any]:
    """Hoeveel historie hebben we, en tot wanneer. Voor de UI en de audit."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS rijen, COUNT(DISTINCT symbol) AS symbolen, "
            "MIN(date) AS van, MAX(date) AS tot FROM market_history"
        ).fetchone()
    return dict(row) if row else {"rijen": 0, "symbolen": 0, "van": None, "tot": None}
