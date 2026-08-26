"""Meeting-notulen — een transcript wordt samengevat en actiepunten worden
CRM-taken. Elke test monkeypatcht `_llm` expliciet: deze module raakt de
echte LLM-gateway aan en een ongemockte aanroep in een testrun zou echte
tokens/kosten en non-determinisme introduceren (zelfde risico als bij
outreach/followup elders in de suite)."""
import json

import pytest


def _mock_llm(response_json):
    async def _llm(system, prompt, max_tokens=800, purpose=""):
        return json.dumps(response_json) if response_json is not None else ""
    return _llm


def test_create_note_rejects_empty_fields(clean_tables):
    from backend.domains.notes import service as notes
    with pytest.raises(ValueError):
        import asyncio
        asyncio.run(notes.maak_notitie("", "iets"))


@pytest.mark.asyncio
async def test_maak_notitie_creates_summary_and_tasks(clean_tables, monkeypatch):
    import backend.domains.publish.content_pipeline as content_pipeline
    monkeypatch.setattr(content_pipeline, "_llm", _mock_llm({
        "summary": "Kort gesprek over de scope van het project.",
        "action_items": ["Stuur offerte naar klant", "Plan vervolgafspraak"],
    }))

    from backend.domains.notes import service as notes
    from backend.domains.crm import service as crm

    company = crm.create_company("NotulenBedrijf")
    note = await notes.maak_notitie(
        "Kennismaking NotulenBedrijf", "Vincent: ... Klant: ...", company_id=company["id"],
    )

    assert note["status"] == "samengevat"
    assert note["summary"] == "Kort gesprek over de scope van het project."
    assert len(note["action_items"]) == 2

    taken = crm.list_tasks(status="open")
    titels = {t["title"] for t in taken}
    assert {"Stuur offerte naar klant", "Plan vervolgafspraak"} <= titels
    assert all(t["company_id"] == company["id"] for t in taken)


@pytest.mark.asyncio
async def test_maak_notitie_zonder_actiepunten_maakt_geen_taken(clean_tables, monkeypatch):
    import backend.domains.publish.content_pipeline as content_pipeline
    monkeypatch.setattr(content_pipeline, "_llm", _mock_llm({
        "summary": "Alleen een update, geen toezeggingen.",
        "action_items": [],
    }))

    from backend.domains.notes import service as notes
    from backend.domains.crm import service as crm

    note = await notes.maak_notitie("Statusupdate", "transcript hier")
    assert note["status"] == "samengevat"
    assert note["action_items"] == []
    assert crm.list_tasks(status="open") == []


@pytest.mark.asyncio
async def test_maak_notitie_faalt_luid_op_leeg_llm_antwoord(clean_tables, monkeypatch):
    import backend.domains.publish.content_pipeline as content_pipeline
    monkeypatch.setattr(content_pipeline, "_llm", _mock_llm(None))

    from backend.domains.notes import service as notes
    from backend.shared.database import get_conn

    note = await notes.maak_notitie("Mislukt gesprek", "transcript")
    assert note["status"] == "mislukt"
    assert note["summary"] == ""

    with get_conn() as conn:
        log = conn.execute(
            "SELECT * FROM activity_log WHERE action = 'notulen_samenvatten_mislukt'"
        ).fetchone()
    assert log is not None
    assert log["status"] == "error"


@pytest.mark.asyncio
async def test_maak_notitie_faalt_luid_op_onleesbaar_json(clean_tables, monkeypatch):
    async def _rommel_llm(system, prompt, max_tokens=800, purpose=""):
        return "dit is geen json"
    import backend.domains.publish.content_pipeline as content_pipeline
    monkeypatch.setattr(content_pipeline, "_llm", _rommel_llm)

    from backend.domains.notes import service as notes
    note = await notes.maak_notitie("Rommelgesprek", "transcript")
    assert note["status"] == "mislukt"


@pytest.mark.asyncio
async def test_list_notes_filters_by_status(clean_tables, monkeypatch):
    import backend.domains.publish.content_pipeline as content_pipeline
    monkeypatch.setattr(content_pipeline, "_llm", _mock_llm({"summary": "ok", "action_items": []}))

    from backend.domains.notes import service as notes
    await notes.maak_notitie("Goed gesprek", "transcript")

    monkeypatch.setattr(content_pipeline, "_llm", _mock_llm(None))
    await notes.maak_notitie("Mislukt gesprek", "transcript")

    assert len(notes.list_notes("samengevat")) == 1
    assert len(notes.list_notes("mislukt")) == 1
    assert len(notes.list_notes()) == 2
