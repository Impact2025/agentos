from fastapi import APIRouter, Query
from ...shared.models import ObsidianSearchOut, ObsidianResult
from ...domains.chat.obsidian import ObsidianService
from ...shared.config import OBSIDIAN_VAULT_PATH

router = APIRouter(prefix="/api/obsidian", tags=["obsidian"])

_obsidian = ObsidianService(OBSIDIAN_VAULT_PATH)


@router.get("/status")
def vault_status():
    return {
        "configured": _obsidian.is_configured,
        "vault_path": OBSIDIAN_VAULT_PATH,
        "total_files": _obsidian.total_file_count() if _obsidian.is_configured else 0,
    }


@router.get("/files")
def list_files():
    return {"files": _obsidian.list_files()}


@router.get("/search", response_model=ObsidianSearchOut)
def search(q: str = Query(..., min_length=1), top_k: int = Query(5, ge=1, le=20)):
    raw = _obsidian.search(q, top_k=top_k)
    results = [
        ObsidianResult(
            file=r["file"],
            path=r["path"],
            score=round(r["score"], 4),
            snippet=r["snippet"],
        )
        for r in raw
    ]
    return ObsidianSearchOut(
        query=q,
        results=results,
        vault_path=OBSIDIAN_VAULT_PATH,
        total_files=_obsidian.total_file_count(),
    )
