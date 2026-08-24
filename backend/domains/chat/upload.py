import os, uuid
from pathlib import Path
from fastapi import APIRouter, HTTPException, UploadFile, File, Form
from fastapi.responses import JSONResponse
from ...shared.config import OBSIDIAN_VAULT_PATH

router = APIRouter(prefix="/api/chat", tags=["chat"])

ALLOWED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp"}
MAX_UPLOAD_SIZE = 20 * 1024 * 1024


def upload_root() -> Path:
    return Path(os.environ.get("IMPACTOS_UPLOAD_ROOT", "D:/APPS/impactos/data/uploads"))


def _build_attachment_text(attachments: list[dict]) -> str:
    if not attachments:
        return ""
    lines = ["\n\n## Bijlagen"]
    for a in attachments:
        lines.append(f"- [{a.get('filename', 'bestand')}]({a.get('url', '')})")
    return "\n".join(lines)


@router.post("/upload")
async def upload_files(
    files: list[UploadFile] = File(...),
):
    root = upload_root()
    root.mkdir(parents=True, exist_ok=True)

    attachments = []
    try:
        for file in files:
            ext = Path(file.filename or "upload.bin").suffix.lower()
            if ext not in ALLOWED_EXTENSIONS:
                raise HTTPException(status_code=415, detail=f"Bestandstype niet toegestaan: {ext}")
            content = await file.read()
            if len(content) > MAX_UPLOAD_SIZE:
                raise HTTPException(status_code=413, detail="Bestand te groot (max 20 MB)")
            name = f"{uuid.uuid4().hex}{ext}"
            dest = root / name
            dest.write_bytes(content)
            attachments.append(
                {
                    "url": f"/uploads/{name}",
                    "filename": file.filename or name,
                    "content_type": file.content_type or "application/octet-stream",
                    "size": len(content),
                }
            )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return JSONResponse({"attachments": attachments})
