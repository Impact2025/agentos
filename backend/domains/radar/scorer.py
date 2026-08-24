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
import re
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

# ── Aparte relevantie-rechter ────────────────────────────────────────────────
# Waarom los van generate_angle: dezelfde call die een overtuigende invalshoek
# VERKOOPT, kan zichzelf niet streng beoordelen — een verkoper scoort zijn eigen
# pitch altijd hoog. Daardoor stempelde het oude match_score bijna alles 85-95.
# Deze rechter ziet géén angle, alleen titel+snippet+merkthema's, en oordeelt
# adversarieel met een verankerde rubric en redenering-vóór-cijfer.
_RELEVANCE_SYSTEM = (
    "Je bent de strenge eindredacteur van een niche-merk. Je beschermt de schaarse "
    "contentcapaciteit: de MEESTE trending stukken zijn NIET de moeite waard voor "
    "juist dit merk, ook al gaan ze over een verwant klinkend thema. Beoordeel hoe "
    "goed een gevonden stuk past bij de KERN van dit merk — niets anders.\n\n"
    "Schaal (wees streng, gebruik nadrukkelijk ook de onderkant):\n"
    "- 0-20  : ander vakgebied; raakt geen enkel kernthema.\n"
    "- 21-40 : zijdelings; deelt hooguit één algemeen woord (bijv. 'AI') zonder de context.\n"
    "- 41-60 : raakvlak, maar niet het hart van het merk; zou een uitstapje zijn.\n"
    "- 61-80 : duidelijk relevant; past bij een kernthema.\n"
    "- 81-100: kernonderwerp; hier gaat dit merk echt over.\n"
    "Een score >70 betekent dat je je reputatie eraan verbindt dat dit op de blog "
    "van DIT merk hoort. Reserveer die.\n\n"
    "Voorbeeld-redenering:\n"
    "- Merk over dieren-adoptie; stuk 'Hoe kies je een goede uitlaatservice' → raakt "
    "huisdierenzorg, kernthema → 72.\n"
    "- Merk over dieren-adoptie; stuk 'AI transformeert de Indiase IT-sector' → deelt "
    "geen enkel kernthema, alleen het modewoord AI → 8.\n\n"
    "Werkwijze: (1) noem in één zin de 2-3 kernthema's van dit merk; (2) zeg in één "
    "zin of en hoe dit stuk die raakt (of juist niet); (3) geef DAARNA pas het cijfer.\n\n"
    "ANTWOORD UITSLUITEND als JSON, zonder markdown:\n"
    '{"reden": "<stap 1 + stap 2, kort>", "score": <geheel getal 0-100>}'
)


def _project_themes(project: Optional[str]) -> str:
    """Kernthema's van een project als anker voor de relevantie-rechter.

    Hergebruikt de gecureerde high-value-tokens (het merk-hart). Zonder curatie
    valt hij terug op de projectnaam zelf."""
    toks = _HIGH_VALUE_TOKENS.get((project or "").lower())
    if toks:
        return ", ".join(t for t, _ in toks[:12])
    return project or "algemeen"


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


def _boost_haystack(text: str) -> str:
    """Normaliseer tekst voor woordgrens-veilige token-matching.

    Vervangt alle niet-alfanumerieke tekens (®, leestekens, koppeltekens) door
    spaties en omringt het geheel met spaties, zodat een token als 'ai' of
    'lego serious play' als heel woord gematcht kan worden met f' {tok} '.
    Zonder deze normalisatie brak '®' de token-match ('lego® serious play®') en
    matchte 'ai' binnen willekeurige woorden ('email', 'detail')."""
    cleaned = re.sub(r"[^a-z0-9]+", " ", text.lower())
    return " " + re.sub(r"\s+", " ", cleaned).strip() + " "


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
    snippet: str = "",
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
    #
    # LET OP: match tegen titel + snippet, NIET tegen het keyword. Het keyword is
    # door de gebruiker ingesteld (bijv. "lego serious play ai draagvlak") en
    # bevat de boost-tokens dus per definitie — het meenemen gaf ÉLK signaal in
    # die bucket de volle bonus, ook een volstrekt off-topic artikel. De bonus
    # hoort te belonen dat het gevónden stuk het onderwerp raakt, niet dat het
    # keyword de juiste woorden bevat.
    project_l = (project or "").lower()
    tokens = _HIGH_VALUE_TOKENS.get(project_l)
    if tokens:
        haystack = _boost_haystack(f"{title} {snippet}")
        bonus = 0.0
        for tok, val in tokens:
            # Woordgrens-veilig: " tok " voorkomt zowel de ®-breuk
            # ("lego® serious play®" → matcht nu "lego serious play") als de
            # substring-lek ("ai" mag "email"/"detail" niet raken).
            if f" {tok} " in haystack:
                bonus += val
        score += min(bonus, HIGH_VALUE_CAP)

    return round(min(score, 100.0), 1)


# Straf voor een onbewezen relevantie: als de AI-match ontbrak of onparseerbaar
# was (match_score = -1), is de topische fit NIET aangetoond. Dat mag geen
# vrijbrief zijn — een vers, keyword-passend maar mogelijk off-topic signaal zou
# anders even hoog scoren als een bewezen-relevant stuk. We dempen het daarom
# onder de bewezen-relevante signalen, zonder het onzichtbaar te maken.
UNKNOWN_MATCH_PENALTY = 15.0


