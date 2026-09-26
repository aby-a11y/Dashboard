"""
Google Search Console client helper.
Handles OAuth auth + all the data-fetching functions used by the dashboard API.

---------------- Multi-account support ----------------
Different clients' sites can live under different Google accounts (each
verified in a separate GSC/GA4 login). Instead of one hardcoded
client_secret.json/token.json, credentials are now organized per "account":

    accounts/
      <account_id>/
        client_secret.json   <- OAuth client secret downloaded from Google Cloud Console
        token.json           <- created automatically on first login for that account

    accounts.json          {account_id: {"label": "my@gmail.com"}}
    active_account.txt     just the account_id currently in use, e.g. "my_gmail"

Only ONE account is "active" at a time — every GSC/GA4 call uses whichever
account is active. Switch it via POST /api/admin/accounts/switch (see main.py).
ga4_client.py reuses get_credentials() from here, so switching accounts here
switches GA4 too.
"""

import os
import json
import datetime
import hashlib
import cache
from db import get_session
from models import GscTrackedKeyword, SiteAccountMap
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

BASE_SCOPES = [
    "https://www.googleapis.com/auth/webmasters.readonly",
    "https://www.googleapis.com/auth/analytics.readonly",
]
GMB_SCOPE = "https://www.googleapis.com/auth/business.manage"
# Requested opportunistically. Not every Google Cloud project has GMB access
# approved (Google approves the Business Profile API per-project, not per
# Google account), so this scope alone must never be allowed to break GSC/GA4/
# ranking for an account that doesn't have it yet — see get_credentials().
SCOPES = BASE_SCOPES + [GMB_SCOPE]

ACCOUNTS_DIR = "accounts"
ACCOUNTS_FILE = "accounts.json"          # {account_id: {"label": "my@gmail.com"}}
ACTIVE_ACCOUNT_FILE = "active_account.txt"  # just the active account_id, e.g. "my_gmail"

_credentials_cache = {}  # {account_id: Credentials} — keeps every account's token warm in memory

GSC_CACHE_TTL_SECONDS = 24 * 60 * 60  # 24 hours
GSC_CACHE_KEY_PREFIX = "gsc_query_cache:"

# Redis-backed now (see cache.py) instead of one shared gsc_query_cache.json
# that every request had to fully read + rewrite. Each entry gets its own
# key with a native TTL, so there's no full-file load/save and no manual
# pruning step needed anymore — Redis expires stale entries on its own,
# which matters once this is fielding queries for 200-300 clients at once.


def _gsc_cache_key(site_url, dimensions, start_date, end_date, row_limit, filters):
    payload = json.dumps({
        "site_url": site_url,
        "dimensions": dimensions,
        "start_date": start_date,
        "end_date": end_date,
        "row_limit": row_limit,
        "filters": filters,
    }, sort_keys=True)
    return GSC_CACHE_KEY_PREFIX + hashlib.md5(payload.encode()).hexdigest()


def _gsc_cache_get(cache_key):
    return cache.get_json(cache_key)


def _gsc_cache_set(cache_key, rows):
    cache.set_json(cache_key, {"rows": rows}, ttl_seconds=GSC_CACHE_TTL_SECONDS)


# ---------------- account registry ----------------

def _load_accounts():
    if not os.path.exists(ACCOUNTS_FILE):
        return {}
    with open(ACCOUNTS_FILE, "r") as f:
        return json.load(f)


def _save_accounts(data):
    with open(ACCOUNTS_FILE, "w") as f:
        json.dump(data, f, indent=2)


def _account_paths(account_id):
    folder = os.path.join(ACCOUNTS_DIR, account_id)
    return {
        "folder": folder,
        "client_secret": os.path.join(folder, "client_secret.json"),
        "token": os.path.join(folder, "token.json"),
    }


def list_accounts():
    """Returns [{account_id, label, active}] for the admin UI's switcher."""
    accounts = _load_accounts()
    active = get_active_account_id()
    return [
        {"account_id": aid, "label": rec.get("label", aid), "active": aid == active}
        for aid, rec in accounts.items()
    ]


