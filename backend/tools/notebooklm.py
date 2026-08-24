"""NotebookLM-client voor Impact OS.

Spreekt de `notebooklm-mcp` MCP-server aan in Streamable-HTTP modus
(standaard op poort 3137, gestart vanuit impactos_service.cmd). De server
drijft een echte Chrome via Patchright en laat ons RAG- onderzoek doen
tegen onze eigen NotebookLM-notebooks (met citations), bronnen injecteren
en Audio Overviews genereren.

Waarom HTTP i.p.v. stdio: Impact OS is een langlopende FastAPI-server.
Een stdio-subprocess per call betekent Chrome telkens opstarten (~10-30s);
een warme HTTP-server antwoordt in milliseconden op het JSON-RPC-laagje.
De client is bewust SYNC (requests) — de caller (researcher-service) doet
het async-werk en blokkeert de event-loop niet.

Alle antwoorden van NotebookLM dragen een `_provenance`-envelop (provider
google-notebooklm, model gemini-2.5, grounding user-documents). Wij
zetten NOTEBOOKLM_AI_MARKER=false zodat het antwoord schoon blijft en
wij de citations/bronnen zelf structureren.
"""
from __future__ import annotations

import json
import logging
import re
import time
from typing import Dict, List, Optional

import requests

log = logging.getLogger(__name__)

# Gebruik dezelfde env-namen als notebooklm-mcp zelf kent, zodat de
# server (die deze al leest) en de client (deze module) niet uit sync raken.
import os

DEFAULT_BASE_URL = os.environ.get("NOTEBOOKLM_BASE_URL", "http://127.0.0.1:3137")
_DEFAULT_TIMEOUT = 120  # NotebookLM kan traag zijn (Gemini + DOM-crawl)


class NotebookLMError(RuntimeError):
    """Opgetuigde fout uit de MCP-laag (of lege respons)."""


# Proces-brede MCP-sessiecache: notebooklm-mcp v2.0 laat maar één
# initialize per proces toe (zie NotebookLMClient.connect).
import threading

_SHARED_LOCK = threading.Lock()
_SHARED_SIDS: Dict[str, str] = {}


def _strip_sse(text: str) -> str:
    """Haal de JSON-body uit een SSE-antwoord ('event: message\\ndata: {...}')."""
    if "data:" not in text:
        return text.strip()
    out = []
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("data:"):
            out.append(line[len("data:"):].strip())
    return "\n".join(out)


