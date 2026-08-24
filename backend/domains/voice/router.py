"""Voice API — spraaksynthese + status + gallery voor de Voice-tab (Apollo-achtige laag).

Endpoints:
  GET  /api/voice/status     -> welke engines beschikbaar zijn (edge-tts / elevenlabs)
  POST /api/voice/speak      -> { text, voice? } -> MP3-audio (audio/mpeg)
  GET  /api/voice/artifacts  -> gallery: wat de voice-laag bouwde (nieuwst eerst)
  POST /api/voice/artifact   -> sla een gebouwd artifact op in de gallery
"""
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

from ...shared.database import get_conn
from .tts import EDGE_NL_VOICE, EDGE_NL_VOICE_ALT, elevenlabs_ready, tts
from .session_log import log_session_to_obsidian, obsidian_configured

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/voice", tags=["voice"])


class SpeakRequest(BaseModel):
    text: str
    voice: str = ""  # leeg = default NL-stem; anders edge-tts voice-naam


class ArtifactRequest(BaseModel):
    project: str = ""
    goal_id: str = ""
    title: str
    transcript: str = ""
    artifact: str = ""
    artifact_type: str = "goal"  # goal | note | page | tool
    status: str = "created"


class LogSessionRequest(BaseModel):
    project: str = ""
    title: str = "Voice-sessie"
    transcript: str
    answer: str = ""
    goal_id: str = ""


@router.get("/status")
def voice_status():
    return {
        "edge_tts_nl": True,
        "edge_voices": [EDGE_NL_VOICE, EDGE_NL_VOICE_ALT],
        "elevenlabs": elevenlabs_ready(),
        "default_voice": EDGE_NL_VOICE,
    }


@router.post("/speak")
def voice_speak(body: SpeakRequest):
    text = (body.text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Leeg bericht")
    if len(text) > 4000:
        text = text[:4000]

    try:
        audio = tts(text, body.voice)
    except Exception as e:
        logger.exception("TTS mislukt")
        raise HTTPException(status_code=502, detail=f"TTS mislukt: {e}")

    return Response(content=audio, media_type="audio/mpeg")


@router.get("/artifacts")
def list_artifacts(limit: int = 50, project: str = ""):
    try:
        with get_conn() as conn:
            if project:
                rows = conn.execute(
                    "SELECT * FROM voice_artifacts WHERE project = ? "
                    "ORDER BY created_at DESC LIMIT ?",
                    (project, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM voice_artifacts ORDER BY created_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            return [dict(r) for r in rows]
    except Exception as e:
        logger.exception("Kon gallery niet lezen")
        raise HTTPException(status_code=500, detail=str(e)[:200])


@router.post("/artifact", status_code=201)
def save_artifact(body: ArtifactRequest):
    if not body.title or not body.title.strip():
        raise HTTPException(status_code=400, detail="Titel verplicht")
    now = datetime.now(timezone.utc).isoformat()
    try:
        with get_conn() as conn:
            cur = conn.execute(
                "INSERT INTO voice_artifacts "
                "(project, goal_id, title, transcript, artifact, artifact_type, status, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    body.project,
                    body.goal_id,
                    body.title.strip(),
                    body.transcript,
                    body.artifact,
                    body.artifact_type,
                    body.status,
                    now,
                ),
            )
            aid = cur.lastrowid
            row = conn.execute(
                "SELECT * FROM voice_artifacts WHERE id = ?", (aid,)
            ).fetchone()
            return dict(row)
    except Exception as e:
        logger.exception("Kon artifact niet opslaan")
        raise HTTPException(status_code=500, detail=str(e)[:200])


@router.post("/log-session", status_code=201)
def log_session(body: LogSessionRequest):
    """Sla een voice-sessie op in Obsidian (memory galaxy) + gallery.

    Geen Obsidian = alleen gallery-entry; nooit een harde fout.
    """
    vault_path = log_session_to_obsidian(
        project=body.project,
        title=body.title,
        transcript=body.transcript,
        answer=body.answer,
        goal_id=body.goal_id,
    )
    now = datetime.now(timezone.utc).isoformat()
    try:
        with get_conn() as conn:
            cur = conn.execute(
                "INSERT INTO voice_artifacts "
                "(project, goal_id, title, transcript, artifact, artifact_type, status, created_at) "
                "VALUES (?, ?, ?, ?, ?, 'note', 'logged', ?)",
                (
                    body.project,
                    body.goal_id,
                    body.title,
                    body.transcript,
                    vault_path or "",
                    now,
                ),
            )
            aid = cur.lastrowid
            row = conn.execute(
                "SELECT * FROM voice_artifacts WHERE id = ?", (aid,)
            ).fetchone()
            result = dict(row)
            result["obsidian_path"] = vault_path
            return result
    except Exception as e:
        logger.exception("Kon sessie niet loggen")
        raise HTTPException(status_code=500, detail=str(e)[:200])


@router.get("/briefing")
def voice_briefing():
    """Iris' dagbriefing als platte tekst, klaar om hardop voor te lezen."""
    try:
        from ..iris import service as iris_service
        report = iris_service.latest_report()
        if not report or not report.get("markdown"):
            return {"available": False, "text": "", "note": "Nog geen briefing gedraaid."}
        return {"available": True, "text": report["markdown"], "report_date": report.get("report_date")}
    except Exception as e:
        logger.exception("Kon briefing niet ophalen")
        raise HTTPException(status_code=500, detail=str(e)[:200])
