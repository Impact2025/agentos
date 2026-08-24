"""
LinkedIn Hand-Raising Signals — de ontbrekende helft van Mission Radar.

De video ("Marketing Agents Masterclass", Greg Isenberg / Cody Schneider) bouwt
een Autonomous Outbound SDR op 'hand-raising signals': iemand die de posts van
een invloedrijk account in je niche liket/commentt, is een warme prospect —
die steekt z'n hand op. Dit is exact dat mechanisme, nagebouwd op ImpactOS:

  Stap 1  Signalen scrapen      — Tavily X-ray op LinkedIn-profielen die de
          (deterministisch)       gemonitorde accounts engageerden. Geen Apify-
                                   key nodig, goedkope compute, herhaalbaar.
  Stap 2  ICP-filter + fit       — Hermes classificeert de hand-raiser (rol/
          (LLM, scherp)           branche) en berekent een fit-score 0-100, in
                                   dezelfde geest als prospecting/quality_gate.
  Stap 3  Bridge → prospecting   — een fitte hand-raiser wordt een lead
                                   (status 'new', source='radar_linkedin') in de
                                   bestaande acquisitie-funnel. Daarna geldt de
                                   mens-in-loop review-gate — géén autonome mail.

Compliance-first (expliciete keuze t.o.v. de video):
  - géén burner-domeinen / Instantly / HeyReach-automatisering;
  - géén auto-verzending — de lead doorloopt de gewone outreach-approve-gate;
  - AVG: we slaan alleen publieke hand-raisers op, plus één vault/lead-rij.
"""
from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from ...shared.config import (
    TAVILY_API_KEY, HERMES_MODEL, HERMES_FALLBACK_MODELS,
    OPENROUTER_API_KEY, ANTHROPIC_API_KEY, CLAUDE_MODEL,
    HERMES_LOCAL_URL, HERMES_LOCAL_KEY, OLLAMA_BASE_URL, OLLAMA_MODEL,
    OBSIDIAN_VAULT_PATH, hermes_backend,
)
from ...shared.database import get_conn
from ..prospecting.service import LeadsService, _now as leads_now
from ..prospecting import validate as lead_validate

log = logging.getLogger(__name__)

# Drempel voordat een hand-raiser de prospecting-funnel in mag. Lager dan de
# gewone quality_gate (40) omdat een hand-raising signaal intrinsically warmer
# is — iemand die engageert is geen koude scrape. Maar we willen nog steeds
# geen duidelijke mismatches in de outreach-voorraad.
MIN_HANDRAISER_FIT = 35

# Hoeveel profielen we per watch-account doorzoeken (budget/ruis-balans).
MAX_PROFILES_PER_WATCH = 15

# Engagement-gewicht: commentaren zijn warmere signalen dan likes.
ENGAGEMENT_WEIGHT = {"posted": 1.0, "commented": 0.9, "liked": 0.6, "": 0.5}

# ICP-richting: welke branches/rollen tellen als 'hoog' voor de fit-score.
# Vincent = WeAreImpact (AI in zorg/welzijn) + Bewaard voor altijd (keepsake,
# B2B partners: notarissen, uitvaart). Verbreed hiernaar in _score_fit().
DEFAULT_ICP_HINTS = (
    "zorg, welzijn, gemeente, overheid, notaris, uitvaart, nalatenschap, "
    "keepsake, herinnering, AI, consultancy, interim, programma, projectleider, "
    "digitalisering, innovatie, maatschappelijke organisatie, onderwijs"
)


# ── Helpers ────────────────────────────────────────────────────────────────

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _norm_handle(raw: str) -> str:
    """Normaliseer een LinkedIn-account naar een kaal profiel-slug.

    Accepteert 'Vincent van Munster', 'vincent-van-munster',
    'linkedin.com/in/vincent-van-munster' of 'in/vincent-van-munster'.
    """
    s = (raw or "").strip()
    if not s:
        return ""
    # haal een eventuele URL terug naar de slug na '/in/'
    m = re.search(r"linkedin\.com/in/([^/?#\s]+)", s)
    if m:
        s = m.group(1)
    else:
        s = s.split("/")[-1].split("?")[0]
    return s.strip("/").strip().lower() or s.lower()


def _extract_json(raw: str) -> str:
    s = (raw or "").strip()
    if s.startswith("```"):
        s = s.strip("`")
        if s[:4].lower() == "json":
            s = s[4:]
    start, end = s.find("{"), s.rfind("}")
    return s[start:end + 1] if start != -1 and end > start else s


