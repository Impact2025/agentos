"""
Mission Radar — Signal Score-berekening + AI angle-generator.

Twee lagen:
  1. compute_signal_score(): LLM-vrije heuristiek (versheid, bron-autoriteit,
     keyword-match, Tavily-relevantie) — draait over ELK gevonden resultaat,
     dus goedkoop en snel.
  2. generate_angle(): het "Radar Trend-Analist"-expertprofiel genereert per
     kansrijk signaal een unieke hook + invalshoek + 3 titelvoorstellen.
     Titels van concurrenten worden dus nooit gekopieerd, alleen als
     inspiratie gebruikt. Strikt JSON-contract, zelfde stijl als de
     Vacature Fit-Analist.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional
from urllib.parse import urlparse

from ...shared.database import get_conn
from ...shared import agent_runner as agent_service
from ...shared.config import hermes_backend

log = logging.getLogger(__name__)

ANALYST_PROFILE_NAME = "Radar Trend-Analist"
_ANALYST_MODEL = "openrouter/openai/gpt-oss-120b:free"

_ANALYST_PROMPT = (
    "Je bent een Nederlandstalige trend-analist voor content-marketing (AEO/SEO). "
    "Je krijgt één trending stuk content van een concurrent of uit de markt "
    "(titel, bron, snippet) plus de context van het eigen project. Jouw taak: "
    "NIET de titel kopiëren, maar een ORIGINELE, betere insteek bedenken die "
    "past bij het eigen merk en kans maakt op Google AI Overviews.\n\n"
    "Regels:\n"
    "- De hook is één zin die meteen nieuwsgierig maakt (B1-niveau, geen clichés).\n"
    "- De invalshoek (angle) beschrijft in 2-3 zinnen welk uniek perspectief wij "
    "kiezen t.o.v. het origineel (information gain).\n"
    "- De 3 titels zijn geoptimaliseerd voor zoekintentie; minimaal één listicle-vorm "
    "('7 manieren om…') omdat listicles sneller ranken in AI Overviews.\n"
    "- match_score = hoe goed dit onderwerp bij het opgegeven project past (0-100). "
    "Wees streng: een onderwerp dat niets met het project te maken heeft scoort <30.\n\n"
    "ANTWOORD UITSLUITEND met één JSON-object, zonder markdown eromheen:\n"
    '{"hook": "<één zin>", "angle": "<2-3 zinnen unieke invalshoek>", '
    '"titles": ["<titel 1>", "<titel 2>", "<titel 3>"], '
    '"match_score": <geheel getal 0-100>}'
)

# Bron-autoriteit: Google hecht momenteel veel waarde aan Reddit-discussies en
# YouTube in AI Overviews — die krijgen dus een hogere basisweging.
_SOURCE_WEIGHTS = {
    "reddit": 20.0,
    "youtube": 16.0,
    "news": 12.0,
    "rss": 10.0,
    "blog": 8.0,
    "overig": 6.0,
}

# ── Project-specifieke waardeboost ──────────────────────────────────────
# Bepaalde keywords/domeinen zijn voor een project veel waardevoller dan de
# generieke heuristiek (versheid+bron) ooit vangt. Een GPS-teamuitje-signaal
# is voor IctusGo goud, maar scoort op zichzelf laag. Deze boost trekt die
# signalen over de AEO-drempel zodat de agent ze zelfstandig aanvalt.
#
# Structuur: project (lowercase) -> lijst van (token, bonus) paren. Een signaal
# krijgt de som van bonussen waarvan een token in titel OF keyword voorkomt
# (geplafonneerd op HIGH_VALUE_CAP per signaal).
_HIGH_VALUE_TOKENS: Dict[str, List[tuple]] = {
    "ictusgo": [
        ("gps", 12.0),
        ("teambuilding", 10.0),
        ("teamuitje", 10.0),
        ("bedrijfsuitje", 10.0),
        ("hoofddorp", 14.0),
        ("schiphol", 12.0),
        ("haarlemmermeer", 12.0),
        ("wkr", 12.0),
        ("csrd", 12.0),
        ("maatschappelijk", 10.0),
        ("sociale impact", 12.0),
        ("geluksmoment", 12.0),
        ("vrijwilliger", 10.0),
        ("citygame", 8.0),
        ("scavenger", 8.0),
        ("flitz", 8.0),
    ],
    "bewaardvoorjou": [
        ("bewaard", 12.0),
        ("spullen", 10.0),
        ("kringloop", 10.0),
        ("secondhand", 10.0),
        ("circular", 10.0),
        ("duurzaam", 8.0),
    ],
    "bijeen": [
        ("bijeen", 12.0),
        ("event", 10.0),
        ("meeting", 10.0),
        ("congres", 10.0),
        ("netwerk", 8.0),
    ],
    "teambuildingmetimpact": [
        ("teambuilding", 12.0),
        ("impact", 12.0),
        ("wkr", 14.0),
        ("csrd", 14.0),
        ("esg", 12.0),
        ("sroi", 14.0),
        ("mvo", 12.0),
        ("vrijwilliger", 12.0),
        ("bedrijfsvrijwilligerswerk", 12.0),
        ("legoseriousplay", 12.0),
        ("lego serious play", 12.0),
        ("meetbare", 12.0),
        ("maatschappelijk", 10.0),
        ("hoofddorp", 14.0),
        ("haarlemmermeer", 14.0),
        ("schiphol", 12.0),
        ("social return", 12.0),
        ("plekken met een verhaal", 10.0),
    ],
    "weareimpact": [
        ("ai", 12.0),
        ("kunstmatige intelligentie", 12.0),
        ("zorg", 14.0),
        ("welzijn", 14.0),
        ("gemeente", 14.0),
        ("sociaal domein", 16.0),
        ("wmo", 12.0),
        ("jeugdzorg", 12.0),
        ("lego serious play", 14.0),
        ("legoseriousplay", 14.0),
        ("change management", 14.0),
        ("verandermanagement", 14.0),
        ("interim", 10.0),
        ("directeur sociaal", 12.0),
        ("digitale transformatie", 12.0),
        ("innovatie", 10.0),
        ("datagedreven", 10.0),
        ("ggz", 10.0),
    ],
    "pootgelukkig": [
        ("adoptie", 12.0),
        ("hond adopteren", 14.0),
        ("kat adopteren", 14.0),
        ("dier adopteren", 12.0),
        ("asiel", 12.0),
        ("dierenasiel", 12.0),
        ("asieldier", 12.0),
        ("herplaatsing", 12.0),
        ("herplaatster", 12.0),
        ("adoptiehond", 12.0),
        ("konijn adopteren", 12.0),
        ("kittens", 10.0),
        ("vrijwilliger", 10.0),
        ("puppycursus", 10.0),
        ("dierenbescherming", 12.0),
        ("licg", 10.0),
        ("dierennoodhulp", 10.0),
        ("uitlaatservice", 10.0),
        ("dierenopvang", 10.0),
    ],
}
HIGH_VALUE_CAP = 22.0  # maximale bonus per signaal

_NOW = lambda: datetime.now(timezone.utc).isoformat()  # noqa: E731


def classify_source(url: str) -> str:
    host = (urlparse(url).netloc or "").lower()
    if "reddit.com" in host:
        return "reddit"
    if "youtube.com" in host or "youtu.be" in host:
        return "youtube"
    if any(n in host for n in ("nu.nl", "nos.nl", "tweakers", "techcrunch", "nrc.nl", "fd.nl")):
        return "news"
    return "blog"


def compute_signal_score(
    title: str,
    url: str,
    keyword: str,
    published_days_ago: int = -1,
    tavily_score: float = 0.0,
    source: Optional[str] = None,
    project: Optional[str] = None,
) -> float:
    """Heuristische Virality/Signal Score 0-100 (LLM-vrij).

    Opbouw: versheid (max 40) + bron-autoriteit (max 20) + keyword-match in
    titel (max 20) + Tavily-relevantie (max 20)."""
    score = 0.0

    # Versheid: vandaag = 40, lineair aflopend naar 0 bij 14 dagen; onbekend = 15.
    if published_days_ago is None or published_days_ago < 0:
        score += 15.0
    else:
        score += max(0.0, 40.0 * (1 - min(published_days_ago, 14) / 14.0))

    score += _SOURCE_WEIGHTS.get(source or classify_source(url), 6.0)

    # Keyword-match: hoeveel woorden van het keyword komen in de titel voor.
    kw_words = [w for w in keyword.lower().split() if len(w) > 2]
    if kw_words:
        hits = sum(1 for w in kw_words if w in title.lower())
        score += 20.0 * (hits / len(kw_words))

    # Tavily geeft per resultaat een relevantiescore 0-1.
    score += min(max(tavily_score, 0.0), 1.0) * 20.0

    # Project-specifieke waardeboost: signalen die het project raken op zijn
    # kernwaarde (bijv. GPS/regio/WKR voor IctusGo) worden over de AEO-drempel
    # getrokken zodat de agent ze zelfstandig aanvalt.
    project_l = (project or "").lower()
    tokens = _HIGH_VALUE_TOKENS.get(project_l)
    if tokens:
        haystack = f"{title} {keyword}".lower()
        bonus = 0.0
        for tok, val in tokens:
            if tok in haystack:
                bonus += val
        score += min(bonus, HIGH_VALUE_CAP)

    return round(min(score, 100.0), 1)


def blend_scores(signal_score: float, match_score: int) -> float:
    """Combineer heuristiek met de AI-profielmatch tot de definitieve score."""
    if match_score is None or match_score < 0:
        return signal_score
    return round(0.55 * signal_score + 0.45 * match_score, 1)


# ── AI-laag ──────────────────────────────────────────────────────────────────

def ensure_analyst_profile() -> None:
    """Seed het Radar Trend-Analist-profiel (idempotent, zelfde patroon als
    pipeline/service.py::_ensure_specialist_profiles)."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id FROM agent_profiles WHERE name = ?", (ANALYST_PROFILE_NAME,)
        ).fetchone()
        if row:
            return
        conn.execute(
            "INSERT INTO agent_profiles (name, model, system_prompt, created_at) "
            "VALUES (?, ?, ?, ?)",
            (ANALYST_PROFILE_NAME, _ANALYST_MODEL, _ANALYST_PROMPT, _NOW()),
        )
    log.info("[radar] Profiel '%s' geseed", ANALYST_PROFILE_NAME)