class NotebookLMClient:
    """Dunne wrapper om de notebooklm-mcp HTTP-server.

    Gebruik als context-manager (houdt één MCP-sessie per client) of roep
    ``connect()`` / ``close()`` handmatig aan.
    """

    def __init__(self, base_url: str = DEFAULT_BASE_URL, timeout: int = _DEFAULT_TIMEOUT):
        self.base = base_url.rstrip("/")
        self.timeout = timeout
        self._sid: Optional[str] = None
        self._session = requests.Session()
        self._notifications_sent = False

    # ── Sessie-leven ────────────────────────────────────────────────
    def connect(self) -> None:
        """MCP initialize + initialized-notification.

        BELANGRIJK (bewezen 2026-08-04): notebooklm-mcp v2.0 accepteert per
        proces maar ÉÉN ``initialize``. Een tweede initialize levert HTTP 500
        ("Already connected to a transport") en dus géén Mcp-Session-Id — dat
        was de oorzaak van de terugkerende kaart "Kennisronde mislukt".
        Daarom cachen we de sessie-id proces-breed per base_url en hergebruiken
        we die in elke volgende client.
        """
        if self._sid is not None:
            return
        with _SHARED_LOCK:
            cached = _SHARED_SIDS.get(self.base)
            if cached:
                self._sid = cached
                self._notifications_sent = True
                return
            self._sid = self._initialize()
            _SHARED_SIDS[self.base] = self._sid

    def _initialize(self) -> str:
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "impactos", "version": "1.0"},
            },
        }
        resp = self._session.post(
            f"{self.base}/mcp",
            json=payload,
            headers={"Accept": "application/json, text/event-stream"},
            timeout=self.timeout,
        )
        sid = resp.headers.get("Mcp-Session-Id") or resp.headers.get("mcp-session-id")
        if not sid:
            raise NotebookLMError(
                "Geen Mcp-Session-Id ontvangen van notebooklm-mcp "
                f"(HTTP {resp.status_code}: {resp.text[:160]})"
            )
        # Initialized-notification (geen id → geen respons verwacht).
        self._session.post(
            f"{self.base}/mcp",
            json={"jsonrpc": "2.0", "method": "notifications/initialized"},
            headers={"Accept": "application/json, text/event-stream",
                     "Content-Type": "application/json",
                     "Mcp-Session-Id": sid},
            timeout=self.timeout,
        )
        self._notifications_sent = True
        return sid

    def _headers(self) -> Dict[str, str]:
        h = {"Accept": "application/json, text/event-stream",
              "Content-Type": "application/json"}
        if self._sid:
            h["Mcp-Session-Id"] = self._sid
        return h

    def close(self) -> None:
        """Laat de gedeelde MCP-sessie bewust OPEN.

        De sessie is proces-breed gedeeld (zie connect); hem hier via DELETE
        sluiten zou elke volgende kennisronde in hetzelfde proces breken,
        omdat de server geen tweede initialize accepteert.
        """
        self._sid = None

    def __enter__(self) -> "NotebookLMClient":
        self.connect()
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ── Low-level tool-call ─────────────────────────────────────────
    def _call(self, tool: str, args: Dict, attempt: int = 1) -> str:
        if not self._sid:
            self.connect()
        payload = {
            "jsonrpc": "2.0",
            "id": int(time.time() * 1000) % 1_000_000 + attempt,
            "method": "tools/call",
            "params": {"name": tool, "arguments": args},
        }
        try:
            resp = self._session.post(
                f"{self.base}/mcp", json=payload,
                headers=self._headers(), timeout=self.timeout,
            )
        except requests.exceptions.Timeout:
            raise NotebookLMError(
                f"NotebookLM timeout ({self.timeout}s) op tool '{tool}'. "
                f"Controleer of de Chrome-sessie levend is."
            )
        # Self-heal: sessie verlopen of server herstart (404/400 op de
        # sessie-id). Gooi de gedeelde cache weg en probeer één keer opnieuw.
        if resp.status_code in (400, 404) and attempt == 1:
            with _SHARED_LOCK:
                _SHARED_SIDS.pop(self.base, None)
            self._sid = None
            log.warning("[notebooklm] MCP-sessie verlopen (HTTP %s) — opnieuw verbinden",
                        resp.status_code)
            return self._call(tool, args, attempt=2)
        raw = resp.text
        body = _strip_sse(raw)
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            raise NotebookLMError(f"Ongeldig JSON-RPC antwoord van '{tool}': {body[:300]}")
        if "error" in data:
            raise NotebookLMError(f"MCP-fout op '{tool}': {data['error']}")
        # De server geeft meestal {'result': {'content': [{'type':'text','text':...}]}}
        result = data.get("result", {})
        content = result.get("content", [])
        if isinstance(content, str):
            return content
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
        return "\n".join(parts).strip()

    # ── High-level helpers ───────────────────────────────────────────
    @staticmethod
    def _unwrap(raw: str) -> Dict:
        """Normaliseer de NotebookLM-response.

        De server geeft soms ``{"success":true,"data":{...}}`` terug,
        soms een bloot ``{"answer":..., "sources":...}`` (docs-schema),
        soms platte tekst. We pakken altijd het rijkste genestte object.
        """
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {"raw": raw}
        if not isinstance(parsed, dict):
            return {"raw": raw}
        # Voorkeur: data.*-envelop
        if isinstance(parsed.get("data"), dict):
            return parsed["data"]
        return parsed

    def health(self) -> Dict:
        return self._unwrap(self._call("get_health", {}))

    def list_notebooks(self) -> List[Dict]:
        data = self._unwrap(self._call("list_notebooks", {}))
        # list_notebooks geeft soms {"notebooks":[...]} direct, soms in data.*
        if "notebooks" in data:
            return data["notebooks"]
        if isinstance(data.get("data"), dict) and "notebooks" in data["data"]:
            return data["data"]["notebooks"]
        return []

    def select_notebook(self, notebook_id: str) -> Dict:
        return {"result": self._call("select_notebook", {"id": notebook_id})}

    def ask(self, question: str, notebook_id: Optional[str] = None,
            source_format: str = "footnotes") -> Dict:
        """Stel een vraag aan een notebook (RAG, grounded op de bronnen).

        Retourneert altijd {'answer': str, 'sources': [...], 'session_id': str,
        'raw': str} — ongeacht welk schema de server teruggeeft.
        """
        args: Dict = {"question": question, "source_format": source_format}
        if notebook_id:
            args["notebook_id"] = notebook_id
        data = self._unwrap(self._call("ask_question", args))
        # NB: NIET nog een keer _call doen voor 'raw' — dat zou de vraag
        # dubbel stellen en de NotebookLM-dagquota (50 queries) halveren.
        return {
            "answer": data.get("answer", ""),
            "sources": data.get("sources", []),
            "session_id": data.get("session_id", ""),
            "notebook_url": data.get("notebook_url", ""),
            "raw": data.get("raw", "") if "answer" not in data else "",
        }

    def add_source_text(self, title: str, text: str,
                       notebook_id: Optional[str] = None) -> Dict:
        args = {"type": "text", "title": title, "text": text}
        if notebook_id:
            args["notebook_id"] = notebook_id
        return self._unwrap(self._call("add_source", args))

    def add_source_url(self, url: str, notebook_id: Optional[str] = None) -> Dict:
        args = {"type": "url", "url": url}
        if notebook_id:
            args["notebook_id"] = notebook_id
        return self._unwrap(self._call("add_source", args))

    def generate_audio(self, notebook_id: Optional[str] = None,
                      custom_prompt: str = "", timeout_ms: int = 600_000) -> Dict:
        args: Dict = {"timeout_ms": timeout_ms}
        if notebook_id:
            args["notebook_id"] = notebook_id
        if custom_prompt:
            args["custom_prompt"] = custom_prompt
        return self._unwrap(self._call("generate_audio", args))