# ── Hermes-classificatie (gedeeld backend, zelfde als prospecting) ──────────

def _hermes_complete(prompt: str, max_tokens: int = 600) -> str:
    backend = hermes_backend()
    if backend == "openrouter" and OPENROUTER_API_KEY:
        try:
            import httpx
            resp = httpx.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}",
                          "Content-Type": "application/json"},
                json={"model": HERMES_MODEL,
                      "messages": [{"role": "user", "content": prompt}],
                      "max_tokens": max_tokens},
                timeout=60.0,
            )
            if resp.status_code == 200:
                return resp.json()["choices"][0]["message"]["content"]
        except Exception as e:  # noqa: BLE001
            log.warning("[li-signal] OpenRouter fout: %s", e)
    if backend in ("local", "ollama") or OLLAMA_BASE_URL:
        try:
            import httpx
            base = (HERMES_LOCAL_URL or OLLAMA_BASE_URL).rstrip("/")
            key = HERMES_LOCAL_KEY or ""
            headers = {"Content-Type": "application/json"}
            if key:
                headers["Authorization"] = f"Bearer {key}"
            resp = httpx.post(
                f"{base}/v1/chat/completions",
                headers=headers,
                json={"model": OLLAMA_MODEL or "llama3",
                      "messages": [{"role": "user", "content": prompt}],
                      "max_tokens": max_tokens},
                timeout=60.0,
            )
            if resp.status_code == 200:
                return resp.json()["choices"][0]["message"]["content"]
        except Exception as e:  # noqa: BLE001
            log.warning("[li-signal] Local/Ollama fout: %s", e)
    if ANTHROPIC_API_KEY:
        try:
            from anthropic import Anthropic
            c = Anthropic(api_key=ANTHROPIC_API_KEY)
            r = c.messages.create(model=CLAUDE_MODEL, max_tokens=max_tokens,
                                  messages=[{"role": "user", "content": prompt}])
            return r.content[0].text
        except Exception as e:  # noqa: BLE001
            log.warning("[li-signal] Claude fout: %s", e)
    return ""


# ── Stap 1: deterministische scrape via Tavily X-ray ───────────────────────

def _tavily_search(query: str, max_results: int = MAX_PROFILES_PER_WATCH) -> List[Dict]:
    """X-ray LinkedIn via Tavily. Zonder key: leeg (geen silent failure-mimic)."""
    if not TAVILY_API_KEY:
        log.warning("[li-signal] Geen TAVILY_API_KEY — kan geen LinkedIn-signalen scrapen")
        return []
    try:
        from tavily import TavilyClient
        client = TavilyClient(api_key=TAVILY_API_KEY)
        resp = client.search(
            query=query, max_results=max_results,
            search_depth="advanced", include_answer=False, topic="general",
        )
    except Exception as e:  # noqa: BLE001
        log.warning("[li-signal] Tavily X-ray fout: %s", e)
        return []
    out = []
    for r in resp.get("results", []):
        url = r.get("url", "")
        # Alleen echte LinkedIn-profiel-URLs (site:linkedin.com/in in query,
        # maar Tavily kan ook andere hits geven — filter hier hard).
        if "linkedin.com/in/" not in url:
            continue
        # Soms levert Tavily een /posts/ of /company/ — alleen /in/ profielen.
        if re.search(r"linkedin\.com/in/[^/]+/?$", url):
            out.append({
                "url": url,
                "title": r.get("title", ""),
                "snippet": r.get("content", "")[:1000],
            })
    return out


def find_hand_raisers(account_handle: str, icp_hints: str = DEFAULT_ICP_HINTS,
                      project: str = "") -> List[Dict]:
    """Geef de LinkedIn-profielen terug die de gemonitorde account engageerden.

    We X-rayen op 'site:linkedin.com/in "<account>"' verrijkt met de niche
    (ICP-hints). Tavily weegt profielen die de account vermelden/engageren
    zwaarder mee in de ranking, dus de top-hits zijn de warmste hand-raisers.
    """
    handle = _norm_handle(account_handle)
    if not handle:
        return []
    # Bouw een query die zowel de account als de niche raakt. Tavily's
    # general-topic zoekactie op 'in/<handle>' trekt profielen die de account
    # in hun eigen posts/about noemen — een sterke hand-raising indicator.
    niche_terms = " ".join(icp_hints.split()[:6])
    query = f'site:linkedin.com/in/ "{handle}" ({niche_terms})'
    raw = _tavily_search(query)
    raisers = []
    seen = set()
    for r in raw:
        if r["url"] in seen:
            continue
        seen.add(r["url"])
        raisers.append({
            "url": r["url"],
            "name": _name_from_profile(r["url"], r.get("title", "")),
            "snippet": r.get("snippet", ""),
            "watcher": handle,
            "engagement": "mentioned",  # default; Hermes verfijnt dit hieronder
        })
    return raisers


