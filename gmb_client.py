"""
Google Business Profile (GMB) client helper.

Reuses the same OAuth credentials as gsc_client/ga4_client (one token,
business.manage scope must already be granted — see gsc_client.get_credentials()).

APIs used (must be enabled in the GCP project):
  - mybusinessbusinessinformation (v1) -> location details, hours, attributes, categories
  - businessprofileperformance (v1)    -> daily metrics + search keywords

Optional (only needed for list_accounts / list_locations_for_account):
  - mybusinessaccountmanagement (v1)   -> enable separately in Cloud Console if you
    want to browse accounts/locations instead of storing location_id per client
    in site_gmb_map.json (recommended for 100+ clients — more reliable, no extra API).
"""

from datetime import date, timedelta
from googleapiclient.discovery import build

from gsc_client import get_credentials, default_date_range  # reuse shared auth + date helper

# Full metric set — Business Profile Performance API DailyMetric enum.
# https://developers.google.com/my-business/reference/performance/rest/v1/DailyMetric
_DAILY_METRICS = [
    "BUSINESS_IMPRESSIONS_DESKTOP_MAPS",
    "BUSINESS_IMPRESSIONS_DESKTOP_SEARCH",
    "BUSINESS_IMPRESSIONS_MOBILE_MAPS",
    "BUSINESS_IMPRESSIONS_MOBILE_SEARCH",
    "BUSINESS_CONVERSATIONS",
    "BUSINESS_DIRECTION_REQUESTS",
    "CALL_CLICKS",
    "WEBSITE_CLICKS",
    "BUSINESS_BOOKINGS",
    "BUSINESS_FOOD_ORDERS",
    "BUSINESS_FOOD_MENU_CLICKS",
]

_LOCATION_READ_MASK = (
    "name,title,storefrontAddress,phoneNumbers,websiteUri,"
    "regularHours,metadata,categories,openInfo,profile,latlng"
)


# ---------------- service builders ----------------

def get_account_service():
    return build("mybusinessaccountmanagement", "v1", credentials=get_credentials())

def get_info_service():
    return build("mybusinessbusinessinformation", "v1", credentials=get_credentials())

def get_performance_service():
    return build("businessprofileperformance", "v1", credentials=get_credentials())


# ---------------- accounts / locations discovery (optional API) ----------------

def list_accounts():
    """Requires mybusinessaccountmanagement API enabled separately."""
    service = get_account_service()
    resp = service.accounts().list().execute()
    return [
        {"name": a["name"], "account_name": a.get("accountName"), "type": a.get("type")}
        for a in resp.get("accounts", [])
    ]

def list_locations_for_account(account_name: str):
    """account_name looks like 'accounts/1234567890'. Requires mybusinessaccountmanagement."""
    service = get_info_service()
    resp = service.accounts().locations().list(
        parent=account_name, readMask=_LOCATION_READ_MASK,
    ).execute()
    return [_shape_location(loc) for loc in resp.get("locations", [])]


# ---------------- location profile (no account API needed if you already have location_id) ----------------

def _format_address(addr):
    if not addr:
        return None
    parts = addr.get("addressLines", []) + [
        addr.get("locality"), addr.get("administrativeArea"), addr.get("postalCode")
    ]
    return ", ".join(p for p in parts if p)

def _format_hours(regular_hours):
    if not regular_hours:
        return None
    out = {}
    for period in regular_hours.get("periods", []):
        day = period.get("openDay")
        out.setdefault(day, []).append({
            "open": period.get("openTime", {}).get("hours", 0),
            "close": period.get("closeTime", {}).get("hours", 0),
        })
    return out

