"""
Google Analytics 4 Data API service.
Haalt wekelijkse rapportdata op via een service account.
"""
from datetime import date, timedelta
from typing import Dict

from ...shared.config import GA4_PROPERTY_ID, GA_SERVICE_ACCOUNT_PATH


def is_configured() -> bool:
    return bool(GA4_PROPERTY_ID)


def _get_client():
    from google.analytics.data_v1beta import BetaAnalyticsDataClient

    if GA_SERVICE_ACCOUNT_PATH:
        from google.oauth2 import service_account
        creds = service_account.Credentials.from_service_account_file(
            GA_SERVICE_ACCOUNT_PATH,
            scopes=["https://www.googleapis.com/auth/analytics.readonly"],
        )
        return BetaAnalyticsDataClient(credentials=creds)
    return BetaAnalyticsDataClient()  # valt terug op GOOGLE_APPLICATION_CREDENTIALS


def fetch_weekly_data(days: int = 7, property_id: str = "") -> Dict:
    """Haalt GA4-data op. `property_id` overschrijft de globale GA4_PROPERTY_ID —
    voor per-site rapportage (bv. een site met een eigen property_id in de
    `sites`-tabel) in plaats van de ene globale property uit .env."""
    from google.analytics.data_v1beta import BetaAnalyticsDataClient
    from google.analytics.data_v1beta.types import (
        DateRange, Dimension, Metric, RunReportRequest, OrderBy,
    )

    client = _get_client()
    end = date.today() - timedelta(days=1)
    start = end - timedelta(days=days - 1)
    date_range = DateRange(
        start_date=start.strftime("%Y-%m-%d"),
        end_date=end.strftime("%Y-%m-%d"),
    )
    prop = f"properties/{property_id or GA4_PROPERTY_ID}"

    def _run(dimensions, metrics, order_bys=None, limit=None):
        kwargs = dict(property=prop, date_ranges=[date_range],
                      dimensions=dimensions, metrics=metrics)
        if order_bys:
            kwargs["order_bys"] = order_bys
        if limit:
            kwargs["limit"] = limit
        return client.run_report(RunReportRequest(**kwargs))

    def v(row, i):
        return row.metric_values[i].value

    def d(row, i):
        return row.dimension_values[i].value

    # Kerncijfers (geen dimensies → één totaalrij)
    overall = client.run_report(RunReportRequest(
        property=prop,
        date_ranges=[date_range],
        metrics=[
            Metric(name="sessions"),
            Metric(name="totalUsers"),
            Metric(name="screenPageViews"),
            Metric(name="engagementRate"),
            Metric(name="averageSessionDuration"),
            Metric(name="bounceRate"),
        ],
    ))

    summary = {}
    if overall.rows:
        r = overall.rows[0]
        summary = {
            "sessions": int(v(r, 0)),
            "users": int(v(r, 1)),
            "pageviews": int(v(r, 2)),
            "engagement_rate": round(float(v(r, 3)) * 100, 1),
            "avg_session_duration": round(float(v(r, 4))),
            "bounce_rate": round(float(v(r, 5)) * 100, 1),
        }

    # Dagelijks overzicht
    daily_resp = _run(
        dimensions=[Dimension(name="date")],
        metrics=[Metric(name="sessions"), Metric(name="totalUsers"), Metric(name="screenPageViews")],
        order_bys=[OrderBy(dimension=OrderBy.DimensionOrderBy(dimension_name="date"))],
    )
    daily = []
    for r in daily_resp.rows:
        raw = d(r, 0)
        fmt = f"{raw[:4]}-{raw[4:6]}-{raw[6:]}" if len(raw) == 8 else raw
        daily.append({"date": fmt, "sessions": int(v(r, 0)), "users": int(v(r, 1)), "pageviews": int(v(r, 2))})

    # Top pagina's
    pages_resp = _run(
        dimensions=[Dimension(name="pagePath"), Dimension(name="pageTitle")],
        metrics=[Metric(name="screenPageViews"), Metric(name="totalUsers"), Metric(name="averageSessionDuration")],
        order_bys=[OrderBy(metric=OrderBy.MetricOrderBy(metric_name="screenPageViews"), desc=True)],
        limit=10,
    )
    top_pages = [
        {
            "path": d(r, 0),
            "title": d(r, 1),
            "pageviews": int(v(r, 0)),
            "users": int(v(r, 1)),
            "avg_duration": round(float(v(r, 2))),
        }
        for r in pages_resp.rows
    ]

    # Verkeersbronnen
    ch_resp = _run(
        dimensions=[Dimension(name="sessionDefaultChannelGroup")],
        metrics=[Metric(name="sessions"), Metric(name="totalUsers")],
        order_bys=[OrderBy(metric=OrderBy.MetricOrderBy(metric_name="sessions"), desc=True)],
    )
    channels = [{"channel": d(r, 0), "sessions": int(v(r, 0)), "users": int(v(r, 1))} for r in ch_resp.rows]

    # Apparaten
    dev_resp = _run(
        dimensions=[Dimension(name="deviceCategory")],
        metrics=[Metric(name="sessions")],
        order_bys=[OrderBy(metric=OrderBy.MetricOrderBy(metric_name="sessions"), desc=True)],
    )
    devices = [{"device": d(r, 0), "sessions": int(v(r, 0))} for r in dev_resp.rows]

    # Top landen
    geo_resp = _run(
        dimensions=[Dimension(name="country")],
        metrics=[Metric(name="sessions")],
        order_bys=[OrderBy(metric=OrderBy.MetricOrderBy(metric_name="sessions"), desc=True)],
        limit=10,
    )
    countries = [{"country": d(r, 0), "sessions": int(v(r, 0))} for r in geo_resp.rows]

    return {
        "period": {"start": start.strftime("%Y-%m-%d"), "end": end.strftime("%Y-%m-%d")},
        "summary": summary,
        "daily": daily,
        "top_pages": top_pages,
        "channels": channels,
        "devices": devices,
        "countries": countries,
    }
