"""
Wat levert een kans op? — één getal met een eenheid.

Aanleiding (2 aug 2026): het Kansen-paneel van WeAreImpact zette
`nictiz.nl` (0 impressies, bedacht) op score 67 en 'programma manager
digitale transformatie' (36 impressies, positie 13,8 — mensen zoeken er
al op en de site verschijnt al) op score 15. Gemeten vraag scoorde vier
keer lager dan giswerk, en "Schrijf alle 11" pakte dus precies de
verkeerde vier eerst.

De oorzaak was niet één foute formule maar twéé schalen die nooit
vergelijkbaar waren:

  * striking-distance: `impressies × nabijheid` — een dimensieloos getal
    dat met het volume meeschaalt (0-400 in de praktijk);
  * cold-start: een vaste 60, handmatig: een vaste 100.

Zodra die in één `ORDER BY opportunity_score DESC` belanden, wint de
constante van de meting. Elke poging om dat met een magische factor recht
te trekken is een nieuwe gok.

Daarom rekent deze module in de énige eenheid waarin de twee soorten wél
te vergelijken zijn — en die bovendien een mens iets zegt: **verwachte
extra klikken per 28 dagen**. Voor een gemeten kans is dat een som over
echte cijfers. Voor een speculatieve kans is het er domweg niet: er is
geen impressiedata, dus elke voorspelling zou verzonnen zijn. Die kansen
krijgen `None` en sorteren daarmee onder álle gemeten kansen — niet omdat
ze slecht zijn, maar omdat "onbekend" nooit boven "gemeten" hoort te
staan.

Bewust zonder LLM: dit getal draagt de prioriteit van de contentmotor.
Een gateway die plat ligt mag de volgorde van het werk niet veranderen.
"""
from __future__ import annotations

from typing import Dict, List, Optional

# Waar mikken we op? Positie 3 is het hoogste dat je met on-page werk
# realistisch haalt vanuit striking distance; positie 1 beloven zou de
# opbrengst structureel ~2,5× overschatten en dan is het getal niets
# meer waard als prioriteit.
TARGET_POSITION = 3.0

# Onder deze impressies is de CTR-schatting ruis: één toevallige klik
# verschuift het percentage al met tientallen procenten. Zelfde drempel
# als `opportunity_quality._REAL_DEMAND_IMPRESSIONS`, en dat is geen
# toeval — het is dezelfde vraag ("meten we hier iets?") en die hoort
# één antwoord te hebben.
MIN_MEASURED_IMPRESSIONS = 10


def expected_ctr(position: float) -> float:
    """Verwachte CTR (%) op een positie.

    Delegeert naar de SEO-Optimizer: dat ís de benchmark van dit systeem,
    en twee benchmarks betekent dat het dashboard en de optimizer elkaar
    vroeg of laat tegenspreken over dezelfde pagina (precies de fout die
    op 25 jul 2026 een CTR-alert op positie 45 opleverde).
    """
    from .optimizer import _expected_ctr
    return _expected_ctr(position)


def uplift_clicks(impressions: int, position: float,
                  current_clicks: int = 0,
                  target: float = TARGET_POSITION) -> Optional[float]:
    """Verwachte extra klikken per 28 dagen bij stijging naar `target`.

    Geeft `None` — niet 0 — als er niets te meten valt. Het verschil doet
    ertoe: 0 betekent "dit levert niets op", `None` betekent "we weten het
    niet". Een kans zonder impressies is het tweede, en die als 0
    presenteren zou hem onder een gemeten kans van 0,2 klikken zetten
    alsof dat een oordeel was.
    """
    try:
        impressions = int(impressions or 0)
        position = float(position or 0)
    except (TypeError, ValueError):
        return None
    if impressions < MIN_MEASURED_IMPRESSIONS or position <= 0:
        return None
    if position <= target:
        # Al op of boven het doel: de winst zit niet meer in stijgen maar
        # in de snippet. Dan is het CTR-gat de eerlijke schatting.
        gap = expected_ctr(position) - (current_clicks / impressions * 100)
        return max(round(impressions * gap / 100, 1), 0.0)
    gained = expected_ctr(target) - expected_ctr(position)
    return max(round(impressions * gained / 100, 1), 0.0)


def is_measured(opp: Dict) -> bool:
    """Rust deze kans op gemeten vraag, of op een aanname?"""
    try:
        return (int(opp.get("impressions") or 0) >= MIN_MEASURED_IMPRESSIONS
                and float(opp.get("position") or 0) > 0)
    except (TypeError, ValueError):
        return False


def score(opp: Dict) -> Optional[float]:
    """De verwachte klikwinst van één kans, of None als hij speculatief is."""
    return uplift_clicks(opp.get("impressions") or 0, opp.get("position") or 0,
                         current_clicks=opp.get("clicks") or 0)


def describe(opp: Dict) -> str:
    """Eén regel die zegt wat deze kans waard is en waaróm.

    Dit is wat er op de kaart komt te staan in plaats van "Score 15.2".
    Een score zonder eenheid is geen informatie: niemand weet of 15 veel
    is, en juist die vraag bepaalt of je hem oppakt.
    """
    if not is_measured(opp):
        return "Geen gemeten vraag — schatting uit het siteprofiel"
    gain = score(opp)
    impressions = int(opp.get("impressions") or 0)
    position = float(opp.get("position") or 0)
    pos_txt = f"{position:.1f}".replace(".", ",")
    if not gain:
        return (f"{impressions} impressies op positie {pos_txt} — "
                "presteert al conform de benchmark")
    gain_txt = f"{gain:.1f}".replace(".", ",")
    return (f"≈ {gain_txt} klikken/maand erbij — {impressions} impressies, "
            f"nu positie {pos_txt}")


def sort_key(opp: Dict):
    """Sorteersleutel: gemeten vraag eerst, daarbinnen de grootste winst.

    Binnen de speculatieve groep valt er niets te meten, dus vallen we
    terug op de opgeslagen `opportunity_score` — puur om de volgorde
    stabiel te houden, niet omdat dat getal iets betekent.
    """
    if is_measured(opp):
        return (0, -(score(opp) or 0.0), 0.0)
    return (1, 0.0, -float(opp.get("opportunity_score") or 0))


def annotate(opportunities: List[Dict]) -> List[Dict]:
    """Plak de klikwinst op elke kans en sorteer eerlijk.

    Laat `opportunity_score` met rust: dat is de opgeslagen historische
    waarde en die overschrijven zou de database vervuilen met een getal
    dat per release van betekenis verandert. De UI leest `potential`.
    """
    for opp in opportunities:
        opp["potential_clicks"] = score(opp)
        opp["potential_label"] = describe(opp)
        opp["demand"] = "gemeten" if is_measured(opp) else "speculatief"
    opportunities.sort(key=sort_key)
    return opportunities


def total_potential(opportunities: List[Dict]) -> float:
    """Opgetelde klikwinst van een lijst kansen — de maat waarin een
    aanbeveling als "schrijf deze 3" een belofte kan doen."""
    return round(sum(o.get("potential_clicks") or score(o) or 0.0
                     for o in opportunities), 1)
