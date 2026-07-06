from fastapi import APIRouter, BackgroundTasks, HTTPException

from ...shared.email_service import is_configured as email_ok
from .ga_service import is_configured as ga_ok
from .reporter import run_weekly_report
from ...shared.config import GA4_PROPERTY_ID, GA_SERVICE_ACCOUNT_PATH, REPORT_EMAIL_TO

router = APIRouter(prefix="/api/analytics", tags=["analytics"])


@router.post("/weekly-report")
async def trigger_weekly_report(background_tasks: BackgroundTasks):
    if not ga_ok():
        raise HTTPException(status_code=503, detail="GA4 niet geconfigureerd — stel GA4_PROPERTY_ID in .env in")
    background_tasks.add_task(run_weekly_report)
    return {"status": "gestart", "message": "Rapport wordt op de achtergrond gegenereerd"}


@router.get("/status")
def analytics_status():
    return {
        "ga4_configured": ga_ok(),
        "ga4_property_id": GA4_PROPERTY_ID,
        "service_account_set": bool(GA_SERVICE_ACCOUNT_PATH),
        "email_configured": email_ok(),
        "email_to": REPORT_EMAIL_TO,
    }
