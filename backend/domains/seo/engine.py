"""
Demand Engine — Goldie's pijler 1 (zoekwoordstrategie).

Haalt zoekwoorddata uit Google Search Console, filtert deterministisch de
'striking distance'-kansen eruit (zoekwoorden waar de site al half op scoort —
positie ~4-20 met veel impressies), en laat vervolgens Claude per kans een
actie en een concrete content-invalshoek bepalen.

Het zware denkwerk doet Claude (slim, duur); het bulk-schrijven doet later
Hermes via de conveyor (goedkoop). Dat is precies de taakverdeling uit de video.
"""
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional

import anthropic
import httpx

from ...shared.config import (
    ANTHROPIC_API_KEY, CLAUDE_MODEL, OPENROUTER_API_KEY, CLAUDE_VIA_OPENROUTER,
    OPENMODEL_API_KEY, OPENMODEL_BASE_URL, OPENMODEL_SMART_MODEL,
    anthropic_configured,
)
from ...shared.database import get_conn
from .gsc import fetch_query_performance

logger = logging.getLogger(__name__)

# 'Striking distance': nog niet in de top, maar wel binnen bereik.
MIN_POSITION = 4.0
MAX_POSITION = 20.0
DEFAULT_MIN_IMPRESSIONS = 20
DEFAULT_LIMIT = 25

# "Laaghangend fruit" uit Goldie's pijler 1: zoekwoorden met veel impressies
# maar weinig klikken (lage CTR) zijn de grootste content-kans — Google toont
# de site al, maar de snippet/pagina zet de impressie niet om. Bij CTR onder
# deze benchmark krijgt de kans een boost; bij CTR op/bovel benchmark blijft de
# score ongewijzigd (geen straf, zodat goede near-winners niet wegvallen).
BENCHMARK_CTR = 2.0       # % CTR waarop een striking-distance-kans "normaal" klikt
CTR_BOOST_PER_PT = 0.25   # elke procentpunt CTR onder de benchmark → +0.25 factor
CTR_BOOST_MAX = 1.6       # bovengrens van de CTR-boost-factor

_POSITION_SPAN = (MAX_POSITION + 1) - MIN_POSITION  # 17


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _strip_json_fences(raw: str) -> str:
    raw = raw.strip()
    if raw.startswith("```"):
        parts = raw.split("```")
        raw = parts[1] if len(parts) > 1 else raw
        if raw.startswith("json"):
            raw = raw[4:]
    return raw.strip()


def _opportunity_score(impressions: int, position: float,
                       ctr: Optional[float] = None) -> float:
    """Kansscore: veel impressies dichtbij pagina 1 = grootste hefboom.

    proximity = 1.0 bij positie 4, daalt naar ~0.06 bij positie 20. Buiten de
    striking-distance-band is de score 0 (geen kans of al goed/te ver weg).

    CTR-factor (Goldie pijler 1): een kans met veel impressies maar een
    abnormaal lage CTR is een grotere content-kans — Google toont de site al,
    de pagina/snippet zet de vertoning niet om in klikken. CTR op of boven
    `BENCHMARK_CTR` verandert de score niet; elke procentpunt eronder tikt de
    factor op (geplafonneerd op `CTR_BOOST_MAX`). Ontbrekende CTR → factor 1.0,
    dus achterwaarts compatibel.

    Let op: dit getal bepaalt alléén welke gemeten kansen de scan haalt en in
    welke volgorde ze de tabel in gaan. De volgorde die de UI en de
    contentmotor zien komt uit `potential.sort_key` — dat rekent in verwachte
    klikken en is dus wél vergelijkbaar met de speculatieve kansen, die hier
    per definitie nooit langskomen (zij hebben geen impressies).
    """
    if position < MIN_POSITION or position > MAX_POSITION:
        return 0.0
    proximity = (MAX_POSITION + 1 - position) / _POSITION_SPAN
    score = impressions * proximity
    if ctr is not None:
        ctr_factor = 1.0 + max(0.0, BENCHMARK_CTR - ctr) * CTR_BOOST_PER_PT
        ctr_factor = min(ctr_factor, CTR_BOOST_MAX)
        score *= ctr_factor
    return round(score, 1)


def find_opportunities(
    rows: List[Dict], min_impressions: int = DEFAULT_MIN_IMPRESSIONS, limit: int = DEFAULT_LIMIT
) -> List[Dict]:
    scored: List[Dict] = []
    for r in rows:
        if r["impressions"] < min_impressions:
            continue
        score = _opportunity_score(r["impressions"], r["position"], r.get("ctr"))
        if score <= 0:
            continue
        scored.append({**r, "opportunity_score": score})
    scored.sort(key=lambda x: x["opportunity_score"], reverse=True)
    return scored[:limit]


def _llm_available() -> bool:
    return anthropic_configured() or bool(OPENMODEL_API_KEY) or bool(OPENROUTER_API_KEY)


