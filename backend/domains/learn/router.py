"""Knowledge Forge — FastAPI-router (/api/learn).

Eindpunten:
  POST /api/learn            body {source: "<pad-of-url>"}  -> leer een document
  POST /api/learn/ask        body {query, top_k?}           -> retrieval + cheat/glossary
  GET  /api/learn/documents  -> lijst geleerde documenten
  DELETE /api/learn/{doc_id} -> document vergeten

De router gebruikt de centrale ImpactOS-LLM (iris.service._llm) als extractie-
motor, met een OpenModel-directe fallback zodat /learn ook werkt als de
Claude-route tijdelijk weg is. Alles draait lokaal/privé: geen document
verlaat de machine behalve naar de LLM die je toch al gebruikt.

Dit is de ImpactOS-tegenhanger van Hermes' "/learn" — maar dan met echte
embeddings-retrieval én een gestructureerde brain file (index/glossary/
cheat-sheet) die in de Obsidian-vault wordt geschreven (SSOT).
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ...knowledge_forge import learn_document, ask, list_documents, delete_document
from ...shared.config import OBSIDIAN_VAULT_PATH

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/learn", tags=["knowledge-forge"])


# ── LLM-adapter (centrale ImpactOS-LLM, met fallback) ────────────────────────

async def _llm_call(system: str, prompt: str, max_tokens: int = 2000) -> Optional[str]:
    """Centrale ImpactOS-LLM voor de brain-file extractie.

    Volgorde (robust, geen enkele failure mag de ingest breken):
      1. OpenModel-gateway (de centrale ImpactOS-LLM-route, werkt altijd
         zolang de key er is — géén fragile iris/chat import nodig).
      2. iris.service._llm (Claude eerst, Hermes-terugval) als secundair.
      3. None -> forge valt terug op de naïeve extractie (nooit leeg).
    """
    # 1) OpenModel (primair)
    try:
        from ...shared.config import (OPENMODEL_API_KEY, OPENMODEL_BASE_URL,
                                       OPENMODEL_MODEL, OPENMODEL_SMART_MODEL)
        if OPENMODEL_API_KEY:
            import httpx
            base = OPENMODEL_BASE_URL.rstrip("/")
            # Probeer primair model, daarna het smart-model als fallback
            # (deepseek-v4-flash kan tijdelijk dood zijn op de gateway).
            for model in (OPENMODEL_MODEL, OPENMODEL_SMART_MODEL):
                if not model:
                    continue
                try:
                    r = httpx.post(
                        f"{base}/v1/chat/completions",
                        headers={"Authorization": f"Bearer {OPENMODEL_API_KEY}",
                                  "Content-Type": "application/json"},
                        json={"model": model, "max_tokens": max_tokens,
                              "messages": [{"role": "system", "content": system},
                                           {"role": "user", "content": prompt}]},
                        timeout=90,
                    )
                    if r.status_code == 200:
                        return r.json()["choices"][0]["message"]["content"]
                    logger.warning("[forge-api] OpenModel (%s) gaf HTTP %s", model, r.status_code)
                except Exception as e:
                    logger.warning("[forge-api] OpenModel (%s) fout: %s", model, e)
    except Exception as e:
        logger.warning("[forge-api] OpenModel primair faalde: %s", e)
    # 2) Claude direct via chat-domain (geen fragile iris-import; werkt zolang
    #    de Claude-key er is). Valt anders terug op Hermes (lokale Ollama).
    try:
        from ..chat import claude as claude_service
        if claude_service.is_configured():
            out = await claude_service.get_response(
                messages=[{"role": "system", "content": system},
                          {"role": "user", "content": prompt}],
                max_tokens=max_tokens,
            )
            if out:
                return out
    except Exception as e:
        logger.warning("[forge-api] Claude direct faalde: %s", e)
    # 3) Hermes (lokale Ollama) als laatste LLM-poging
    try:
        from ..chat import hermes as hermes_service
        full = ""
        async for chunk in hermes_service.stream_response(
            messages=[{"role": "user", "content": prompt}],
            system=system, max_tokens=max_tokens,
        ):
            if isinstance(chunk, dict) and chunk.get("type") == "text":
                full += chunk.get("text", "")
        if full.strip():
            return full
    except Exception as e:
        logger.warning("[forge-api] Hermes direct faalde: %s", e)
    return None


# ── Schemas ──────────────────────────────────────────────────────────────────

class LearnRequest(BaseModel):
    source: str  # pad (.pdf/.docx/.md/.txt) of http(s)-URL


class AskRequest(BaseModel):
    query: str
    top_k: int = 5


# ── Routes ───────────────────────────────────────────────────────────────────

@router.post("")
async def learn(req: LearnRequest, request: Request):
    vault = OBSIDIAN_VAULT_PATH
    result = await learn_document(req.source, _llm_call, vault_path=vault)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("reason", "mislukt"))
    return result


@router.post("/ask")
async def learn_ask(req: AskRequest):
    if not req.query or not req.query.strip():
        raise HTTPException(status_code=400, detail="query verplicht")
    return ask(req.query, top_k=max(1, min(20, req.top_k)))


@router.get("/documents")
async def learn_list():
    return list_documents()


class CompareRequest(BaseModel):
    doc_a: str  # doc_id van het eerste document
    doc_b: str  # doc_id van het tweede document


@router.post("/compare")
async def learn_compare(req: CompareRequest):
    if not req.doc_a or not req.doc_b:
        raise HTTPException(status_code=400, detail="doc_a en doc_b verplicht")
    result = compare(req.doc_a, req.doc_b)
    if not result.get("ok"):
        raise HTTPException(status_code=404, detail=result.get("reason", "niet gevonden"))
    return result


@router.delete("/{doc_id}")
async def learn_delete(doc_id: str):
    ok = delete_document(doc_id)
    if not ok:
        raise HTTPException(status_code=404, detail="document niet gevonden")
    return {"ok": True, "deleted": doc_id}
