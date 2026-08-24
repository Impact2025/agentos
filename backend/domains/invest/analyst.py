"""De analist — Claude Code krijgt de feiten en moet zijn idee bewijzen.

De ronde in vier stappen:

  1. Impact OS bouwt een werkmap met de harde feiten: koershistorie als CSV,
     de deterministische kenmerken, de open posities, de geleerde lessen en
     de eigen trefkans.
  2. Claude Code draait in die map. Hij mag Python schrijven en draaien, en de
     opdracht is expliciet: **eerst backtesten, dan pas voorstellen**.
  3. Hij schrijft `voorstel.json` volgens een strak contract.
  4. Python valideert dat contract tegen de wérkelijkheid — bestaat het
     symbool, klopt de koers, zit er een stop op, is er een backtest.

Stap 4 is het hart. Een LLM die getallen mag noemen zonder controle, noemt
vroeg of laat een koers die niet bestaat; niet uit kwade wil, maar omdat het
een taalmodel is. De prompt zegt "verzin geen koersen" — dat is een wens. Deze
validatie is de handhaving, en zij bepaalt wat er in de gate belandt.

**De backtest-eis is geen formaliteit.** Een voorstel zonder backtest-artefact
wordt geweigerd, hoe overtuigend de these ook klinkt. Dat is de vertaling van
"elke run die 'klaar' claimt hoort een artefact te hebben" naar dit domein, en
het is meteen de scherpste kwaliteitsgate die je op een taalmodel kunt zetten:
het dwingt hem zijn eigen idee te toetsen vóór jij ernaar kijkt.
"""
from __future__ import annotations

import csv
import json
import logging
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ...shared import claude_code
from ...shared.learning import lessons_block, track_record
from . import features, history, portfolio, universe

logger = logging.getLogger(__name__)

AGENT = "invest"

# Hoeveel voorstellen één ronde hoogstens mag opleveren. Een analist die er
# tien tegelijk voorstelt, denkt niet na maar hagelschiet — en een inbox met
# tien beleggingsbesluiten wordt niet gelezen maar weggeklikt.
MAX_VOORSTELLEN = 3

_CONTRACT = """{
  "marktbeeld": "<2-4 zinnen over het regime, in gewone taal>",
  "les": "<één zin: de regel die je backtest heeft opgeleverd, of \\"\\" als je er geen hebt>",
  "voorstellen": [
    {
      "symbol": "<ticker uit universum.json, verhandelbaar>",
      "side": "buy | sell",
      "these": "<max 3 zinnen: waarom nu, waarom dit instrument>",
      "stop": <getal: koers waarop de these gebroken is>,
      "target": <getal of null>,
      "horizon_dagen": <5..60>,
      "invalidatie": "<wat je zou zien als je ongelijk hebt>",
      "confidence": "laag | midden | hoog",
      "backtest": "<bestandsnaam van je backtest-script of -verslag in deze map>",
      "backtest_uitkomst": "<1-2 zinnen: wat de backtest liet zien, incl. het aantal waarnemingen>"
    }
  ],
  "afgevallen": [
    {"symbol": "<ticker>", "reden": "<waarom je dit idee zélf hebt verworpen>"}
  ]
}"""


def _opdracht(pf: Dict[str, Any]) -> str:
    return f"""Je bent de beleggingsanalist van een portefeuille van €{pf['start_capital']:,.0f}
(ETF's, aandelen, crypto en edelmetalen). Je werkt in deze map. Alle feiten die je nodig
hebt staan er al in — je hebt geen internet en je hoeft niets op te halen.

BESTANDEN
  marktbeeld.json   — regime, per instrument trend/momentum/RSI/ATR, correlaties
  koersen.csv       — dagkoersen (symbol,date,open,high,low,close,volume), 2 jaar
  portefeuille.json — cash, open posities met kostprijs, stop en these
  lessen.md         — wat deze agent eerder heeft geleerd, met trefkans
  universum.json    — welke tickers bestaan en welke verhandelbaar zijn

OPDRACHT
1. Lees de bestanden en vorm een beeld van het marktregime.
2. Bedenk hoogstens {MAX_VOORSTELLEN} concrete ideeën.
3. **Toets elk idee vóórdat je het voorstelt.** Schrijf een Python-script dat de
   regel achter je idee over koersen.csv draait (bijv. "koop wanneer RSI < 35 en
   de koers boven het 200-daags gemiddelde ligt, verkoop na 20 dagen of op de
   stop"). Draai het. Rapporteer hoeveel waarnemingen het opleverde, de trefkans
   en het gemiddelde resultaat. Een regel met minder dan 15 waarnemingen is geen
   bewijs — zeg dat er dan bij, of laat het idee vallen.
4. Houdt een idee de backtest niet, verwerp het dan zelf en zet het in
   "afgevallen". Dat is een goede uitkomst, geen mislukking. Nul voorstellen met
   een eerlijke reden is meer waard dan drie zwakke.
5. Schrijf je conclusie naar **voorstel.json** in exact dit formaat:

{_CONTRACT}

REGELS
- Gebruik uitsluitend koersen uit koersen.csv. Noem geen enkel getal dat je daar
  niet hebt gelezen of berekend.
- Alleen tickers die in universum.json als verhandelbaar staan. Een index
  (^GSPC, ^AEX) of macro-reeks (^VIX, DX-Y.NYB) is context, geen positie.
- Elk voorstel heeft een stop. Zonder stop is er geen positiegrootte te bepalen
  en wordt het voorstel geweigerd.
- Stel niets voor in een instrument waar de portefeuille al een open positie in
  heeft; bijkopen in een lopende positie doen we niet.
- Je bepaalt de grootte niet — dat doet de risicomodule op basis van je stop.
- Schrijf in het Nederlands.
- Je koopt of verkoopt niets. Je levert een voorstel af; een mens beslist."""