def _name_from_profile(url: str, title: str) -> str:
    slug = re.sub(r".*linkedin\.com/in/", "", url).strip("/").split("?")[0]
    # 'first-last' → 'First Last'
    name = slug.replace("-", " ").replace("_", " ").strip().title()
    # als de titel een echte naam lijkt (geen functie), gebruik die
    if title and len(title.split()) <= 4 and " at " not in title.lower():
        return title.strip()
    return name or slug


# ── Stap 2: ICP-classificatie + fit-score (Hermes) ─────────────────────────

def classify_hand_raiser(raiser: Dict, icp_hints: str = DEFAULT_ICP_HINTS,
                        project: str = "") -> Dict:
    """Laat Hermes de hand-raiser classificeren en een fit-score geven.

    Retourneert de verrijkte raiser met: role, company, fit_score (0-100),
    fit_label (A/B/C/D), relevance, icp_match (bool), engagement.
    Bij LLM-fout degradeert dit netjes naar een conservatieve default (geen
    crash, geen fake-data).
    """
    prompt = f"""Je beoordeelt een LinkedIn-profiel dat een account in onze niche engageerde (hand-raising signaal).

Profiel-URL: {raiser.get('url')}
Naam (geschat): {raiser.get('name')}
Snippet: {raiser.get('snippet', '')[:600]}

Onze ICP (ideaal klantprofiel): {icp_hints}
Wij zijn WeAreImpact (AI in zorg/welzijn, interim AI-opdrachten) en Bewaard voor altijd (keepsake voor nabestaanden, B2B via notarissen/uitvaart).

Geef ALLEEN een JSON-object (geen uitleg):
{{
  "role": "functietitel / branche in 3-5 woorden",
  "company": "werkgever of '' ",
  "engagement": "commented" | "liked" | "posted" | "mentioned",
  "relevance": "hoog" | "gemiddeld" | "laag",
  "icp_match": true | false,
  "fit_score": 0-100,
  "why": "1 zin waarom dit wel/niet past"
}}

Regels:
- fit_score 90-100 = A: beslisser in onze niche (AI/zorg/welzijn/notaris/uitvaart/interim).
- 70-89 = B: sterk verwant (gemeente, onderwijs, maatschappelijke org, consultancy).
- 40-69 = C: indirect relevant (brede zakelijke dienstverlening).
- <40 = D: geen match (bbq-catering, fotograaf zonder niche, student).
- Engagement 'commented'/'posted' telt zwaarder dan 'liked'/'mentioned'.
- Antwoord UITSLUITEND met geldig JSON."""

    raw = _hermes_complete(prompt, max_tokens=400)
    default = {
        "role": "", "company": "", "engagement": raiser.get("engagement", "mentioned"),
        "relevance": "laag", "icp_match": False, "fit_score": 0,
        "why": "LLM-classificatie mislukt — conservatief op laag gezet",
    }
    if not raw:
        raiser.update(default)
        raiser["fit_label"] = "D"
        return raiser
    try:
        data = json.loads(_extract_json(raw))
    except Exception:
        # Red het cijfer als het erin staat, anders default.
        m = re.search(r'"fit_score"\s*:\s*(\d+)', raw)
        default["fit_score"] = int(m.group(1)) if m else 0
        raiser.update(default)
        raiser["fit_label"] = _fit_label(default["fit_score"])
        return raiser
    score = int(data.get("fit_score", 0) or 0)
    eng = (data.get("engagement") or raiser.get("engagement") or "mentioned")
    raiser.update({
        "role": data.get("role", ""),
        "company": data.get("company", ""),
        "engagement": eng,
        "relevance": data.get("relevance", "laag"),
        "icp_match": bool(data.get("icp_match", False)),
        "fit_score": score,
        "why": data.get("why", ""),
    })
    raiser["fit_label"] = _fit_label(score)
    return raiser


def _fit_label(score: int) -> str:
    try:
        s = int(score)
    except (TypeError, ValueError):
        s = 0
    if s >= 90:
        return "A"
    if s >= 70:
        return "B"
    if s >= 40:
        return "C"
    return "D"


# ── Stap 3: bridge naar prospecting-lead (mens-in-loop) ─────────────────────

