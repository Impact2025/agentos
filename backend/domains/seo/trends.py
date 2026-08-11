"""
Trend-brug — Mission Radar-signalen als tweede zoekwoordbron voor de Demand Engine.

De striking-distance-scan (engine.py) is reactief: hij vindt alleen zoekwoorden
waar de site al half op scoort. Trends zijn het complement — first-mover zijn op
een vers onderwerp is hoe je AI Overviews-citaties pakt vóórdat er concurrentie
is. De Mission Radar scant al elke 4 uur op trends per project; deze module zet
de topsignalen om in `opportunities`, zodat ze in dezelfde contentpijplijn
(select_topic → meertraps-generator → Wachtrij) meelopen als de GSC-kansen.

Koppeling site ↔ radar-project: op genormaliseerde naam (radar_signals.project
vs. sites.name). Elk geconverteerd signaal gaat naar status 'targeted' zodat een
volgende sync 'm niet opnieuw aanbiedt.

**Wat hier grondig misging (gemeten 3 aug 2026).** Het zoekwoord kwam niet uit
het signaal maar uit de watchlist: `_signal_query` gaf `sig["keyword"]` terug,
en dat is de regel die Vincent zelf had ingetypt. Alle 38 kansen die deze brug
ooit heeft gemaakt waren dáárdoor letterlijk een watchlist-regel — geen enkele
kwam uit wat de radar vond. Erger: de dedupe liep op exacte querytekst, dus na
één conversie was elk watchlist-woord voor altijd verbruikt. Sinds 27 juli
leverde de brug niets meer op terwijl er dagelijks honderden signalen bij kwamen,
en niets meldde dat. Ondertussen stond boven elke kans "Trending (Radar-score
76)" — een onware bewering over de herkomst.

Drie regels die dat afdekken:

  1. Het onderwerp komt uit de **titel van het gevonden stuk**, ontdaan van
     merkstaarten. Kan daar geen bruikbaar zoekwoord uit, dan geen kans.
  2. Dedupe loopt via `opportunity_quality` — dezelfde poort en dezelfde
     `is_same_topic` als het Kansen-paneel. Twee antwoorden op "is dit al
     gedaan?" is precies hoe die twee uit elkaar lopen.
  3. Alleen signalen die de radar-signaalpoort (`radar/quality.py`) hebben
     overleefd én een aantoonbare relevantie hebben, mogen door. Een vacature
     of een dienstpagina van een concurrent werd anders een artikelopdracht.
"""
from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional

from ...shared.database import get_conn
from . import sites as sites_service

logger = logging.getLogger(__name__)

# Alleen echte topsignalen worden een content-kans — de Radar-tab blijft de
# plek voor alles daaronder.
TREND_MIN_SIGNAL_SCORE = 60.0
TREND_MAX_PER_SYNC = 3  # per site per sync, zodat trends de GSC-kansen niet verdringen

# Een signaal zonder aangetoonde relevantie wordt nooit een artikel. `-1` betekent
# "de rechter heeft er niet naar gekeken" en dat is geen vrijbrief: vóór 3 aug
# 2026 kon een ongescoord signaal via de heuristiek alsnog boven de 60 komen en
# de contentmotor in rollen.
TREND_MIN_MATCH = 55


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _norm_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


# Merkstaarten die scrapers in elke titel achterlaten: "… - Vilans",
# "… | Publieke Sector | Haute Equipe". Ze horen niet in een zoekwoord.
_MERKSTAART = re.compile(r"\s*[|–—·»]\s*[^|–—·»]{2,40}\s*$")
_BRONSTAART = re.compile(r"\s+-\s+[A-Z][\w&.\- ]{2,30}$")

# Ruis die een titel geen zoekwoord maakt.
_TITEL_RUIS = re.compile(r"^\s*(link to|https?://)", re.I)


