"""
Google Search Console client helper.
Handles OAuth auth + all the data-fetching functions used by the dashboard API.
"""

import os
import datetime
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

SCOPES = [
    "https://www.googleapis.com/auth/webmasters.readonly",
    "https://www.googleapis.com/auth/analytics.readonly",
]
CLIENT_SECRET_FILE = "client_secret.json"
TOKEN_FILE = "token.json"

_credentials = None  # cached credentials (safe to share — this is just the token)


def get_service():
    """Build a fresh API client + HTTP connection for every call.
    The underlying httplib2 connection is NOT safe to share across
    concurrent requests, so we deliberately do not cache the service."""
    global _credentials
    if _credentials is None or not _credentials.valid:
        _credentials = get_credentials()
    return build("searchconsole", "v1", credentials=_credentials, cache_discovery=False)


def get_credentials():
    creds = None
    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRET_FILE, SCOPES)
            creds = flow.run_local_server(port=0, prompt="select_account")

        with open(TOKEN_FILE, "w") as f:
            f.write(creds.to_json())

    return creds


def list_sites():
    service = get_service()
    result = service.sites().list().execute()
    return [s["siteUrl"] for s in result.get("siteEntry", [])]


def default_date_range(days=28):
    """GSC data usually has a 2-3 day delay, so end 3 days ago."""
    end = datetime.date.today() - datetime.timedelta(days=3)
    start = end - datetime.timedelta(days=days)
    return start.isoformat(), end.isoformat()


def query_search_analytics(site_url, dimensions, start_date=None, end_date=None,
                            row_limit=100, filters=None):
    """Generic search analytics query."""
    if not start_date or not end_date:
        start_date, end_date = default_date_range()

    body = {
        "startDate": start_date,
        "endDate": end_date,
        "dimensions": dimensions,
        "rowLimit": row_limit,
    }
    if filters:
        body["dimensionFilterGroups"] = [{"filters": filters}]

    service = get_service()
    response = service.searchanalytics().query(siteUrl=site_url, body=body).execute()
    return response.get("rows", [])


def get_summary(site_url, start_date=None, end_date=None):
    """Overall totals for the period (no dimension breakdown)."""
    rows = query_search_analytics(site_url, dimensions=[], start_date=start_date,
                                   end_date=end_date, row_limit=1)
    if not rows:
        return {"clicks": 0, "impressions": 0, "ctr": 0, "position": 0}
    r = rows[0]
    return {
        "clicks": r.get("clicks", 0),
        "impressions": r.get("impressions", 0),
        "ctr": round(r.get("ctr", 0) * 100, 2),
        "position": round(r.get("position", 0), 1),
    }


def get_queries(site_url, start_date=None, end_date=None, limit=25):
    rows = query_search_analytics(site_url, dimensions=["query"], start_date=start_date,
                                   end_date=end_date, row_limit=limit)
    return [
        {
            "query": r["keys"][0],
            "clicks": r.get("clicks", 0),
            "impressions": r.get("impressions", 0),
            "ctr": round(r.get("ctr", 0) * 100, 2),
            "position": round(r.get("position", 0), 1),
        }
        for r in rows
    ]


def get_pages(site_url, start_date=None, end_date=None, limit=25):
    rows = query_search_analytics(site_url, dimensions=["page"], start_date=start_date,
                                   end_date=end_date, row_limit=limit)
    return [
        {
            "page": r["keys"][0],
            "clicks": r.get("clicks", 0),
            "impressions": r.get("impressions", 0),
            "ctr": round(r.get("ctr", 0) * 100, 2),
            "position": round(r.get("position", 0), 1),
        }
        for r in rows
    ]


def get_devices(site_url, start_date=None, end_date=None):
    rows = query_search_analytics(site_url, dimensions=["device"], start_date=start_date,
                                   end_date=end_date, row_limit=10)
    return [
        {
            "device": r["keys"][0],
            "clicks": r.get("clicks", 0),
            "impressions": r.get("impressions", 0),
            "ctr": round(r.get("ctr", 0) * 100, 2),
            "position": round(r.get("position", 0), 1),
        }
        for r in rows
    ]


def get_countries(site_url, start_date=None, end_date=None, limit=15):
    rows = query_search_analytics(site_url, dimensions=["country"], start_date=start_date,
                                   end_date=end_date, row_limit=limit)
    return [
        {
            "country": r["keys"][0],
            "clicks": r.get("clicks", 0),
            "impressions": r.get("impressions", 0),
            "ctr": round(r.get("ctr", 0) * 100, 2),
            "position": round(r.get("position", 0), 1),
        }
        for r in rows
    ]


