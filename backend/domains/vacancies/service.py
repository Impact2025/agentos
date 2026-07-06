"""
Opdrachten-zoekagent — vindt interim-/zzp-vacatures voor Vincent van Munster via
LinkedIn Jobs, Freelance.nl, Indeed, BMC.nl en een brede webzoekactie (vangt o.a.
Nationale Vacaturebank, Yacht, Solid, Interim Netwerk).

Pipeline per vacature:
  1. Tavily web-zoekactie (site: gefilterd per bron)  → url + titel + snippet
  2. Paginatekst-scrape (best effort, faalt stil)      → volledigere omschrijving
  3. AI fit-analyse via het "Vacature Fit-Analist"-expertprofiel (agent_runner)
  4. SQLite (dedupliceren op exacte URL)
"""
import json
import re
import uuid
import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from ...shared.config import TAVILY_API_KEY, hermes_backend
from ...shared.database import get_conn
from ...shared import agent_runner as agent_service
from . import scraper

log = logging.getLogger(__name__)

# ── Vaste zoekcriteria (aanpasbaar via de request-body in de router) ──────────

DEFAULT_ROLES: List[str] = [
    "Interim Projectleider Sociaal Domein",
    "AI Consultant",
    "Directeur Welzijn",
    "Kwartiermaker AI Innovatie",
    "Verandermanager Digitale Transformatie",
    "Interim Manager Welzijn Zorg",
]

# site: gefilterde bronnen; "overig" = brede zoekactie zonder site-restrictie.
SOURCES: Dict[str, Optional[str]] = {
    "linkedin": "site:linkedin.com/jobs",
    "freelance_nl": "site:freelance.nl",
    "indeed": "site:indeed.nl",
    "bmc": "site:bmc.nl",
    "overig": None,
}

FIT_PROFILE_NAME = "Vacature Fit-Analist"
MAX_AGE_DAYS = 21  # max. 3 weken oud — oudere/verlopen vacatures worden niet opgeslagen.


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _strip_json(raw: str) -> str:
    raw = raw.strip()
    if raw.startswith("```"):
        parts = raw.split("```")
        raw = parts[1] if len(parts) > 1 else raw
        if raw.startswith("json"):
            raw = raw[4:]
    return raw.strip()


def _resolve_model_override(profile_model: Optional[str]) -> Optional[str]:
    """Profielmodel → waarde die de actieve backend snapt (alleen openrouter krijgt override)."""
    if not profile_model:
        return None
    m = profile_model.strip()
    if hermes_backend() == "openrouter":
        return m[len("openrouter/"):] if m.startswith("openrouter/") else m
    return None


