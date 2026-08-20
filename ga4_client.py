"""
Google Analytics 4 (GA4) client helper.
Reuses the same OAuth credentials as gsc_client (one token, two scopes).
"""

import datetime
from google.analytics.data_v1beta import BetaAnalyticsDataClient
from google.analytics.data_v1beta.types import (
    RunReportRequest,
    DateRange,
    Dimension,
    Metric,
)

from gsc_client import get_credentials, default_date_range  # reuse shared auth + date helper


def get_client():
    """Fresh GA4 client per call — same reasoning as gsc_client.get_service():
    avoid sharing a connection across concurrent requests.
    NOTE: no local credential caching here — gsc_client.get_credentials()
    already caches per active account, and re-fetching from there (cheap,
    just a dict lookup) means GA4 always follows whichever account is
    currently switched on, instead of getting stuck on whichever account
    happened to be active the first time this was called."""
    return BetaAnalyticsDataClient(credentials=get_credentials())


def _prop(property_id: str) -> str:
    return f"properties/{property_id}"


def get_summary(property_id, start_date=None, end_date=None):
    if not start_date or not end_date:
        start_date, end_date = default_date_range()

    client = get_client()
    request = RunReportRequest(
        property=_prop(property_id),
        date_ranges=[DateRange(start_date=start_date, end_date=end_date)],
        metrics=[
            Metric(name="newUsers"),
            Metric(name="totalUsers"),
            Metric(name="bounceRate"),
            Metric(name="engagementRate"),
            Metric(name="eventsPerSession"),
            Metric(name="averageSessionDuration"),
            Metric(name="screenPageViews"),
            Metric(name="sessions"),
        ],
    )
    response = client.run_report(request)

    if not response.rows:
        return {
            "new_users": 0, "total_users": 0, "bounce_rate": 0, "engagement_rate": 0,
            "events_per_session": 0, "avg_session_duration": 0, "views": 0, "sessions": 0,
        }

    v = response.rows[0].metric_values
    return {
        "new_users": int(float(v[0].value)),
        "total_users": int(float(v[1].value)),
        "bounce_rate": round(float(v[2].value) * 100, 2),
        "engagement_rate": round(float(v[3].value) * 100, 2),
        "events_per_session": round(float(v[4].value), 2),
        "avg_session_duration": round(float(v[5].value), 0),  # seconds
        "views": int(float(v[6].value)),
        "sessions": int(float(v[7].value)),
    }


def get_traffic_sources(property_id, start_date=None, end_date=None):
    """Sessions broken down by default channel group (Direct, Organic Search, etc.)"""
    if not start_date or not end_date:
        start_date, end_date = default_date_range()

    client = get_client()
    request = RunReportRequest(
        property=_prop(property_id),
        date_ranges=[DateRange(start_date=start_date, end_date=end_date)],
        dimensions=[Dimension(name="sessionDefaultChannelGroup")],
        metrics=[Metric(name="sessions")],
    )
    response = client.run_report(request)

    rows = [
        {"channel": row.dimension_values[0].value, "sessions": int(float(row.metric_values[0].value))}
        for row in response.rows
    ]
    rows.sort(key=lambda r: -r["sessions"])
    return rows


def get_trend(property_id, start_date=None, end_date=None):
    """Daily active users + sessions for charting."""
    if not start_date or not end_date:
        start_date, end_date = default_date_range()

    client = get_client()
    request = RunReportRequest(
        property=_prop(property_id),
        date_ranges=[DateRange(start_date=start_date, end_date=end_date)],
        dimensions=[Dimension(name="date")],
        metrics=[Metric(name="activeUsers"), Metric(name="sessions")],
    )
    response = client.run_report(request)

    rows = [
        {
            "date": row.dimension_values[0].value,  # YYYYMMDD
            "active_users": int(float(row.metric_values[0].value)),
            "sessions": int(float(row.metric_values[1].value)),
        }
        for row in response.rows
    ]
    rows.sort(key=lambda r: r["date"])
    # reformat date to YYYY-MM-DD for consistency with the GSC trend endpoint
    for r in rows:
        d = r["date"]
        r["date"] = f"{d[0:4]}-{d[4:6]}-{d[6:8]}"
    return rows


def get_top_pages(property_id, start_date=None, end_date=None, limit=15):
    if not start_date or not end_date:
        start_date, end_date = default_date_range()

    client = get_client()
    request = RunReportRequest(
        property=_prop(property_id),
        date_ranges=[DateRange(start_date=start_date, end_date=end_date)],
        dimensions=[Dimension(name="pagePath")],
        metrics=[Metric(name="screenPageViews"), Metric(name="activeUsers")],
        limit=limit,
    )
    response = client.run_report(request)

    rows = [
        {
            "page": row.dimension_values[0].value,
            "views": int(float(row.metric_values[0].value)),
            "users": int(float(row.metric_values[1].value)),
        }
        for row in response.rows
    ]
    rows.sort(key=lambda r: -r["views"])
    return rows
def list_all_properties():
    """Lists every GA4 property (name + numeric ID) across all Analytics
    accounts the authenticated user has access to — one call."""
    from google.analytics.admin_v1beta import AnalyticsAdminServiceClient

    creds = get_credentials()
    client = AnalyticsAdminServiceClient(credentials=creds)

    results = []
    for summary in client.list_account_summaries():
        for prop in summary.property_summaries:
            results.append({
                "account_name": summary.display_name,
                "property_name": prop.display_name,
                "property_id": prop.property.split("/")[-1],
            })
    return results