def add_account(account_id, label, client_secret_bytes):
    """Registers a new account and writes its client_secret.json to
    accounts/<account_id>/client_secret.json. Does NOT log in yet — the
    first call that needs Google data (e.g. switching to it, then hitting
    /api/sites) will trigger the OAuth flow and create token.json."""
    account_id = account_id.strip()
    if not account_id:
        raise ValueError("account_id is required")

    paths = _account_paths(account_id)
    os.makedirs(paths["folder"], exist_ok=True)
    with open(paths["client_secret"], "wb") as f:
        f.write(client_secret_bytes)

    accounts = _load_accounts()
    accounts[account_id] = {"label": label or account_id}
    _save_accounts(accounts)
    return {"account_id": account_id, "label": accounts[account_id]["label"]}


def delete_account(account_id):
    accounts = _load_accounts()
    if account_id not in accounts:
        return False
    del accounts[account_id]
    _save_accounts(accounts)
    _credentials_cache.pop(account_id, None)
    # NOTE: intentionally not deleting accounts/<account_id>/ from disk —
    # avoids accidentally nuking a token.json you'd need to re-auth from
    # scratch. Remove the folder manually if you're sure.
    return True


def get_active_account_id():
    if os.path.exists(ACTIVE_ACCOUNT_FILE):
        with open(ACTIVE_ACCOUNT_FILE, "r") as f:
            aid = f.read().strip()
            if aid:
                return aid
    # Fallback: no active_account.txt yet — use the first registered account
    accounts = _load_accounts()
    if accounts:
        return next(iter(accounts))
    return None


def set_active_account(account_id):
    accounts = _load_accounts()
    if account_id not in accounts:
        raise ValueError(f"No such account_id: {account_id}")
    with open(ACTIVE_ACCOUNT_FILE, "w") as f:
        f.write(account_id)
    return account_id


# ---------------- credentials (per active account) ----------------

class AccountNeedsLoginError(Exception):
    """Raised by get_credentials_for(..., interactive=False) when an
    account has no usable token yet. Used during multi-account scans so
    one never-logged-in account doesn't pop open a browser window (or
    worse, 5 of them at once) — the scan just skips it and reports it."""
    def __init__(self, account_id):
        self.account_id = account_id
        super().__init__(f"Account '{account_id}' needs an interactive login "
                          f"(switch to it once via the account switcher).")


