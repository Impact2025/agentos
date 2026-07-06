"""
Infinite Context Engine — de brug tussen AI Agents en Obsidian.

Three-part loop ("The Loop"):
  1. READ:   Haal relevante context uit Obsidian + OMI vóór elke agent-run
  2. ACT:    Injecteer context in system prompt, voer taak uit
  3. WRITE:  Log resultaat terug naar Obsidian + OMI (dagelijks log + taak-specifiek)

Over tijd groeit de vault als een zelflerend geheugen:
  - OMI/OMI-notities vullen de input-kant (real-time context uit gesprekken)
  - Agent-sessies vullen de output-kant
  - Elke nieuwe run leest alle historie + OMI-memories → agents worden elke dag slimmer

Gebruik:
  engine = InfiniteContextEngine(OBSIDIAN_VAULT_PATH)
  ctx = engine.build_task_context("Doelgroepanalyse", project="WeAreImpact")
  engine.log_task_completion(goal_id, task_id, title, result, project="WeAreImpact")
"""
import json
import logging
import re
import os
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_AGENTOS_FOLDER = "AgentOS"
_SESSIONS_FOLDER = f"{_AGENTOS_FOLDER}/Sessions"
_GOALS_FOLDER = f"{_AGENTOS_FOLDER}/Goals"
_TASKS_FOLDER = f"{_AGENTOS_FOLDER}/Tasks"
_OMI_FOLDER = f"{_AGENTOS_FOLDER}/OMI"

# ── OMI-connector (Open Memory Interface) ─────────────────────────────

