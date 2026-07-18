"""Researcher-domein — de NotebookLM-onderzoek-agent voor Agent OS.

Sluit de cyclus: AgentOS genereert een onderzoeksvraag -> duwt die
naar NotebookLM (RAG, gegrond op JOUW eigen notebooks) -> schrijft
het rapport terug in de Obsidian-vault -> blogs halen dat rapport op
als context.

Waarom een eigen domein en niet alles in de radar-stoppen?
- Radar = concurrentie/feed-monitoring (koud signaal). Researcher = diepte-
  onderzoek op de eigen kennisbasis (warm signaal). Twee verschillende
  verantwoordelijkheden, twee tabellen, twee UI-panelen.
- De blog-integratie haalt onderzoeksrapporten op via VaultReader
  (30_Resources of 10_Projects/{project}/onderzoek), losgekoppeld
  van de radar-trends.

Patroon (gekopieerd van radar): ensure_schema() -> service ->
router -> in scheduler geregistreerd als JobSpec.

Zie backend/tools/notebooklm.py voor de MCP-client.
"""
from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from ...shared.config import (
    OBSIDIAN_VAULT_PATH, NOTEBOOKLM_BASE_URL, NOTEBOOKLM_TIMEOUT,
    NOTEBOOKLM_ENABLED, NOTEBOOKLM_DEFAULT_NOTEBOOK,
)
from ...shared.database import get_conn
from ...shared import agent_runner as agent_service
from ...tools.notebooklm import NotebookLMClient

log = logging.getLogger(__name__)


# ── Vault-bestemmingen ────────────────────────────────────────
RESEARCH_VAULT_DIR = "onderzoek"          # onder 10_Projects/{project}/
RESEARCH_RESOURCES_DIR = "30_Resources/Onderzoek"  # project-loos


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _slugify(text: str, max_len: int = 60) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (text or "onderzoek").lower()).strip("-")
    while "--" in slug:
        slug = slug.replace("--", "-")
    return (slug or "onderzoek")[:max_len].rstrip("-")