class VacancyService:
    def __init__(self):
        self._tavily = None
        if TAVILY_API_KEY:
            try:
                from tavily import TavilyClient
                self._tavily = TavilyClient(api_key=TAVILY_API_KEY)
            except Exception:
                pass

    # ── Zoeken ───────────────────────────────────────────────────────────────

    def search_web(self, role: str, site_filter: Optional[str], max_results: int = 4) -> List[Dict]:
        """Tavily-zoekactie voor één rol × bron. Geen noise-domain-exclusie (die zou
        juist linkedin/freelance uitsluiten, wat hier het doel is).

        `start_date` beperkt Tavily's resultaten tot content met een (geschatte)
        publicatiedatum na dat punt — een eerste, server-side filter op actualiteit
        (max. 3 weken oud). De AI fit-analyse doet daarna een tweede, strengere
        check op basis van de daadwerkelijke paginatekst (zie MAX_AGE_DAYS)."""
        if not self._tavily:
            log.warning("[vacancies] Geen TAVILY_API_KEY geconfigureerd")
            return []
        query = f"{site_filter} {role} interim opdracht zzp" if site_filter else f"{role} interim opdracht zzp vacature"
        start_date = (datetime.now(timezone.utc) - timedelta(days=MAX_AGE_DAYS)).strftime("%Y-%m-%d")
        try:
            resp = self._tavily.search(
                query=query,
                max_results=max_results + 2,
                search_depth="advanced",
                include_answer=False,
                start_date=start_date,
            )
            return [
                {
                    "title": r.get("title", ""),
                    "url": r.get("url", ""),
                    "snippet": r.get("content", ""),
                }
                for r in resp.get("results", [])
            ][:max_results]
        except Exception as e:
            log.error("[vacancies] Tavily fout: %s", e)
            return []

    def scrape_description(self, url: str) -> str:
        return scraper.scrape_text(url)

    # ── AI fit-analyse ───────────────────────────────────────────────────────

    def _fit_config(self) -> tuple:
        """(system_prompt, model_override) uit het Vacature Fit-Analist-profiel."""
        with get_conn() as conn:
            row = conn.execute(
                "SELECT system_prompt, model FROM agent_profiles WHERE name = ?",
                (FIT_PROFILE_NAME,),
            ).fetchone()
        if row and (row["system_prompt"] or "").strip():
            return row["system_prompt"].strip(), _resolve_model_override(row["model"])
        # Fallback als het profiel nog niet geseed is (zou niet moeten gebeuren, ensure_expert_team
        # draait bij startup) - dan gewoon geen fit-analyse, neutrale score.
        return "", None

    async def analyze_fit(
        self, title: str, organization: str, url: str, source: str,
        snippet: str, description: str,
        _attempt: int = 0,
    ) -> Dict:
        system, model_override = self._fit_config()
        if not system:
            return {
                "fit_score": 50, "fit_rationale": "Fit-Analist-profiel niet gevonden.",
                "hours_detected": "", "location_detected": "", "contract_type_detected": "onbekend",
                "posted_days_ago": -1,
            }

        context = description[:3000] if description else snippet[:800]
        today = datetime.now(timezone.utc).strftime("%d-%m-%Y")
        user_content = (
            f"Vandaag is {today}.\n\n"
            f"Titel: {title}\nOrganisatie: {organization or 'onbekend'}\nBron: {source}\n"
            f"URL: {url}\n\nBeschikbare tekst:\n{context}"
        )

        chunks: List[str] = []
        async for ev in agent_service.run_agent(
            messages=[{"role": "user", "content": user_content}],
            system_prompt=system,
            agent="hermes",
            model_override=model_override,
            use_tools=False,
            max_tokens=700,
        ):
            if ev.get("type") == "text":
                chunks.append(ev["text"])
        raw = "".join(chunks)

        if not raw:
            # Eén automatische retry — lege Hermes-responses zijn vrijwel altijd
            # een tijdelijke hapering, geen structurele storing.
            if _attempt == 0:
                return await self.analyze_fit(
                    title, organization, url, source, snippet, description, _attempt=1,
                )
            return {
                "fit_score": 50, "fit_rationale": "Geen AI-analyse beschikbaar (backend onbereikbaar).",
                "hours_detected": "", "location_detected": "", "contract_type_detected": "onbekend",
                "posted_days_ago": -1,
            }
        try:
            parsed = json.loads(_strip_json(raw))
        except Exception:
            if _attempt == 0:
                return await self.analyze_fit(
                    title, organization, url, source, snippet, description, _attempt=1,
                )
            return {
                "fit_score": 50, "fit_rationale": raw[:300],
                "hours_detected": "", "location_detected": "", "contract_type_detected": "onbekend",
                "posted_days_ago": -1,
            }
        try:
            parsed["posted_days_ago"] = int(parsed.get("posted_days_ago", -1))
        except (TypeError, ValueError):
            parsed["posted_days_ago"] = -1
        return parsed

    # ── Opslaan / CRUD ───────────────────────────────────────────────────────

    def is_duplicate(self, url: str) -> bool:
        """Dedupliceer op exacte URL (i.p.v. domein - meerdere vacatures kunnen op
        hetzelfde jobboard-domein staan)."""
        with get_conn() as conn:
            row = conn.execute("SELECT id FROM vacancies WHERE url = ?", (url,)).fetchone()
        return row is not None

    def save_to_db(self, vacancy: Dict) -> Dict:
        vacancy_id = str(uuid.uuid4())
        now = _now()
        with get_conn() as conn:
            conn.execute(
                """INSERT INTO vacancies
                   (id, title, organization, url, source, role_query, location, hours_text,
                    contract_type, description, fit_score, fit_rationale, posted_days_ago,
                    status, search_query, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    vacancy_id,
                    vacancy["title"],
                    vacancy.get("organization", ""),
                    vacancy["url"],
                    vacancy.get("source", "overig"),
                    vacancy.get("role_query", ""),
                    vacancy.get("location", ""),
                    vacancy.get("hours_text", ""),
                    vacancy.get("contract_type", ""),
                    vacancy.get("description", ""),
                    vacancy.get("fit_score", 50),
                    vacancy.get("fit_rationale", ""),
                    vacancy.get("posted_days_ago", -1),
                    vacancy.get("status", "new"),
                    vacancy.get("search_query", ""),
                    now, now,
                ),
            )
        return {**vacancy, "id": vacancy_id, "created_at": now, "updated_at": now}

    def list_vacancies(
        self, status: Optional[str] = None, min_score: Optional[int] = None,
    ) -> List[Dict]:
        where, params = [], []
        if status:
            where.append("status = ?")
            params.append(status)
        if min_score is not None:
            where.append("fit_score >= ?")
            params.append(min_score)
        clause = ("WHERE " + " AND ".join(where)) if where else ""
        with get_conn() as conn:
            rows = conn.execute(
                f"SELECT * FROM vacancies {clause} ORDER BY fit_score DESC, created_at DESC",
                params,
            ).fetchall()
        return [dict(r) for r in rows]

    def update_status(self, vacancy_id: str, status: str) -> Optional[Dict]:
        now = _now()
        with get_conn() as conn:
            cur = conn.execute(
                "UPDATE vacancies SET status = ?, updated_at = ? WHERE id = ?",
                (status, now, vacancy_id),
            )
            if cur.rowcount == 0:
                return None
            row = conn.execute("SELECT * FROM vacancies WHERE id = ?", (vacancy_id,)).fetchone()
        return dict(row)

    def delete_vacancy(self, vacancy_id: str) -> bool:
        with get_conn() as conn:
            cur = conn.execute("DELETE FROM vacancies WHERE id = ?", (vacancy_id,))
        return cur.rowcount > 0

    def get_stats(self) -> Dict:
        with get_conn() as conn:
            total = conn.execute("SELECT COUNT(*) FROM vacancies").fetchone()[0]
            by_status = dict(conn.execute(
                "SELECT status, COUNT(*) FROM vacancies GROUP BY status"
            ).fetchall())
        return {
            "total": total,
            "new": by_status.get("new", 0),
            "interesting": by_status.get("interesting", 0),
            "rejected": by_status.get("rejected", 0),
            "applied": by_status.get("applied", 0),
        }

    # ── Volledige scan (gebruikt door SSE-router én de scheduler-job) ───────

    async def run_scan(self, roles: Optional[List[str]] = None, max_per_source: int = 3):
        """Doorloopt rol x bron, dedupliceert, analyseert fit en slaat op.
        Async generator: yieldt voortgangsevents (zelfde vorm als de SSE-router ze
        doorstuurt), zodat zowel de router als de scheduler-job dit kunnen consumeren."""
        roles = roles or DEFAULT_ROLES
        total_saved = 0
        yield {"type": "scan_start", "roles": roles, "sources": list(SOURCES.keys())}

        for role in roles:
            for source, site_filter in SOURCES.items():
                yield {"type": "query_start", "role": role, "source": source}
                results = self.search_web(role, site_filter, max_per_source)
                new_results = [r for r in results if r.get("url") and not self.is_duplicate(r["url"])]

                for r in new_results:
                    yield {"type": "analyzing", "title": r["title"], "source": source}
                    description = self.scrape_description(r["url"])
                    fit = await self.analyze_fit(
                        r["title"], "", r["url"], source, r["snippet"], description,
                    )

                    posted_days_ago = fit.get("posted_days_ago", -1)
                    if isinstance(posted_days_ago, int) and posted_days_ago > MAX_AGE_DAYS:
                        yield {
                            "type": "vacancy_skipped_expired", "title": r["title"],
                            "source": source, "posted_days_ago": posted_days_ago,
                        }
                        continue

                    vacancy = {
                        "title": r["title"][:250],
                        "organization": "",
                        "url": r["url"],
                        "source": source,
                        "role_query": role,
                        "location": fit.get("location_detected", ""),
                        "hours_text": fit.get("hours_detected", ""),
                        "contract_type": fit.get("contract_type_detected", "onbekend"),
                        "description": (description or r["snippet"])[:2000],
                        "fit_score": fit.get("fit_score", 50),
                        "fit_rationale": fit.get("fit_rationale", ""),
                        "posted_days_ago": posted_days_ago,
                        "status": "new",
                        "search_query": f"{role} [{source}]",
                    }
                    saved = self.save_to_db(vacancy)
                    total_saved += 1
                    yield {"type": "vacancy_saved", "vacancy": saved, "total_saved": total_saved}

                yield {
                    "type": "query_done", "role": role, "source": source,
                    "found": len(new_results), "skipped": len(results) - len(new_results),
                }

        yield {"type": "scan_done", "total_saved": total_saved}


_svc_singleton: Optional[VacancyService] = None


def get_service() -> VacancyService:
    global _svc_singleton
    if _svc_singleton is None:
        _svc_singleton = VacancyService()
    return _svc_singleton


async def run_vacancy_scan_job() -> None:
    """Entry point voor de scheduler (backend/scheduler.py) - consumeert run_scan()
    zonder de events door te sturen (die zijn alleen relevant voor de SSE-route)."""
    svc = get_service()
    saved = 0
    async for ev in svc.run_scan():
        if ev.get("type") == "vacancy_saved":
            saved = ev.get("total_saved", saved)
    log.info("[vacancies] Geplande scan klaar: %s nieuwe vacatures opgeslagen", saved)