# ── De werkmap ─────────────────────────────────────────────────────────────

def bouw_werkmap(pf: Dict[str, Any]) -> Path:
    """Zet alle feiten klaar. De map is meteen het artefact van de ronde: wie
    later vraagt waaróm iets is voorgesteld, vindt hier de exacte invoer."""
    map_ = claude_code.maak_werkmap("beursmeester")

    beeld = features.marktbeeld()
    (map_ / "marktbeeld.json").write_text(
        json.dumps(beeld, indent=2, ensure_ascii=False), encoding="utf-8")

    with (map_ / "koersen.csv").open("w", newline="", encoding="utf-8") as f:
        schrijver = csv.writer(f)
        schrijver.writerow(["symbol", "date", "open", "high", "low", "close", "volume"])
        for sym in universe.symbolen():
            for rij in history.reeks(sym, dagen=520):
                schrijver.writerow([sym, rij["date"], rij["open"], rij["high"],
                                    rij["low"], rij["close"], rij["volume"]])

    snap = portfolio.snapshot(pf["id"])
    (map_ / "portefeuille.json").write_text(
        json.dumps({
            "cash": snap["cash"],
            "nav": snap["nav"] if snap["volledig"] else None,
            "nav_volledig": snap["volledig"],
            "posities": [
                {k: p.get(k) for k in
                 ("symbol", "qty", "avg_price", "stop", "target", "thesis", "opened_on", "pnl_pct")}
                for p in snap["posities"]
            ],
            "rendement": portfolio.rendement(pf["id"]),
        }, indent=2, ensure_ascii=False), encoding="utf-8")

    (map_ / "universum.json").write_text(json.dumps({
        "verhandelbaar": universe.verhandelbare_symbolen(),
        "alleen_context": [s for s in universe.symbolen()
                           if not universe.is_verhandelbaar(s)],
        "benchmark": universe.BENCHMARK,
    }, indent=2, ensure_ascii=False), encoding="utf-8")

    trefkans = track_record(AGENT)
    lessen = lessons_block(AGENT, max_n=8) or "Nog geen geleerde lessen — dit is een jonge agent."
    (map_ / "lessen.md").write_text(
        f"# Wat deze agent heeft geleerd\n\n{lessen}\n\n"
        f"## Eigen trefkans\n"
        f"- Afgerekende voorspellingen: {trefkans['correct']} raak, {trefkans['wrong']} mis, "
        f"{trefkans['unclear']} onbeslist\n"
        f"- Trefkans: {trefkans['accuracy'] if trefkans['accuracy'] is not None else 'nog niet meetbaar'}"
        f"{'%' if trefkans['accuracy'] is not None else ''}\n"
        f"- Nog open: {trefkans['open']}\n\n"
        "Een lage trefkans is geen reden om voorzichtiger te formuleren, maar om "
        "minder en beter onderbouwde voorstellen te doen.\n",
        encoding="utf-8")

    return map_


# ── Validatie: het contract tegen de werkelijkheid ─────────────────────────

def _als_getal(waarde: Any) -> Optional[float]:
    try:
        if waarde is None or waarde == "":
            return None
        return float(waarde)
    except (TypeError, ValueError):
        return None