_ANNOTATE_SYSTEM = (
    "Je bent een Nederlandse SEO-strateeg. Je krijgt 'striking distance'-zoekwoorden "
    "uit Google Search Console (zoekwoorden waar de site al half op scoort). Per zoekwoord "
    "bepaal je: (1) action = 're-optimaliseren' wanneer er waarschijnlijk al een pagina voor "
    "bestaat die je kunt aanscherpen, of 'nieuwe-content' wanneer je er beter nieuwe content "
    "omheen bouwt; (2) angle = één concrete, onderscheidende content-invalshoek (max 12 woorden); "
    "(3) rationale = in één zin waarom dit kansrijk is, onderbouwd met positie/impressies/CTR. "
    "Wees concreet en vermijd algemeenheden."
)


def _claude_complete(system: str, prompt: str, max_tokens: int = 2000) -> str:
    """Vraag een Claude-completion via de eerste werkende route.

    1. Directe Anthropic-API (als ANTHROPIC_API_KEY geldig is).
    2. Claude-model via de OpenModel-gateway (OPENMODEL_SMART_MODEL) — op deze
       machine de primaire route.
    3. Claude via OpenRouter (CLAUDE_VIA_OPENROUTER) als laatste terugval.
    """
    errors = []
    if anthropic_configured():
        try:
            client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
            resp = client.messages.create(
                model=CLAUDE_MODEL, max_tokens=max_tokens,
                system=system, messages=[{"role": "user", "content": prompt}],
            )
            return resp.content[0].text
        except Exception as e:  # noqa: BLE001
            errors.append(f"anthropic: {e}")

    if OPENMODEL_API_KEY:
        try:
            resp = httpx.post(
                OPENMODEL_BASE_URL.rstrip("/") + "/v1/messages",
                headers={
                    "Authorization": f"Bearer {OPENMODEL_API_KEY}",
                    "anthropic-version": "2023-06-01",
                    "Content-Type": "application/json",
                },
                json={
                    "model": OPENMODEL_SMART_MODEL, "max_tokens": max_tokens,
                    "system": system,
                    "messages": [{"role": "user", "content": prompt}],
                },
                timeout=120.0,
            )
            resp.raise_for_status()
            _data = resp.json()
            usage = _data.get("usage") or {}
            if usage:
                from ...shared.outcomes import log_llm_usage
                log_llm_usage(
                    backend="openmodel", model=OPENMODEL_SMART_MODEL, route="seo-engine",
                    prompt_tokens=usage.get("input_tokens", 0),
                    completion_tokens=usage.get("output_tokens", 0),
                    total_tokens=usage.get("input_tokens", 0) + usage.get("output_tokens", 0),
                )
            text = "".join(
                b.get("text", "") for b in _data.get("content", [])
                if isinstance(b, dict)
            )
            if text.strip():
                return text
            errors.append("openmodel: lege respons")
        except Exception as e:  # noqa: BLE001
            errors.append(f"openmodel: {e}")

    if OPENROUTER_API_KEY:
        try:
            resp = httpx.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                    "HTTP-Referer": "http://localhost:1250",
                    "X-Title": "Agent OS Demand Engine",
                },
                json={
                    "model": CLAUDE_VIA_OPENROUTER,
                    "max_tokens": max_tokens,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": prompt},
                    ],
                },
                timeout=60.0,
            )
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"]
        except Exception as e:  # noqa: BLE001
            errors.append(f"openrouter: {e}")

    raise RuntimeError("Geen werkende Claude-route. " + " | ".join(errors))


def _annotate(opportunities: List[Dict], site_name: str) -> List[Dict]:
    """Laat Claude per kans action/angle/rationale bepalen. Index-gealigneerd."""
    base = [{"action": "", "angle": "", "rationale": ""} for _ in opportunities]
    if not opportunities or not _llm_available():
        return base

    table = "\n".join(
        f"{i + 1}. zoekwoord={o['query']!r} positie={o['position']} "
        f"impressies={o['impressions']} klikken={o['clicks']} ctr={o['ctr']}%"
        for i, o in enumerate(opportunities)
    )
    prompt = (
        f"Site: {site_name}\n\n"
        f"Hieronder {len(opportunities)} zoekwoorden uit Search Console, gesorteerd op kans:\n"
        f"{table}\n\n"
        f"Geef een JSON-array met exact {len(opportunities)} objecten, in DEZELFDE volgorde:\n"
        '[{"action": "re-optimaliseren of nieuwe-content", "angle": "...", "rationale": "..."}]\n'
        "Antwoord UITSLUITEND met de JSON-array, geen extra tekst."
    )

    try:
        raw = _claude_complete(_ANNOTATE_SYSTEM, prompt, max_tokens=2000)
        arr = json.loads(_strip_json_fences(raw))
        for i in range(min(len(arr), len(base))):
            item = arr[i] or {}
            action = (item.get("action") or "").strip().lower()
            if action not in ("re-optimaliseren", "nieuwe-content"):
                action = "nieuwe-content"
            base[i] = {
                "action": action,
                "angle": (item.get("angle") or "").strip(),
                "rationale": (item.get("rationale") or "").strip(),
            }
    except Exception as e:  # noqa: BLE001
        print(f"[demand] Claude annotatie mislukt: {e}")
    return base


