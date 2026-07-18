"""Canva Connect API client — agents vullen een vaste Brand Template automatisch.

Twee modi:
  1. AUTOFILL (voorkeur): als CANVA_BRAND_TEMPLATE_ID is gezet, wordt per pack
     jouw BESTAANDE 'Insta/FB advertenties Bewaardvoorjou'-template gekopieerd en
     de gemarkeerde tekstvelden (Headline / Subtext) automatisch ingevuld via de
     Canva Autofill-api. Dit is wat de vraag eigenlijk is: "agents veranderen de
     template steeds in Canva" — zonder dat jij handmatig tekst overtypt.
  2. FALLBACK: zonder template-id maakt Canva Connect een LEEG poster-design van de
     headline (zodat je in ieder geval een kant-en-klaar design hebt om te openen).
     Zonder de drie Connect-credentials (CLIENT_ID/SECRET/REFRESH_TOKEN) blijft
     alles netjes misconigureerd en valt de caller terug op de Canva-ready *brief*
     (template + Midjourney-prompt) — de mens doet de handmatige bewerking.

Verificatie-achtergrond: de precieze endpoint-namen zijn geschreven tegen Canva's
Connect-docs (v1). Endpoint-paden staan in Canva-autofill.md.

Flow (Autofill):
  token  = refresh_token-grant                              (zie get_access_token)
  POST   {BASE}/brand-templates/{id}/autofill
          Authorization: Bearer ***
          body: { "brand_template_id": id, "data": { "<veld>": {...} }, "title": "..." }
       -> { "design": { "id": "...", "edit_url": "https://www.canva.com/design/..." } }
  (optioneel) POST {BASE}/designs/{design_id}/exports       -> PNG/JPG download-URL

De eenmalige handmatige stap die de agent NIET kan doen: markeer in Canva de
tekstelementen van je template als "data-veld" en sla het op als Brand Template,
en zet CANVA_BRAND_TEMPLATE_ID (+ credentials) in .env. Daarna loopt alles
geautomatiseerd.
"""
import logging
import time
from typing import Dict, List, Optional

import httpx

from .config import (
    CANVA_CLIENT_ID, CANVA_CLIENT_SECRET, CANVA_REFRESH_TOKEN,
    CANVA_BRAND_TEMPLATE_ID, CANVA_TEMPLATE_FIELDS, CANVA_FOLDER_ID,
)

logger = logging.getLogger(__name__)

CANVA_TOKEN_URL = "https://api.canva.com/rest/v1/oauth/token"
CANVA_API_BASE = "https://api.canva.com/rest/v1"

# Design-type per platform-formaat (Canva Connect ondersteunt deze types).
_FORMAT_TO_TYPE = {
    "1080x1080": "poster",   # vierkant IG/FB
    "1080x1920": "poster",   # verticaal TT/Reels
    "1200x628": "poster",    # landschap
}

# Default-veldmap: brief-veld -> Canva data-veldnaam.
_DEFAULT_FIELD_MAP = {"headline": "Headline", "subtext": "Subtext"}


def canva_ready() -> bool:
    """Zijn de Canva Connect-credentials aanwezig? (Apps-SDK vars tellen niet.)"""
    return bool(CANVA_CLIENT_ID and CANVA_CLIENT_SECRET and CANVA_REFRESH_TOKEN)


def canva_template_edit_url() -> str:
    """Directe 'open je vaste template'-link (edit-URL van de Brand Template)."""
    if not CANVA_BRAND_TEMPLATE_ID:
        return ""
    return f"https://www.canva.com/design/{CANVA_BRAND_TEMPLATE_ID}/edit"


def autofill_ready() -> bool:
    """Kan Autofill lopen? (credentials + een Brand-Template-id vereist.)"""
    return canva_ready() and bool(CANVA_BRAND_TEMPLATE_ID)


