"""Iris' kennisbank: onderzoek van Vincent distilleren, opslaan en toepassen."""
import json
import os

import pytest


@pytest.fixture()
def know_clean(conn, clean_tables):
    yield
    from backend.shared.database import get_conn
    with get_conn() as c:
        c.execute("DELETE FROM iris_knowledge")


def _mock_distiller(monkeypatch, tags=None, scope="all"):
    """Laat de LLM een vaste distillatie teruggeven."""
    from backend.domains.iris import service
    payload = json.dumps({
        "samenvatting": "GEO draait om zichtbaarheid in AI-antwoorden.",
        "principes": ["Schrijf citeerbare, feitelijke alinea's",
                      "Gebruik duidelijke vraag-kop + kort antwoord"],
        "tags": tags if tags is not None else ["geo", "seo"],
        "scope": scope,
    })

    async def fake_llm(system, prompt, max_tokens=1500):
        return payload
    monkeypatch.setattr(service, "_llm", fake_llm)


@pytest.mark.asyncio
async def test_distill_fallback_zonder_llm(know_clean, monkeypatch):
    from backend.domains.iris import service, knowledge

    async def empty_llm(system, prompt, max_tokens=1500):
        return ""
    monkeypatch.setattr(service, "_llm", empty_llm)

    body = ("# GEO onderzoek\n\nEen intro-zin over GEO.\n\n"
            "## Wees citeerbare\n- Gebruik statistieken met bron\n- Kort antwoord bovenaan")
    result = await knowledge._distill("GEO onderzoek", body)
    assert result["summary"]
    assert any("citeerbare" in p.lower() or "statistieken" in p.lower() for p in result["principles"])
    assert result["scope"] == "all"


@pytest.mark.asyncio
async def test_add_manual_note_en_ophalen(know_clean, monkeypatch):
    from backend.domains.iris import knowledge
    _mock_distiller(monkeypatch, tags=["geo"], scope="all")

    kid = await knowledge.add_manual_note("GEO-onderzoek", "Uitgebreid onderzoek naar "
                                          "Generative Engine Optimization en AI-search zichtbaarheid.")
    assert kid
    items = knowledge.list_knowledge()
    assert len(items) == 1
    assert items[0]["title"] == "GEO-onderzoek"
    assert "geo" in items[0]["tags"]
    assert items[0]["principles"]


@pytest.mark.asyncio
async def test_add_manual_note_weigert_te_kort(know_clean, monkeypatch):
    from backend.domains.iris import knowledge
    _mock_distiller(monkeypatch)
    assert await knowledge.add_manual_note("x", "te kort") is None


@pytest.mark.asyncio
async def test_guidance_filtert_op_tag_en_scope(know_clean, monkeypatch):
    from backend.domains.iris import knowledge
    # GEO-kennis (relevant voor schrijven) + finance-kennis (niet).
    _mock_distiller(monkeypatch, tags=["geo"], scope="all")
    await knowledge.add_manual_note("GEO", "Onderzoek naar generative engine optimization en meer.")
    _mock_distiller(monkeypatch, tags=["finance"], scope="all")
    await knowledge.add_manual_note("Finance", "Iets over kwartaalcijfers en rapportage enzovoort.")

    guidance = knowledge.guidance_for_writing("WeAreImpact")
    assert "citeerbare" in guidance.lower()
    # De prompt-block voor Iris zelf bevat alle actieve kennis.
    block = knowledge.knowledge_prompt_block()
    assert "GEO" in block and "Finance" in block


@pytest.mark.asyncio
async def test_scope_beperkt_tot_project(know_clean, monkeypatch):
    from backend.domains.iris import knowledge
    _mock_distiller(monkeypatch, tags=["seo"], scope="Bijeen")
    await knowledge.add_manual_note("Bijeen-tactiek", "Specifiek onderzoek voor het Bijeen-project en zo.")
    # Andere projecten zien deze kennis niet in de schrijf-guidance.
    assert knowledge.guidance_for_writing("WeAreImpact") == ""
    assert "citeerbare" in knowledge.guidance_for_writing("Bijeen").lower()


