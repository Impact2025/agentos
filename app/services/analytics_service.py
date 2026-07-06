import os
from google.analytics.data_v1beta import BetaAnalyticsDataClient
from google.analytics.data_v1beta.types import RunRealtimeReportRequest, Dimension, Metric, RunReportRequest, DateRange

class AnalyticsService:
    def __init__(self):
        self.property_id = os.getenv("GA4_PROPERTY_ID")
        # De SDK pakt automatisch de GOOGLE_APPLICATION_CREDENTIALS env var op
        self.client = BetaAnalyticsDataClient()

    def get_live_right_now(self):
        """Haalt realtime data op van de afgelopen 30 minuten."""
        try:
            request = RunRealtimeReportRequest(
                property=f"properties/{self.property_id}",
                dimensions=[Dimension(name="minutesAgo")],
                metrics=[Metric(name="activeUsers")]
            )
            response = self.client.run_realtime_report(request)
            total_active = sum(int(row.metric_values[0].value) for row in response.rows)
            return {"active_users_last_30_mins": total_active}
        except Exception as e:
            return {"error": f"Realtime mislukt: {str(e)}"}

    def get_today_summary(self):
        """Haalt de belangrijkste statistieken van vandaag tot nu toe op."""
        try:
            request = RunReportRequest(
                property=f"properties/{self.property_id}",
                date_ranges=[DateRange(start_date="today", end_date="today")],
                dimensions=[Dimension(name="sessionSource")],
                metrics=[Metric(name="activeUsers"), Metric(name="screenPageViews")]
            )
            response = self.client.run_report(request)
            
            sources = []
            total_views = 0
            total_users = 0
            
            for row in response.rows:
                source = row.dimension_values[0].value
                users = int(row.metric_values[0].value)
                views = int(row.metric_values[1].value)
                total_users += users
                total_views += views
                sources.append({"source": source, "users": users, "pageviews": views})
                
            return {
                "summary": {"total_users_today": total_users, "total_pageviews_today": total_views},
                "top_sources": sources[:5]
            }
        except Exception as e:
            return {"error": f"Rapport mislukt: {str(e)}"}