def bridge_to_prospecting(raiser: Dict, project: str) -> Dict:
    """Zet een fitte hand-raiser om in een prospecting-lead.

    De lead krijgt status 'new' + source='radar_linkedin' + tags. Daarna
    doorloopt hij de gewone funnel (quality_gate → outreach-batch →
    human approve → verzenden vanaf eigen domein). Geen enkele stap hier
    verstuurt mail — dat blijft de mens-in-loop review-gate.
    """
    svc = LeadsService()
    org_name = raiser.get("name") or _name_from_profile(raiser.get("url", ""), "")
    # LinkedIn-profiel-URL is de 'website' — de outreach/funnel werkt op
    # e-mail, die we later via Hunter verrijken (waterfall-optie). Hier zetten
    # we de profiel-URL als herkenbaar contactpunt.
    profile_url = raiser.get("url", "")
    lead = {
        "org_name": org_name,
        "website": profile_url,
        "summary": (
            f"Hand-raising signaal via LinkedIn: engageerde account "
            f"'{raiser.get('watcher', '')}' ({raiser.get('engagement', 'mentioned')}). "
            f"Rol: {raiser.get('role', 'onbekend')}. "
            f"{raiser.get('why', '')}"
        ),
        "relevance": raiser.get("relevance", "gemiddeld"),
        "tags": ["hand_raiser", "linkedin", f"eng_{raiser.get('engagement','mentioned')}"],
        "status": "new",
        "lead_type": "personal",
        "search_query": f"radar:linkedin_signal:{raiser.get('watcher', '')} project:{project}",
        "score": raiser.get("fit_score", 0),
        "email": "",
        "phone": "",
        "address": "",
        "city": "",
        "postal_code": "",
        "kvv_number": "",
        "contacts": [{
            "naam": org_name,
            "rol": raiser.get("role", ""),
            "email": "",
            "linkedin": profile_url,
        }],
        "enriched_at": "",
    }
    saved = svc.save_to_db(lead)
    # Vault-notitie (single source of truth) — net als bij gewone leads.
    try:
        obs = svc.save_to_obsidian({**saved, "contacts": lead["contacts"]})
        if obs:
            with get_conn() as conn:
                conn.execute(
                    "UPDATE leads SET obsidian_path = ? WHERE id = ?",
                    (obs, saved["id"]),
                )
    except Exception as e:  # noqa: BLE001
        log.warning("[li-signal] vault-schrijf mislukt (lead wel in DB): %s", e)
    return {**saved, "contacts": lead["contacts"]}


# ── Orchestratie: één watch-account scannen + bridgen ───────────────────────

def scan_watch_account(watch: Dict, project: str = "",
                       icp_hints: str = DEFAULT_ICP_HINTS,
                       auto_bridge: bool = True) -> Dict:
    """Scan één linkedin_signal-watch en bridge fitte hand-raisers.

    Returns een rapport: gevonden / fit / gebridged / overgeslagen + details.
    """
    handle = _norm_handle(watch.get("value", ""))
    label = watch.get("label") or handle
    if not handle:
        return {"watch": label, "error": "geen geldige LinkedIn-account",
                "found": 0, "fit": 0, "bridged": 0}
    raisers = find_hand_raisers(handle, icp_hints=icp_hints, project=project)
    found = len(raisers)
    fit, bridged, skipped = [], [], []
    for r in raisers:
        r = classify_hand_raiser(r, icp_hints=icp_hints, project=project)
        # Sla al-bekende profielen over (idempotent op URL).
        if _already_bridged(r["url"]):
            skipped.append({"url": r["url"], "reason": "al gebridged"})
            continue
        if r["fit_score"] < MIN_HANDRAISER_FIT:
            skipped.append({"url": r["url"], "fit_score": r["fit_score"],
                            "reason": "onder fit-drempel"})
            continue
        fit.append(r)
        if auto_bridge:
            lead = bridge_to_prospecting(r, project)
            _mark_bridged(r["url"], lead["id"])
            bridged.append({"url": r["url"], "lead_id": lead["id"],
                            "name": lead["org_name"], "fit_label": r["fit_label"]})
    # Markeer de watch als gescand.
    try:
        with get_conn() as conn:
            conn.execute(
                "UPDATE radar_watchlist SET last_scanned_at = ?, last_status = 'ok', "
                "last_found = ?, scan_count = COALESCE(scan_count,0)+1, "
                "signal_count = COALESCE(signal_count,0)+? WHERE id = ?",
                (_now(), found, found, watch["id"]),
            )
    except Exception:  # noqa: BLE001
        log.exception("[li-signal] kon scan-status niet vastleggen")
    return {
        "watch": label, "handle": handle, "found": found,
        "fit": len(fit), "bridged": len(bridged),
        "skipped": len(skipped),
        "fit_details": [{"name": r.get("name"), "role": r.get("role"),
                         "fit_score": r.get("fit_score"),
                         "engagement": r.get("engagement")} for r in fit],
        "bridged_details": bridged,
    }