def scan_site(
    site: Dict,
    days: int = 90,
    min_impressions: int = DEFAULT_MIN_IMPRESSIONS,
    limit: int = DEFAULT_LIMIT,
) -> Dict:
    """Draai een volledige Demand-Engine-scan voor één site en persisteer de kansen.

    Bestaande kansen met status != 'new' (al in behandeling/gepubliceerd/genegeerd)
    blijven staan en worden niet opnieuw aangeboden.
    """
    gsc_property = (site.get("gsc_property") or "").strip()
    if not gsc_property:
        raise ValueError("Site heeft geen gsc_property ingesteld.")

    rows = fetch_query_performance(gsc_property, days=days)
    opportunities = find_opportunities(rows, min_impressions=min_impressions, limit=limit)
    annotations = _annotate(opportunities, site.get("name") or gsc_property)

    scanned_at = _now()
    saved: List[Dict] = []
    with get_conn() as conn:
        existing = {
            row["query"]
            for row in conn.execute(
                "SELECT query FROM opportunities WHERE site_id = ? AND status != 'new'",
                (site["id"],),
            ).fetchall()
        }
        conn.execute(
            "DELETE FROM opportunities WHERE site_id = ? AND status = 'new'", (site["id"],)
        )
        for opp, ann in zip(opportunities, annotations):
            if opp["query"] in existing:
                continue
            oid = str(uuid.uuid4())
            conn.execute(
                """INSERT INTO opportunities
                   (id, site_id, query, clicks, impressions, ctr, position,
                    opportunity_score, action, angle, rationale, status, scanned_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'new', ?)""",
                (
                    oid, site["id"], opp["query"], opp["clicks"], opp["impressions"],
                    opp["ctr"], opp["position"], opp["opportunity_score"],
                    ann["action"], ann["angle"], ann["rationale"], scanned_at,
                ),
            )
            saved.append({
                "id": oid, "site_id": site["id"], "status": "new",
                "scanned_at": scanned_at, **opp, **ann,
            })

    # Cold-start: leverde GSC niets op én staat er ook niets meer open, dan
    # zit deze site vast (nieuwe site zonder rankings). Genereer dan kansen
    # uit het site-profiel zodat de contentmotor kan blijven draaien.
    cold_started: List[Dict] = []
    if not saved and not list_opportunities(site_id=site["id"], status="new"):
        cold_started = cold_start_opportunities(site)
        # Nog steeds niets? Dan ontbreekt waarschijnlijk het site-profiel (verse
        # site). Seed dan baseline-kansen uit de site-naam + vraag-intenties,
        # zodat de Kansen-tab niet leeg blijft en de contentmotor wel start.
        if not cold_started:
            cold_started = seed_baseline_opportunities(site)
        saved.extend(cold_started)

    return {
        "site_id": site["id"],
        "scanned_at": scanned_at,
        "analysed": len(rows),
        "found": len(opportunities),
        "new": len(saved),
        "cold_start": len(cold_started),
        "opportunities": saved,
    }


_COLD_START_SYSTEM = (
    "Je bent een Nederlandse SEO-strateeg gespecialiseerd in nieuwe websites zonder "
    "rankinghistorie. Je bedenkt long-tail zoekwoorden waar een verse site realistisch "
    "op kan scoren: specifiek, vraaggedreven, lage concurrentie, aansluitend op het "
    "site-profiel. Geen generieke head-terms (daar wint een nieuwe site nooit). "
    "Antwoord UITSLUITEND met een JSON-array."
)

_COLD_START_SCORE = 60.0  # onder echte striking-distance-kansen, boven niets