# ── Agentic-loop tool ───────────────────────────────────────────────
import asyncio
from concurrent.futures import ThreadPoolExecutor
from .base import Tool, ToolResult
from ..shared.config import (
    NOTEBOOKLM_BASE_URL, NOTEBOOKLM_TIMEOUT, NOTEBOOKLM_ENABLED,
)

_executor = ThreadPoolExecutor(max_workers=2)


class NotebookLMResearchTool(Tool):
    """Onderzoek-agent: stel een vraag aan een NotebookLM-notebook.

    Het antwoord is RAG — gegrond op JOUW eigen bronnen in dat
    notebook (blog posts, KB, concurrentie-analyse), niet op het open web.
    Gebruik dit voor diepte-onderzoek dat op jouw kennis baseert, en
    ``web_search`` voor actuele feiten van buitenaf.
    """
    name = "notebooklm_research"
    description = (
        "Stel een onderzoeksvraag aan een van Vincents NotebookLM-notebooks "
        "(RAG — gegrond op de eigen bronnen: blog posts, KB-artikelen, "
        "concurrentie-analyses). Geeft een antwoord MET bronvermelding "
        "(titel + excerpt per citaat). Gebruik dit voor diepte-onderzoek dat "
        "op de eigen kennis baseert; gebruik web_search voor vers frije "
        "feiten van het open web. Parameters: question (verplicht), "
        "notebook_id (optioneel — laat leeg voor de standaard SEO-notebook)."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "question": {"type": "string",
                         "description": "De onderzoeksvraag, in het Nederlands."},
            "notebook_id": {
                "type": "string",
                "description": ("Notebook-id uit de library (bijv. "
                               "'weareimpact-seo-research', 'weareimpact-podcast'). "
                               "Leég = standaard SEO-notebook."),
                "default": "",
            },
        },
        "required": ["question"],
    }

    async def run(self, question: str, notebook_id: str = "") -> ToolResult:
        if not NOTEBOOKLM_ENABLED:
            return ToolResult(self.name, "NotebookLM-agent staat uit (NOTEBOOKLM_ENABLED=0).", error=True)
        nb = notebook_id or None
        loop = asyncio.get_event_loop()
        try:
            result = await loop.run_in_executor(
                _executor, self._sync_ask, question, nb,
            )
        except Exception as e:
            return ToolResult(self.name, f"NotebookLM-fout: {e}", error=True)
        answer = result.get("answer", "")
        sources = result.get("sources", [])
        if not answer:
            return ToolResult(self.name, "NotebookLM gaf geen antwoord (auth/timeout?).", error=True)
        parts = [f"# NotebookLM-onderzoek\n\n**Vraag:** {question}\n"]
        if sources:
            parts.append("**Geraadpleegde bronnen:**")
            for s in sources:
                ex = (s.get("excerpt") or "").strip()
                parts.append(f"- {s.get('title', '?')}" + (f": {ex}" if ex else ""))
            parts.append("")
        parts.append(answer)
        return ToolResult(self.name, "\n".join(parts))

    @staticmethod
    def _sync_ask(question: str, notebook_id: Optional[str]) -> Dict:
        with NotebookLMClient(base_url=NOTEBOOKLM_BASE_URL,
                             timeout=NOTEBOOKLM_TIMEOUT) as c:
            return c.ask(question, notebook_id=notebook_id)