def valideer(rauw: Dict[str, Any], werkmap: Path,
             portfolio_id: str) -> Tuple[List[Dict[str, Any]], List[Dict[str, str]]]:
    """Zet de rauwe uitvoer om in voorstellen die klóppen. Geeft (goedgekeurd,
    geweigerd-met-reden) terug — een geweigerd voorstel verdwijnt nooit stil,
    want dan leer je niet dat de analist structureel iets fout doet."""
    goed: List[Dict[str, Any]] = []
    fout: List[Dict[str, str]] = []
    gezien: set = set()

    for v in (rauw.get("voorstellen") or [])[:MAX_VOORSTELLEN + 2]:
        if not isinstance(v, dict):
            fout.append({"symbol": "?", "reden": "geen object in de voorstellenlijst"})
            continue
        sym = str(v.get("symbol") or "").strip()
        side = str(v.get("side") or "buy").strip().lower()

        if not universe.is_verhandelbaar(sym):
            fout.append({"symbol": sym or "?",
                         "reden": "geen verhandelbaar instrument uit het universum"})
            continue
        if sym in gezien:
            fout.append({"symbol": sym, "reden": "twee voorstellen voor hetzelfde instrument"})
            continue
        if side not in ("buy", "sell"):
            fout.append({"symbol": sym, "reden": f"onbekende orderkant '{side}'"})
            continue

        laatste = history.laatste_slot(sym)
        if not laatste:
            fout.append({"symbol": sym, "reden": "geen koershistorie"})
            continue
        if history.is_verouderd(sym):
            fout.append({"symbol": sym,
                         "reden": f"koers is te oud (laatste dag {laatste[0]})"})
            continue
        ref_dag, ref_prijs = laatste

        stop = _als_getal(v.get("stop"))
        if stop is None:
            fout.append({"symbol": sym, "reden": "geen stop opgegeven"})
            continue
        # Een stop die aan de verkeerde kant van de koers ligt, is geen stop
        # maar een tikfout — en zou de positiegrootte laten ontploffen.
        if side == "buy" and stop >= ref_prijs:
            fout.append({"symbol": sym,
                         "reden": f"stop ({stop:g}) ligt op of boven de koers ({ref_prijs:g})"})
            continue
        afstand = abs(ref_prijs - stop) / ref_prijs
        if afstand > 0.5:
            fout.append({"symbol": sym,
                         "reden": f"stop staat {afstand * 100:.0f}% van de koers — geen invalidatie"})
            continue

        backtest = str(v.get("backtest") or "").strip()
        # De harde eis. Een naam die naar niets verwijst telt niet als bewijs;
        # anders is "backtest": "ja" genoeg om de gate te passeren.
        if not backtest or not (werkmap / backtest).exists():
            fout.append({"symbol": sym,
                         "reden": f"backtest-artefact ontbreekt in de werkmap ('{backtest or 'niets opgegeven'}')"})
            continue

        if side == "buy" and portfolio.positie(portfolio_id, sym):
            fout.append({"symbol": sym, "reden": "er is al een open positie; bijkopen doen we niet"})
            continue
        if side == "sell" and not portfolio.positie(portfolio_id, sym):
            fout.append({"symbol": sym, "reden": "verkoopvoorstel zonder open positie"})
            continue

        gezien.add(sym)
        goed.append({
            "symbol": sym,
            "side": side,
            "asset_class": universe.asset_class(sym),
            "ref_price": ref_prijs,
            "ref_date": ref_dag,
            "stop": stop,
            "target": _als_getal(v.get("target")),
            "horizon_days": max(5, min(60, int(_als_getal(v.get("horizon_dagen")) or 20))),
            "thesis": str(v.get("these") or "")[:600],
            "invalidation": str(v.get("invalidatie") or "")[:300],
            "confidence": str(v.get("confidence") or "").strip().lower()[:10],
            "backtest_ref": str(werkmap / backtest),
            "backtest_uitkomst": str(v.get("backtest_uitkomst") or "")[:300],
        })
        if len(goed) >= MAX_VOORSTELLEN:
            break

    return goed, fout


# ── De ronde ───────────────────────────────────────────────────────────────