def _shape_location(loc):
    metadata = loc.get("metadata") or {}
    open_info = loc.get("openInfo") or {}
    primary_cat = ((loc.get("categories") or {}).get("primaryCategory") or {})
    latlng = loc.get("latlng") or {}
    return {
        "location_id": loc["name"].split("/")[-1],
        "title": loc.get("title"),
        "address": _format_address(loc.get("storefrontAddress")),
        "phone": (loc.get("phoneNumbers") or {}).get("primaryPhone"),
        "website": loc.get("websiteUri"),
        "maps_uri": metadata.get("mapsUri"),
        "new_review_uri": metadata.get("newReviewUri"),
        "primary_category": primary_cat.get("displayName"),
        "additional_categories": [c.get("displayName") for c in (loc.get("categories") or {}).get("additionalCategories", [])],
        "labels": loc.get("labels", []),
        "status": open_info.get("status"),  # OPEN / CLOSED_TEMPORARILY / CLOSED_PERMANENTLY
        "hours": _format_hours(loc.get("regularHours")),
        "description": (loc.get("profile") or {}).get("description"),
        "lat": latlng.get("latitude"),
        "lng": latlng.get("longitude"),
        "can_update": metadata.get("canUpdate", False),
        "duplicate_location": metadata.get("duplicate"),
    }

def get_location_details(location_id: str):
    """Full profile for one location. Only needs Business Information API —
    no account listing required if you already have the location_id."""
    service = get_info_service()
    loc = service.locations().get(
        name=f"locations/{location_id}", readMask=_LOCATION_READ_MASK,
    ).execute()
    return _shape_location(loc)

def get_locations_batch(location_ids: list):
    """Fetch details for several location_ids in one pass — used for the
    admin portfolio view / multi-client summary. Not a true batch API call
    (Business Information API has no batchGet for full reads), just loops;
    fine for a few dozen at a time."""
    return [get_location_details(lid) for lid in location_ids]


# ---------------- write operations (use with care — edits the LIVE listing) ----------------

def update_hours(location_id: str, hours_by_day: dict):
    """hours_by_day: {"MONDAY": [{"open": "09:00", "close": "18:00"}], ...}
    Overwrites regularHours entirely — pass the FULL week, not a partial patch."""
    periods = []
    for day, ranges in hours_by_day.items():
        for r in ranges:
            oh, om = r["open"].split(":")
            ch, cm = r["close"].split(":")
            periods.append({
                "openDay": day,
                "openTime": {"hours": int(oh), "minutes": int(om)},
                "closeDay": day,
                "closeTime": {"hours": int(ch), "minutes": int(cm)},
            })
    service = get_info_service()
    return service.locations().patch(
        name=f"locations/{location_id}",
        updateMask="regularHours",
        body={"regularHours": {"periods": periods}},
    ).execute()

def update_description(location_id: str, description: str):
    service = get_info_service()
    return service.locations().patch(
        name=f"locations/{location_id}",
        updateMask="profile.description",
        body={"profile": {"description": description}},
    ).execute()


# ---------------- category search (for onboarding new listings) ----------------

def search_categories(query: str, region_code: str = "IN", language_code: str = "en"):
    service = get_info_service()
    resp = service.categories().list(
        regionCode=region_code, languageCode=language_code,
        filter=f'displayName="{query}"', view="BASIC",
    ).execute()
    return [{"category_id": c["name"], "display_name": c.get("displayName")} for c in resp.get("categories", [])]


# ---------------- performance metrics ----------------

def get_trend(location_id: str, start_date: str = None, end_date: str = None):
    """Daily values for every metric in _DAILY_METRICS, one row per day."""
    if not start_date or not end_date:
        start_date, end_date = default_date_range()
    s = date.fromisoformat(start_date)
    e = date.fromisoformat(end_date)

    service = get_performance_service()
    resp = service.locations().fetchMultiDailyMetricsTimeSeries(
        location=f"locations/{location_id}",
        dailyMetrics=_DAILY_METRICS,
        **{
            "dailyRange.startDate.year": s.year,
            "dailyRange.startDate.month": s.month,
            "dailyRange.startDate.day": s.day,
            "dailyRange.endDate.year": e.year,
            "dailyRange.endDate.month": e.month,
            "dailyRange.endDate.day": e.day,
        },
    ).execute()

    by_date = {}
    for series in resp.get("multiDailyMetricTimeSeries", []):
        for ts in series.get("dailyMetricTimeSeries", []):
            metric = ts["dailyMetric"]
            for dp in ts.get("timeSeries", {}).get("datedValues", []):
                d = dp.get("date", {})
                day_str = f"{d.get('year')}-{d.get('month', 0):02d}-{d.get('day', 0):02d}"
                by_date.setdefault(day_str, {"date": day_str})
                by_date[day_str][metric.lower()] = int(dp.get("value", 0))

    rows = list(by_date.values())
    rows.sort(key=lambda r: r["date"])
    return rows