def blend_scores(signal_score: float, match_score: int) -> float:
    """Combineer heuristiek met de AI-profielmatch tot de definitieve score.

    - match_score >= 0: relevantie is beoordeeld → weeg mee (0.55/0.45).
    - match_score < 0 (onbekend/gefaald): relevantie is NIET aangetoond → damp
      de heuristische score i.p.v. hem vol te laten tellen. Zo zakt een signaal
      met een mislukt relevantie-oordeel onder de signalen die het wél haalden.
    """
    if match_score is None or match_score < 0:
        return round(max(0.0, signal_score - UNKNOWN_MATCH_PENALTY), 1)
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


def _salvage_angle(raw: str) -> Optional[Dict]:
    """Vis velden uit een respons die géén geldige JSON is.

    Het gratis analist-model levert regelmatig bijna-JSON (trailing komma, tekst
    eromheen, een los veld op een nieuwe regel). Vroeger belandde zo'n respons op
    match_score=-1 en verloor het signaal zijn relevantie-oordeel volledig — de
    hoofdoorzaak dat ~de helft van alle signalen ongewogen bleef. Deze salvage
    haalt met regex minstens de match_score (en waar mogelijk hook/titels) eruit,
    zodat de relevantie alsnog meetelt. Geeft None als er niets bruikbaars in zit.
    """
    m = re.search(r'match[_ ]?score["\']?\s*[:=]\s*(\d{1,3})', raw, re.IGNORECASE)
    if not m:
        return None
    match = max(-1, min(int(m.group(1)), 100))
    hook_m = re.search(r'hook["\']?\s*[:=]\s*["\']([^"\']{3,300})', raw, re.IGNORECASE)
    titles = re.findall(r'["\']([^"\']{8,200})["\']', raw)
    return {
        "hook": (hook_m.group(1).strip() if hook_m else ""),
        "angle": raw[:400],
        "titles": [t.strip() for t in titles[:3]],
        "match_score": match,
    }


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
            purpose="radar-angle",
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
        # Geen geldig JSON na retry — probeer de relevantie + hook/titels alsnog
        # met regex te redden, zodat match_score niet stilletjes op -1 valt.
        salvaged = _salvage_angle(raw)
        if salvaged:
            log.info("[radar] Angle-respons gered via salvage voor %s (match=%s)",
                     url, salvaged["match_score"])
            return salvaged
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


async def score_relevance(
    title: str, snippet: str, project: str, _attempt: int = 0,
) -> int:
    """Onafhankelijk relevantie-oordeel (0-100) door een strenge eindredacteur.

    Los van generate_angle zodat het cijfer niet besmet raakt door de zojuist
    verkochte invalshoek. Retourneert -1 als het oordeel echt niet lukt (dan
    dempt blend_scores het signaal — geen vrijbrief). Eén retry bij lege/kapotte
    respons; daarna een regex-salvage van het cijfer."""
    _system, model_override = _analyst_config()  # respecteer model-keuze uit profiel
    user_content = (
        f"Merk: {project or 'algemeen'}\n"
        f"Kernthema's van dit merk: {_project_themes(project)}\n\n"
        f"Gevonden stuk:\nTitel: {title}\n"
        f"Snippet: {(snippet or '')[:1200]}\n\n"
        "Beoordeel de fit met de kern van dit merk."
    )
    chunks: List[str] = []
    try:
        async for ev in agent_service.run_agent(
            messages=[{"role": "user", "content": user_content}],
            system_prompt=_RELEVANCE_SYSTEM,
            agent="hermes",
            model_override=model_override,
            use_tools=False,
            # Ruim budget: de rechter redeneert eerst (2 zinnen) en geeft DAARNA
            # het cijfer. Met te weinig tokens vreet de redenering — of een
            # <think>-block van deepseek — het budget op en wordt het cijfer
            # afgekapt (→ onparseerbaar → -1). 300 bleek te krap.
            max_tokens=700,
            purpose="radar-relevantie",
        ):
            if ev.get("type") == "text":
                chunks.append(ev["text"])
    except Exception as e:
        log.warning("[radar] Relevantie-oordeel mislukt voor '%s': %s", title[:60], e)
        return -1

    raw = "".join(chunks)
    if not raw and _attempt == 0:
        return await score_relevance(title, snippet, project, _attempt=1)
    if not raw:
        return -1
    score = _extract_relevance_score(raw)
    if score is None and _attempt == 0:
        return await score_relevance(title, snippet, project, _attempt=1)
    if score is None:
        return -1
    return max(0, min(score, 100))


def _extract_relevance_score(raw: str) -> Optional[int]:
    """Haal het cijfer uit de respons, robuust voor een zwak model.

    Alleen een EXPLICIET cijfer telt: (1) nette JSON, of (2) een 'score'-veld met
    regex. Bewust GÉÉN 'pak het laatste getal'-vangnet: de redenering bevat vaak
    getallen ($100m, 2.5B) en een fout hoog cijfer zou een off-topic stuk juist
    aanvallen. Geen cijfer → None → de aanroeper geeft -1 (blend dempt veilig)."""
    try:
        return int(json.loads(_strip_json(raw)).get("score"))
    except Exception:
        pass
    m = re.search(r'score["\']?\s*[:=]\s*(\d{1,3})', raw, re.IGNORECASE)
    return int(m.group(1)) if m else None
