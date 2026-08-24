import base64
import json
from pathlib import Path
from fastapi import APIRouter, HTTPException, UploadFile, File, Form
from fastapi.responses import StreamingResponse, JSONResponse
from ...shared.models import ChatRequest
from ...domains.chat import service as memory_service
from ...domains.chat import journey as journey_service
from ...domains.chat import hermes as hermes_service
from ...domains.chat.upload import upload_root
from ...domains.finance.prompts import FINANCE_DAILY_SYSTEM
from ...shared.agent_runner import run_agent
from ...domains.chat.obsidian import ObsidianService
from ...shared.config import OBSIDIAN_VAULT_PATH

router = APIRouter(prefix="/api/chat", tags=["chat"])

# Claude/Anthropic-vision accepteert per afbeelding maximaal ~5 MB aan ruwe
# bytes (base64 groeit dat nog eens 33%); een grotere upload negeren we voor
# de LLM-call in plaats van de gateway een oversized request te laten weigeren.
_MAX_VISION_IMAGE_BYTES = 5 * 1024 * 1024


def _image_content_blocks(attachments: list) -> tuple:
    """Bouwt Anthropic-content-blocks uit geüploade afbeeldingen + een lijst
    bestandsnamen die zijn overgeslagen (te groot of onvindbaar) voor een
    nette melding aan de gebruiker."""
    blocks = []
    skipped = []
    root = upload_root()
    for a in attachments or []:
        content_type = a.get("content_type", "")
        if not content_type.startswith("image/"):
            continue
        filename = a.get("filename") or "afbeelding"
        url = a.get("url", "")
        path = root / Path(url).name
        try:
            data = path.read_bytes()
        except OSError:
            skipped.append(filename)
            continue
        if len(data) > _MAX_VISION_IMAGE_BYTES:
            skipped.append(filename)
            continue
        blocks.append({
            "type": "image",
            "source": {"type": "base64", "media_type": content_type, "data": base64.b64encode(data).decode()},
        })
    return blocks, skipped

_obsidian = ObsidianService(OBSIDIAN_VAULT_PATH)

_BASE_SYSTEM = """Je bent een intelligente AI-assistent in het Impact OS dashboard.
Je bent behulpzaam, beknopt en nauwkeurig. Antwoord altijd in het Nederlands tenzij de gebruiker een andere taal gebruikt.
Gebruik markdown voor je antwoorden. Vermeld altijd de taal in codeblokken.

Je hebt toegang tot tools. Gebruik ze proactief als ze relevant is.
- obsidian_search: zoek in de persoonlijke kennisbank van de gebruiker
- obsidian_write: sla informatie of gegenereerde content op in de vault
- web_search: zoek actuele informatie op het web
- create_task: maak een taak aan in het Kanban-bord
- list_tasks: bekijk de huidige taken
- calendar_create: zet een afspraak of terugkerend blok in de agenda vanuit een vrije zin (bv. "blok elke maandag 08.30-10.00 voor Focustijd", "dinsdag 18 augustus 12.15 tandarts"). De afspraak komt als voorstel in het Actiecentrum en is met één tik geboekt. Zeg NOOIT dat je geen agenda-tool hebt of dat de gebruiker het zelf in Google Calendar moet zetten — roep gewoon calendar_create aan met de volledige zin.

## Afbeeldingen (bv. een rooster)
Kan de gebruiker een afbeelding uploaden, dan zie je die letterlijk (vision) — niet alleen een bestandsnaam. Bij een rooster, planning of agenda-foto: lees elke losse afspraak (dag, datum indien zichtbaar, tijd, omschrijving/locatie) en roep voor élke afspraak apart `calendar_create` aan met een volledige zin ("maandag 25 augustus 09.00-10.30 Wiskunde"). Herhaal dit voor alle regels die je herkent — verzin geen datum/tijd die je niet kunt lezen, vraag dan liever om verduidelijking. Sluit af met een korte opsomming van wat je hebt voorgesteld; elke afspraak wacht nog op goedkeuring in het Actiecentrum, dus zeg dat er niets automatisch geboekt is.
- delegate: delegeer een grote opdracht aan een team parallelle achtergrond-workers
- delegation_status: bekijk de voortgang van een eerder gestarte delegatie

Combineer meerdere tools als dat de gebruiker beter helpt. Wees transparant over wat je doet.

## Delegeren (Lead Agent-gedrag)
Jij bent de Lead Agent (orchestrator). Bij een grote opdracht die uiteenvalt in
ONAFHANKELIJKE deeltaken
splits je die zelf op in 2-6 concrete workers en roep je `delegate` aan:
- Eén worker voor keyword/markt-research, en aparte workers per blogpost/onderdeel.
- Formuleer per worker een strak, self-contained doel met het verwachte eindproduct.
- Geef een `cta` mee (de conversiehook naar community/product) die elke worker moet verweven.
`delegate` blokkeert NIET: zodra je het hebt aangeroepen, vat je voor de gebruiker
kort samen welke workers nu in de achtergrond draaien — wacht niet op resultaten,
die verschijnen vanzelf als zelfstandige berichten in het dashboard.

## Denkproces (voor Mission Control)
Voordat je een tool aanroept of een beslissing neemt, formuleer je eerst een korte 'Thought':
één à twee zinnen waarin je uitlegt waaróm je deze stap zet en welk resultaat je verwacht.
Schrijf die Thought als gewone tekst vlak vóór de tool-aanroep. Dit stelt Mission Control in
staat om je logica te mappen. Als een tool een fout of leeg resultaat teruggeeft, meld dat
expliciet en verzin geen gegevens — beschrijf liever wat er misging.
"""

