from fastapi import APIRouter, BackgroundTasks

from .reporter import run_daily_report, run_weekly_report

router = APIRouter(prefix="/api/finance", tags=["finance"])


# POST + GET: POST is netjes voor de UI/API, GET is handig om snel vanuit de
# browser-adresbalk een rapport te starten tijdens het testen.
@router.post("/daily-report")
@router.get("/daily-report")
async def trigger_daily_report(background_tasks: BackgroundTasks):
    background_tasks.add_task(run_daily_report)
    return {"status": "gestart", "message": "Dagrapport wordt op de achtergrond gegenereerd. Check straks het dashboard."}


@router.post("/weekly-report")
@router.get("/weekly-report")
async def trigger_finance_weekly_report(background_tasks: BackgroundTasks):
    background_tasks.add_task(run_weekly_report)
    return {"status": "gestart", "message": "Weekrapport (macro & liquiditeit) wordt op de achtergrond gegenereerd. Check straks het dashboard."}