def get_credentials_for(account_id, interactive=False):
    """Like get_credentials() but for an explicit account_id — not
    necessarily the active one. interactive=False (used by bulk scans)
    raises AccountNeedsLoginError instead of launching a browser."""
    creds = _credentials_cache.get(account_id)
    if creds and creds.valid:
        return creds

    paths = _account_paths(account_id)
    creds = None
    if os.path.exists(paths["token"]):
        creds = Credentials.from_authorized_user_file(paths["token"], SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception as ex:
                if not _is_invalid_scope_error(ex):
                    raise
                creds = Credentials.from_authorized_user_file(paths["token"], BASE_SCOPES)
                creds.refresh(Request())
        elif interactive:
            if not os.path.exists(paths["client_secret"]):
                raise RuntimeError(
                    f"Missing {paths['client_secret']} — put that account's OAuth "
                    f"client secret file there, then switch to it again."
                )
            try:
                flow = InstalledAppFlow.from_client_secrets_file(paths["client_secret"], SCOPES)
                creds = flow.run_local_server(port=0, prompt="select_account")
            except Exception as ex:
                if not _is_invalid_scope_error(ex):
                    raise
                flow = InstalledAppFlow.from_client_secrets_file(paths["client_secret"], BASE_SCOPES)
                creds = flow.run_local_server(port=0, prompt="select_account")
        else:
            raise AccountNeedsLoginError(account_id)

        with open(paths["token"], "w") as f:
            f.write(creds.to_json())

    _credentials_cache[account_id] = creds
    return creds


def resolve_account_for_site(site_url):
    """Which registered account actually has this site — from the
    auto-discovered site_account_map (see list_all_sites_across_accounts()).
    Falls back to the currently active account if this site hasn't been
    discovered yet, so nothing breaks for a brand-new/unmapped site —
    just re-run auto-discovery, or switch accounts manually this once."""
    with get_session() as session:
        row = session.get(SiteAccountMap, site_url)
        if row:
            return row.gsc_account_id
    return get_active_account_id()


def get_service(site_url=None):
    """Build a fresh API client + HTTP connection for every call.
    The underlying httplib2 connection is NOT safe to share across
    concurrent requests, so we deliberately do not cache the service.
    Pass site_url so this automatically uses whichever of your registered
    accounts actually has that site (see resolve_account_for_site) instead
    of always using whatever account happens to be "active"."""
    creds = get_credentials(site_url)
    return build("searchconsole", "v1", credentials=creds, cache_discovery=False)


def _is_invalid_scope_error(ex):
    return "invalid_scope" in str(ex).lower()


def get_credentials(site_url=None):
    account_id = resolve_account_for_site(site_url) if site_url else get_active_account_id()
    if not account_id:
        raise RuntimeError(
            "No Google account configured yet — add one via POST /api/admin/accounts "
            "(see accounts/ folder layout in gsc_client.py)."
        )
    return get_credentials_for(account_id, interactive=True)


def list_sites():
    cache_key = GSC_CACHE_KEY_PREFIX + "list_sites::" + (get_active_account_id() or "")
    entry = _gsc_cache_get(cache_key)
    if entry:
        return entry["rows"]

    service = get_service()
    result = service.sites().list().execute()
    sites = [s["siteUrl"] for s in result.get("siteEntry", [])]

    _gsc_cache_set(cache_key, sites)
    return sites


def list_sites_for(account_id):
    """Same as list_sites() but for an explicit account, not necessarily
    the active one — used by the multi-account scan below so it never
    needs to flip active_account.txt just to look."""
    cache_key = GSC_CACHE_KEY_PREFIX + "list_sites::" + account_id
    entry = _gsc_cache_get(cache_key)
    if entry:
        return entry["rows"]

    creds = get_credentials_for(account_id, interactive=False)
    service = build("searchconsole", "v1", credentials=creds, cache_discovery=False)
    result = service.sites().list().execute()
    sites = [s["siteUrl"] for s in result.get("siteEntry", [])]

    _gsc_cache_set(cache_key, sites)
    return sites


def list_all_sites_across_accounts():
    """Scans every registered account (all 5 Gmail logins, say) for the
    sites it has Search Console access to, and auto-fills site_account_map
    with which account owns each one. This is what makes a newly-granted
    client access show up in the dashboard on its own — no manual account
    switch, no manual DB edit. An account with no valid token yet (never
    logged into once) is skipped, not blocked on — it's reported back so
    you know to log into it once via the account switcher.

    Returns {"sites": [site_url, ...], "accounts_needing_login": [account_id, ...]}."""
    all_sites = []
    needs_login = []
    with get_session() as session:
        for account in list_accounts():
            account_id = account["account_id"]
            try:
                sites = list_sites_for(account_id)
            except AccountNeedsLoginError:
                needs_login.append(account_id)
                continue
            except Exception:
                # Token exists but refresh failed (expired/revoked), that
                # account's GCP project doesn't have the Search Console API
                # enabled, etc. Skip just this one account instead of
                # failing site-loading for every client — same treatment
                # as "needs login", since re-switching to it (which
                # re-triggers the OAuth flow) fixes both cases.
                needs_login.append(account_id)
                continue
            for site_url in sites:
                row = session.get(SiteAccountMap, site_url)
                if row is None:
                    row = SiteAccountMap(site_url=site_url, gsc_account_id=account_id)
                else:
                    row.gsc_account_id = account_id
                session.add(row)
                all_sites.append(site_url)
    return {"sites": sorted(set(all_sites)), "accounts_needing_login": needs_login}


def default_date_range(days=28):
    """GSC data usually has a 2-3 day delay, so end 3 days ago."""
    end = datetime.date.today() - datetime.timedelta(days=3)
    start = end - datetime.timedelta(days=days)
    return start.isoformat(), end.isoformat()


def query_search_analytics(site_url, dimensions, start_date=None, end_date=None,
                            row_limit=100, filters=None):
    """Generic search analytics query — results are cached to disk for
    GSC_CACHE_TTL_SECONDS (24h) since GSC data itself only refreshes once
    a day or two anyway. This is the single choke point every GSC-backed
    endpoint (admin dashboard AND client portal) goes through, so caching
    here caches everything downstream: summary, queries, pages, trend,
    rank tracker, keyword history — all of it. Cuts real Search Console
    API calls, server load, and GSC quota usage massively."""
    if not start_date or not end_date:
        start_date, end_date = default_date_range()

    cache_key = _gsc_cache_key(site_url, dimensions, start_date, end_date, row_limit, filters)
    entry = _gsc_cache_get(cache_key)
    if entry:
        return entry["rows"]

    body = {
        "startDate": start_date,
        "endDate": end_date,
        "dimensions": dimensions,
        "rowLimit": row_limit,
    }
    if filters:
        body["dimensionFilterGroups"] = [{"filters": filters}]

    service = get_service(site_url)
    response = service.searchanalytics().query(siteUrl=site_url, body=body).execute()
    rows = response.get("rows", [])

    _gsc_cache_set(cache_key, rows)
    return rows


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


def get_tracked_keywords(site_url):
    with get_session() as session:
        record = session.get(GscTrackedKeyword, site_url)
        return list(record.keywords) if record else []


def set_tracked_keywords(site_url, keywords):
    """Overwrite the tracked-keyword list for a site. Dedupes (case-insensitive),
    strips whitespace, drops empties, preserves the order given."""
    cleaned, seen = [], set()
    for kw in keywords:
        kw = (kw or "").strip()
        if kw and kw.lower() not in seen:
            cleaned.append(kw)
            seen.add(kw.lower())
    with get_session() as session:
        record = session.get(GscTrackedKeyword, site_url)
        if record is None:
            record = GscTrackedKeyword(site_url=site_url, keywords=cleaned)
        else:
            record.keywords = cleaned
        session.add(record)
    return cleaned


def get_keyword_position_history(site_url, keyword, start_date=None, end_date=None):
    """Daily average position (+ clicks/impressions) for one exact-match keyword.
    Powers the per-keyword trend chart in the Rank Tracker UI."""
    if not start_date or not end_date:
        start_date, end_date = default_date_range()
    rows = query_search_analytics(
        site_url, dimensions=["date"], start_date=start_date, end_date=end_date,
        row_limit=1000,
        filters=[{"dimension": "query", "operator": "equals", "expression": keyword}],
    )
    rows.sort(key=lambda r: r["keys"][0])
    return [
        {
            "date": r["keys"][0],
            "position": round(r.get("position", 0), 1),
            "clicks": r.get("clicks", 0),
            "impressions": r.get("impressions", 0),
        }
        for r in rows
    ]


def get_rank_tracker_summary(site_url, keywords, start_date=None, end_date=None):
    """Current vs. previous-period position for a list of exact-match tracked
    keywords — the classic rank-tracker table (keyword / position / change).
    A keyword with zero impressions in a period comes back as position=None
    ('not found' in the UI), matching how GSC actually behaves."""
    if not start_date or not end_date:
        start_date, end_date = default_date_range()
    prev_start, prev_end = _previous_period(start_date, end_date)

    results = []
    for kw in keywords:
        curr_rows = query_search_analytics(
            site_url, dimensions=["query"], start_date=start_date, end_date=end_date,
            row_limit=1, filters=[{"dimension": "query", "operator": "equals", "expression": kw}],
        )
        prev_rows = query_search_analytics(
            site_url, dimensions=["query"], start_date=prev_start, end_date=prev_end,
            row_limit=1, filters=[{"dimension": "query", "operator": "equals", "expression": kw}],
        )
        curr = curr_rows[0] if curr_rows else None
        prev = prev_rows[0] if prev_rows else None

        curr_pos = round(curr.get("position", 0), 1) if curr else None
        prev_pos = round(prev.get("position", 0), 1) if prev else None

        results.append({
            "keyword": kw,
            "position": curr_pos,
            "previous_position": prev_pos,
            # negative = improved (rank moved to a lower/better number) — same convention as get_movers
            "change": round(curr_pos - prev_pos, 1) if (curr_pos is not None and prev_pos is not None) else None,
            "clicks": curr.get("clicks", 0) if curr else 0,
            "impressions": curr.get("impressions", 0) if curr else 0,
        })
    return results


def get_sitemaps(site_url):
    cache_key = GSC_CACHE_KEY_PREFIX + "sitemaps::" + site_url
    entry = _gsc_cache_get(cache_key)
    if entry:
        return entry["rows"]

    service = get_service(site_url)
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

    _gsc_cache_set(cache_key, sitemaps)
    return sitemaps