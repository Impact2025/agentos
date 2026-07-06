"""Sites-API — CRUD voor het site-portfolio van de Demand Engine."""
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from . import sites as sites_service

router = APIRouter(prefix="/api/sites", tags=["sites"])


class SiteCreate(BaseModel):
    name: str
    base_url: Optional[str] = ""
    gsc_property: Optional[str] = ""
    publish_api_url: Optional[str] = ""
    publish_api_key: Optional[str] = ""
    default_author: Optional[str] = ""


class SiteUpdate(BaseModel):
    name: Optional[str] = None
    base_url: Optional[str] = None
    gsc_property: Optional[str] = None
    publish_api_url: Optional[str] = None
    publish_api_key: Optional[str] = None
    default_author: Optional[str] = None
    linkedin_token: Optional[str] = None
    linkedin_user_urn: Optional[str] = None
    facebook_page_id: Optional[str] = None
    facebook_page_token: Optional[str] = None
    instagram_business_id: Optional[str] = None
    twitter_api_key: Optional[str] = None
    twitter_api_secret: Optional[str] = None
    twitter_access_token: Optional[str] = None
    twitter_access_secret: Optional[str] = None
    auto_content_enabled: Optional[bool] = None


@router.get("")
def list_sites():
    return sites_service.list_sites()


@router.post("", status_code=201)
def create_site(body: SiteCreate):
    try:
        return sites_service.create_site(body.model_dump())
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.patch("/{site_id}")
def update_site(site_id: str, body: SiteUpdate):
    updated = sites_service.update_site(site_id, body.model_dump(exclude_unset=True))
    if not updated:
        raise HTTPException(status_code=404, detail="Site niet gevonden")
    return updated


@router.delete("/{site_id}", status_code=204)
def delete_site(site_id: str):
    if not sites_service.delete_site(site_id):
        raise HTTPException(status_code=404, detail="Site niet gevonden")