_FINANCE_SYSTEM = FINANCE_DAILY_SYSTEM

# Spraakgesprek: het antwoord wordt hardop voorgelezen (TTS) én als lopende
# tekst getoond, niet als opgemaakt document. Markdown-koppen, streepjes-lijstjes
# en emoji's zijn dan ruis die de spraaklaag letterlijk uitspreekt of onvertaald
# op het scherm laat staan ("hekje hekje", "sterretje sterretje"). Dezelfde
# persona als de dagelijkse briefing (Iris), zodat "met wie praat ik" overal
# hetzelfde antwoord heeft.
_VOICE_SYSTEM = """Je bent Iris, de manager-agent van Impact OS — dezelfde Iris die de dagelijkse briefing schrijft. Dit gesprek is een gesproken gesprek: je antwoord wordt hardop voorgelezen en verschijnt daarnaast als lopende tekst op het scherm.
Antwoord daarom altijd in vloeiend, zakelijk Nederlands spreektaal-proza: geen markdown-koppen (#, ##), geen bullet- of streepjeslijstjes, geen sterretjes voor vet of cursief, geen emoji's, geen codeblokken tenzij er expliciet om code wordt gevraagd. Noem meerdere punten in gewone zinnen ("Ten eerste... Daarnaast... Tot slot...") in plaats van een lijst. Wees kort en ter zake — een paar zinnen, geen lap tekst.

Je hebt toegang tot tools. Gebruik ze proactief als dat relevant is.
- obsidian_search: zoek in de persoonlijke kennisbank van de gebruiker
- obsidian_write: sla informatie of gegenereerde content op in de vault
- web_search: zoek actuele informatie op het web
- create_task: maak een taak aan in het Kanban-bord
- list_tasks: bekijk de huidige taken
- calendar_create: zet een afspraak of terugkerend blok in de agenda vanuit een vrije zin. De afspraak komt als voorstel in het Actiecentrum en is met één tik geboekt. Zeg nooit dat je geen agenda-tool hebt.
- delegate: delegeer een grote opdracht aan een team parallelle achtergrond-workers
- delegation_status: bekijk de voortgang van een eerder gestarte delegatie

Combineer meerdere tools als dat de gebruiker beter helpt. Wees transparant over wat je doet, maar altijd in gewoon gesproken Nederlands, nooit in opgemaakte tekst.
"""


def _build_system_prompt(query: str, use_obsidian: bool, agent: str = "claude", voice: bool = False) -> str:
    if agent == "finance":
        prompt = _FINANCE_SYSTEM
    elif voice:
        prompt = _VOICE_SYSTEM
        if use_obsidian and _obsidian.is_configured:
            context = _obsidian.build_context(query)
            if context:
                prompt += f"\n\nContext uit de Obsidian-vault (automatisch gevonden, gebruik dit als achtergrond, lees het niet letterlijk voor):\n\n{context}"
    else:
        prompt = _BASE_SYSTEM
        if use_obsidian and _obsidian.is_configured:
            context = _obsidian.build_context(query)
            if context:
                prompt += f"\n\n## Context uit Obsidian vault (automatisch gevonden)\n\n{context}"
    return prompt