@pytest.mark.asyncio
async def test_sync_vault_ingest_update_en_deactivate(know_clean, monkeypatch, tmp_path):
    from backend.domains.iris import knowledge
    _mock_distiller(monkeypatch)
    # Nep-vault met de kennisbank-map.
    vault = tmp_path / "vault"
    (vault / knowledge.KNOWLEDGE_DIRNAME).mkdir(parents=True)
    monkeypatch.setattr(knowledge, "OBSIDIAN_VAULT_PATH", str(vault))

    doc = vault / knowledge.KNOWLEDGE_DIRNAME / "geo.md"
    doc.write_text("# GEO\n\nOnderzoek naar generative engine optimization met veel details erin.",
                   encoding="utf-8")

    r1 = await knowledge.sync_knowledge()
    assert r1["ingested"] == 1
    assert len(knowledge.list_knowledge()) == 1

    # Ongewijzigd → skip.
    r2 = await knowledge.sync_knowledge()
    assert r2["skipped"] == 1 and r2["ingested"] == 0

    # Gewijzigd → update.
    doc.write_text("# GEO v2\n\nHerzien onderzoek met nieuwe inzichten en aanvullende details hierin.",
                   encoding="utf-8")
    r3 = await knowledge.sync_knowledge()
    assert r3["updated"] == 1

    # Verwijderd → gedeactiveerd (niet meer actief in de lijst).
    doc.unlink()
    r4 = await knowledge.sync_knowledge()
    assert r4["deactivated"] == 1
    assert len(knowledge.list_knowledge()) == 0


@pytest.mark.asyncio
async def test_sync_leest_ook_pdf(know_clean, monkeypatch, tmp_path):
    from backend.domains.iris import knowledge
    _mock_distiller(monkeypatch)
    vault = tmp_path / "vault"
    (vault / knowledge.KNOWLEDGE_DIRNAME).mkdir(parents=True)
    monkeypatch.setattr(knowledge, "OBSIDIAN_VAULT_PATH", str(vault))

    # Minimale echte PDF met een tekstlaag via pypdf/reportlab-vrije bytes:
    # we schrijven een PDF met genoeg tekst door pypdf's writer te gebruiken.
    from pypdf import PdfWriter
    import io
    # pypdf kan geen tekst tekenen; gebruik een vooraf bekende kleine PDF-bytes
    # met tekst is te complex. In plaats daarvan monkeypatchen we _read_pdf zodat
    # we het pad (glob → _read_doc → distill → upsert) toetsen, niet pypdf zelf.
    pdf = vault / knowledge.KNOWLEDGE_DIRNAME / "geo-briefing.pdf"
    pdf.write_bytes(b"%PDF-1.4 dummy")
    monkeypatch.setattr(knowledge, "_read_pdf",
                        lambda p: "Uitgebreide GEO-briefing met veel bruikbare inzichten over AI-search.")

    r = await knowledge.sync_knowledge()
    assert r["ingested"] == 1
    items = knowledge.list_knowledge()
    assert len(items) == 1
    # PDF-titel komt van de bestandsnaam (stem), niet uit de tekst.
    assert items[0]["title"] == "geo-briefing"


@pytest.mark.asyncio
async def test_sync_telt_onleesbare_pdf(know_clean, monkeypatch, tmp_path):
    from backend.domains.iris import knowledge
    _mock_distiller(monkeypatch)
    vault = tmp_path / "vault"
    (vault / knowledge.KNOWLEDGE_DIRNAME).mkdir(parents=True)
    monkeypatch.setattr(knowledge, "OBSIDIAN_VAULT_PATH", str(vault))
    (vault / knowledge.KNOWLEDGE_DIRNAME / "leeg.pdf").write_bytes(b"%PDF-1.4")
    monkeypatch.setattr(knowledge, "_read_pdf", lambda p: "")  # geen tekstlaag
    r = await knowledge.sync_knowledge()
    assert r["ingested"] == 0 and r["unreadable"] == 1


@pytest.mark.asyncio
async def test_knowledge_endpoints(know_clean, monkeypatch):
    from fastapi.testclient import TestClient
    from backend.main import app
    from backend.domains.iris import knowledge
    _mock_distiller(monkeypatch)

    client = TestClient(app)
    r = client.get("/api/iris/knowledge")
    assert r.status_code == 200 and "items" in r.json()

    r = client.post("/api/iris/knowledge", json={"title": "GEO",
                    "text": "Onderzoek naar generative engine optimization en AI-search zichtbaarheid enzo."})
    assert r.status_code == 200
    kid = r.json()["id"]

    r = client.delete("/api/iris/knowledge/" + kid)
    assert r.status_code == 200
    r = client.delete("/api/iris/knowledge/" + kid)
    assert r.status_code == 404


def test_guidance_defensief_bij_content_pipeline(know_clean):
    """De content-pipeline-hook mag nooit crashen, ook zonder kennis."""
    from backend.domains.publish.content_pipeline import _iris_writing_guidance
    assert _iris_writing_guidance("WeAreImpact") == ""