def _totals(rows):
    totals = {m.lower(): 0 for m in _DAILY_METRICS}
    for r in rows:
        for m in totals:
            totals[m] += r.get(m, 0)
    total_impressions = sum(totals[m] for m in totals if m.startswith("business_impressions"))
    return {
        "total_impressions": total_impressions,
        "website_clicks": totals.get("website_clicks", 0),
        "call_clicks": totals.get("call_clicks", 0),
        "direction_requests": totals.get("business_direction_requests", 0),
        "conversations": totals.get("business_conversations", 0),
        "bookings": totals.get("business_bookings", 0),
        "food_orders": totals.get("business_food_orders", 0),
        "food_menu_clicks": totals.get("business_food_menu_clicks", 0),
    }

def get_summary(location_id: str, start_date: str = None, end_date: str = None):
    return _totals(get_trend(location_id, start_date, end_date))

def get_comparison(location_id: str, start_date: str, end_date: str):
    """Current period vs the immediately preceding period of equal length —
    same convention as gsc_client.get_comparison."""
    s = date.fromisoformat(start_date)
    e = date.fromisoformat(end_date)
    days = (e - s).days + 1
    prev_end = s - timedelta(days=1)
    prev_start = prev_end - timedelta(days=days - 1)

    current = get_summary(location_id, start_date, end_date)
    previous = get_summary(location_id, prev_start.isoformat(), prev_end.isoformat())
    return {"current": current, "previous": previous,
            "previous_start": prev_start.isoformat(), "previous_end": prev_end.isoformat()}

def get_search_keywords(location_id: str, months_back: int = 1):
    """Search terms people used to find this listing — monthly only,
    that's the finest grain the Performance API exposes for this metric."""
    today = date.today()
    first_of_this_month = today.replace(day=1)
    target = (first_of_this_month - timedelta(days=1)).replace(day=1)
    for _ in range(months_back - 1):
        target = (target - timedelta(days=1)).replace(day=1)

    service = get_performance_service()
    resp = service.locations().searchkeywords().impressions().monthly().list(
        parent=f"locations/{location_id}",
        **{
               "monthlyRange.startMonth.year": target.year,
               "monthlyRange.startMonth.month": target.month,
               "monthlyRange.endMonth.year": target.year,
               "monthlyRange.endMonth.month": target.month,
        },
    ).execute()

    rows = [
        {
            "keyword": item.get("searchKeyword"),
            "impressions": item.get("insightsValue", {}).get("value")
                or item.get("insightsValue", {}).get("threshold"),
        }
        for item in resp.get("searchKeywordsCounts", [])
    ]
    rows.sort(key=lambda r: -(r["impressions"] or 0))
    return rows


# ---------------- portfolio view (across many/all clients at once) ----------------

def get_portfolio_summary(location_ids: dict, start_date: str = None, end_date: str = None):
    """location_ids: {site_url: location_id} — e.g. loaded straight from
    site_gmb_map.json. Returns one summary row per client, plus errors for
    any location that failed (e.g. access not granted yet), so one bad
    client doesn't 500 the whole dashboard."""
    if not start_date or not end_date:
        start_date, end_date = default_date_range()
    rows, errors = [], []
    for site_url, loc_id in location_ids.items():
        try:
            data = get_summary(loc_id, start_date, end_date)
            rows.append({"site_url": site_url, "location_id": loc_id, **data})
        except Exception as ex:
            errors.append({"site_url": site_url, "location_id": loc_id, "error": str(ex)})
    rows.sort(key=lambda r: -r["total_impressions"])
    return {"start_date": start_date, "end_date": end_date, "rows": rows, "errors": errors}