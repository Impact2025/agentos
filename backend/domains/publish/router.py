"""
Publish router — publiceer Loop-artikelen naar Netlify.

  POST /api/publish            → publiceer een artikel (titel + HTML-body) naar een site.
  GET  /api/publish?site_id=   → lijst gepubliceerde pagina's van een site.
"""
import httpx
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from typing import Optional

from . import service as netlify_service

router = APIRouter(prefix="/api/publish", tags=["publish"])


class PublishRequest(BaseModel):
    site_id: str
    title: str
    html_body: str
    slug: Optional[str] = None


@router.post("", status_code=201)
async def publish(body: PublishRequest):
    try:
        return await netlify_service.publish_article(
            body.site_id, body.title, body.html_body, body.slug
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except httpx.HTTPStatusError as exc:
        detail = exc.response.text[:300] if exc.response is not None else str(exc)
        raise HTTPException(
            status_code=502,
            detail=f"Netlify-deploy mislukt ({exc.response.status_code}): {detail}",
        )
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"Netlify onbereikbaar: {exc}")


@router.get("")
def list_published(site_id: str = Query(...)):
    return netlify_service.list_pages(site_id)