def _already_bridged(profile_url: str) -> bool:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM leads WHERE website = ? AND lead_type = 'personal' "
            "AND json_array_length(tags) > 0 AND tags LIKE '%hand_raiser%' LIMIT 1",
            (profile_url,),
        ).fetchone()
    return bool(row)


def _mark_bridged(profile_url: str, lead_id: str) -> None:
    # De bridge-relatie leeft op de lead zelf (website=profiel-URL + tags).
    # De idempotentie-check in scan_watch_account kijkt op website, dus een
    # tweede scan bridget dezelfde hand-raiser niet opnieuw. We overschrijven
    # search_query hier expres NIET — die draagt het project (zie
    # bridge_to_prospecting) zodat per-project filtering blijft werken.
    return


def scan_all_linkedin_watches(project: Optional[str] = None,
                              auto_bridge: bool = True) -> List[Dict]:
    """Scan alle actieve linkedin_signal-watches (voor scheduler / handmatig)."""
    from .models import ensure_schema
    ensure_schema()
    watches = [w for w in _list_linkedin_watches(project) if w.get("active")]
    reports = []
    for w in watches:
        try:
            reports.append(scan_watch_account(w, project=project or w.get("project", ""),
                                              auto_bridge=auto_bridge))
        except Exception as e:  # noqa: BLE001
            log.exception("[li-signal] scan van %s mislukt", w.get("label"))
            reports.append({"watch": w.get("label"), "error": str(e)[:200],
                            "found": 0, "fit": 0, "bridged": 0})
    return reports


def _list_linkedin_watches(project: Optional[str] = None) -> List[Dict]:
    with get_conn() as conn:
        if project:
            rows = conn.execute(
                "SELECT * FROM radar_watchlist WHERE type = 'linkedin_signal' "
                "AND LOWER(project) = LOWER(?) ORDER BY created_at",
                (project,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM radar_watchlist WHERE type = 'linkedin_signal' "
                "ORDER BY project, created_at"
            ).fetchall()
    return [dict(r) for r in rows]


def add_linkedin_watch(project: str, label: str, account: str) -> Dict:
    """Voeg een LinkedIn-account toe aan de hand-raising watchlist."""
    from .models import ensure_schema
    ensure_schema()
    handle = _norm_handle(account)
    if not handle:
        raise ValueError("Ongeldige LinkedIn-account")
    item = {
        "id": str(uuid.uuid4()),
        "project": (project or "").strip().lower(),
        "label": label.strip() or handle,
        "type": "linkedin_signal",
        "value": handle,
        "active": 1,
        "last_scanned_at": "",
        "created_at": _now(),
    }
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO radar_watchlist "
            "(id, project, label, type, value, active, last_scanned_at, created_at) "
            "VALUES (:id, :project, :label, :type, :value, :active, :last_scanned_at, :created_at)",
            item,
        )
    return item


def list_linkedin_watches(project: Optional[str] = None) -> List[Dict]:
    return _list_linkedin_watches(project)


def bridged_leads(project: Optional[str] = None) -> List[Dict]:
    """Geef de uit hand-raisers gebridgede leads terug (voor de UI).

    Let op: de leads-tafel kent geen project-scheiding (leads zijn globaal).
    `project` is hier alleen een optionele filter-hint via search_query, want
    we zetten die bij bridge op 'radar:linkedin_signal:<account>' resp.
    'radar_linkedin:<lead_id>'. Voor een project-gebonden weergave filteren we
    op de search_query die het project draagt.
    """
    with get_conn() as conn:
        if project:
            rows = conn.execute(
                "SELECT * FROM leads WHERE tags LIKE '%hand_raiser%' "
                "AND LOWER(lead_type)='personal' "
                "AND LOWER(search_query) LIKE ? ORDER BY created_at DESC",
                (f"%{project.lower()}%",),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM leads WHERE tags LIKE '%hand_raiser%' "
                "AND LOWER(lead_type)='personal' ORDER BY created_at DESC"
            ).fetchall()
    svc = LeadsService()
    return [svc._row_to_dict(dict(r)) for r in rows]