class OmiConnector:
    """Bridge naar de Open Memory Interface API.

    OMI is een achtergrond-app die via microfoon/screen vastlegt wat je
    gedurende de dag doet en hier automatisch notities van maakt.

    Data stroom:
      OMI hardware/desktop → OMI cloud API → memories + conversations
      └→ Ook exporteerbaar naar Obsidian als Markdown

    Deze connector geeft de AgentOS toegang tot OMI's API voor:
    - READ:  memories ophalen (gestructureerde weetjes over de gebruiker)
    - READ:  conversations doorzoeken (real-time context)
    - WRITE: memories aanmaken (agent-resultaten terug naar OMI)

    Gebruik:
      omi = OmiConnector()
      memories = omi.get_memories(categories=["work", "skills"])
      ctx = omi.build_context("marketing campagne")
    """

    API_BASE = "https://api.omi.me/v1/mcp"

    def __init__(self):
        self._api_key = os.getenv("OMI_API_KEY", "")
        self._enabled = bool(self._api_key)

        # Fallback: check .omi/config.toml
        if not self._api_key:
            try:
                config_path = Path.home() / ".omi" / "config.toml"
                if config_path.exists():
                    import tomllib
                    config = config_path.read_bytes()
                    data = tomllib.loads(config)
                    # Zoek in profielen naar api_key
                    profiles = data.get("profile", {})
                    if isinstance(profiles, dict):
                        for name, prof in profiles.items():
                            if isinstance(prof, dict) and prof.get("api_key"):
                                self._api_key = prof["api_key"]
                                self._enabled = True
                                break
            except Exception:
                pass

    @property
    def is_configured(self) -> bool:
        """Of OMI beschikbaar is (API key ingesteld)."""
        return self._enabled

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

    # ── READ: Context uit OMI ──────────────────────────────────────────

    def get_memories(self, categories: Optional[List[str]] = None,
                     limit: int = 20, offset: int = 0) -> List[Dict]:
        """Haal OMI-memories op (gestructureerde weetjes).

        Categories: core, hobbies, lifestyle, interests, habits,
                    work, skills, learnings, other
        """
        if not self._enabled:
            return []

        try:
            import httpx
            params: Dict[str, Any] = {"limit": limit, "offset": offset}
            if categories:
                params["categories"] = ",".join(categories)
            resp = httpx.get(
                f"{self.API_BASE}/get_memories",
                headers=self._headers(),
                params=params,
                timeout=10,
            )
            if resp.status_code == 200:
                data = resp.json()
                return data.get("memories", data.get("data", []))
            else:
                logger.warning(f"OMI get_memories: {resp.status_code} {resp.text[:200]}")
                return []
        except Exception as e:
            logger.debug(f"OMI get_memories error: {e}")
            return []

    def search_memories(self, query: str, limit: int = 10) -> List[Dict]:
        """Doorzoek OMI-memories met een natuurlijke taal query."""
        if not self._enabled:
            return []

        try:
            import httpx
            resp = httpx.post(
                f"{self.API_BASE}/search_memories",
                headers=self._headers(),
                json={"query": query, "limit": limit},
                timeout=10,
            )
            if resp.status_code == 200:
                data = resp.json()
                return data.get("memories", data.get("data", []))
            return []
        except Exception as e:
            logger.debug(f"OMI search_memories error: {e}")
            return []

    def get_conversations(self, categories: Optional[List[str]] = None,
                          start_date: Optional[str] = None,
                          end_date: Optional[str] = None,
                          limit: int = 10) -> List[Dict]:
        """Haal recente OMI-conversaties op."""
        if not self._enabled:
            return []

        try:
            import httpx
            params: Dict[str, Any] = {"limit": limit}
            if categories:
                params["categories"] = ",".join(categories)
            if start_date:
                params["start_date"] = start_date
            if end_date:
                params["end_date"] = end_date
            resp = httpx.get(
                f"{self.API_BASE}/get_conversations",
                headers=self._headers(),
                params=params,
                timeout=10,
            )
            if resp.status_code == 200:
                data = resp.json()
                return data.get("conversations", data.get("data", []))
            return []
        except Exception as e:
            logger.debug(f"OMI get_conversations error: {e}")
            return []

    def search_conversations(self, query: str, limit: int = 10) -> List[Dict]:
        """Doorzoek OMI-conversaties."""
        if not self._enabled:
            return []

        try:
            import httpx
            resp = httpx.post(
                f"{self.API_BASE}/search_conversations",
                headers=self._headers(),
                json={"query": query, "limit": limit},
                timeout=10,
            )
            if resp.status_code == 200:
                data = resp.json()
                return data.get("conversations", data.get("data", []))
            return []
        except Exception as e:
            logger.debug(f"OMI search_conversations error: {e}")
            return []

    # ── Context-building (OMI → system prompt) ───────────────────────

    def build_context(self, query: str = "", max_chars: int = 2000) -> str:
        """Bouw context uit OMI voor in de system prompt.

        Haalt memories + conversations op, formatteert als markdown.
        Fallback naar Obsidian-geëxporteerde OMI notities als de API
        niet beschikbaar is.
        """
        parts: List[str] = []

        # Pijler 1: Memories (gestructureerde weetjes)
        memories = self.get_memories(limit=10)
        if memories:
            mem_lines = ["### OMI-memories (automatisch vastgelegd)"]
            for m in memories[:5]:
                content = m.get("content", m.get("text", ""))
                cat = m.get("category", m.get("type", "onbekend"))
                if content:
                    mem_lines.append(f"- **{cat}**: {content[:300].strip()}")
            parts.append("\n".join(mem_lines))

        # Pijler 2: Doorzoek memories op query
        if query:
            search_results = self.search_memories(query, limit=5)
            if search_results:
                search_lines = ["### OMI — relevante memories"]
                for m in search_results:
                    content = m.get("content", m.get("text", ""))
                    if content:
                        search_lines.append(f"- {content[:400].strip()}")
                parts.append("\n".join(search_lines))

        # Pijler 3: Recente conversaties
        convos = self.get_conversations(limit=3)
        if convos:
            conv_lines = ["### OMI — recente conversaties"]
            for c in convos:
                title = c.get("title", c.get("name", "Gesprek"))
                summary = c.get("summary", c.get("text", ""))
                conv_lines.append(f"- **{title}**: {(summary or '')[:300].strip()}")
            parts.append("\n".join(conv_lines))

        if not parts:
            return ""

        combined = "\n\n".join(parts)
        if len(combined) > max_chars:
            combined = combined[:max_chars] + "\n…[OMI context afgekapt]"
        return combined

    # ── WRITE: Naar OMI ───────────────────────────────────────────────

    def create_memory(self, content: str, category: str = "other") -> Optional[str]:
        """Maak een OMI-memory aan.

        Retourneert het ID van de memory, of None bij fout.
        """
        if not self._enabled or not content:
            return None

        try:
            import httpx
            resp = httpx.post(
                f"{self.API_BASE}/create_memory",
                headers=self._headers(),
                json={"content": content, "category": category},
                timeout=10,
            )
            if resp.status_code in (200, 201):
                data = resp.json()
                return data.get("id", data.get("memory_id", "created"))
            return None
        except Exception as e:
            logger.debug(f"OMI create_memory error: {e}")
            return None