@router.post("/stream")
async def stream_chat(req: ChatRequest):
    session = memory_service.get_session(req.session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Sessie niet gevonden")

    history = memory_service.get_messages_for_api(req.session_id)
    system_prompt = _build_system_prompt(req.message, req.use_obsidian, req.agent, req.voice)

    # Circuit-breaker: interactieve chat ook onder de dagquota houden.
    # Zonder deze check ramde één drukke sessie 49 calls/uur op het dure
    # model (incident 2026-07-10). Bij een provider-403-quota geeft de
    # router een leesbare fout i.p.v. de kale browser-modal.
    from ...shared.outcomes import require_llm_budget
    try:
        require_llm_budget("chat")
    except Exception as e:
        raise HTTPException(
            status_code=429,
            detail=f"LLM-daglimiet bereikt: {e}",
        )

    # Vision vergt zowel het Anthropic-blokformaat (OpenModel-gateway; de
    # OpenAI-compat-fallbacks verwachten platte string-content en crashen op
    # een blokken-lijst) als een model dat beelddata écht leest — geverifieerd
    # via OPENMODEL_VISION_MODEL, zie shared/config.py voor waarom dat leeg
    # start. Zonder allebei lezen we de afbeelding niet, met een duidelijke
    # melding i.p.v. een kapotte call of een LLM die doet alsof.
    from ...shared.config import hermes_backend, OPENMODEL_VISION_MODEL
    vision_available = hermes_backend() == "openmodel" and bool(OPENMODEL_VISION_MODEL)
    image_blocks, skipped_images = _image_content_blocks(req.attachments) if vision_available else ([], [])
    if not vision_available and any(
        a.get("content_type", "").startswith("image/") for a in (req.attachments or [])
    ):
        skipped_images = [a.get("filename", "afbeelding") for a in (req.attachments or []) if a.get("content_type", "").startswith("image/")]
    if image_blocks:
        text = req.message or "Wat staat er op deze afbeelding? Als het een rooster of planning is, zet de afspraken in mijn agenda."
        llm_content = image_blocks + [{"type": "text", "text": text}]
    else:
        llm_content = req.message
    history.append({"role": "user", "content": llm_content})
    # DB-geheugen bewaart bewust geen ruwe base64 (elke volgende beurt zou dan
    # steeds meer afbeeldingdata meesturen naar de LLM) — alleen een tekstspoor.
    persisted = req.message
    if image_blocks or skipped_images:
        names = ", ".join(a.get("filename", "afbeelding") for a in (req.attachments or []) if a.get("content_type", "").startswith("image/"))
        persisted = (f"{persisted}\n\n" if persisted else "") + f"[afbeelding geüpload: {names}]"
    memory_service.add_message(req.session_id, "user", persisted)

    journey_id = journey_service.start_journey(
        session_id=req.session_id,
        agent=req.agent,
        model=hermes_service.active_model(),
        user_message=req.message,
    )

    def _record(seq: int, event: dict) -> None:
        """Best-effort: logging mag de stream nooit breken."""
        try:
            etype = event.get("type", "")
            if etype == "tool_start":
                journey_service.record_event(
                    journey_id, seq, "tool_start", name=event.get("name", ""),
                    content=json.dumps(event.get("input", {}), ensure_ascii=False),
                )
            elif etype == "tool_result":
                journey_service.record_event(
                    journey_id, seq, "tool_result", name=event.get("name", ""),
                    content=event.get("output", ""), is_error=event.get("error", False),
                )
            elif etype in ("thought", "text"):
                journey_service.record_event(journey_id, seq, etype, content=event.get("text", ""))
            elif etype == "error":
                journey_service.record_event(
                    journey_id, seq, "error", content=event.get("message", ""), is_error=True,
                )
        except Exception:
            pass

    async def event_stream():
        full_response = ""
        seq = 0
        had_error = False
        total_tokens = 0
        text_buf = ""

        def flush_text(s: int) -> int:
            nonlocal text_buf
            if text_buf:
                _record(s, {"type": "text", "text": text_buf})
                text_buf = ""
                return s + 1
            return s

        if skipped_images:
            note = "⚠︎ " + ", ".join(skipped_images) + " kon ik niet lezen — er is nog geen vision-model geconfigureerd (of het bestand was te groot/onvindbaar, max 5 MB). Typ de inhoud even uit, dan werk ik daarmee verder.\n\n"
            yield f"data: {json.dumps({'type': 'text', 'text': note})}\n\n"
            full_response += note
            text_buf += note

        try:
            model_override = OPENMODEL_VISION_MODEL if image_blocks else None
            async for event in run_agent(
                history, system_prompt, req.agent, model_override=model_override,
                purpose=f"chat:{req.agent}",
            ):
                etype = event.get("type")
                if etype == "text":
                    full_response += event["text"]
                    text_buf += event["text"]
                elif etype == "usage":
                    total_tokens += event.get("total_tokens", 0) or 0
                else:
                    seq = flush_text(seq)
                    _record(seq, event)
                    seq += 1
                    if etype == "error":
                        had_error = True
                yield f"data: {json.dumps(event)}\n\n"
            seq = flush_text(seq)
        except Exception as e:
            had_error = True
            seq = flush_text(seq)
            journey_service.record_event(journey_id, seq, "error", content=str(e), is_error=True)
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"
        finally:
            if full_response:
                memory_service.add_message(req.session_id, "assistant", full_response)
            journey_service.finish_journey(
                journey_id,
                status="error" if had_error else "done",
                final_text=full_response,
                total_tokens=total_tokens,
            )
            yield "data: [DONE]\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