def cold_start_opportunities(site: Dict, count: int = 8) -> List[Dict]:
    """Kansen genereren voor een site zonder bruikbare GSC-data.

    Striking-distance vereist bestaande posities mét impressies — een site
    zonder live content heeft die per definitie niet, dus zonder deze
    cold-start blijft zo'n site eeuwig op 0 artikelen hangen. De kansen komen
    uit het site-profiel (kennisbank) en worden als handmatige kans opgeslagen;
    de contentmotor pakt ze daarna gewoon op. Vereist een LLM."""
    if not _llm_available():
        return []
    from .knowledge import get_site_knowledge
    kb = get_site_knowledge(site)
    profile = kb.get("profile") or ""
    if len(profile) < 40:
        return []  # zonder profiel wordt keyword-onderzoek giswerk — niet doen

    with get_conn() as conn:
        existing = {
            r["query"].strip().lower()
            for r in conn.execute(
                "SELECT query FROM opportunities WHERE site_id = ?", (site["id"],)
            ).fetchall()
        }

    prompt = (
        f"Site: {site.get('name')} ({site.get('base_url', '')})\n\n"
        f"## Site-profiel\n{profile[:2000]}\n\n"
        + (f"## CTA's / diensten\n- " + "\n- ".join(kb.get("ctas", [])[:6]) + "\n\n"
           if kb.get("ctas") else "")
        + f"Deze site heeft nog geen rankings. Bedenk {count} long-tail "
        "content-kansen waarmee de site zijn eerste organische bezoekers kan "
        "winnen. Geef een JSON-array met exact dit formaat:\n"
        '[{"query": "het zoekwoord (3-6 woorden, zoals mensen echt zoeken)", '
        '"angle": "concrete onderscheidende invalshoek (max 12 woorden)", '
        '"rationale": "waarom een nieuwe site hier kan winnen, één zin"}]'
    )
    try:
        raw = _claude_complete(_COLD_START_SYSTEM, prompt, max_tokens=2500)
        items = json.loads(_strip_json_fences(raw))
        assert isinstance(items, list)
    except Exception as e:  # noqa: BLE001
        print(f"[demand] Cold-start keyword-onderzoek mislukt: {e}")
        return []

    # Zelfde poort als de trend-brug (`trends.py`): een kans die de gate niet
    # doorstaat (kannibaal, ruis, of — sinds 9 aug 2026 — vormt geen echte
    # zoekopdracht) mag ook via deze tweede aanmaakroute niet ontstaan. Zonder
    # dit zou een toekomstige LLM-brainstorm alsnog een Engelse of afgekapte
    # "query" kunnen opleveren die er via cold-start omheen glipt.
    from . import opportunity_quality as quality
    coverage = quality.site_coverage(site)

    created: List[Dict] = []
    for item in items[:count]:
        query = (item.get("query") or "").strip() if isinstance(item, dict) else ""
        if not query or query.lower() in existing:
            continue
        oordeel = quality.assess({"query": query}, coverage, site)
        if oordeel.get("filter_reason"):
            continue
        existing.add(query.lower())
        created.append(create_manual_opportunity(
            site_id=site["id"], query=query,
            angle=(item.get("angle") or "").strip(),
            rationale=(item.get("rationale") or "").strip(),
            action="nieuwe-content", opportunity_score=_COLD_START_SCORE,
        ))
    return created


# Vraag-intenties die Goldie expliciet noemt als hoog-converterend voor nieuwe
# sites (hoe/wat/waar/beste/ervaringen). Zonder profiel-onderzoek is dit de
# minimal-viable fallback zodat de Kansen-tab van een jonge site niet leeg
# blijft en de contentmotor niet drooglopen.
_BASELINE_INTENTS = [
    ("hoe", "hoe je {onderwerp} het beste aanpakt"),
    ("wat", "wat is {onderwerp} precies"),
    ("waar", "waar vind je {onderwerp} in Nederland"),
    ("beste", "de beste {onderwerp} opties op een rij"),
    ("ervaringen", "ervaringen met {onderwerp} van echte gebruikers"),
    ("tips", "praktische tips voor {onderwerp}"),
    ("voordelen", "de voordelen van {onderwerp} op een rij"),
    ("kosten", "wat kost {onderwerp} en waar moet je op letten"),
]


def seed_baseline_opportunities(site: Dict, count: int = 8) -> List[Dict]:
    """Laatste-redmiddel fallback voor sites zonder profiel én zonder GSC-data.

    `cold_start_opportunities` vereist een site-profiel van >=40 chars; een
    verse site zonder profiel levert daardoor niets op en de Kansen-tab blijft
    leeg (de praktijk: 254 opportunities, maar slechts 18 met GSC-impressies).
    Deze functie seedt baseline-kansen uit de site-naam + Goldie's vraag-intenties
    zodat de contentmotor wél kan starten. Nooit een vervanger voor echt
    keyword-onderzoek — wel de ontsnapping uit de lege-tab-limbo.
    """
    name = (site.get("name") or "").strip()
    if not name:
        return []
    subject = name.split()[0].lower()  # eerste woord als onderwerp (bijv. "bewaardvoorjou")

    with get_conn() as conn:
        existing = {
            r["query"].strip().lower()
            for r in conn.execute(
                "SELECT query FROM opportunities WHERE site_id = ?", (site["id"],)
            ).fetchall()
        }

    created: List[Dict] = []
    for intent, tmpl in _BASELINE_INTENTS[:count]:
        query = tmpl.format(onderwerp=subject).strip()
        if query.lower() in existing:
            continue
        existing.add(query.lower())
        created.append(create_manual_opportunity(
            site_id=site["id"], query=query,
            angle=f"baseline-kans voor nieuwe site ({intent})",
            rationale=f"Geen GSC-data en geen profiel — vraag-intentie '{intent}' "
                      f"als minimale start voor de contentmotor.",
            action="nieuwe-content", opportunity_score=50.0,
        ))
    return created