# ── Infinite Context Engine (hoofdklasse) ─────────────────────────────

class InfiniteContextEngine:
    """Bridge between AI agents and the Obsidian vault + OMI for persistent context.

    The "Oneindige Loop":
      1. READ context from Obsidian + OMI
      2. ACT: agents execute with rich context
      3. WRITE results back to Obsidian + OMI
      4. Repeat — each run is smarter than the last
    """

    def __init__(self, vault_path: str):
        self._vault_path = Path(vault_path) if vault_path else None
        self._obsidian = None  # lazy init via _get_obsidian()
        self._omi = OmiConnector()  # kan niet-fataal falen

    def _get_obsidian(self):
        """Lazy import — vermijd circular imports bij module-level init."""
        if self._obsidian is None:
            from ..domains.chat.obsidian import ObsidianService
            vault = str(self._vault_path) if self._vault_path else ""
            self._obsidian = ObsidianService(vault)
        return self._obsidian

    @property
    def is_configured(self) -> bool:
        return self._vault_path is not None and self._vault_path.exists()

    @property
    def omi_configured(self) -> bool:
        return self._omi.is_configured

    # ── READ: Context opbouwen ──────────────────────────────────────────

    def build_task_context(self, title: str, description: str = "",
                           goal_id: str = "", project: str = "") -> str:
        """Bouw een context-block voor een taak — uit Obsidian + OMI.

        Zoekt naar:
        1. Relevante Obsidian-notities (merk, project, eerdere resultaten)
        2. OMI-memories (automatisch vastgelegde weetjes)
        3. OMI-conversaties (recente gesprekken)
        4. Vandaag's dagboek (eerdere agent-sessies vandaag)

        Retourneert een markdown-blok dat in de system_prompt kan worden geplakt.
        """
        if not self.is_configured and not self._omi.is_configured:
            return ""

        parts: List[str] = []

        # Pijler 1: Obsidian — merk- en projectkennis
        keywords = f"{title} {description} {project} context strategie merk"
        obs = self._get_obsidian()
        if obs.is_configured:
            brand_ctx = obs.build_context(keywords, max_chars=1500)
            if brand_ctx:
                parts.append(f"## Relevante kennis uit Obsidian\n{brand_ctx}")

        # Pijler 2: OMI — real-time context (gesprekken, memories)
        if self._omi.is_configured:
            omi_ctx = self._omi.build_context(
                query=f"{title} {project}",
                max_chars=1500,
            )
            if omi_ctx:
                parts.append(f"## OMI-context (automatisch vastgelegd)\n{omi_ctx}")

        # Pijler 3: Vandaag's dagboek (uit Obsidian-agent sessies)
        if self.is_configured:
            daily = self._get_todays_log()
            if daily:
                daily_snippet = daily[:600] if len(daily) > 600 else daily
                parts.append(f"## Vandaag (dagboek)\n{daily_snippet}")

        return "\n\n".join(parts)

    def build_goal_context(self, objective: str, project: str = "") -> str:
        """Bouw context voor goal-decompositie — Obsidian + OMI."""
        parts: List[str] = []

        # Obsidian — merk + project + eerdere resultaten
        if self.is_configured:
            keywords = f"{objective} {project} strategie doelgroep fasen planning"
            obs = self._get_obsidian()
            ctx = obs.build_context(keywords, max_chars=1500)
            if ctx:
                parts.append(f"## Merk- & projectkader (Obsidian)\n{ctx}")

        # OMI — relevante memories over dit project/doel
        if self._omi.is_configured:
            omi_ctx = self._omi.build_context(
                query=f"{objective} {project}",
                max_chars=1000,
            )
            if omi_ctx:
                parts.append(f"## OMI-context\n{omi_ctx}")

        return "\n\n".join(parts)

    def get_daily_context(self, project: str = "") -> str:
        """Haal de dagelijkse context op — wat is er vandaag al gebeurd.

        Leest:
        - Vandaag's dagboek (AgentOS/Sessions/<datum>.md)
        - Alle doelen die vandaag actief waren
        - Recente brand context (Obsidian)
        - OMI-memories van vandaag (real-time)
        """
        parts: List[str] = []

        # 1. Vandaag's agent-sessies (Obsidian)
        if self.is_configured:
            daily = self._get_todays_log()
            if daily:
                parts.append(f"## Agent-activiteit vandaag\n{daily}")

        # 2. Merk- & projectkader
        keywords = f"{project} context merkrichtlijnen project strategie" if project else "context merkrichtlijnen project strategie"
        obs = self._get_obsidian()
        if obs.is_configured:
            brand_ctx = obs.build_context(keywords, max_chars=1000)
            if brand_ctx:
                parts.append(f"## Merk- & projectkader\n{brand_ctx}")

        # 3. OMI — vandaag's gesprekken
        if self._omi.is_configured:
            today_str = date.today().strftime("%Y-%m-%d")
            convos = self._omi.get_conversations(
                start_date=today_str,
                limit=5,
            )
            if convos:
                conv_lines = ["## OMI — gesprekken vandaag"]
                for c in convos:
                    title = c.get("title", c.get("name", "Gesprek"))
                    summary = c.get("summary", c.get("text", ""))
                    conv_lines.append(f"- **{title}**: {(summary or '')[:200].strip()}")
                parts.append("\n".join(conv_lines))

        return "\n\n".join(parts)

    # ── WRITE: Loggen naar Obsidian ────────────────────────────────────

    def log_task_completion(self, goal_id: str, task_id: str,
                            title: str, skill: str, result: str,
                            project: str = "", duration_ms: int = 0) -> Optional[Path]:
        """Schrijf een taak-resultaat naar Obsidian + OMI.

        Schrijft naar:
          AgentOS/Tasks/JJJJ-WW/task-<short_id>.md  (per taak)
          AgentOS/Sessions/<datum>.md                 (dagboek — append)
          OMI: create_memory()                        (OMI-memory)

        Retourneert het pad naar het taak-bestand, of None bij fout.
        """
        if not self.is_configured:
            return None

        vault = self._vault_path

        # ── Per-taak log ────────────────────────────────────────────
        week_tag = date.today().strftime("%Y-W%W")
        task_folder = vault / f"{_TASKS_FOLDER}/{week_tag}"
        task_folder.mkdir(parents=True, exist_ok=True)

        # Maak een leesbare bestandsnaam
        safe_title = re.sub(r'[^a-zA-Z0-9\s-]', '', title).strip().replace(' ', '-')[:40]
        short_id = task_id[-8:] if len(task_id) > 8 else task_id
        filename = f"task-{short_id}-{safe_title}.md"
        task_path = task_folder / filename

        content = (
            f"# {title}\n\n"
            f"- **Goal**: {goal_id}\n"
            f"- **Skill**: {skill}\n"
            f"- **Duur**: {duration_ms}ms\n"
            f"- **Datum**: {datetime.now().isoformat()}\n"
            f"{'  - **Project**: ' + project if project else ''}\n\n"
            f"## Resultaat\n\n{result[:2000]}\n"
        )
        try:
            task_path.write_text(content, encoding="utf-8")
        except OSError as e:
            logger.warning(f"Kon taak-log niet schrijven naar {task_path}: {e}")
            return None

        # ── Dagboek — append ────────────────────────────────────────
        self._append_to_daily_log(
            f"### ✅ Taak voltooid: {title}\n"
            f"- Skill: {skill}\n"
            f"- Result (eerste 200 chars): {(result or '')[:200].strip()}\n"
            f"- [[{task_path.relative_to(vault).as_posix().replace('.md', '')}]]\n\n"
        )

        # ── OMI — memory aanmaken ──────────────────────────────────
        if self._omi.is_configured:
            try:
                category = _skill_to_omi_category(skill)
                omi_content = (
                    f"Agent OS taak voltooid: {title}\n"
                    f"Project: {project}\n"
                    f"Skill: {skill}\n"
                    f"Resultaat (verkort): {(result or '')[:500].strip()}"
                )
                self._omi.create_memory(omi_content, category=category)
            except Exception as e:
                logger.debug(f"OMI taak-memory aanmaken mislukt: {e}")

        return task_path

    def log_goal_completion(self, goal_id: str, title: str, objective: str,
                            project: str, summary: str,
                            phase_count: int, task_count: int,
                            completed: int, failed: int) -> Optional[Path]:
        """Schrijf een goal-summary naar Obsidian + OMI na afloop van de executie-loop.

        Schrijft naar:
          AgentOS/Goals/goal-<short_id>.md
          OMI: create_memory()
        """
        if not self.is_configured:
            return None

        vault = self._vault_path
        goals_folder = vault / _GOALS_FOLDER
        goals_folder.mkdir(parents=True, exist_ok=True)

        safe_title = re.sub(r'[^a-zA-Z0-9\s-]', '', title).strip().replace(' ', '-')[:40]
        short_id = goal_id[-8:] if len(goal_id) > 8 else goal_id
        filename = f"goal-{short_id}-{safe_title}.md"
        goal_path = goals_folder / filename

        status = "✅ Voltooid" if failed == 0 else ("⚠️ Deels voltooid" if completed > 0 else "❌ Mislukt")

        content = (
            f"# {title}\n\n"
            f"- **Status**: {status}\n"
            f"- **Project**: {project}\n"
            f"- **Doel**: {objective}\n"
            f"- **Fasen**: {phase_count}\n"
            f"- **Taken**: {completed}/{task_count} voltooid, {failed} mislukt\n"
            f"- **Datum**: {datetime.now().isoformat()}\n\n"
            f"## Samenvatting\n\n{summary}\n"
        )
        try:
            goal_path.write_text(content, encoding="utf-8")
        except OSError as e:
            logger.warning(f"Kon goal-log niet schrijven naar {goal_path}: {e}")
            return None

        # Dagboek notificatie
        self._append_to_daily_log(
            f"### {status}: **{title}**\n"
            f"- {completed}/{task_count} taken voltooid ({failed} mislukt)\n"
            f"- Doel: {objective[:100]}\n"
            f"- [[{goal_path.relative_to(vault).as_posix().replace('.md', '')}]]\n\n"
        )

        # ── OMI — memory aanmaken ──────────────────────────────────
        if self._omi.is_configured:
            try:
                omi_content = (
                    f"{status}: {title}\n"
                    f"Doel: {objective}\n"
                    f"{completed}/{task_count} taken voltooid in {phase_count} fasen"
                )
                self._omi.create_memory(omi_content, category="work")
            except Exception as e:
                logger.debug(f"OMI goal-memory aanmaken mislukt: {e}")

        return goal_path

    def log_agent_session(self, title: str, summary: str,
                          project: str = "", tags: Optional[List[str]] = None) -> None:
        """Log een vrije-vorm agent-sessie naar het dagboek + OMI.

        Gebruik dit voor ad-hoc agent-runs die geen deel uitmaken van een goal.
        """
        tag_str = ""
        if tags:
            tag_str = " ".join(f"#{t}" for t in tags)
        if project:
            tag_str += f" #{project.replace(' ', '_')}"

        # Obsidian dagboek
        if self.is_configured:
            self._append_to_daily_log(
                f"### 🧠 Sessie: {title}\n"
                f"{tag_str}\n"
                f"{summary[:500].strip()}\n\n"
            )

        # OMI memory
        if self._omi.is_configured:
            try:
                omi_content = (
                    f"Agent sessie: {title}\n"
                    f"{summary[:500].strip()}"
                )
                self._omi.create_memory(omi_content, category="learnings")
            except Exception as e:
                logger.debug(f"OMI session-memory aanmaken mislukt: {e}")

    # ── Daily log helpers ────────────────────────────────────────────

    def _get_todays_log(self) -> str:
        """Lees vandaag's dagboek (leeg als die niet bestaat)."""
        today = date.today().strftime("%Y-%m-%d")
        log_path = self._vault_path / _SESSIONS_FOLDER / f"{today}.md"
        if log_path.exists():
            try:
                return log_path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                return ""
        return ""

    def _append_to_daily_log(self, entry: str) -> None:
        """Append een entry naar vandaag's dagboek. Maak het bestand aan als het nog niet bestaat."""
        if not self.is_configured:
            return

        vault = self._vault_path
        sessions_folder = vault / _SESSIONS_FOLDER
        sessions_folder.mkdir(parents=True, exist_ok=True)

        today = date.today().strftime("%Y-%m-%d")
        log_path = sessions_folder / f"{today}.md"

        if log_path.exists():
            try:
                existing = log_path.read_text(encoding="utf-8")
            except OSError:
                existing = ""
        else:
            existing = (
                f"# AgentOS — {today}\n\n"
                f"Automatisch agent-activiteitenlogboek.\n\n"
                f"---\n\n"
            )

        try:
            log_path.write_text(existing + "\n" + entry + "\n", encoding="utf-8")
        except OSError as e:
            logger.warning(f"Kon dagboek niet schrijven naar {log_path}: {e}")

    # ── Vault samenvatting (voor onboarding) ──────────────────────────

    def summarize_vault(self, project: str = "", max_notes: int = 5) -> str:
        """Geef een overzicht van de vault — wat staat erin, wat zijn de belangrijkste notities.

        Gebruik dit in een system prompt zodat een agent weet wat er in de vault staat
        zonder alles te hoeven lezen.
        """
        if not self.is_configured:
            return ""

        obs = self._get_obsidian()
        keywords = project or ""
        results = obs.search(keywords or "project notitie merk", top_k=max_notes)

        if not results:
            return ""

        parts = [
            f"## Obsidian vault overzicht ({project or 'algemeen'})" if project
            else "## Obsidian vault overzicht"
        ]

        for r in results:
            parts.append(
                f"### {r['file']}\n"
                f"📁 `{r['path']}`\n"
                f"Relevantie: {r['score']:.2f}\n\n"
                f"{r['snippet'][:400]}\n"
            )

        return "\n\n".join(parts)


def _skill_to_omi_category(skill: str) -> str:
    """Vertaal een AgentOS skill naar een OMI memory category."""
    mapping = {
        "research":       "learnings",
        "content-writer": "work",
        "content-editor": "work",
        "content-judge":  "work",
        "seo":            "work",
        "video-builder":  "work",
        "video-director": "work",
        "outreach":       "work",
        "publisher":      "work",
        "analyst":        "work",
        "designer":       "work",
    }
    return mapping.get(skill, "other")