def _signal_query(sig: Dict) -> str:
    """Het onderwerp waarop we content bouwen — uit het signáál.

    Hier zat de zwaarste fout van de radar (gemeten 3 aug 2026). Deze functie
    gaf `sig["keyword"]` terug: het watchlist-zoekwoord dat Vincent zelf had
    ingetypt. Alle 38 kansen die deze brug ooit heeft opgeleverd waren daardoor
    lettérlijk een regel uit de watchlist ('hond adopteren', 'teambuilding
    haarlemmermeer') — geen enkele kwam uit wat de radar had gevonden. En omdat
    de dedupe op querytekst loopt, was elk watchlist-woord na één keer voor
    altijd verbruikt: de brug leverde sinds 27 juli niets meer, terwijl er
    dagelijks signalen bij kwamen. Een module die stil klaar is en dat niet
    meldt, is erger dan een module die faalt.

    Het onderwerp zit in de titel van het gevónden stuk. Die wordt ontdaan van
    merkstaarten ("… - Vilans", "… | Haute Equipe") — anders wordt de naam van
    een concurrent ons zoekwoord.
    """
    titel = re.sub(r"\s+", " ", sig.get("title") or "").strip()
    if not titel or _TITEL_RUIS.match(titel):
        return ""
    kort = _MERKSTAART.sub("", titel)
    kort = _BRONSTAART.sub("", kort).strip(" -–—|·")
    # Zoekresultaten leveren afgekapte titels ("… heb ik daar enige ..."); dat
    # beletselteken hoort niet in een zoekwoord, en het halve woord ervoor ook
    # niet als het duidelijk is afgesneden.
    kort = re.sub(r"\s*(\.\.\.|…)\s*$", "", kort).strip(" ,-–—")
    # Te veel weggesneden? Dan was de hele titel de merknaam; val terug op de
    # volledige titel in plaats van op een restje van drie letters.
    if len(kort) < 12:
        kort = titel
    return kort[:80].strip()


def _existing_queries(conn, site_id: str) -> set:
    """De zoekwoorden die deze site al als kans heeft — in welke status ook.

    Blijft nodig náást `opportunity_quality.site_coverage`: die kijkt naar
    gepubliceerde en in behandeling zijnde content, deze naar de kansenlijst
    zelf. Een kans die nog niemand heeft opgepakt staat in geen van beide
    andere bronnen.
    """
    rows = conn.execute(
        "SELECT query FROM opportunities WHERE site_id = ?", (site_id,)
    ).fetchall()
    return {(r["query"] or "").strip().lower() for r in rows}


def _bruikbare_query(query: str) -> bool:
    """Genoeg zoekintentie om een artikel op te bouwen?

    Dezelfde maatstaf als de Kansen-poort hanteert voor 'te-vaag', en bewust
    díe functie en niet een eigen variant: twee antwoorden op "is dit een
    zoekwoord?" is hoe de contentmotor en het Kansen-paneel uit elkaar lopen.
    """
    from . import opportunity_quality as oq
    return len(oq.tokens(query)) >= 2 and len(oq.normalize(query)) >= 8