def _lees_voorstel(werkmap: Path) -> Optional[Dict[str, Any]]:
    bestand = werkmap / "voorstel.json"
    if not bestand.exists():
        return None
    try:
        return json.loads(bestand.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        logger.warning("[invest] voorstel.json is geen geldige JSON: %s", e)
        return None


async def _terugval_via_gateway(pf: Dict[str, Any], werkmap: Path) -> Optional[Dict[str, Any]]:
    """Als Claude Code niet beschikbaar is: één gewone modelaanroep over de
    gateway. Bewust karig — zonder werkmap kan het model niets backtesten, dus
    dit levert per definitie voorstellen op die de backtest-eis niet halen. Het
    resultaat is dan ook alleen het marktbeeld; de voorstellen sneuvelen in
    `valideer`, en dat is de bedoeling. Liever geen voorstel dan een onbewezen
    voorstel dat er precies zo uitziet als een bewezen voorstel.
    """
    from ..chat.claude import get_response

    beeld = features.marktbeeld()
    kern = {s: beeld["instrumenten"][s] for s in universe.verhandelbare_symbolen()
            if s in beeld["instrumenten"]}
    prompt = (
        "Hieronder staat het marktbeeld van vandaag als JSON. Vat in maximaal zes zinnen "
        "samen wat het regime is en waar het risico zit. Doe géén concrete "
        "koopvoorstellen — die vereisen een backtest die je hier niet kunt draaien.\n\n"
        + json.dumps({"regime": beeld["regime"], "instrumenten": kern,
                      "correlaties": beeld["correlaties"]}, ensure_ascii=False)[:12000]
    )
    try:
        tekst = await get_response(
            [{"role": "user", "content": prompt}],
            system_prompt="Je bent een nuchtere macro-analist. Cijfers vóór meningen.",
            max_tokens=1200, purpose="invest-terugval",
        )
    except Exception as e:
        logger.warning("[invest] terugval-analyse faalde: %s", e)
        return None
    (werkmap / "terugval.md").write_text(tekst, encoding="utf-8")
    return {"marktbeeld": tekst.strip(), "voorstellen": [], "afgevallen": []}


async def analyseer(portfolio_id: str = portfolio.STANDAARD_ID) -> Dict[str, Any]:
    """Eén analyse-ronde. Retourneert het verslag; schrijft zelf niets naar de
    voorstellen-tabel — dat doet `service.py`, ná de risicotoets."""
    import asyncio

    pf = portfolio.ensure_portfolio(portfolio_id)
    werkmap = bouw_werkmap(pf)

    status = claude_code.beschikbaar()
    denkwerk = "claude_code"
    rauw: Optional[Dict[str, Any]] = None
    notitie = ""
    limiet = False

    if status["ok"]:
        resultaat = await asyncio.to_thread(
            claude_code.run, _opdracht(pf), workspace=werkmap, doel="beursmeester",
            verwacht_bestand="voorstel.json",
        )
        if resultaat.ok:
            rauw = _lees_voorstel(werkmap)
            if rauw is None:
                notitie = "voorstel.json was onleesbaar"
        else:
            notitie = resultaat.reden
            limiet = resultaat.limiet_bereikt
    else:
        notitie = status["reden"]
        limiet = "limiet" in status["reden"]

    if rauw is None:
        # Terugval mét label. Een terugval die zich voordoet als het echte werk
        # is erger dan geen werk: dan denk je dat de agent heeft nagedacht.
        denkwerk = "terugval"
        rauw = await _terugval_via_gateway(pf, werkmap)
        if rauw is None:
            return {
                "ok": False, "denkwerk": "geen", "werkmap": str(werkmap),
                "reden": notitie or "geen enkele analyse-route beschikbaar",
                "limiet": limiet, "voorstellen": [], "geweigerd": [],
            }

    goed, geweigerd = valideer(rauw, werkmap, portfolio_id)
    (werkmap / "validatie.json").write_text(
        json.dumps({"goedgekeurd": goed, "geweigerd": geweigerd}, indent=2,
                   ensure_ascii=False, default=str),
        encoding="utf-8")

    return {
        "ok": True,
        "denkwerk": denkwerk,
        "werkmap": str(werkmap),
        "reden": notitie,
        # Waaróm er is teruggevallen bepaalt welke knop eronder hoort: een
        # bereikte abonnementslimiet vraagt om een mens, een netwerkblip niet.
        "limiet": limiet,
        "marktbeeld": str(rauw.get("marktbeeld") or "")[:2000],
        # De les is de regel áchter de voorstellen, afgeleid uit de backtest.
        # Zij is het ding dat wint of verliest aan vertrouwen als de
        # voorspellingen worden afgerekend — niet de individuele these.
        "les": str(rauw.get("les") or "").strip()[:300],
        "voorstellen": goed,
        "geweigerd": geweigerd,
        "afgevallen": rauw.get("afgevallen") or [],
    }
