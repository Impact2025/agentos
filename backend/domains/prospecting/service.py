"""
Lead Scraper Service — wereldklasse B2B lead-generatie met NAW-verrijking.

Pipeline per lead:
  1. Tavily web-zoekactie  →  url + snippet
  2. Website scraper       →  phone / email / adres / postcode / stad / KvK
  3. AI-analyse (Hermes)   →  summary / contacts / relevantie / tags
  4. OpenKVK.nl lookup     →  KvK + vestigingsadres (als nog leeg)
  5. SQLite + Obsidian

Batch-modus: meerdere queries, sectortemplate, regio-suffix, deduplicatie op domein.
"""
import uuid
import json
import re
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Dict, Optional
from urllib.parse import urlparse

import httpx

from ...shared.config import (
    ANTHROPIC_API_KEY, CLAUDE_MODEL,
    OPENROUTER_API_KEY, HERMES_MODEL, HERMES_FALLBACK_MODELS,
    HERMES_LOCAL_URL, HERMES_LOCAL_KEY,
    OLLAMA_BASE_URL, OLLAMA_MODEL,
    OBSIDIAN_VAULT_PATH,
    hermes_backend,
)
from ...shared.database import get_conn
from .scraper import ScraperService
from .hunter import HunterService
from . import kvk as kvk_service
from . import validate

log = logging.getLogger(__name__)

# ── Sectortemplate-queries (voor NL B2B prospecting) ─────────────────────────

BATCH_TEMPLATES: Dict[str, List[str]] = {
    "notarissen_nl": [
        "notariskantoor Amsterdam",
        "notariskantoor Rotterdam",
        "notariskantoor Den Haag",
        "notariskantoor Utrecht",
        "notariskantoor Eindhoven",
        "notariskantoor Tilburg",
        "notariskantoor Groningen",
        "notariskantoor Almere",
        "notariskantoor Breda",
        "notariskantoor Nijmegen",
        "notariskantoor Apeldoorn",
        "notariskantoor Haarlem",
        "notariskantoor Arnhem",
        "notariskantoor Enschede",
        "notariskantoor Zaandam",
    ],
    "uitvaart_nl": [
        "uitvaartondernemer Amsterdam",
        "begrafenisondernemer Rotterdam",
        "uitvaartondernemer Utrecht",
        "begrafenisondernemer Den Haag",
        "uitvaartzorg Eindhoven",
        "crematorium Noord-Holland",
        "begrafenisondernemer Gelderland",
        "uitvaartcentrum Overijssel",
        "begrafenisondernemer Groningen",
        "uitvaartondernemer Limburg",
        "uitvaartzorg Friesland",
        "begrafenisondernemer Zeeland",
        "uitvaartondernemer Brabant",
        "rouwbegeleiding Amsterdam",
        "uitvaartverzorger Haarlem",
    ],
    "zorg_nl": [
        "verzorgingshuis Amsterdam ouderen",
        "woonzorgcentrum Rotterdam",
        "seniorenwoningen Utrecht",
        "thuiszorg organisatie Noord-Holland",
        "ouderenzorg Gelderland",
        "verpleeghuis Noord-Brabant",
        "zorginstelling ouderen Den Haag",
        "seniorenzorg Overijssel",
        "ouderenwelzijn Groningen",
        "woonzorg Zeeland",
        "thuiszorg Friesland",
        "ouderenzorg Drenthe",
        "woonzorgcentrum Flevoland",
        "seniorenhuis Limburg",
        "thuiszorg ouderen Gelderland",
    ],
    "weareimpact_ai": [
        "AI consultancy zorginstelling",
        "digitale transformatie welzijnsorganisatie",
        "AI strategie zorg Nederland",
        "AI implementatie welzijn",
        "interim AI projectleider zorg",
        "AI adviseur ouderenzorg",
        "warme zorg door slimme tech",
        "AI coach zorgmedewerkers",
        "generatieve AI zorgsector",
        "AI oplossingen maatschappelijke organisaties",
        "LEGO Serious Play zorg",
        "AI optimalisatie zorgprocessen",
        "digitale zorg innovatie",
        "AI ondersteuning zorgprofessionals",
        "chatbot zorg organisatie",
    ],
    "weareimpact_opdrachten": [
        "AI interim opdracht zorg",
        "AI projectleider welzijn vacature",
        "AI consultant gezocht zorginstelling",
        "interim AI manager zorg vacature",
        "AI implementatie opdracht welzijnsorganisatie",
        "digitale transformatie projectleider zorg",
        "AI innovatie opdracht gemeente",
        "AI strategie opdracht zorgverzekeraar",
        "freelance AI expert zorg gezocht",
        "AI traject zorginstelling opdracht",
        "AI coach welzijn opdracht",
        "AI optimalisatie opdracht zorgproces",
        "generatieve AI project zorg",
        "AI change manager zorg opdracht",
        "AI opdracht ouderenzorg technologie",
    ],
}