async def run_weekly_demand_scan() -> None:
    """Scheduler (ma 06:15): kansen-scan voor alle sites met GSC, inclusief
    cold-start voor sites zonder rankings. Zonder deze job raakt de kansen-
    voorraad op en valt de di/vr-contentmotor stil zonder dat iemand het ziet."""
    import asyncio
    from ...shared.outcomes import log_outcome
    from . import sites as sites_service

    # Eerst opruimen, dan pas nieuwe kansen zoeken: een zoekwoord waarvan het
    # artikel is afgewezen hoort weer beschikbaar te zijn vóór we ergens anders
    # een nieuwe kans vandaan halen. Zonder deze stap groeit 'in_progress'
    # monotoon en droogt de contentmotor op met een volle tabel.
    try:
        await asyncio.to_thread(reconcile_opportunities)
    except Exception as e:  # noqa: BLE001
        logger.warning("[demand] Reconciliatie mislukt (niet fataal): %s", e)

    scanned, new_total, cold_total, grounded_total, failed = 0, 0, 0, 0, []
    for s in sites_service.list_sites():
        site = sites_service.get_site(s["id"]) or s
        if not (site.get("gsc_property") or "").strip():
            continue
        try:
            res = await asyncio.to_thread(scan_site, site)
            scanned += 1
            new_total += res.get("new", 0)
            cold_total += res.get("cold_start", 0)
        except Exception as e:  # noqa: BLE001
            failed.append(site.get("name") or site["id"])
            print(f"[demand] Weekscan mislukt voor {site.get('name')}: {e}")
            continue
        # Demand→Researcher-brug: grond de verse top-kansen in NotebookLM zodat
        # de di/vr-contentmotor op eigen onderzoek schrijft i.p.v. LLM-giswerk.
        # Best-effort — een kapotte NotebookLM mag de weekscan nooit breken.
        try:
            from ..researcher.service import get_service as researcher_service
            grounded_total += await researcher_service().ground_new_opportunities(site)
        except Exception as e:  # noqa: BLE001
            print(f"[demand] NotebookLM-grounding overgeslagen voor {site.get('name')}: {e}")
    log_outcome(
        "SEO", "demand_scan",
        f"Wekelijkse Demand-scan: {scanned} site(s), {new_total} nieuwe kans(en)"
        + (f" waarvan {cold_total} via cold-start" if cold_total else "")
        + (f"; {grounded_total} kans(en) gegrond in NotebookLM" if grounded_total else "")
        + (f"; mislukt: {', '.join(failed[:5])}" if failed else ""),
        artifact="/api/seo/opportunities",
        next_step=("Controleer de GSC-koppeling van de mislukte site(s)." if failed
                   else "Niets — de contentmotor pakt de kansen automatisch op (di/vr)."),
        status="error" if failed and not scanned else "ok",
    )


