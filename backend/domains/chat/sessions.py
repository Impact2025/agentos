from fastapi import APIRouter, HTTPException
from typing import List
from ...shared.models import SessionCreate, SessionOut, MessageOut
from ...domains.chat import service as memory_service

router = APIRouter(prefix="/api/sessions", tags=["sessions"])


@router.get("", response_model=List[SessionOut])
def list_sessions():
    return memory_service.list_sessions()


@router.post("", response_model=SessionOut, status_code=201)
def create_session(body: SessionCreate):
    return memory_service.create_session(body.name, body.agent)


@router.get("/{session_id}", response_model=SessionOut)
def get_session(session_id: str):
    session = memory_service.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Niet gevonden")
    return session


@router.get("/{session_id}/messages", response_model=List[MessageOut])
def get_messages(session_id: str):
    if not memory_service.get_session(session_id):
        raise HTTPException(status_code=404, detail="Sessie niet gevonden")
    return memory_service.get_messages(session_id)


@router.delete("/{session_id}", status_code=204)
def delete_session(session_id: str):
    if not memory_service.delete_session(session_id):
        raise HTTPException(status_code=404, detail="Niet gevonden")
