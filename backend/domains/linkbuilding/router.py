"""Linkbuilding-API — funnel, batch, review-gate en monitor.

POST /{id}/outreach-approve is de ENIGE plek waar een linkbuilding-mail
daadwerkelijk vertrekt (na menselijke goedkeuring in het Actiecentrum).
"""
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from ...shared.database import get_conn
from ...shared.outcomes import log_outcome
from . import service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/linkbuilding", tags=["linkbuilding"])


class ProspectPatch(BaseModel):
    contact_email: Optional[str] = None
    target_url: Optional[str] = None
    anchor_text: Optional[str] = None
    status: Optional[str] = None


class OutreachApproveRequest(BaseModel):
    subject: str = ""   # optioneel: door Vincent aangepaste onderwerpregel
    body: str = ""      # optioneel: door Vincent aangepaste mailtekst


@router.get("/prospects")
def list_prospects(site_id: str = "", status: str = ""):
    """Alle linkkansen, optioneel gefilterd op site en/of funnel-stap."""
    return service.list_prospects(site_id=site_id, status=status)


@router.get("/funnel")
def funnel(site_id: str = ""):
    """De linkbuilding-formule: funnel-standen, ratio's en live links."""
    return service.funnel_stats(site_id=site_id)


@router.get("/placements")
def list_placements(site_id: str = "", status: str = ""):
    """Concrete linkafspraken en hun bewijsstatus (pending/live/lost)."""
    q = "SELECT * FROM link_placements WHERE 1=1"
    params: list = []
    if site_id:
        q += " AND site_id = ?"
        params.append(site_id)
    if status:
        q += " AND status = ?"
        params.append(status)
    q += " ORDER BY updated_at DESC"
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(q, params).fetchall()]


@router.post("/prospect-run")
async def prospect_run(site_id: str = Query(""), count: int = Query(10, ge=1, le=25)):
    """Zoek en kwalificeer nu linkkansen (één site, of alle sites met base_url).
    Verstuurt niets."""
    from ..seo.sites import get_site, list_sites
    from .prospector import run_prospecting_for_site

    if site_id:
        site = get_site(site_id)
        if not site:
            raise HTTPException(status_code=404, detail="Site niet gevonden")
        sites = [site]
    else:
        sites = [s for s in (get_site(x["id"]) for x in list_sites())
                 if s and (s.get("base_url") or "").strip()]
    reports = [await run_prospecting_for_site(s, max_new=count) for s in sites]
    # Kon er voor geen enkele site gezocht worden, dan is dat een storing en geen
    # lege uitslag — anders leest de UI "geen kansen gevonden" terwijl er niets
    # gezocht is.
    errors = [r["error"] for r in reports if r.get("error")]
    if reports and len(errors) == len(reports):
        raise HTTPException(status_code=502,
                            detail=f"Websearch niet beschikbaar: {errors[0]}")
    return {"reports": reports}


@router.post("/outreach-batch")
async def outreach_batch(site_id: str = Query(""), count: int = Query(0, ge=0, le=25)):
    """Zet nu een batch outreach-concepten klaar ter review (default: weektarget).
    Verstuurt niets — concepten verschijnen in het Actiecentrum."""
    from .outreach import prepare_linkbuilding_batch
    return await prepare_linkbuilding_batch(count=count, site_id=site_id)


@router.get("/outreach-review")
def outreach_review():
    """Alle linkbuilding-concepten die op menselijke goedkeuring wachten."""
    return service.list_prospects(status="outreach_review")


@router.post("/auto-approve")
async def auto_approve(site_id: str = Query("")):
    """Goldie-modus: stuur de hele review-wachtrij in één keer verstuurt.

    Alleen effectief als LINKBUILD_AUTO_APPROVE=1 in .env staat; anders rapporteert
    hij neutraal dat de gate uit staat. Verstuurt niets wat email_ok() afkeurt."""
    from .outreach import auto_approve_review_queue
    return await auto_approve_review_queue(site_id=site_id)


