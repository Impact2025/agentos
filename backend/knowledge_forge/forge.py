"""Knowledge Forge — de orchestrator.

Pijplijn (de "/learn" van AgentOS, maar wereldklasse & privé):

    source (.pdf/.docx/.md/.txt/url)
        │
        ├─ read_document()         → platte tekst
        ├─ split_into_chunks()     → structurele chunks (per hoofdstuk)
        ├─ extract_brain_file()    → index + glossary + cheat_sheet (LLM, m. terugval)
        ├─ embed(chunks)           → vectors (Ollama-dense óf sparse-cosine)
        │
        └─ persist()  → SQLite (knowledge_forge_documents + _chunks)
                        + vault-note (de brain file, leesbaar voor Vincent)

Daarna: ask(query) haalt de meest relevante chunks (semantisch) én toont de
cheat-sheet/glossary (gestructureerd). Dat is precies wat de Hermes-video
belooft: de agent kijkt in de index, opent alleen de relevante sectie, en
antwoordt vanuit die sectie — zónder het document opnieuw te lezen.

Storage: SQLite in de bestaande agentos.db (geen nieuwe infra). Vectors als
JSON-blob per chunk. Voor een grote vault zou je naar pgvector/chroma
migreren, maar het contract (embed + similarity) maakt dat later pijnloos.

Defensief: een rotbestand breekt de hele sync niet; een LLM-storing levert
een naïeve brain file; een embed-fout valt terug op sparse.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .embeddings import get_embedder
from .readers import read_document, split_into_chunks
from .brain_file import extract_brain_file, _extract_json

logger = logging.getLogger(__name__)

_VAULT_NOTE_DIR = "AgentOS/KnowledgeForge"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()[:16]


# ── DB schema (idempotent) ───────────────────────────────────────────────────

def ensure_schema(conn) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS knowledge_forge_documents (
            id TEXT PRIMARY KEY,
            title TEXT,
            source TEXT,
            source_path TEXT,
            content_hash TEXT,
            brain_file TEXT,            -- JSON: index/glossary/cheat_sheet
            chunk_count INTEGER,
            provider TEXT,             -- 'ollama' | 'sparse'
            created_at TEXT,
            updated_at TEXT,
            active INTEGER DEFAULT 1
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS knowledge_forge_chunks (
            id TEXT PRIMARY KEY,
            doc_id TEXT,
            idx INTEGER,
            heading TEXT,
            text TEXT,
            vector TEXT,               -- JSON array of floats (of leeg bij sparse-fout)
            FOREIGN KEY (doc_id) REFERENCES knowledge_forge_documents(id)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_forge_chunk_doc ON knowledge_forge_chunks(doc_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_forge_doc_source ON knowledge_forge_documents(source_path)")


# ── Ingest ───────────────────────────────────────────────────────────────────

async def learn_document(source: str, llm_call, vault_path: str = "",
                         ollama_base: str = "", ollama_model: str = "llama3.1",
                         conn=None) -> Dict[str, Any]:
    """Leer een document. Retourneert een rapport.

    ``llm_call(system, prompt, max_tokens) -> Optional[str]`` — de centrale
    AgentOS-LLM. ``vault_path`` — als gezet, schrijft de brain file als
    vault-note (Vincent-leesbaar, SSOT).
    """
    own_conn = conn is None
    if own_conn:
        from ..shared.database import get_conn
        with get_conn() as conn:
            return await _run_learn(conn, source, llm_call, vault_path)
    return await _run_learn(conn, source, llm_call, vault_path)


async def _run_learn(conn, source, llm_call, vault_path: str = "") -> Dict[str, Any]:
    try:
        title, text = read_document(source)
        if len(text) < 40:
            return {"ok": False, "reason": "onleesbaar of te kort document",
                    "source": source}
        chunks = split_into_chunks(text)
        if not chunks:
            return {"ok": False, "reason": "geen chunks uit document", "source": source}

        # Brain file (LLM + terugval)
        brain = await extract_brain_file(title, chunks, llm_call)

        # Embeddings (Ollama-dense óf sparse-cosine met terugval)
        embedder = get_embedder()
        # fit sparse op dit document voor betere IDF (goedkoop)
        if embedder.provider != "ollama":
            embedder._sparse.fit([c["text"] for c in chunks])
        vectors = embedder.embed([c["text"] for c in chunks])
        brain["provider"] = embedder.provider

        # Persist
        doc_id = _persist(conn, source, title, text, chunks, brain, vectors, embedder.provider)

        # Vault-note (SSOT, leesbaar)
        vault_note_path = ""
        if vault_path:
            vault_note_path = _write_vault_note(vault_path, title, source, brain, chunks)

        conn.commit()
        return {
            "ok": True,
            "doc_id": doc_id,
            "title": title,
            "chunks": len(chunks),
            "glossary_terms": len(brain["glossary"]),
            "cheat_rules": len(brain["cheat_sheet"]),
            "index_sections": len(brain["index"]),
            "provider": embedder.provider,
            "llm_used": brain.get("llm_used", False),
            "vault_note": vault_note_path,
        }
    except Exception as e:
        logger.exception("[forge] learn_document crash: %s", e)
        return {"ok": False, "reason": f"exception: {e}", "source": source}


# ── Persist ──────────────────────────────────────────────────────────────────

def _persist(conn, source, title, text, chunks, brain, vectors, provider) -> str:
    ensure_schema(conn)
    h = _hash(text)
    source_path = source if not source.startswith("http") else ""
    existing = conn.execute(
        "SELECT id, content_hash, active FROM knowledge_forge_documents WHERE source_path = ?",
        (source_path,),
    ).fetchone() if source_path else None

    # Alleen "niets gewijzigd" als de rij actief én de hash gelijk is.
    # Een inactieve rij (verwijderd via /api) of een nieuwe hash => volledige re-ingest.
    if existing and existing["active"] == 1 and existing["content_hash"] == h:
        conn.execute(
            "UPDATE knowledge_forge_documents SET updated_at=? WHERE id=?",
            (_now(), existing["id"]),
        )
        return existing["id"]

    doc_id = existing["id"] if existing else str(uuid.uuid4())
    # chunks van een eerdere versie wissen (verse insert)
    conn.execute("DELETE FROM knowledge_forge_chunks WHERE doc_id = ?", (doc_id,))

    conn.execute(
        """INSERT OR REPLACE INTO knowledge_forge_documents
           (id, title, source, source_path, content_hash, brain_file, chunk_count,
            provider, created_at, updated_at, active)
           VALUES (?,?,?,?,?,?,?,?,?,?,1)""",
        (doc_id, title[:200], source[:500], source_path, h,
         json.dumps(brain, ensure_ascii=False), len(chunks), provider,
         _now(), _now()),
    )
    for c, vec in zip(chunks, vectors):
        conn.execute(
            """INSERT INTO knowledge_forge_chunks (id, doc_id, idx, heading, text, vector)
               VALUES (?,?,?,?,?,?)""",
            (str(uuid.uuid4()), doc_id, c["index"], c.get("heading", "")[:160],
             c["text"], json.dumps(vec) if vec else "[]"),
        )
    return doc_id


def _write_vault_note(vault_path: str, title: str, source: str,
                      brain: Dict, chunks: List[dict]) -> str:
    try:
        vault = Path(vault_path)
        folder = vault / _VAULT_NOTE_DIR
        folder.mkdir(parents=True, exist_ok=True)
        safe = re.sub(r"[^a-zA-Z0-9 _-]", "", title)[:60].strip().replace(" ", "-")
        path = folder / f"{safe}.md"
        lines = [f"# 📚 Knowledge Forge — {title}", ""]
        lines.append(f"- **Bron**: `{source}`")
        lines.append(f"- **Geleerd**: {_now()}")
        lines.append(f"- **Embedding-provider**: {brain.get('provider','?')}")
        lines.append(f"- **LLM-extractie**: {'ja' if brain.get('llm_used') else 'nee (naïef)'}")  # noqa
        lines.append("")
        lines.append("## Cheat Sheet (direct toepasbaar)")
        for r in brain.get("cheat_sheet", []):
            lines.append(f"- {r}")
        lines.append("")
        lines.append("## Index")
        for s in brain.get("index", []):
            lines.append(f"### {s.get('section','')}")
            if s.get("summary"):
                lines.append(s["summary"])
        lines.append("")
        lines.append("## Glossary")
        for g in brain.get("glossary", []):
            lines.append(f"- **{g.get('term','')}**: {g.get('definition','')}")
        lines.append("")
        lines.append("---")
        lines.append(f"_Automatisch gegenereerd door AgentOS Knowledge Forge. "
                     f"{len(chunks)} chunks geïndexeerd._")
        path.write_text("\n".join(lines), encoding="utf-8")
        return str(path)
    except Exception as e:
        logger.warning("[forge] vault-note schrijven mislukt: %s", e)
        return ""


# ── Retrieval ───────────────────────────────────────────────────────────────

def ask(query: str, top_k: int = 5, conn=None) -> Dict[str, Any]:
    """Beantwoord een vraag uit geleerde documenten.

    Combineert:
      - semantische chunk-retrieval (embeddings + cosine)
      - de gestructureerde cheat-sheet/glossary (altijd meegegeven)

    Retourneert {answer_context, sources[], cheat_sheet[], glossary[]}.
    """
    own_conn = conn is None
    if own_conn:
        from ..shared.database import get_conn
        with get_conn() as conn:
            return _run_ask(conn, query, top_k)
    return _run_ask(conn, query, top_k)


def _run_ask(conn, query: str, top_k: int = 5) -> Dict[str, Any]:
    try:
        ensure_schema(conn)
        # Alle chunks van actieve documenten
        rows = conn.execute(
            """SELECT c.doc_id, c.idx, c.heading, c.text, c.vector, d.title
               FROM knowledge_forge_chunks c
               JOIN knowledge_forge_documents d ON d.id = c.doc_id
               WHERE d.active = 1"""
        ).fetchall()
        if not rows:
            return {"answer_context": "", "sources": [], "cheat_sheet": [],
                    "glossary": [], "provider": get_embedder().provider}

        embedder = get_embedder()
        q_vec = embedder.embed([query])[0]

        scored = []
        for r in rows:
            vec = json.loads(r["vector"]) if r["vector"] not in ("", "[]") else []
            sim = embedder.similarity(q_vec, vec) if vec else 0.0
            scored.append((sim, r))
        scored.sort(key=lambda x: x[0], reverse=True)

        top = scored[:top_k]
        sources = []
        ctx_parts = []
        for sim, r in top:
            if sim <= 0.0 and embedder.provider == "sparse":
                # bij sparse kan 0 voorkomen bij geen overlap — toch tonen als
                # er weinig chunks zijn (fallback op top-k op volgorde)
                pass
            head = r["heading"] or f"(sectie {r['idx']})"
            sources.append({"doc": r["title"], "section": head, "score": round(sim, 4)})
            ctx_parts.append(f"### {head} ({r['title']})\n{r['text']}")

        # Brain files van de top-documenten voor cheat/glossary
        cheat, glossary = [], []
        seen_docs = set()
        for _, r in top:
            if r["doc_id"] in seen_docs:
                continue
            seen_docs.add(r["doc_id"])
            rec = conn.execute(
                "SELECT brain_file FROM knowledge_forge_documents WHERE id = ?",
                (r["doc_id"],)).fetchone()
            if rec and rec["brain_file"]:
                bf = json.loads(rec["brain_file"])
                cheat.extend(bf.get("cheat_sheet", []))
                glossary.extend(bf.get("glossary", []))

        return {
            "answer_context": "\n\n".join(ctx_parts),
            "sources": sources,
            "cheat_sheet": cheat[:12],
            "glossary": glossary[:12],
            "provider": embedder.provider,
        }
    except Exception:
        raise


# ── Document-vergelijking (de video's "vergelijk twee boeken"-feature) ────────

def compare(doc_a_id: str, doc_b_id: str, conn=None) -> Dict[str, Any]:
    """Vergelijk twee geleerde documenten.

    Vindt:
      - gedeelde glossary-termen (beide docs noemen het)
      - disagreements: een term met een ándere definitie in beide docs
      - unieke inzichten: cheat-regels / secties die maar in één doc zitten
      - semantische overlap: cosine tussen de chunk-wolken van beide docs

    Retourneert een gestructureerd vergelijkingsrapport. Dit is de AgentOS-
    tegenhanger van de Hermes-"compare two books"-belofte (die nog niet live
    was in de video).
    """
    own = conn is None
    if own:
        from ..shared.database import get_conn
        with get_conn() as conn:
            return _compare(conn, doc_a_id, doc_b_id)
    return _compare(conn, doc_a_id, doc_b_id)


def _compare(conn, doc_a_id: str, doc_b_id: str) -> Dict[str, Any]:
    ensure_schema(conn)
    embedder = get_embedder()

    def _doc(doc_id):
        row = conn.execute(
            "SELECT id, title, brain_file FROM knowledge_forge_documents WHERE id=?",
            (doc_id,)).fetchone()
        if not row:
            return None
        bf = json.loads(row["brain_file"] or "{}")
        # chunk-vectoren voor semantische overlap
        chunks = conn.execute(
            "SELECT text, vector FROM knowledge_forge_chunks WHERE doc_id=?",
            (doc_id,)).fetchall()
        return {
            "id": row["id"], "title": row["title"],
            "glossary": bf.get("glossary", []),
            "cheat": bf.get("cheat_sheet", []),
            "index": bf.get("index", []),
            "chunks": [(c["text"], json.loads(c["vector"]) if c["vector"] not in ("", "[]") else [])
                       for c in chunks],
        }

    a, b = _doc(doc_a_id), _doc(doc_b_id)
    if not a or not b:
        return {"ok": False, "reason": "een of beide documenten niet gevonden"}

    # Glossary-vergelijking
    terms_a = {g["term"].lower(): g["definition"] for g in a["glossary"]}
    terms_b = {g["term"].lower(): g["definition"] for g in b["glossary"]}
    shared_terms = sorted(set(terms_a) & set(terms_b))
    disagreements = []
    for t in shared_terms:
        da, db = terms_a[t].strip(), terms_b[t].strip()
        # simpele disagree-detectie: definities verschillen significant
        if da and db and da[:40].lower() != db[:40].lower():
            disagreements.append({
                "term": t,
                "def_a": da[:200],
                "def_b": db[:200],
            })
    unique_a = sorted(set(terms_a) - set(terms_b))
    unique_b = sorted(set(terms_b) - set(terms_a))

    # Cheat-sheet verschillen (unieke inzichten)
    cheat_a = set(c[:60].lower() for c in a["cheat"])
    cheat_b = set(c[:60].lower() for c in b["cheat"])
    unique_cheat_a = [c for c in a["cheat"] if c[:60].lower() in (cheat_a - cheat_b)]
    unique_cheat_b = [c for c in b["cheat"] if c[:60].lower() in (cheat_b - cheat_a)]

    # Semantische overlap: gemiddelde max-cosine tussen chunk-wolken
    def _cloud(chunks):
        return [v for _, v in chunks if v]
    cloud_a, cloud_b = _cloud(a["chunks"]), _cloud(b["chunks"])
    overlap = 0.0
    if cloud_a and cloud_b:
        best = []
        for va in cloud_a:
            scores = [embedder.similarity(va, vb) for vb in cloud_b]
            best.append(max(scores))
        overlap = sum(best) / len(best)

    return {
        "ok": True,
        "doc_a": a["title"], "doc_b": b["title"],
        "shared_terms": shared_terms,
        "disagreements": disagreements,
        "unique_terms_a": unique_a,
        "unique_terms_b": unique_b,
        "unique_insights_a": unique_cheat_a[:8],
        "unique_insights_b": unique_cheat_b[:8],
        "semantic_overlap": round(overlap, 3),
        "provider": embedder.provider,
    }

def list_documents(conn=None) -> List[Dict]:
    own = conn is None
    if own:
        from ..shared.database import get_conn
        with get_conn() as conn:
            return _list_docs(conn)
    return _list_docs(conn)


def _list_docs(conn) -> List[Dict]:
    ensure_schema(conn)
    out = []
    for r in conn.execute(
        "SELECT id, title, source, chunk_count, provider, updated_at "
        "FROM knowledge_forge_documents WHERE active=1 "
        "ORDER BY updated_at DESC"
    ).fetchall():
        rec = dict(r)
        # llm_used zit in de brain_file-JSON; eruit halen voor de API
        try:
            bf = json.loads(rec.get("brain_file") or "{}")
            rec["llm_used"] = bf.get("llm_used", False)
        except Exception:
            rec["llm_used"] = False
        out.append(rec)
    return out


def delete_document(doc_id: str, conn=None) -> bool:
    own = conn is None
    if own:
        from ..shared.database import get_conn
        with get_conn() as conn:
            return _delete_doc(conn, doc_id)
    return _delete_doc(conn, doc_id)


def _delete_doc(conn, doc_id: str) -> bool:
    ensure_schema(conn)
    conn.execute("UPDATE knowledge_forge_documents SET active=0 WHERE id=?", (doc_id,))
    conn.commit()
    return True
