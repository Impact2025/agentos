"""Knowledge Forge — document-leren voor Impact OS.

Publieke API:
  - learn_document(source, llm_call, ...)  -> ingest + index + brain file
  - ask(query, top_k)                      -> retrieval + cheat/glossary
  - list_documents() / delete_document()

Zie forge.py voor de volledige pijplijn en het ontwerp.
"""
from .forge import (
    learn_document,
    ask,
    list_documents,
    delete_document,
    compare,
    ensure_schema,
)
from .embeddings import get_embedder, EmbeddingEngine

__all__ = [
    "learn_document",
    "ask",
    "list_documents",
    "delete_document",
    "ensure_schema",
    "get_embedder",
    "EmbeddingEngine",
]