def sync_trend_opportunities(site: Dict) -> Dict:
    """Zet verse Radar-topsignalen voor deze site om in Demand Engine-kansen.

    Idempotent: geconverteerde signalen krijgen status 'targeted' en queries
    die al als kans bestaan (welke status dan ook) worden overgeslagen."""
    try:
        from ..radar.models import ensure_schema
        ensure_schema()
    except Exception as e:  # radar-domein hoort te bestaan, maar faal zacht
        logger.warning("[trends] Radar-schema niet beschikbaar: %s", e)
        return {"site": site.get("name"), "created": 0, "skipped": 0}

    from . import opportunity_quality as oq

    site_key = _norm_name(site.get("name"))
    created: List[Dict] = []
    skipped = 0
    # Wat de site al dekt (live artikelen, wachtrij, extern CMS). Dit is dezelfde
    # bron als het Kansen-paneel gebruikt, zodat een trend die allang een artikel
    # heeft hier net zo goed sneuvelt als daar.
    try:
        coverage = oq.site_coverage(site)
    except Exception:  # noqa: BLE001 — zonder dekking liever ongefilterd dan stil leeg
        logger.exception("[trends] Kon bestaande content van %s niet lezen", site.get("name"))
        coverage = []

    with get_conn() as conn:
        signals = conn.execute(
            "SELECT * FROM radar_signals WHERE status = 'new' AND signal_score >= ? "
            "AND COALESCE(filter_reason, '') = '' AND ai_match_score >= ? "
            "ORDER BY signal_score DESC",
            (TREND_MIN_SIGNAL_SCORE, TREND_MIN_MATCH),
        ).fetchall()
        bestaand = _existing_queries(conn, site["id"])
        for row in signals:
            sig = dict(row)
            if _norm_name(sig.get("project")) != site_key:
                continue
            if len(created) >= TREND_MAX_PER_SYNC:
                break
            query = _signal_query(sig)
            if not query or not _bruikbare_query(query):
                skipped += 1
                continue
            # Dedupe langs `is_same_topic` in plaats van op exacte tekst: twee
            # artikelen over hetzelfde onderwerp met een andere kop zijn hetzelfde
            # onderwerp, en dat is precies het antwoord dat overal gelijk moet zijn.
            if any(oq.is_same_topic(query, q) for q in bestaand):
                skipped += 1
                continue
            oordeel = oq.assess({"query": query}, coverage, site)
            if oordeel.get("filter_reason"):
                skipped += 1
                continue
            oid = str(uuid.uuid4())
            angle = (sig.get("ai_angle") or "").strip() or f"Listicle rond '{query}'"
            hook = (sig.get("ai_hook") or "").strip()
            # Zeg wat het ís. De oude tekst begon met "Trending (Radar-score 76)"
            # boven een zoekwoord dat gewoon uit de watchlist kwam — een onware
            # bewering over de herkomst, en die stond bij alle 38 kansen die deze
            # brug ooit maakte. 'Vers' claimen we alleen als de bron een
            # publicatiedatum gaf.
            dagen = sig.get("published_days_ago")
            versheid = (f"{dagen} dagen oud" if isinstance(dagen, int) and dagen >= 0
                        else "publicatiedatum onbekend")
            rationale = (
                f"Radarsignaal ({versheid}, relevantie {sig.get('ai_match_score')}/100) "
                f"via {sig.get('source') or 'web'}: {sig.get('url', '')}"
                + (f" — {hook}" if hook else "")
            )[:400]
            conn.execute(
                """INSERT INTO opportunities
                   (id, site_id, query, clicks, impressions, ctr, position,
                    opportunity_score, action, angle, rationale, status, scanned_at)
                   VALUES (?, ?, ?, 0, 0, 0, 0, ?, 'nieuwe-content', ?, ?, 'new', ?)""",
                (oid, site["id"], query, float(sig.get("signal_score") or 0),
                 angle, rationale, _now()),
            )
            conn.execute(
                "UPDATE radar_signals SET status = 'targeted', updated_at = ? WHERE id = ?",
                (_now(), sig["id"]),
            )
            bestaand.add(query.lower())
            created.append({"id": oid, "query": query, "signal_id": sig["id"]})
    if created:
        oq.invalidate(site["id"])
    return {"site": site.get("name"), "created": len(created),
            "skipped": skipped, "opportunities": created}


def sync_all_trend_opportunities() -> Dict:
    """Trend-sync voor alle sites — draait automatisch na elke Radar sky-scan
    en is handmatig te triggeren via POST /api/demand/trend-sync."""
    results: Dict[str, Dict] = {}
    total = 0
    for s in sites_service.list_sites():
        full = sites_service.get_site(s["id"])
        if not full:
            continue
        try:
            result = sync_trend_opportunities(full)
        except Exception as e:
            logger.exception("[trends] Trend-sync mislukt voor %s", full.get("name"))
            result = {"site": full.get("name"), "created": 0, "error": str(e)[:200]}
        results[full["name"]] = result
        total += result.get("created", 0)
    if total:
        from ...shared.outcomes import log_outcome
        log_outcome("SEO", "trend-kansen",
                    f"{total} trending zoekwoord(en) uit de Mission Radar als content-kans klaargezet",
                    next_step="Ze lopen mee in de eerstvolgende content-run (di/vr) — of start een run-now")
    return {"total_created": total, "sites": results}