TEMPLATE_LEAD_TYPE: Dict[str, str] = {
    "notarissen_nl": "notarissen",
    "uitvaart_nl": "uitvaart",
    "zorg_nl": "zorg",
    "weareimpact_ai": "ai-consultancy",
    "weareimpact_opdrachten": "ai-opdracht",
    "custom": "overig",
}

_NOISE_DOMAINS = [
    "marktplaats.nl", "funda.nl", "bol.com", "amazon.nl", "amazon.com",
    "facebook.com", "instagram.com", "twitter.com",
    "wikipedia.org", "youtube.com", "reddit.com", "thuisbezorgd.nl",
    "independer.nl", "zorgwijzer.nl", "zorgkaart.nl", "zoekbedrijven.nl",
    "kvk.nl", "kvknummer.nl",
]


# ── Helpers ───────────────────────────────────────────────────────────────────

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _slug(text: str) -> str:
    return re.sub(r"[^\w\-]", "-", text.lower().strip())[:60]


def _strip_json(raw: str) -> str:
    raw = raw.strip()
    if raw.startswith("```"):
        parts = raw.split("```")
        raw = parts[1] if len(parts) > 1 else raw
        if raw.startswith("json"):
            raw = raw[4:]
    return raw.strip()


_LEAD_SCORE_RE = re.compile(r'"score"\s*:\s*(-?\d+)')


def _salvage_score(raw: str) -> Optional[int]:
    """Red het fitnessscore-cijfer uit JSON die niet volledig parseert.

    Zelfde storing als `vacancies/service.py:_salvage_fit` (9 aug 2026): een
    afgekapte respons (max_tokens=900) mist alleen de sluithaak, niet de
    inhoud — "score": 8 staat er gewoon. Zonder redding viel dat cijfer weg en
    kwam er via `.get('score', 50)` een neutrale 50 voor terug, wat een
    duidelijke afwijzing (8) in een middelmatige-lijkende lead veranderde."""
    m = _LEAD_SCORE_RE.search(raw)
    if not m:
        return None
    try:
        return int(m.group(1))
    except ValueError:
        return None


def _domain(url: str) -> str:
    try:
        d = urlparse(url).netloc.lower()
        return d[4:] if d.startswith("www.") else d
    except Exception:
        return ""


# ── Service ───────────────────────────────────────────────────────────────────