def get_trend(site_url, start_date=None, end_date=None):
    """Daily clicks/impressions trend for charting."""
    rows = query_search_analytics(site_url, dimensions=["date"], start_date=start_date,
                                   end_date=end_date, row_limit=1000)
    rows.sort(key=lambda r: r["keys"][0])
    return [
        {
            "date": r["keys"][0],
            "clicks": r.get("clicks", 0),
            "impressions": r.get("impressions", 0),
            "ctr": round(r.get("ctr", 0) * 100, 2),
            "position": round(r.get("position", 0), 1),
        }
        for r in rows
    ]


def _previous_period(start_date, end_date):
    """Given a date range, return the immediately preceding period of the same length."""
    s = datetime.date.fromisoformat(start_date)
    e = datetime.date.fromisoformat(end_date)
    length = (e - s).days
    prev_end = s - datetime.timedelta(days=1)
    prev_start = prev_end - datetime.timedelta(days=length)
    return prev_start.isoformat(), prev_end.isoformat()


def _pct_change(current, previous):
    if previous == 0:
        return None  # can't compute a meaningful % change from zero
    return round(((current - previous) / previous) * 100, 1)


def get_comparison(site_url, start_date=None, end_date=None):
    """Current period vs. the immediately preceding period of the same length."""
    if not start_date or not end_date:
        start_date, end_date = default_date_range()
    prev_start, prev_end = _previous_period(start_date, end_date)

    current = get_summary(site_url, start_date, end_date)
    previous = get_summary(site_url, prev_start, prev_end)

    return {
        "current_period": {"start": start_date, "end": end_date, **current},
        "previous_period": {"start": prev_start, "end": prev_end, **previous},
        "change": {
            "clicks_pct": _pct_change(current["clicks"], previous["clicks"]),
            "impressions_pct": _pct_change(current["impressions"], previous["impressions"]),
            "ctr_pct": _pct_change(current["ctr"], previous["ctr"]),
            # position is "lower is better" — report the raw point difference, negative = improved
            "position_change": round(current["position"] - previous["position"], 1),
        },
    }


def get_movers(site_url, start_date=None, end_date=None, limit=10, min_impressions=5):
    """Queries whose average position improved or declined the most between
    the current period and the immediately preceding period."""
    if not start_date or not end_date:
        start_date, end_date = default_date_range()
    prev_start, prev_end = _previous_period(start_date, end_date)

    current_rows = query_search_analytics(site_url, dimensions=["query"], start_date=start_date,
                                           end_date=end_date, row_limit=1000)
    previous_rows = query_search_analytics(site_url, dimensions=["query"], start_date=prev_start,
                                            end_date=prev_end, row_limit=1000)

    prev_map = {r["keys"][0]: r for r in previous_rows}

    movers = []
    for r in current_rows:
        query = r["keys"][0]
        prev = prev_map.get(query)
        if not prev:
            continue  # query didn't appear last period — not a fair comparison
        if r.get("impressions", 0) < min_impressions and prev.get("impressions", 0) < min_impressions:
            continue  # too little data to be meaningful

        curr_pos = r.get("position", 0)
        prev_pos = prev.get("position", 0)
        movers.append({
            "query": query,
            "current_position": round(curr_pos, 1),
            "previous_position": round(prev_pos, 1),
            "position_change": round(curr_pos - prev_pos, 1),  # negative = improved
            "current_clicks": r.get("clicks", 0),
            "previous_clicks": prev.get("clicks", 0),
        })

    gainers = sorted([m for m in movers if m["position_change"] < 0],
                      key=lambda m: m["position_change"])[:limit]
    losers = sorted([m for m in movers if m["position_change"] > 0],
                     key=lambda m: -m["position_change"])[:limit]

    return {"gainers": gainers, "losers": losers}


def get_sitemaps(site_url):
    service = get_service()
    result = service.sitemaps().list(siteUrl=site_url).execute()
    sitemaps = []
    for s in result.get("sitemap", []):
        sitemaps.append({
            "path": s.get("path"),
            "last_submitted": s.get("lastSubmitted"),
            "last_downloaded": s.get("lastDownloaded"),
            "is_pending": s.get("isPending", False),
            "errors": s.get("errors", 0),
            "warnings": s.get("warnings", 0),
            "contents": [
                {"type": c.get("type"), "submitted": c.get("submitted"), "indexed": c.get("indexed")}
                for c in s.get("contents", [])
            ],
        })
    return sitemaps