def _field_map() -> Dict[str, str]:
    """Parse CANVA_TEMPLATE_FIELDS ('k=v,k2=v2') naar brief-veld -> Canva-veld."""
    out: Dict[str, str] = dict(_DEFAULT_FIELD_MAP)
    raw = (CANVA_TEMPLATE_FIELDS or "").strip()
    for pair in raw.split(","):
        if "=" not in pair:
            continue
        k, v = pair.split("=", 1)
        k, v = k.strip().lower(), v.strip()
        if k and v:
            out[k] = v
    return out


# ── Token-cache (in-process; vernieuwt alleen als nodig) ──────────────────
_token_cache: dict = {"access_token": None, "expires_at": 0, "refresh_token": None}


def get_access_token() -> str:
    """Verschaf een geldig access-token, vernieuw via refresh_token indien nodig.

    Werpt RuntimeError als Canva niet geconfigureerd is of de refresh faalt.
    """
    if not canva_ready():
        raise RuntimeError("Canva Connect niet geconfigureerd (CANVA_CLIENT_ID/SECRET/REFRESH_TOKEN ontbreken)")
    now = time.time()
    if _token_cache["access_token"] and _token_cache["expires_at"] > now + 60:
        return _token_cache["access_token"]
    # Gebruik de meest recente refresh-token (Canva levert soms een nieuwe).
    refresh = _token_cache.get("refresh_token") or CANVA_REFRESH_TOKEN
    resp = httpx.post(
        CANVA_TOKEN_URL,
        data={
            "grant_type": "refresh_token",
            "client_id": CANVA_CLIENT_ID,
            "client_secret": CANVA_CLIENT_SECRET,
            "refresh_token": refresh,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=30,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"Canva token-vernieuwing mislukt ({resp.status_code}): {resp.text[:200]}")
    body = resp.json()
    _token_cache["access_token"] = body["access_token"]
    _token_cache["expires_at"] = now + int(body.get("expires_in", 3600))
    if body.get("refresh_token"):
        _token_cache["refresh_token"] = body["refresh_token"]
    return _token_cache["access_token"]


def _parse_dims(dimensions: str):
    try:
        w, h = (int(x) for x in dimensions.lower().split("x"))
        return w, h
    except Exception:
        return 1080, 1080


def _build_autofill_data(brief: dict) -> Dict[str, dict]:
    """Bouw de 'data'-map voor Autofill: Canva-veldnaam -> { 'text': waarde }.

    Alleen velden waarvoor de brief ook een waarde heeft worden meegestuurd,
    zodat ontbrekende velden in de template onaangetast blijven.
    """
    fmap = _field_map()
    data: Dict[str, dict] = {}
    for brief_key, canva_field in fmap.items():
        val = (brief.get(brief_key) or "").strip()
        if val:
            data[canva_field] = {"type": "text", "text": val}
    return data


def autofill_template(brief: dict, title: Optional[str] = None) -> dict:
    """Vul de geconfigureerde Brand Template automatisch in.

    Retourneert {'design_id':..., 'edit_url':..., 'title':...} bij succes,
    anders {'error':...}. Nooit een crash.
    """
    if not autofill_ready():
        return {"error": "Autofill niet gereed (CANVA_BRAND_TEMPLATE_ID en credentials vereist)"}
    try:
        token = get_access_token()
        template_id = CANVA_BRAND_TEMPLATE_ID
        data = _build_autofill_data(brief)
        if not data:
            return {"error": "Geen invulbare velden in de brief"}
        payload: dict = {
            "brand_template_id": template_id,
            "data": data,
            "title": (title or brief.get("headline") or "Social post")[:100],
        }
        if CANVA_FOLDER_ID:
            payload["folder"] = {"id": CANVA_FOLDER_ID}
        resp = httpx.post(
            f"{CANVA_API_BASE}/brand-templates/{template_id}/autofill",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=30,
        )
        if resp.status_code not in (200, 201):
            return {"error": f"Canva autofill HTTP {resp.status_code}: {resp.text[:250]}"}
        d = (resp.json() or {}).get("design", {})
        return {
            "design_id": d.get("id"),
            "edit_url": d.get("edit_url", ""),
            "title": payload["title"],
        }
    except Exception as e:
        logger.warning("Canva autofill mislukt: %s", e)
        return {"error": str(e)[:250]}


def create_design_from_brief(brief: dict) -> dict:
    """Maak een (LEEG) Canva-design op basis van de beeld-brief.

    Fallback-wEG als er geen template-id is: het design krijgt de kop als titel.
    De eigenlijke tekst/opmaak doe je in Canva zelf (of via Autofill hierboven).
    """
    try:
        token = get_access_token()
        w, h = _parse_dims(brief.get("dimensions", "1080x1080"))
        design_type = _FORMAT_TO_TYPE.get(brief.get("dimensions", ""), "poster")
        title = (brief.get("headline") or "Social post")[:100]
        payload = {
            "design_type": design_type,
            "title": title,
            "width": w,
            "height": h,
        }
        if CANVA_FOLDER_ID:
            payload["folder"] = {"id": CANVA_FOLDER_ID}
        resp = httpx.post(
            f"{CANVA_API_BASE}/designs",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=30,
        )
        if resp.status_code not in (200, 201):
            return {"error": f"Canva createDesign HTTP {resp.status_code}: {resp.text[:200]}"}
        d = (resp.json() or {}).get("design", {})
        return {"design_id": d.get("id"), "edit_url": d.get("edit_url")}
    except Exception as e:
        logger.warning("Canva design-aanmaak mislukt: %s", e)
        return {"error": str(e)[:200]}


def fill_or_create(brief: dict, title: Optional[str] = None) -> dict:
    """Voorkeur: Autofill van de vaste template. Anders: leeg design.

    Retourneert altijd {'design_id':..., 'edit_url':..., 'method': 'autofill'|'create'|'none', ...}.
    Bij 'none' is er een 'error' en moet de caller terugvallen op de brief.
    """
    if autofill_ready():
        res = autofill_template(brief, title=title)
        if res.get("design_id"):
            res["method"] = "autofill"
            return res
        # Autofill mislukt (bv. veldnaam fout) — probeer nog wél een leeg design.
        logger.warning("Autofill faalde, val terug op leeg design: %s", res.get("error"))
        cr = create_design_from_brief(brief)
        if cr.get("design_id"):
            cr["method"] = "create"
            return cr
        cr["method"] = "none"
        return cr
    if canva_ready():
        cr = create_design_from_brief(brief)
        if cr.get("design_id"):
            cr["method"] = "create"
            return cr
        cr["method"] = "none"
        return cr
    return {"method": "none", "error": "Canva Connect niet geconfigureerd"}


def export_design_png(design_id: str, max_wait: int = 20) -> dict:
    """Exporteer een design naar een PNG-download-URL (poll)."""
    try:
        token = get_access_token()
        r = httpx.post(
            f"{CANVA_API_BASE}/designs/{design_id}/exports",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json={"format": {"type": "png"}, "pages": [1]},
            timeout=30,
        )
        if r.status_code not in (200, 201):
            return {"error": f"Canva export HTTP {r.status_code}: {r.text[:200]}"}
        job = (r.json() or {}).get("job", {})
        job_id = job.get("id")
        if not job_id:
            return {"error": "Geen export-job-id ontvangen"}
        # Poll tot klaar
        for _ in range(max_wait):
            sr = httpx.get(
                f"{CANVA_API_BASE}/designs/{design_id}/exports/{job_id}",
                headers={"Authorization": f"Bearer {token}"},
                timeout=30,
            )
            if sr.status_code == 200:
                st = (sr.json() or {})
                if st.get("status") == "success":
                    return {"url": (st.get("result") or {}).get("download_url", "")}
                if st.get("status") == "failed":
                    return {"error": "Canva export mislukt"}
            time.sleep(1)
        return {"error": "Canva export timeout"}
    except Exception as e:
        return {"error": str(e)[:200]}