@router.post("/{prospect_id}/outreach-approve")
async def approve_outreach(prospect_id: str,
                           body: OutreachApproveRequest = OutreachApproveRequest()):
    """DE verzendknop: verstuur het goedgekeurde concept via Outlook/Graph.

    Dit is de enige plek waar linkbuilding-mail de deur uitgaat.
    Status → contacted (met tijdstempel: de input van de formule)."""
    from ..outlook import service as outlook
    from .outreach import email_ok

    prospect = service.get_prospect(prospect_id)
    if not prospect:
        raise HTTPException(status_code=404, detail="Linkkans niet gevonden")

    subject = (body.subject or prospect.get("outreach_subject") or "").strip()
    mail_body = (body.body or prospect.get("outreach_draft") or "").strip()
    if not subject or not mail_body:
        raise HTTPException(status_code=422,
                            detail="Geen concept aanwezig — draai eerst de outreach-batch.")

    target = (prospect.get("contact_email") or "").strip()
    ok, why = email_ok(target)
    if not ok:
        raise HTTPException(
            status_code=422,
            detail=f"Contactadres ({target or 'leeg'}) niet bruikbaar ({why}) — "
                   "versturen geweigerd. Vul een adres in via PATCH of wijs de kans af.",
        )
    # Echte token-check (geen alleen de gecachte account): een verlopen
    # refresh-token laat is_authenticated() op True staan maar geeft géén
    # token -> dat zou anders een ongevangen RuntimeError (HTTP 500) geven.
    if not outlook.get_valid_token():
        raise HTTPException(
            status_code=422,
            detail="Outlook-sessie ongeldig of verlopen — log opnieuw in via "
                   "Instellingen → Outlook en probeer het daarna opnieuw.",
        )

    result = await outlook.send_new_email(
        to=target, subject=subject, body_html=mail_body.replace("\n", "<br>"),
    )
    if not result.get("success"):
        raise HTTPException(
            status_code=502,
            detail=f"Versturen mislukt: {result.get('error', result)}",
        )

    # Bewaar wat er echt verstuurd is (evt. door Vincent aangepast) als record.
    with get_conn() as conn:
        conn.execute(
            "UPDATE link_prospects SET outreach_subject = ?, outreach_draft = ? "
            "WHERE id = ?",
            (subject, mail_body, prospect_id),
        )
    updated = service.advance_prospect(prospect_id, "contacted")

    log_outcome(
        "Linkbuilding", "link_outreach_sent",
        f"Link-outreach verstuurd aan {prospect['domain']} ({target}): '{subject}'",
        next_step="De monitor checkt dagelijks of de link geplaatst wordt; "
                  "reply-detectie staat aan.",
    )
    return {"status": "sent", "to": target, "subject": subject, "prospect": updated}


@router.post("/{prospect_id}/outreach-dismiss")
def dismiss_outreach(prospect_id: str):
    """Wijs een concept af: de linkkans gaat naar 'lost' (met tijdstempel)."""
    prospect = service.get_prospect(prospect_id)
    if not prospect:
        raise HTTPException(status_code=404, detail="Linkkans niet gevonden")
    with get_conn() as conn:
        conn.execute(
            "UPDATE link_prospects SET outreach_subject = '', outreach_draft = '', "
            "outreach_drafted_at = '' WHERE id = ?",
            (prospect_id,),
        )
    service.advance_prospect(prospect_id, "lost")
    return {"status": "dismissed", "prospect_id": prospect_id, "back_to": "lost"}


@router.patch("/{prospect_id}")
def patch_prospect(prospect_id: str, body: ProspectPatch):
    """Handmatige correcties: contactadres, linkdoel, ankertekst of status."""
    prospect = service.get_prospect(prospect_id)
    if not prospect:
        raise HTTPException(status_code=404, detail="Linkkans niet gevonden")
    updates, params = [], []
    for field in ("contact_email", "target_url", "anchor_text", "status"):
        val = getattr(body, field)
        if val is not None:
            updates.append(f"{field} = ?")
            params.append(val.strip())
    if updates:
        params.append(prospect_id)
        with get_conn() as conn:
            conn.execute(
                f"UPDATE link_prospects SET {', '.join(updates)}, "
                "updated_at = datetime('now') WHERE id = ?", params)
    return service.get_prospect(prospect_id)


@router.post("/monitor-run")
def monitor_run():
    """Controleer nu alle pending en live placements (crawlt, verstuurt niets)."""
    from .monitor import check_placements
    return check_placements()