def _analyst_config() -> tuple:
    """(system_prompt, model_override) uit het profiel — handmatige aanpassingen
    aan het profiel in de UI blijven zo gerespecteerd."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT system_prompt, model FROM agent_profiles WHERE name = ?",
            (ANALYST_PROFILE_NAME,),
        ).fetchone()
    if row and (row["system_prompt"] or "").strip():
        model = (row["model"] or "").strip()
        if hermes_backend() == "openrouter" and model:
            model = model[len("openrouter/"):] if model.startswith("openrouter/") else model
        else:
            model = None
        return row["system_prompt"].strip(), model
    return _ANALYST_PROMPT, None


def _strip_json(raw: str) -> str:
    raw = raw.strip()
    if raw.startswith("```"):
        parts = raw.split("```")
        raw = parts[1] if len(parts) > 1 else raw
        if raw.startswith("json"):
            raw = raw[4:]
    start, end = raw.find("{"), raw.rfind("}")
    if start != -1 and end > start:
        raw = raw[start:end + 1]
    return raw.strip()


_EMPTY_ANGLE = {"hook": "", "angle": "", "titles": [], "match_score": -1}


async def generate_angle(
    title: str, url: str, source: str, snippet: str,
    keyword: str, project: str,
    _attempt: int = 0,
) -> Dict:
    """Genereer hook + unieke invalshoek + titels voor één signaal. Faalt zacht
    (lege velden + match_score -1) zodat een LLM-storing de scan nooit blokkeert.
    Bij een lege of onparseerbare respons volgt één automatische retry."""
    system, model_override = _analyst_config()
    user_content = (
        f"Eigen project: {project or 'algemeen'}\n"
        f"Gemonitord keyword/domein: {keyword}\n\n"
        f"Gevonden trending content:\n"
        f"Titel: {title}\nBron: {source} ({url})\n"
        f"Snippet:\n{(snippet or '')[:1500]}"
    )
    chunks: List[str] = []
    try:
        async for ev in agent_service.run_agent(
            messages=[{"role": "user", "content": user_content}],
            system_prompt=system,
            agent="hermes",
            model_override=model_override,
            use_tools=False,
            max_tokens=1000,
        ):
            if ev.get("type") == "text":
                chunks.append(ev["text"])
    except Exception as e:
        log.warning("[radar] Angle-generatie mislukt voor %s: %s", url, e)
        return dict(_EMPTY_ANGLE)

    raw = "".join(chunks)
    if not raw:
        if _attempt == 0:
            log.info("[radar] Lege angle-respons voor %s — retry", url)
            return await generate_angle(title, url, source, snippet, keyword, project, _attempt=1)
        return dict(_EMPTY_ANGLE)
    try:
        parsed = json.loads(_strip_json(raw))
    except Exception:
        if _attempt == 0:
            log.info("[radar] Onparseerbare angle-respons voor %s — retry", url)
            return await generate_angle(title, url, source, snippet, keyword, project, _attempt=1)
        # Geen geldig JSON na retry — gebruik de ruwe tekst dan tenminste als angle.
        return {**_EMPTY_ANGLE, "angle": raw[:400]}

    titles = parsed.get("titles") or []
    if not isinstance(titles, list):
        titles = [str(titles)]
    try:
        match = int(parsed.get("match_score", -1))
    except (TypeError, ValueError):
        match = -1
    return {
        "hook": str(parsed.get("hook", ""))[:500],
        "angle": str(parsed.get("angle", ""))[:1000],
        "titles": [str(t)[:200] for t in titles[:3]],
        "match_score": max(-1, min(match, 100)),
    }