# ── Schema ───────────────────────────────────────────────────────
def ensure_schema() -> None:
    with get_conn() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS researcher_jobs (
                id           TEXT PRIMARY KEY,
                project      TEXT NOT NULL DEFAULT '',
                question     TEXT NOT NULL,
                notebook_id  TEXT NOT NULL DEFAULT '',
                status       TEXT NOT NULL DEFAULT 'new',
                answer       TEXT,
                sources      TEXT,            -- JSON: [{title, excerpt, url}]
                vault_path   TEXT,
                error        TEXT,
                created_at   TEXT NOT NULL,
                run_at       TEXT,
                updated_at   TEXT NOT NULL
            )
            """
        )


# ── Service ────────────────────────────────────────────────────
class ResearcherService:
    def __init__(self):
        ensure_schema()

    # ── CRUD ────────────────────────────────────────────────
    def list_jobs(self, project: Optional[str] = None,
                 status: Optional[str] = None) -> List[Dict]:
        where, params = [], []
        if project:
            where.append("project = ?"); params.append(project)
        if status:
            where.append("status = ?"); params.append(status)
        clause = ("WHERE " + " AND ".join(where)) if where else ""
        with get_conn() as conn:
            rows = conn.execute(
                f"SELECT * FROM researcher_jobs {clause} "
                f"ORDER BY created_at DESC LIMIT 200", params,
            ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            try:
                d["sources"] = json.loads(d.get("sources") or "[]")
            except Exception:
                d["sources"] = []
            out.append(d)
        return out

    def get_job(self, job_id: str) -> Optional[Dict]:
        with get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM researcher_jobs WHERE id = ?", (job_id,)
            ).fetchone()
        if not row:
            return None
        d = dict(row)
        try:
            d["sources"] = json.loads(d.get("sources") or "[]")
        except Exception:
            d["sources"] = []
        return d

    # ── Onderzoek uitvoeren ─────────────────────────────────
    async def run_research(self, project: str, question: str,
                         notebook_id: Optional[str] = None) -> Dict:
        """Voer één onderzoeksvraag uit tegen een notebook.

        Schrijft het rapport (antwoord + citations) naar de vault en
        retourneert de job-row. Async omdat de NotebookLM-call traag
        kan zijn (Gemini + DOM-crawl, tot NOTEBOOKLM_TIMEOUT).
        """
        if not NOTEBOOKLM_ENABLED:
            raise RuntimeError("NotebookLM-agent staat uit (NOTEBOOKLM_ENABLED=0)")
        nb = notebook_id or NOTEBOOKLM_DEFAULT_NOTEBOOK
        job_id = str(uuid.uuid4())
        now = _now()
        with get_conn() as conn:
            conn.execute(
                """INSERT INTO researcher_jobs
                   (id, project, question, notebook_id, status, created_at, updated_at)
                   VALUES (?,?,?,?, 'running', ?, ?)""",
                (job_id, project, question, nb, now, now),
            )

        try:
            with NotebookLMClient(base_url=NOTEBOOKLM_BASE_URL,
                                 timeout=NOTEBOOKLM_TIMEOUT) as c:
                res = c.ask(question, notebook_id=nb)
            answer = res.get("answer", "").strip()
            sources = res.get("sources", [])
            if not answer:
                raise RuntimeError(
                    "NotebookLM gaf geen antwoord (auth verlopen of timeout?). "
                    "Draai re_auth via de notebooklm-mcp server."
                )
            vault_rel = self._write_report(
                project, question, nb, answer, sources, job_id,
            )
            with get_conn() as conn:
                conn.execute(
                    """UPDATE researcher_jobs
                       SET status='done', answer=?, sources=?, vault_path=?,
                           run_at=?, updated_at=?
                       WHERE id=?""",
                    (answer, json.dumps(sources, ensure_ascii=False),
                     vault_rel, now, now, job_id),
                )
            log.info("[researcher] '%s' -> %s", question[:60], vault_rel)
            return self.get_job(job_id)
        except Exception as e:
            err = str(e)[:400]
            with get_conn() as conn:
                conn.execute(
                    """UPDATE researcher_jobs
                       SET status='error', error=?, updated_at=? WHERE id=?""",
                    (err, now, job_id),
                )
            log.exception("[researcher] onderzoek '%s' mislukt", question[:60])
            raise

    # ── Rapport naar vault ───────────────────────────────────
    def _write_report(self, project: str, question: str, notebook_id: str,
                      answer: str, sources: List[Dict], job_id: str,
                      ) -> Optional[str]:
        if not OBSIDIAN_VAULT_PATH:
            return None
        vault = Path(OBSIDIAN_VAULT_PATH)
        if not vault.exists():
            return None

        # Project-specifiek eerst, anders in 30_Resources (project-loos).
        if project:
            dest = vault / "10_Projects" / project / RESEARCH_VAULT_DIR
        else:
            dest = vault / RESEARCH_RESOURCES_DIR
        dest.mkdir(parents=True, exist_ok=True)

        slug = _slugify(f"{project}-{question}" if project else question)
        note_path = dest / f"{slug}.md"
        today = datetime.now().strftime("%Y-%m-%d")

        src_lines = []
        for i, s in enumerate(sources[:10], 1):
            title = s.get("title", "?")
            ex = (s.get("excerpt") or "").strip().replace("\n", " ")
            src_lines.append(f"{i}. **{title}** — {ex}")
        src_block = "\n".join(src_lines) if src_lines else "_Geen citations teruggekregen._"

        content = (
            f"# Onderzoek: {question}\n\n"
            f"## Vraag\n{question}\n\n"
            f"## Antwoord (NotebookLM · RAG op eigen bronnen)\n\n{answer}\n\n"
            f"## Geraadpleegde bronnen\n{src_block}\n\n"
            f"---\n"
            f"_Gegenereerd door de NotebookLM-onderzoek-agent op {today}. "
            f"Notebook: `{notebook_id}` · Job: `{job_id}`._\n"
        )
        note_path.write_text(content, encoding="utf-8")
        return str(note_path.relative_to(vault))

    # ── Demand Engine -> NotebookLM (kennis gronden) ─────────────
    def _already_grounded(self, project: str, query: str) -> bool:
        """True als er al een afgerond onderzoeksrapport voor deze query ligt.

        Match op de query-tekst in de onderzoeksvraag: de vraag wordt hieronder
        altijd om de letterlijke query heen gebouwd, dus LIKE volstaat en we
        hoeven geen aparte koppel-tabel bij te houden."""
        with get_conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM researcher_jobs WHERE project = ? AND status = 'done' "
                "AND question LIKE ? LIMIT 1",
                (project, f"%{query}%"),
            ).fetchone()
        return bool(row)

    async def ground_new_opportunities(self, site: Dict,
                                      max_questions: int = 3) -> int:
        """Demand→Researcher-brug: grond de top-kansen van een site in NotebookLM.

        Voor elke nieuwe Demand Engine-kans (hoogste opportunity_score eerst)
        zonder bestaand onderzoeksrapport wordt één onderzoeksvraag tegen het
        notebook gedraaid. Het rapport landt in de vault
        (10_Projects/{project}/onderzoek/) waar content_pipeline._vault_context
        het automatisch oppakt — zo is elk artikel gegrond op eigen bronnen
        i.p.v. alleen LLM-kennis. Sequentieel en met een harde cap: NotebookLM
        drijft een echte browser en is traag/quota-gevoelig.

        Retourneert het aantal gestarte-en-geslaagde onderzoeken. Faalt zacht:
        een kapotte NotebookLM (auth verlopen) breekt de aanroeper nooit."""
        if not NOTEBOOKLM_ENABLED:
            return 0
        from ..seo import engine as demand_engine
        project = (site.get("name") or "").strip()
        if not project:
            return 0
        kansen = demand_engine.list_opportunities(site_id=site["id"], status="new")
        done = 0
        for opp in kansen:
            if done >= max_questions:
                break
            query = (opp.get("query") or "").strip()
            if not query or self._already_grounded(project, query):
                continue
            angle = (opp.get("angle") or "").strip()
            question = (
                f"Wat moet een gezaghebbend artikel over '{query}' behandelen? "
                + (f"Invalshoek: {angle}. " if angle else "")
                + "Geef uit de bronnen: de belangrijkste feiten en cijfers, "
                "veelgestelde vragen van de doelgroep, veelgemaakte fouten of "
                "misvattingen, en unieke inzichten die concurrenten missen."
            )
            try:
                await self.run_research(project, question)
                done += 1
            except Exception as e:  # noqa: BLE001
                # Eén fout = vrijwel altijd NotebookLM zelf (auth/timeout);
                # doorproberen verbrandt dan alleen maar tijd.
                log.warning("[researcher] Grounden van '%s' mislukt (%s) — "
                            "rest van de batch overgeslagen", query[:60], str(e)[:160])
                break
        return done

    # ── Radar-signaal -> NotebookLM-bron ─────────────────────
    async def push_signal_as_source(self, signal: Dict,
                                   notebook_id: Optional[str] = None) -> Dict:
        """Duw een Radar-signaal als brondocument naar een notebook.

        Sluit de bestaande radar.build_notebooklm_package() aan: die
        maakt het bronpakket in de vault; deze stap duwt het daadwerkelijk
        het notebook IN zodat NotebookLM er later op kan RAG-en.
        """
        if not NOTEBOOKLM_ENABLED:
            raise RuntimeError("NotebookLM-agent staat uit (NOTEBOOKLM_ENABLED=0)")
        nb = notebook_id or NOTEBOOKLM_DEFAULT_NOTEBOOK
        title = (signal.get("ai_titles") or [signal.get("title", "Signaal")])[0] \
            if isinstance(signal.get("ai_titles"), list) else signal.get("title", "Signaal")
        text = (
            f"# {title}\n\n"
            f"**Bron:** {signal.get('url', '')}\n"
            f"**Keyword:** {signal.get('keyword', '')}\n\n"
            f"{signal.get('snippet', '')}\n\n"
            f"**Invalshoek:** {signal.get('ai_angle', '')}\n"
            f"**Hook:** {signal.get('ai_hook', '')}\n"
        )
        with NotebookLMClient(base_url=NOTEBOOKLM_BASE_URL,
                             timeout=NOTEBOOKLM_TIMEOUT) as c:
            res = c.add_source_text(title, text, notebook_id=nb)
        return res


# ── Singleton ─────────────────────────────────────────────────
_service: Optional[ResearcherService] = None


def get_service() -> ResearcherService:
    global _service
    if _service is None:
        _service = ResearcherService()
    return _service