class LeadsService:
    def __init__(self):
        self.vault_path = Path(OBSIDIAN_VAULT_PATH) if OBSIDIAN_VAULT_PATH else None
        self._scraper = ScraperService()
        self._hunter = HunterService()

        # Claude als fallback als Hermes niet werkt
        self._claude = None
        if ANTHROPIC_API_KEY:
            try:
                import anthropic
                self._claude = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
            except Exception:
                pass


    # ── Zoeken ───────────────────────────────────────────────────────────────

    def search_web(self, query: str, max_results: int = 6, include_linkedin: bool = False,
                   raise_errors: bool = False) -> List[Dict]:
        """Web search via de gedeelde zoeklaag (Tavily → Brave-fallback).
        Optioneel: LinkedIn niet uitsluiten. `raise_errors=True` laat een
        provider-fout (bv. quota op alle providers) doorborrelen — de UI-flows
        houden het oude stille []-gedrag, agent-flows (Iris) willen luid falen."""
        from ...shared import websearch
        exclude = [] if include_linkedin else _NOISE_DOMAINS
        try:
            return websearch.search(query, max_results=max_results,
                                    exclude_domains=exclude)
        except Exception as e:
            log.error("[leads] Zoekfout voor '%s': %s", query, e)
            if raise_errors:
                raise
            return []

    def search_linkedin_people(self, query: str, max_results: int = 6) -> List[Dict]:
        """Zoek LinkedIn-profielen van beslissers in een sector/organisatie."""
        linkedin_query = f"site:linkedin.com/in {query}"
        return self.search_web(linkedin_query, max_results, include_linkedin=True)

    def run_search_batch(self, queries: List[str], lead_type: str = "overig",
                         max_per_query: int = 4) -> Dict:
        """Programmatische (niet-SSE) batch-zoekactie — dezelfde keten als de
        Leads-tab (zoeken → dedupe → scrapen → AI-analyse → Obsidian + DB),
        maar aanroepbaar door agents zoals Iris' lead_search_run. Een
        provider-fout op álle queries gooit door, zodat de aanroeper een
        foutkaart kan loggen i.p.v. stil met 0 leads te eindigen."""
        found = saved = failed_queries = skipped = 0
        last_error = ""
        for query in queries:
            try:
                results = self.search_web(query, max_per_query, raise_errors=True)
            except Exception as e:  # noqa: BLE001
                failed_queries += 1
                last_error = str(e)[:200]
                continue
            results = [r for r in results if not self.is_duplicate(r["url"])]
            found += len(results)
            for r in results:
                # Vóór het scrapen en de LLM-analyse: is dit überhaupt een
                # organisatie? Op 27 jul 2026 was 60% van de leadvoorraad een
                # paginatitel van een artikel of vacature ('Top AI Consulting
                # Companies in the Netherlands'). Elke zo'n rij kostte een
                # scrape én een LLM-call, en verpestte daarna de conversiecijfers
                # van de acquisitieformule. Hier weggooien is het goedkoopst.
                geschikt, reden = validate.looks_like_organisation(
                    r.get("title", ""), r.get("url", ""), r.get("snippet", ""))
                if not geschikt:
                    log.info("[leads] Overgeslagen (%s): %s", reden,
                             (r.get("title") or "")[:70])
                    skipped += 1
                    continue
                try:
                    org_name = validate.clean_org_name(r["title"], r["url"])
                    scraped = self.scrape_and_enrich(r["url"], org_name)
                    analysis = self.analyze_lead(org_name, r["url"], r["snippet"], scraped)
                    lead = {
                        "org_name":    org_name,
                        "website":     r["url"],
                        "summary":     analysis.get("summary", ""),
                        "contacts":    analysis.get("contacts", []),
                        "relevance":   analysis.get("relevance", "gemiddeld"),
                        "tags":        analysis.get("tags", []),
                        "status":      "new",
                        "search_query": query,
                        "lead_type":   lead_type,
                        "phone":       scraped.get("phone") or analysis.get("phone", ""),
                        "email":       scraped.get("email") or analysis.get("email", ""),
                        "address":     scraped.get("address") or scraped.get("address_raw", "")
                                       or analysis.get("address", ""),
                        "city":        scraped.get("city") or analysis.get("city", ""),
                        "postal_code": scraped.get("postal_code") or analysis.get("postal_code", ""),
                        "kvk_number":  scraped.get("kvk_number") or analysis.get("kvk_number", ""),
                        "enriched_at": "",
                        "score":       analysis.get("score", 50),
                    }
                    if lead["phone"] or lead["address"] or lead["email"]:
                        lead["enriched_at"] = _now()
                        lead["status"] = "enriched"
                    lead["obsidian_path"] = self.save_to_obsidian(lead) or ""
                    self.save_to_db(lead)
                    saved += 1
                except Exception as e:  # noqa: BLE001
                    log.warning("[leads] Lead verwerken mislukt (%s): %s", r.get("url"), e)
        if failed_queries == len(queries) and queries:
            raise RuntimeError(
                f"Alle {len(queries)} zoekopdrachten faalden — zoekprovider plat "
                f"(laatste fout: {last_error})")
        return {"queries": len(queries), "failed_queries": failed_queries,
                "found": found, "saved": saved, "skipped": skipped}

    # ── Scraping & verrijking ─────────────────────────────────────────────────

    def scrape_and_enrich(self, url: str, org_name: str = "") -> Dict:
        """Scrape een URL en verrijk met KvK-lookup. Retourneert NAW-dict."""
        scraped = self._scraper.scrape(url)

        # KvK-lookup als KvK ontbreekt na scraping
        if not scraped["kvk_number"] and (org_name or scraped.get("city")):
            kvk_data = kvk_service.lookup_by_name(org_name, scraped.get("city", ""))
            if kvk_data:
                for field in ("kvk_number", "address", "postal_code", "city"):
                    if not scraped.get(field):
                        scraped[field] = kvk_data.get(field, "")

        return scraped

    # ── AI-analyse ────────────────────────────────────────────────────────────

    def analyze_lead(
        self,
        org_name: str,
        url: str,
        snippet: str,
        scraped: Optional[Dict] = None,
    ) -> Dict:
        """
        AI-analyse van een lead op basis van snippet + gescrapede pagina-inhoud.
        Routeert via Hermes (OpenRouter/lokaal); valt terug op Claude.
        """
        page_text = (scraped or {}).get("page_text", "") if scraped else ""
        context = page_text[:3000] if page_text else snippet[:600]

        prompt = f"""Je analyseert een zakelijk contactadres voor B2B-outreach (NL).

Bedrijfsnaam: {org_name}
Website: {url}
Paginainhoud (verkort):
{context}

Geef een JSON-object met exact deze structuur:
{{
  "summary": "2-3 zinnen: wat doet dit bedrijf en waarom interessant als lead?",
  "contacts": [
    {{"naam": "Voornaam Achternaam", "rol": "Functietitel", "email": "email@domein.nl"}}
  ],
  "phone": "hoofdtelefoon bijv. 020-1234567 (leeg als niet gevonden)",
  "email": "hoofdemail bijv. info@bedrijf.nl (leeg als niet gevonden)",
  "address": "straatnaam + huisnr (leeg als niet gevonden)",
  "postal_code": "1234 AB (leeg als niet gevonden)",
  "city": "Plaatsnaam (leeg als niet gevonden)",
  "kvk_number": "8-cijferig KvK-nummer (leeg als niet gevonden)",
  "relevance": "hoog",
  "score": 65,
  "tags": ["notaris", "testament"]
}}

Regels:
- Gebruik ALLEEN informatie die daadwerkelijk in de paginainhoud staat
- contacts-array mag leeg zijn
- relevance: hoog / gemiddeld / laag
- score: 0-100 fitnessscore; 100 = perfecte B2B-match (professioneel, beslisser bereikbaar, groot potentieel), 0 = helemaal niet interessant
- Maximaal 3 tags
- Antwoord UITSLUITEND met geldig JSON, geen extra tekst"""

        raw = self._hermes_complete(prompt, max_tokens=900)
        if not raw:
            return {
                "summary": snippet[:300], "contacts": [], "phone": "",
                "email": "", "address": "", "postal_code": "", "city": "",
                "kvk_number": "", "relevance": "onbeoordeeld", "score": None, "tags": [],
            }
        try:
            return json.loads(_strip_json(raw))
        except Exception:
            # Zelfde storing als vacancies/service.py: de JSON is vaak alleen
            # afgekapt, niet inhoudelijk stuk. Red het cijfer vóórdat we het
            # opgeven — anders wordt een lage score (bijv. 8, duidelijk geen
            # fit) via de 50-fallback een schijnbaar middelmatige lead.
            return {
                "summary": raw[:300], "contacts": [], "phone": "",
                "email": "", "address": "", "postal_code": "", "city": "",
                "kvk_number": "", "relevance": "onbeoordeeld",
                "score": _salvage_score(raw), "tags": [],
            }

    def _hermes_complete(self, prompt: str, max_tokens: int = 900) -> str:
        """Synchrone LLM-call via geconfigureerde Hermes-backend."""
        backend = hermes_backend()

        if backend == "openrouter":
            return self._openrouter_complete(prompt, max_tokens)
        if backend in ("local", "ollama"):
            return self._openai_compat_complete(prompt, max_tokens, backend)

        # Anthropic-fallback
        if self._claude:
            try:
                resp = self._claude.messages.create(
                    model=CLAUDE_MODEL,
                    max_tokens=max_tokens,
                    messages=[{"role": "user", "content": prompt}],
                )
                return resp.content[0].text
            except Exception as e:
                log.warning("[leads] Claude fout: %s", e)
        return ""

    def _openrouter_complete(self, prompt: str, max_tokens: int) -> str:
        models = [HERMES_MODEL] + list(HERMES_FALLBACK_MODELS)
        for model in models:
            try:
                with httpx.Client(timeout=45.0) as client:
                    r = client.post(
                        "https://openrouter.ai/api/v1/chat/completions",
                        headers={
                            "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                            "Content-Type": "application/json",
                            "HTTP-Referer": "http://localhost:1250",
                            "X-Title": "Agent OS Leads",
                        },
                        json={
                            "model": model,
                            "messages": [{"role": "user", "content": prompt}],
                            "max_tokens": max_tokens,
                            "stream": False,
                        },
                    )
                    if r.status_code == 429:
                        continue
                    r.raise_for_status()
                    return r.json()["choices"][0]["message"]["content"]
            except Exception as e:
                log.debug("[leads] OpenRouter %s fout: %s", model, e)
        return ""

    def _openai_compat_complete(self, prompt: str, max_tokens: int, backend: str) -> str:
        if backend == "local":
            base_url = HERMES_LOCAL_URL.rstrip("/")
            model = "hermes"
            headers = {
                # Keyless Ollama/LM Studio-tier: lege key → `Bearer ` crasht h11.
                "Authorization": f"Bearer {HERMES_LOCAL_KEY or 'ollama'}",
                "Content-Type": "application/json",
            }
        else:
            base_url = OLLAMA_BASE_URL.rstrip("/")
            model = OLLAMA_MODEL
            headers = {"Content-Type": "application/json"}
        try:
            with httpx.Client(timeout=60.0) as client:
                r = client.post(
                    f"{base_url}/chat/completions",
                    headers=headers,
                    json={
                        "model": model,
                        "messages": [{"role": "user", "content": prompt}],
                        "max_tokens": max_tokens,
                        "stream": False,
                    },
                )
                r.raise_for_status()
                return r.json()["choices"][0]["message"]["content"]
        except Exception as e:
            log.debug("[leads] %s complete fout: %s", backend, e)
            return ""

    # ── Opslaan ───────────────────────────────────────────────────────────────

    def save_to_db(self, lead: Dict) -> Dict:
        lead_id = str(uuid.uuid4())
        now = _now()
        tags = lead.get("tags") or []
        contacts = lead.get("contacts") or []
        with get_conn() as conn:
            conn.execute(
                """INSERT INTO leads
                   (id, org_name, website, contacts, summary, relevance, status,
                    search_query, obsidian_path,
                    phone, email, address, city, postal_code, kvk_number,
                    lead_type, enriched_at, score, tags,
                    hunter_verified, email_status,
                    created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    lead_id,
                    lead["org_name"],
                    lead.get("website", ""),
                    json.dumps(contacts, ensure_ascii=False),
                    lead.get("summary", ""),
                    lead.get("relevance", "gemiddeld"),
                    lead.get("status", "new"),
                    lead.get("search_query", ""),
                    lead.get("obsidian_path", ""),
                    lead.get("phone", ""),
                    lead.get("email", ""),
                    lead.get("address", ""),
                    lead.get("city", ""),
                    lead.get("postal_code", ""),
                    lead.get("kvk_number", ""),
                    lead.get("lead_type", "overig"),
                    lead.get("enriched_at", ""),
                    lead.get("score", 50),
                    json.dumps(tags, ensure_ascii=False),
                    int(lead.get("hunter_verified", 0)),
                    lead.get("email_status", ""),
                    now, now,
                ),
            )
        return {**lead, "id": lead_id, "contacts": contacts, "tags": tags,
                "created_at": now, "updated_at": now}

    def enrich_lead(self, lead_id: str) -> Optional[Dict]:
        """
        Scrape website opnieuw + AI-analyse + automatische Hunter-verrijking.
        Status-transitie: any → enriched → valid (zodra deliverable e-mail gevonden).
        """
        with get_conn() as conn:
            row = conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
        if not row:
            return None
        lead = dict(row)

        scraped = self.scrape_and_enrich(lead["website"], lead["org_name"])
        snippet = lead.get("summary", "")
        analysis = self.analyze_lead(lead["org_name"], lead["website"], snippet, scraped)

        # Merge: scraped data heeft voorrang voor directe extracties, AI voor contextfields
        updates = {
            "phone":       scraped.get("phone") or analysis.get("phone", ""),
            "email":       scraped.get("email") or analysis.get("email", ""),
            "address":     scraped.get("address") or scraped.get("address_raw", "") or analysis.get("address", ""),
            "city":        scraped.get("city") or analysis.get("city", ""),
            "postal_code": scraped.get("postal_code") or analysis.get("postal_code", ""),
            "kvk_number":  scraped.get("kvk_number") or analysis.get("kvk_number", ""),
            "summary":     analysis.get("summary") or lead.get("summary", ""),
            # `or` zou een echte score van 0 (terecht "geen fit") ook laten
            # doorvallen naar de oude score — expliciet op None toetsen houdt
            # een 0 een 0. Levert een nieuwe analyse niets op, dan blijft de
            # vorige score staan (die kan zelf ook al None zijn — dat is
            # eerlijker dan een verzonnen 50).
            "score":       analysis.get("score") if analysis.get("score") is not None
                           else lead.get("score"),
            "enriched_at": _now(),
            "status":      "enriched",
        }

        # Contacts samenvoegen (AI)
        new_contacts = analysis.get("contacts") or []
        merged_contacts = new_contacts if new_contacts else json.loads(lead.get("contacts") or "[]")

        # Hunter-verrijking: zoek contacten als geen e-mail gevonden via scraping
        has_email = updates["email"] or any(c.get("email") for c in merged_contacts)
        if not has_email and self._hunter.is_configured() and lead.get("website"):
            hunter_contacts = self._hunter.domain_search(lead["website"])
            if hunter_contacts:
                verified = self._hunter.verify_contacts(hunter_contacts)
                # Voeg Hunter-contacten samen met bestaande (dedupliceer op e-mail)
                existing_emails = {c.get("email", "").lower() for c in merged_contacts}
                for hc in verified:
                    if hc.get("email", "").lower() not in existing_emails:
                        merged_contacts.append(hc)
                # Kies het eerste deliverable e-mail als hoofdemail van de lead
                deliverable = self._hunter.first_deliverable(verified)
                if deliverable:
                    updates["email"] = deliverable["email"]
                    updates["email_status"] = "deliverable"
                    updates["status"] = "valid"
                updates["hunter_verified"] = 1

        updates["contacts"] = json.dumps(merged_contacts, ensure_ascii=False)

        # Tags samenvoegen
        old_tags = json.loads(lead.get("tags") or "[]")
        new_tags = list(set(old_tags + (analysis.get("tags") or [])))
        updates["tags"] = json.dumps(new_tags, ensure_ascii=False)
        updates["updated_at"] = _now()

        set_clause = ", ".join(f"{k} = ?" for k in updates)
        vals = list(updates.values()) + [lead_id]
        with get_conn() as conn:
            conn.execute(f"UPDATE leads SET {set_clause} WHERE id = ?", vals)
            row = conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()

        return self._row_to_dict(dict(row))

    def hunter_enrich(self, lead_id: str) -> Optional[Dict]:
        """
        Expliciete Hunter.io-verrijking: zoek contacten voor het domein,
        verifieer e-mails en zet status op valid als deliverable gevonden.
        """
        if not self._hunter.is_configured():
            return None
        with get_conn() as conn:
            row = conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
        if not row:
            return None
        lead = self._row_to_dict(dict(row))

        contacts = self._hunter.domain_search(lead.get("website", ""))
        if not contacts:
            # Markeer wel dat Hunter gedraaid is (ook bij 0 resultaten)
            with get_conn() as conn:
                conn.execute(
                    "UPDATE leads SET hunter_verified=1, updated_at=? WHERE id=?",
                    (_now(), lead_id),
                )
                row = conn.execute("SELECT * FROM leads WHERE id=?", (lead_id,)).fetchone()
            return self._row_to_dict(dict(row))

        verified = self._hunter.verify_contacts(contacts)

        # Samenvoegen met bestaande contacten (dedupliceer op e-mail)
        existing = lead.get("contacts") or []
        existing_emails = {c.get("email", "").lower() for c in existing}
        for hc in verified:
            if hc.get("email", "").lower() not in existing_emails:
                existing.append(hc)

        updates: Dict = {
            "contacts":        json.dumps(existing, ensure_ascii=False),
            "hunter_verified": 1,
            "updated_at":      _now(),
        }

        deliverable = self._hunter.first_deliverable(verified)
        if deliverable:
            updates["email_status"] = "deliverable"
            if not lead.get("email"):
                updates["email"] = deliverable["email"]
            # Bevorder naar 'valid' als nog niet verder in de funnel
            if lead.get("status") in ("new", "enriched", ""):
                updates["status"] = "valid"

        set_clause = ", ".join(f"{k} = ?" for k in updates)
        vals = list(updates.values()) + [lead_id]
        with get_conn() as conn:
            conn.execute(f"UPDATE leads SET {set_clause} WHERE id = ?", vals)
            row = conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()

        return self._row_to_dict(dict(row))

    def save_to_obsidian(self, lead: Dict) -> Optional[str]:
        if not self.vault_path or not self.vault_path.exists():
            return None

        leads_dir = self.vault_path / "Leads"
        leads_dir.mkdir(exist_ok=True)
        slug = _slug(lead["org_name"])
        file_path = leads_dir / f"{slug}.md"

        naw_lines = ""
        if lead.get("address"):
            naw_lines += f"- **Adres:** {lead['address']}"
            if lead.get("postal_code") or lead.get("city"):
                naw_lines += f", {lead.get('postal_code', '')} {lead.get('city', '')}".rstrip()
            naw_lines += "\n"
        if lead.get("phone"):
            naw_lines += f"- **Telefoon:** [{lead['phone']}](tel:{lead['phone']})\n"
        if lead.get("email"):
            naw_lines += f"- **E-mail:** [{lead['email']}](mailto:{lead['email']})\n"
        if lead.get("kvk_number"):
            naw_lines += f"- **KvK:** {lead['kvk_number']}\n"

        contacts_lines = ""
        for c in (lead.get("contacts") or []):
            line = f"- **{c.get('naam', '?')}**"
            if c.get("rol"):
                line += f" — {c['rol']}"
            if c.get("email"):
                line += f" · [{c['email']}](mailto:{c['email']})"
            contacts_lines += line + "\n"

        tags = (lead.get("tags") or [])
        tags_yaml = ", ".join(f'"{t}"' for t in tags + ["lead", lead.get("lead_type", "overig")])

        md = f"""---
name: {lead['org_name']}
website: {lead.get('website', '')}
status: {lead.get('status', 'prospect')}
relevance: {lead.get('relevance', 'gemiddeld')}
lead_type: {lead.get('lead_type', 'overig')}
tags: [{tags_yaml}]
created: {datetime.now().strftime('%Y-%m-%d')}
---

## {lead['org_name']}

{f"🔗 [{lead.get('website', '')}]({lead.get('website', '')})" if lead.get('website') else ""}

### NAW
{naw_lines or '_Geen NAW-gegevens beschikbaar_'}

### Samenvatting
{lead.get('summary', '_Geen samenvatting_')}

### Contactpersonen
{contacts_lines or '_Geen contactpersonen_'}

### Pipeline
Status: **{lead.get('status', 'prospect').title()}**

---
*Gevonden via: "{lead.get('search_query', '')}"*
"""
        file_path.write_text(md, encoding="utf-8")
        return str(file_path.relative_to(self.vault_path))

    # ── CRUD ─────────────────────────────────────────────────────────────────

    def list_leads(
        self,
        status: Optional[str] = None,
        lead_type: Optional[str] = None,
    ) -> List[Dict]:
        where, params = [], []
        if status:
            where.append("status = ?")
            params.append(status)
        if lead_type:
            where.append("lead_type = ?")
            params.append(lead_type)
        clause = ("WHERE " + " AND ".join(where)) if where else ""
        with get_conn() as conn:
            rows = conn.execute(
                f"SELECT * FROM leads {clause} ORDER BY created_at DESC", params
            ).fetchall()
        return [self._row_to_dict(dict(r)) for r in rows]

    def update_status(self, lead_id: str, status: str) -> Optional[Dict]:
        now = _now()
        with get_conn() as conn:
            cur = conn.execute(
                "UPDATE leads SET status = ?, updated_at = ? WHERE id = ?",
                (status, now, lead_id),
            )
            if cur.rowcount == 0:
                return None
            row = conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
        return self._row_to_dict(dict(row))

    def get_lead(self, lead_id: str) -> Optional[Dict]:
        with get_conn() as conn:
            row = conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
        return self._row_to_dict(dict(row)) if row else None

    def delete_lead(self, lead_id: str) -> bool:
        with get_conn() as conn:
            cur = conn.execute("DELETE FROM leads WHERE id = ?", (lead_id,))
        return cur.rowcount > 0

    def send_outreach_email(self, lead_id: str, custom_message: str = "") -> Dict:
        """Genereer een persoonlijke outreach-mail via Hermes en verstuur via Microsoft Graph.

        Gebruikt de Outlook/Graph-integratie i.p.v. SMTP — werkt met
        Security Defaults / Conditional Access (geen app-wachtwoord nodig).
        """
        import asyncio
        from ...domains.outlook import service as outlook

        lead = self.get_lead(lead_id)
        if not lead:
            return {"status": "error", "detail": "Lead niet gevonden"}

        if not outlook.is_authenticated():
            return {
                "status": "error",
                "detail": "Outlook/Graph niet geauthenticeerd. Ga naar Instellingen -> Outlook en log in met je Microsoft-account (v.munster@weareimpact.nl).",
            }

        # Bepaal het doel-emailadres
        target_email = lead.get("email", "")
        contacts = lead.get("contacts") or []
        if not target_email and contacts:
            target_email = contacts[0].get("email", "")
        if not target_email:
            return {"status": "error", "detail": "Geen e-mailadres bekend voor deze lead. Verrijk eerst de lead (Hunter.io of scrape)."}

        org_name = lead.get("org_name", "de organisatie")
        city = lead.get("city", "")
        summary = lead.get("summary", "")[:500]

        # Hermes genereert de outreach-tekst
        system_prompt = (
            "Je genereert een korte, persoonlijke B2B-outreachmail in het Nederlands. "
            "Toon: direct, oprecht, geen jargon. Geen bijlagen, geen lange aanhef. "
            "Maximaal 150 woorden. Geef ALLEEN de e-mail body (geen onderwerpregel)."
        )
        user_prompt = (
            f"Schrijf een e-mail van Vincent van Munster (WeAreImpact) aan {org_name}"
            f"{' in ' + city if city else ''}.\n\n"
            f"Context over de organisatie:\n{summary}\n\n"
            f"Kernboodschap:\n"
            f"- Ik ben de virtuele collega van Vincent en help hem met het vinden van "
            f"AI-opdrachten in zorg en welzijn.\n"
            f"- Vincent is beschikbaar per juli 2026 voor interim AI-opdrachten, "
            f"implementatietrajecten en AI-consultancy.\n"
            f"- WeAreImpact helpt organisaties met warme zorg door slimme tech: "
            f"van AI-assistenten tot autonome workflows.\n\n"
            f"{custom_message}\n\n"
            f"Sluit af met een vriendelijke, laagdrempelige call-to-action "
            f"(bijv. 'Benieuwd of we iets voor elkaar kunnen betekenen?').\n"
            f"Onderteken met:\n"
            f"Vincent van Munster\nWeAreImpact\nv.munster@weareimpact.nl\n0614470977"
        )

        email_body = self._hermes_complete(
            f"{system_prompt}\n\n{user_prompt}", max_tokens=600
        )
        if not email_body:
            email_body = (
                f"Hoi,\n\nIk ben de virtuele collega van Vincent van WeAreImpact. "
                f"Vincent is per juli 2026 beschikbaar voor AI-opdrachten in zorg en welzijn. "
                f"Zouden we eens kunnen sparren of er mogelijkheden zijn?\n\n"
                f"Groet,\nVincent van Munster\nWeAreImpact\nv.munster@weareimpact.nl\n0614470977"
            )

        subject = f"AI-ondersteuning voor {org_name} — Vincent beschikbaar per juli"

        # Wrap plain text in simpele HTML voor Graph API
        html_body = email_body.replace("\n", "<br>")

        try:
            result = asyncio.run(outlook.send_new_email(
                to=target_email,
                subject=subject,
                body_html=html_body,
            ))
            if result.get("success"):
                # Via de funnel zodat contacted_at gestempeld wordt — anders
                # telt deze verstuurde mail niet mee in de conversieformule.
                from . import funnel
                funnel.advance_lead(lead_id, "contacted")
                return {
                    "status": "sent",
                    "to": target_email,
                    "subject": subject,
                    "body": email_body,
                }
            else:
                return {
                    "status": "error",
                    "detail": f"Graph API fout: {result}",
                    "body": email_body,
                }
        except RuntimeError as e:
            return {
                "status": "error",
                "detail": f"Authenticatiefout: {e}. Log opnieuw in via Instellingen -> Outlook.",
                "body": email_body,
            }
        except Exception as e:
            return {
                "status": "error",
                "detail": f"Versturen mislukt: {e}",
                "body": email_body,
            }

    def get_stats(self) -> Dict:
        with get_conn() as conn:
            total = conn.execute("SELECT COUNT(*) FROM leads").fetchone()[0]
            by_status = dict(conn.execute(
                "SELECT status, COUNT(*) FROM leads GROUP BY status"
            ).fetchall())
            by_type = dict(conn.execute(
                "SELECT lead_type, COUNT(*) FROM leads GROUP BY lead_type"
            ).fetchall())
            enriched = conn.execute(
                "SELECT COUNT(*) FROM leads WHERE enriched_at != ''"
            ).fetchone()[0]
            with_phone = conn.execute(
                "SELECT COUNT(*) FROM leads WHERE phone != ''"
            ).fetchone()[0]
            with_email = conn.execute(
                "SELECT COUNT(*) FROM leads WHERE email != '' OR contacts != '[]'"
            ).fetchone()[0]
        return {
            "total": total,
            "enriched": enriched,
            "with_phone": with_phone,
            "with_email": with_email,
            "by_status": by_status,
            "by_type": by_type,
        }

    def is_duplicate(self, url: str) -> bool:
        """Controleer of een lead met hetzelfde domein al bestaat."""
        dom = _domain(url)
        if not dom:
            return False
        with get_conn() as conn:
            row = conn.execute(
                "SELECT id FROM leads WHERE website LIKE ?", (f"%{dom}%",)
            ).fetchone()
        return row is not None

    # ── Intern ────────────────────────────────────────────────────────────────

    @staticmethod
    def _row_to_dict(d: Dict) -> Dict:
        for field in ("contacts", "tags"):
            try:
                d[field] = json.loads(d.get(field) or "[]")
            except Exception:
                d[field] = []
        d.setdefault("hunter_verified", 0)
        d.setdefault("email_status", "")
        return d