def create_manual_opportunity(
    site_id: str, query: str, angle: str, rationale: str,
    action: str = "nieuwe-content", opportunity_score: float = 100.0,
) -> Dict:
    """Voeg een kans handmatig toe (bv. uit keyword-onderzoek) i.p.v. via een GSC-scan.

    Voor jonge sites die nog niet ranken voor een zoekwoord levert GSC geen impressies,
    dus `scan_site` kan die kansen nooit vinden (striking-distance vereist al posities
    4-20 mét impressies). Dit is de ontsnappingsklep voor nog-niet-geschreven content.
    """
    oid = str(uuid.uuid4())
    scanned_at = _now()
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO opportunities
               (id, site_id, query, clicks, impressions, ctr, position,
                opportunity_score, action, angle, rationale, status, scanned_at)
               VALUES (?, ?, ?, 0, 0, 0, 0, ?, ?, ?, ?, 'new', ?)""",
            (oid, site_id, query, opportunity_score, action, angle, rationale, scanned_at),
        )
    return {
        "id": oid, "site_id": site_id, "query": query, "clicks": 0, "impressions": 0,
        "ctr": 0, "position": 0, "opportunity_score": opportunity_score,
        "action": action, "angle": angle, "rationale": rationale,
        "status": "new", "scanned_at": scanned_at,
    }


def list_opportunities(site_id: Optional[str] = None, status: Optional[str] = None) -> List[Dict]:
    clauses, params = [], []
    if site_id:
        clauses.append("site_id = ?")
        params.append(site_id)
    if status:
        if status == "open":
            # "Open" = nog niet afgerond (niet gepubliceerd en niet genegeerd)
            clauses.append("status IN ('new', 'in_progress')")
        else:
            clauses.append("status = ?")
            params.append(status)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    with get_conn() as conn:
        rows = conn.execute(
            f"SELECT * FROM opportunities{where} "
            "ORDER BY opportunity_score DESC, impressions DESC",
            params,
        ).fetchall()
    # De SQL-volgorde is alleen een stabiele voorsortering; het échte oordeel
    # valt in `potential`. Zonder deze stap wint de vaste cold-start-score (60)
    # het van elke gemeten kans, want die scoort op zijn eigen schaal 3-20 —
    # en dan pakt `select_topic` structureel het bedachte zoekwoord eerst.
    from . import potential
    return potential.annotate([dict(r) for r in rows])


def _fetch_published_jobs(site_id: str) -> list:
    """De gepubliceerde content_jobs van een site, één keer opgehaald.

    `_published_job_for_query` deed deze query vroeger zelf, per opportunity
    aangeroepen — bij 38 kansen dus 38 keer dezelfde tabel scannen (~4-5s per
    site, gemeten 9 aug 2026 toen de Control Room deze functie voor alle
    projecten tegelijk ging aanroepen). `list_opportunities_truth` haalt de
    rijen nu één keer op en geeft ze door aan elke matchpoging."""
    with get_conn() as conn:
        return conn.execute(
            "SELECT id, title, keyword, status, slug, publish_result "
            "FROM content_jobs WHERE site_id = ? AND status = 'published' "
            "ORDER BY created_at DESC",
            (site_id,),
        ).fetchall()


def _match_published_job(rows: list, query: str) -> Optional[Dict]:
    """Zoek in reeds opgehaalde gepubliceerd-live content_jobs naar een match
    voor `query`.

    `content_jobs` is de canonieke bron van wat er écht op de site live staat
    (status='published' + een site-URL in publish_result). Als die er is,
    is de opportunity de facto 'published' — ongeacht wat het opportunities-
    vlaggetje zegt. Zo kan de Kansen-UI nooit meer liegen.
    """
    if not query:
        return None
    from . import opportunity_quality as quality
    for r in rows:
        # Matchen via `opportunity_quality`, niet via substring. De oude
        # substring-test ("kw in q or q in kw") noemde 'levensverhaal laten
        # schrijven kosten' gepubliceerd zodra 'levensverhaal laten schrijven'
        # live stond — een échte andere zoekintentie die zijn eigen pagina
        # verdient, stil weggeschreven als 'al gedaan' (2 aug 2026). Twee
        # verschillende antwoorden op dezelfde vraag is precies hoe zulke
        # fouten ontstaan; er is er nu nog één.
        if quality.is_same_topic(query, r["keyword"] or "", r["title"] or "",
                                 r["slug"] or ""):
            pr = r["publish_result"]
            url = None
            if pr:
                try:
                    import json as _json
                    parsed = _json.loads(pr) if isinstance(pr, str) else pr
                    url = (parsed.get("site") or {}).get("url") or parsed.get("url")
                except Exception:
                    url = None
            if not url and r["slug"]:
                # URL uit vault-site base_url opbouwen kan hier niet (geen site-row);
                # frontend heeft de base_url en kan dit aanvullen indien nodig.
                pass
            return {"content_job_id": r["id"], "title": r["title"],
                    "slug": r["slug"], "live_url": url}
    return None


def _published_job_for_query(site_id: str, query: str) -> Optional[Dict]:
    """Enkelvoudige match — voor aanroepers die niet al over meerdere
    opportunities lopen (dus geen baat hebben bij het zelf cachen van
    `_fetch_published_jobs`). `list_opportunities_truth` gebruikt deze niet
    meer (zie de docstring bij `_fetch_published_jobs`)."""
    return _match_published_job(_fetch_published_jobs(site_id), query)


def list_opportunities_truth(site_id: Optional[str] = None,
                             status: Optional[str] = None,
                             include_filtered: bool = False) -> List[Dict]:
    """Als `list_opportunities`, maar corrigeert de status naar de WAARHEID
    en houdt alles tegen wat geen échte kans (meer) is.

    Twee lagen:

    1. Statuswaarheid. Een opportunity telt pas als 'published' als er
       daadwerkelijk een live artikel (content_job, status='published' + URL)
       aan hangt. Zo wordt de "Open (n)"-telling nooit meer vertekend door een
       mislukte terugkoppeling uit de schrijfpipeline.
    2. Kwaliteitsgate (`opportunity_quality`). Elke kans krijgt een
       `filter_reason`: al live, ligt in de Wachtrij, kannibaliseert bestaande
       content, navigatiezoekopdracht, andere taal, of te vaag. Alleen kansen
       zónder reden komen door de 'new'/'open'-filters heen — dat is wat het
       Kansen-paneel toont en wat `select_topic` mag oppakken.

    Niets verdwijnt: `status='uitgefilterd'` (of `include_filtered=True`) geeft
    juist de afgekeurde kansen mét het bewijs waarom. Een filter dat je niet
    kunt controleren is niet te vertrouwen.
    """
    kansen = list_opportunities(site_id=site_id,
                                status=None if status == "uitgefilterd" else status)
    if not site_id:
        return kansen
    published_rows = _fetch_published_jobs(site_id)
    for opp in kansen:
        has_live_flag = bool(opp.get("live_url"))
        if opp.get("status") == "published":
            # Gepubliceerd volgens de vlag. Respecteer een bestaande live_url
            # (die is de sterkste waarheid — artikel staat daadwerkelijk live,
            # ook al is de bijbehorende content_job later bv. ge-reject).
            if has_live_flag:
                continue
            # Geen live_url: check of er wél een gepubliceerd artikel is dat
            # we alsnog kunnen koppelen.
            job = _match_published_job(published_rows, opp.get("query", ""))
            if job:
                opp["live_url"] = job["live_url"]
                opp["content_job_id"] = job["content_job_id"]
            else:
                # Vlag zegt gepubliceerd, maar niets live gevonden → degradeer.
                opp["status"] = "in_progress"
                opp["published_at"] = None
        else:
            # Niet-published volgens de vlag: upgrade als er wél een live
            # artikel voor deze query bestaat (de pipeline-sync kan gemist zijn).
            job = _match_published_job(published_rows, opp.get("query", ""))
            if job:
                opp["status"] = "published"
                opp["live_url"] = job["live_url"]
                opp["content_job_id"] = job["content_job_id"]
                opp["published_at"] = opp.get("published_at") or _now()
    # Kwaliteitsgate: markeer dubbelen, kannibalen en ruis (en corrigeer de
    # status waar het oordeel harder is dan de vlag — een kans waarvoor een
    # concept in de Wachtrij ligt is 'in behandeling', geen nieuwe kans).
    _annotate_quality(site_id, kansen)

    # Als er expliciet op een status gefilterd werd, filter opnieuw op de
    # *gecorrigeerde* status — anders blijft een kans die we net naar
    # 'published' hebben gezet tellen in een 'in_progress'-query (en omgekeerd).
    if status == "uitgefilterd":
        # Precies wat er uit de Nieuw/Open-lijst is gewied: kannibalen en ruis.
        # Kansen die 'al live' of 'in de Wachtrij' bleken hebben een eerlijke
        # status gekregen en staan gewoon in hun eigen bak — die hier ook nog
        # eens tonen maakt de telling dubbel.
        return [o for o in kansen
                if o.get("filter_reason") and o["status"] == "new"]
    if status:
        if status == "open":
            kansen = [o for o in kansen if o["status"] in ("new", "in_progress")]
        else:
            kansen = [o for o in kansen if o["status"] == status]
    if not include_filtered and status in ("new", "open"):
        # Alleen wat zich nog steeds als níeuw werk aanbiedt hoeft gewied te
        # worden. 'al-live' en 'in-wachtrij' hebben hierboven al een eerlijke
        # status gekregen en horen in de Open-lijst thuis; wat blijft staan op
        # 'new' mét een reden is kannibalisatie of ruis.
        kansen = [o for o in kansen
                  if not (o.get("filter_reason") and o["status"] == "new")]
    return kansen


def _annotate_quality(site_id: str, kansen: List[Dict]) -> None:
    """Voeg het kwaliteitsoordeel toe en laat het de status corrigeren.

    Best-effort: gaat de gate stuk (bv. onbereikbare sitemap), dan blijft de
    lijst gewoon zichtbaar zonder oordeel — een kapotte filter mag het
    Kansen-paneel nooit leegtrekken, want "geen kansen" leest als "niets te
    doen" en dat is een gevaarlijker leugen dan een dubbele kans.
    """
    if not kansen:
        return
    try:
        from . import opportunity_quality as quality
        from . import sites as sites_service
        site = sites_service.get_site(site_id)
        if not site:
            return
        quality.annotate(kansen, site)
    except Exception as e:  # noqa: BLE001
        logger.warning("[demand] Kwaliteitsgate overgeslagen voor %s: %s",
                       site_id, str(e)[:200])
        return
    for opp in kansen:
        reason = opp.get("filter_reason")
        if reason == "al-live" and opp["status"] != "published":
            opp["status"] = "published"
            opp["live_url"] = opp.get("live_url") or opp.get("filter_url")
            opp["content_job_id"] = opp.get("content_job_id") or opp.get("filter_job_id")
        elif reason == "in-wachtrij" and opp["status"] == "new":
            # Er ligt al een concept: dit is werk in uitvoering, geen vrij
            # zoekwoord. Vóór 2 aug 2026 bestond deze uitkomst niet en bood het
            # paneel 'consultant sociaal domein' aan terwijl het artikel al een
            # dag in de Wachtrij lag.
            opp["status"] = "in_progress"
            opp["content_job_id"] = opp.get("content_job_id") or opp.get("filter_job_id")


def reconcile_opportunities(site_id: Optional[str] = None) -> Dict[str, int]:
    """Schrijf de waarheid uit `list_opportunities_truth` ook echt weg.

    Waarom dit nodig is (27 jul 2026): er stonden 62 kansen op 'in_progress'
    tegen 11 op 'published'. `select_topic` zet een kans op 'in_progress' zodra
    hij hem uitdeelt, maar niets zet hem ooit terug. Loopt het artikel daarna
    vast op de kwaliteitsgate, wordt het afgewezen, of struikelt de publicatie —
    dan is dat zoekwoord voorgoed verbruikt zonder dat er iets live staat.

    `list_opportunities_truth` corrigeerde de status al bij het lézen, maar
    `select_topic` leest `list_opportunities(status='new')` uit de tabel zelf en
    ziet die correctie dus nooit. Daardoor droogt de contentmotor op terwijl er
    tientallen bruikbare zoekwoorden in de tabel staan.

    Drie uitkomsten per kans:
      - er staat een artikel live      → 'published' + live_url
      - het artikel is afgewezen/vast  → terug naar 'new' (opnieuw oppakbaar)
      - er loopt nog werk              → laat 'in_progress' staan
    """
    from ..publish import content_pipeline as cp

    telling = {"published": 0, "vrijgegeven": 0, "ongewijzigd": 0}
    site_ids = [site_id] if site_id else [
        s["id"] for s in _all_site_ids()
    ]
    for sid in site_ids:
        for opp in list_opportunities(site_id=sid, status="in_progress"):
            job = _published_job_for_query(sid, opp.get("query", ""))
            if job:
                update_opportunity(opp["id"], status="published",
                                   live_url=job.get("live_url") or None,
                                   published_at=opp.get("published_at") or _now())
                telling["published"] += 1
                continue
            if _has_open_job(sid, opp.get("query", "")):
                telling["ongewijzigd"] += 1
                continue
            # Geen live artikel en geen lopend werk: het zoekwoord is vrij.
            update_opportunity(opp["id"], status="new")
            telling["vrijgegeven"] += 1
    logger.info("[demand] Kansen gereconcilieerd: %s", telling)
    return telling


def _all_site_ids() -> List[Dict]:
    with get_conn() as conn:
        return [dict(r) for r in conn.execute("SELECT id FROM sites")]


def _has_open_job(site_id: str, query: str) -> bool:
    """Loopt er nog werk voor dit zoekwoord? (wachtrij of verbeterronde)"""
    if not (query or "").strip():
        return False
    from . import opportunity_quality as quality
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT keyword, title, slug FROM content_jobs WHERE site_id = ? "
            "AND status IN ('pending_review', 'needs_work', 'approved', 'publish_failed')",
            (site_id,),
        ).fetchall()
    for r in rows:
        # Ook op titel/slug vergelijken: jobs uit de goal-engine hebben een leeg
        # keyword-veld en matchten daardoor nooit — dan geeft de reconciliatie
        # een zoekwoord vrij waarvoor het artikel al in de Wachtrij ligt.
        if quality.is_same_topic(query, r["keyword"] or "", r["title"] or "",
                                 r["slug"] or ""):
            return True
    return False


def update_opportunity(opp_id: str, status: Optional[str] = None,
                       live_url: Optional[str] = None,
                       published_at: Optional[str] = None) -> Optional[Dict]:
    """Werk een kans bij. Status én/of live-URL/publicatietimestamp kunnen los worden gezet.

    `live_url` wordt door de write-and-publish pipeline teruggeschreven zodra een
    artikel écht live staat — zo kan de Kansen-card in de UI onderscheiden tussen
    "handmatig op Gepubliceerd gevinkt" en "staat daadwerkelijk live op de site".
    """
    sets, params = [], []
    allowed = {"new", "in_progress", "published", "dismissed"}
    if status is not None:
        if status not in allowed:
            raise ValueError(f"Ongeldige status '{status}'. Toegestaan: {sorted(allowed)}")
        sets.append("status = ?")
        params.append(status)
    if live_url is not None:
        sets.append("live_url = ?")
        params.append(live_url)
    if published_at is not None:
        sets.append("published_at = ?")
        params.append(published_at)
    if not sets:
        return None
    with get_conn() as conn:
        cur = conn.execute(
            f"UPDATE opportunities SET {', '.join(sets)} WHERE id = ?",
            params + [opp_id],
        )
        if cur.rowcount == 0:
            return None
        row = conn.execute("SELECT * FROM opportunities WHERE id = ?", (opp_id,)).fetchone()
    return dict(row)


# Alias zodat bestaande callers (frontend updateOppStatus) blijven werken.
def update_opportunity_status(opp_id: str, status: str) -> Optional[Dict]:
    return update_opportunity(opp_id, status=status)
