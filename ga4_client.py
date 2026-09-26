"""
Google Analytics 4 (GA4) client helper.
Reuses the same OAuth credentials as gsc_client (one token, two scopes).
"""

from datetime import datetime, date, time, timedelta
from google.analytics.data_v1beta import BetaAnalyticsDataClient
from google.analytics.data_v1beta.types import (
    RunReportRequest,
    DateRange,
    Dimension,
    Metric,
)

import gsc_client
from gsc_client import get_credentials, default_date_range  # reuse shared auth + date helper
from domain_utils import extract_domain
from db import get_session
from models import SiteGA4Map


def _resolve_account_for_property(property_id):
    """Reverse lookup: which site is this GA4 property mapped to
    (site_ga4_map), then which registered account owns that site
    (site_account_map) — so GA4 calls automatically use the right one of
    your 5 Gmail accounts, same as GSC does, without you telling it."""
    if not property_id:
        return None
    with get_session() as session:
        from models import SiteAccountMap
        ga4_row = session.query(SiteGA4Map).filter(
            SiteGA4Map.ga4_property_id == property_id
        ).first()
        if not ga4_row:
            return None
        acct_row = session.get(SiteAccountMap, ga4_row.site_url)
        return acct_row.gsc_account_id if acct_row else None


def get_client(property_id=None):
    """Fresh GA4 client per call — same reasoning as gsc_client.get_service():
    avoid sharing a connection across concurrent requests. Pass property_id
    so this resolves the correct account automatically (see
    _resolve_account_for_property); falls back to the active account if
    the property isn't mapped yet (old behaviour, nothing breaks)."""
    account_id = _resolve_account_for_property(property_id)
    if account_id:
        creds = gsc_client.get_credentials_for(account_id, interactive=True)
    else:
        creds = get_credentials()
    return BetaAnalyticsDataClient(credentials=creds)


def _prop(property_id: str) -> str:
    return f"properties/{property_id}"


def get_summary(property_id, start_date=None, end_date=None):
    if not start_date or not end_date:
        start_date, end_date = default_date_range()

    client = get_client(property_id)
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

    client = get_client(property_id)
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

    client = get_client(property_id)
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

    client = get_client(property_id)
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


# ---------------- multi-account auto-discovery ----------------

def list_all_properties_for(account_id):
    """Same as list_all_properties() but for an explicit account, not the
    active one — used by discover_property_map() to scan every registered
    account. Also fetches each property's web data stream(s) so we get an
    actual website URL to match against, not just a display name."""
    from google.analytics.admin_v1beta import AnalyticsAdminServiceClient

    creds = gsc_client.get_credentials_for(account_id, interactive=False)
    client = AnalyticsAdminServiceClient(credentials=creds)

    results = []
    for summary in client.list_account_summaries():
        for prop in summary.property_summaries:
            property_id = prop.property.split("/")[-1]
            urls = []
            try:
                for stream in client.list_data_streams(parent=prop.property):
                    web = getattr(stream, "web_stream_data", None)
                    if web and web.default_uri:
                        urls.append(web.default_uri)
            except Exception:
                pass  # property with no web stream (app-only), or no access — skip its URLs
            results.append({
                "account_name": summary.display_name,
                "property_name": prop.display_name,
                "property_id": property_id,
                "urls": urls,
            })
    return results


def discover_property_map(known_site_urls):
    """Scans every registered account for GA4 properties, matches each
    property's website URL (by domain) against known_site_urls (the sites
    list_all_sites_across_accounts() just found in GSC), and auto-fills
    site_ga4_map for every match — replaces manually copy-pasting property
    IDs into the JSON file / admin form one by one.

    known_site_urls: iterable of GSC-style site_url strings.
    Returns {"matched": [{"site_url", "property_id"}], "unmatched_properties":
    [{"property_name", "property_id"}], "accounts_needing_login": [...]}."""
    domain_to_site = {extract_domain(u): u for u in known_site_urls}
    matched, unmatched, needs_login = [], [], []

    with get_session() as session:
        for account in gsc_client.list_accounts():
            account_id = account["account_id"]
            try:
                properties = list_all_properties_for(account_id)
            except gsc_client.AccountNeedsLoginError:
                needs_login.append(account_id)
                continue
            except Exception:
                continue  # e.g. Analytics Admin API not enabled for this account yet

            for prop in properties:
                site_url = None
                for url in prop["urls"]:
                    site_url = domain_to_site.get(extract_domain(url))
                    if site_url:
                        break
                if not site_url:
                    unmatched.append({"property_name": prop["property_name"],
                                       "property_id": prop["property_id"]})
                    continue
                row = session.get(SiteGA4Map, site_url)
                if row is None:
                    row = SiteGA4Map(site_url=site_url, ga4_property_id=prop["property_id"])
                else:
                    row.ga4_property_id = prop["property_id"]
                session.add(row)
                matched.append({"site_url": site_url, "property_id": prop["property_id"]})

    return {"matched": matched, "unmatched_properties": unmatched, "accounts_needing_login": needs_login}