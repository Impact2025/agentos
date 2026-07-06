import json
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException
from typing import List
from ...shared.models import AgentProfileCreate, AgentProfileUpdate, AgentProfileOut
from ...shared.database import get_conn

router = APIRouter(prefix="/api/agents", tags=["agents"])


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_mcp(value) -> List[str]:
    """mcp_servers wordt als CSV (of legacy JSON) opgeslagen; lees robuust terug als lijst."""
    if isinstance(value, list):
        return value
    if not value:
        return []
    s = str(value).strip()
    if s.startswith("["):
        try:
            return [str(x) for x in json.loads(s)]
        except json.JSONDecodeError:
            pass
    return [p.strip() for p in s.split(",") if p.strip()]


def _to_out(row) -> dict:
    d = dict(row)
    d["mcp_servers"] = _parse_mcp(d.get("mcp_servers"))
    return d


@router.get("", response_model=List[AgentProfileOut])
def list_profiles() -> List[AgentProfileOut]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, name, model, system_prompt, "
            "COALESCE(memory_session,'') AS memory_session, "
            "COALESCE(mcp_servers,'[]') AS mcp_servers, created_at "
            "FROM agent_profiles ORDER BY created_at ASC"
        ).fetchall()
    return [_to_out(r) for r in rows]


@router.post("", response_model=AgentProfileOut, status_code=201)
def create_profile(body: AgentProfileCreate):
    now = _now()
    with get_conn() as conn:
        cur = conn.execute(
            """
            INSERT INTO agent_profiles (name, model, system_prompt, memory_session, mcp_servers, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                body.name,
                body.model,
                body.system_prompt or "",
                body.memory_session or "",
                ",".join(body.mcp_servers or []),
                now,
            ),
        )
        new_id = cur.lastrowid
        row = conn.execute(
            "SELECT id, name, model, system_prompt, "
            "COALESCE(memory_session,'') AS memory_session, "
            "COALESCE(mcp_servers,'[]') AS mcp_servers, created_at "
            "FROM agent_profiles WHERE id = ?",
            (new_id,),
        ).fetchone()
    return _to_out(row)


@router.patch("/{profile_id}", response_model=AgentProfileOut)
def update_profile(profile_id: int, body: AgentProfileUpdate):
    updates = {
        k: v
        for k, v in body.model_dump(exclude_none=True).items()
        if k not in {"mcp_servers"} or v is not None
    }
    if "mcp_servers" in updates and updates["mcp_servers"] is not None:
        updates["mcp_servers"] = ",".join(updates["mcp_servers"])

    with get_conn() as conn:
        row = conn.execute(
            "SELECT id, name, model, system_prompt, "
            "COALESCE(memory_session,'') AS memory_session, "
            "COALESCE(mcp_servers,'[]') AS mcp_servers, created_at "
            "FROM agent_profiles WHERE id = ?",
            (profile_id,),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Agent-profiel niet gevonden")
        if updates:
            fields = ", ".join(f"{k} = ?" for k in updates)
            conn.execute(
                f"UPDATE agent_profiles SET {fields} WHERE id = ?",
                list(updates.values()) + [profile_id],
            )
            row = conn.execute(
                "SELECT id, name, model, system_prompt, "
                "COALESCE(memory_session,'') AS memory_session, "
                "COALESCE(mcp_servers,'[]') AS mcp_servers, created_at "
                "FROM agent_profiles WHERE id = ?",
                (profile_id,),
            ).fetchone()
    return _to_out(row)


@router.delete("/{profile_id}", status_code=204)
def delete_profile(profile_id: int):
    with get_conn() as conn:
        cur = conn.execute("DELETE FROM agent_profiles WHERE id = ?", (profile_id,))
    if cur.rowcount == 0:
        raise HTTPException(status_code=404, detail="Agent-profiel niet gevonden")
