"""Tests voor Knowledge Forge — geen externe LLM/embed-deps vereist.

Dekt:
- readers: markdown + pdf-native extractie + structure-aware chunking
- embeddings: sparse-vector cosine werkt zonder Ollama
- brain_file: naïeve terugval (geen LLM) levert geldige structuur
- forge: end-to-end ingest + ask() retrieval tegen de test-DB
"""
import os
import textwrap
from pathlib import Path

import pytest


# ── Fixtures ─────────────────────────────────────────────────────────────────

_SAMPLE_MD = textwrap.dedent("""
# Schrijf-DNA Vincent

Dit is de schrijfstijl van Vincent. Hij schrijft in de ik-persoon.

## Ervarings-Carrousel

De Ervarings-Carrousel is een techniek waarbij je de lezer meeneemt via
persoonlijke ervaringen in plaats van droge theorie.

- Begin altijd met een concrete situatie uit de praktijk.
- Wissel af tussen vertellen en adviseren.
- Eindig met een herkenbaar resultaat.

## E-E-A-T

E-E-A-T — Experience, Expertise, Authoritativeness, Trustworthiness. Google gebruikt dit om content te beoordelen.

- Laat echte ervaring zien met voorbeelden.
- Bouw autoriteit op via consistente publicatie.

## SEO-richtlijnen

Schrijf voor mensen, niet voor robots. Gebruik de zoekintentie als leidraad.
""").strip()


@pytest.fixture()
def sample_md(tmp_path):
    p = tmp_path / "schrijf-dna.md"
    p.write_text(_SAMPLE_MD, encoding="utf-8")
    return str(p)


# ── Readers ──────────────────────────────────────────────────────────────────

def test_read_markdown_returns_title_and_text(sample_md):
    from backend.knowledge_forge.readers import read_document
    title, text = read_document(sample_md)
    assert "Schrijf-DNA Vincent" in title
    assert "Ervarings-Carrousel" in text
    assert "E-E-A-T" in text


def test_pdf_native_extracts_text(tmp_path):
    from backend.knowledge_forge.readers import _read_pdf_native
    # Minimale PDF met een Tj-string-operatie
    pdf = tmp_path / "t.pdf"
    pdf.write_bytes(b"%PDF-1.4\n1 0 obj<</Length 40>>stream\nBT (Hallo Wereld) Tj ET\nendstream\nendobj\n")
    out = _read_pdf_native(pdf)
    assert "Hallo" in out and "Wereld" in out


def test_chunking_respects_structure(sample_md):
    from backend.knowledge_forge.readers import read_document, split_into_chunks
    _, text = read_document(sample_md)
    chunks = split_into_chunks(text)
    assert len(chunks) >= 3
    # elke chunk heeft een heading of is de eerste (titel-loos maar ok)
    headings = {c["heading"] for c in chunks if c["heading"]}
    assert "Ervarings-Carrousel" in headings or any("Ervarings" in h for h in headings)
    # chunks zijn niet belachelijk groot
    assert all(len(c["text"]) <= 2000 for c in chunks)


# ── Embeddings (sparse, geen Ollama) ────────────────────────────────────────

def test_sparse_embedder_cosine_similarity():
    from backend.knowledge_forge.embeddings import SparseEmbedder
    e = SparseEmbedder()
    a = e.embed_one("de ervarings-carrousel techniek voor het schrijven van blogs")
    b = e.embed_one("schrijven van blogs met de ervarings-carrousel methode")
    c = e.embed_one("belastingaangifte box drie voor zzp ers berekenen voordeel")
    sim_ab = e.similarity(a, b)
    sim_ac = e.similarity(a, c)
    # semantisch verwante teksten scoren hoger dan volstrekte vreemden
    assert sim_ab > sim_ac
    assert 0.0 <= sim_ab <= 1.0


def test_embedding_engine_falls_back_to_sparse_without_ollama():
    from backend.knowledge_forge.embeddings import EmbeddingEngine
    # geen base-url => forced sparse
    eng = EmbeddingEngine(ollama_base="", ollama_model="llama3.1")
    vecs = eng.embed(["ervarings-carrousel schrijven", "seo richtlijnen google"])
    assert eng.provider == "sparse"
    assert len(vecs) == 2
    assert eng.similarity(vecs[0], vecs[0]) > 0.99  # zichzelf == 1


# ── Brain file (naïeve terugval zonder LLM) ─────────────────────────────────

def test_naive_brain_file_from_markdown(sample_md):
    from backend.knowledge_forge.readers import read_document, split_into_chunks
    from backend.knowledge_forge.brain_file import _naive_brain_file
    _, text = read_document(sample_md)
    chunks = split_into_chunks(text)
    bf = _naive_brain_file("Schrijf-DNA Vincent", chunks)
    assert bf["llm_used"] is False
    # index gebouwd uit koppen
    assert any("Ervarings" in s["section"] for s in bf["index"])
    # cheat-sheet uit lijst-items
    assert any("Begin altijd" in r for r in bf["cheat_sheet"])
    # glossary uit "Term — def"
    assert any(g["term"] == "E-E-A-T" for g in bf["glossary"])


# ── End-to-end ingest + retrieval (geen LLM => naïef, sparse) ───────────────

async def test_learn_and_ask_end_to_end(sample_md, tmp_path):
    from backend.knowledge_forge import learn_document, ask
    from backend.shared.database import get_conn

    async def no_llm(system, prompt, max_tokens=2000):
        return None  # dwing naïeve terugval af

    vault = str(tmp_path / "vault")
    result = await learn_document(sample_md, no_llm, vault_path=vault)
    assert result["ok"] is True
    assert result["chunks"] >= 3
    # provider is 'sparse' (geen Ollama) of 'ollama' (dense via de
    # ingestelde embeddings-backend) — beide zijn een geldig resultaat.
    assert result["provider"] in ("sparse", "ollama")
    assert result["llm_used"] is False
    assert result["vault_note"]  # brain file geschreven naar vault

    # vault-note bestaat en bevat cheat-sheet
    note = Path(result["vault_note"])
    assert note.exists()
    content = note.read_text(encoding="utf-8")
    assert "Cheat Sheet" in content
    assert "Knowledge Forge" in content

    # ask() haalt de juiste sectie op voor een gerichte vraag
    res = ask("Wat is de Ervarings-Carrousel?", top_k=3)
    assert res["answer_context"]
    assert any("Ervarings" in s["section"] for s in res["sources"])
    assert res["cheat_sheet"]  # cheat-sheet altijd meegeleverd


async def test_learn_idempotent_on_same_content(sample_md):
    from backend.knowledge_forge import learn_document, list_documents
    from backend.shared.database import get_conn

    async def no_llm(system, prompt, max_tokens=2000):
        return None

    r1 = await learn_document(sample_md, no_llm)
    docs1 = [d for d in list_documents() if d["source"] == sample_md]
    assert len(docs1) == 1
    r2 = await learn_document(sample_md, no_llm)
    docs2 = [d for d in list_documents() if d["source"] == sample_md]
    # zelfde hash => geen dubbele documenten voor deze exacte bron
    assert len(docs2) == 1
    assert r2["doc_id"] == r1["doc_id"]


async def test_learn_unreadable_returns_error(tmp_path):
    from backend.knowledge_forge import learn_document
    bad = tmp_path / "leeg.pdf"
    bad.write_bytes(b"%PDF-1.4")  # geen tekstlaag

    async def no_llm(system, prompt, max_tokens=2000):
        return None

    result = await learn_document(str(bad), no_llm)
    assert result["ok"] is False
    assert "onleesbaar" in result["reason"]
