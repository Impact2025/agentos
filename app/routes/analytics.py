from fastapi import APIRouter
from analytics_service import AnalyticsService  # Pas aan op basis van je mappenstructuur

router = APIRouter(prefix="/api/analytics", tags=["Analytics"])

@router.get("/live")
def get_live_status():
    service = AnalyticsService()
    return {
        "status": "success",
        "data": {
            "right_now": service.get_live_right_now(),
            "today_so_far": service.get_today_summary()
        }
    }