"""
SEO Client Dashboard — FastAPI backend
Serves the dashboard UI + JSON API endpoints backed by Google Search Console.

Run with: uvicorn main:app
Then open: http://127.0.0.1:8000
"""

import datetime
from fastapi import FastAPI, Query, HTTPException, Header, Depends, UploadFile, File, Form
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from typing import Optional, List
from pydantic import BaseModel
import requests
import jwt as pyjwt
from googleapiclient.errors import HttpError
from google.api_core.exceptions import GoogleAPICallError

import gsc_client
import ga4_client
import serper_client
import client_auth
import workflow_store
import scheduler as email_scheduler
from pdf_report import generate_pdf
from fastapi.responses import StreamingResponse
import admin_auth

app = FastAPI(title="SEO Client Dashboard API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def _start_email_scheduler():
    """Starts the persisted APScheduler instance so pending workflow
    sends/reminders (from before a restart) pick back up automatically."""
    email_scheduler.start()


def _dates(start_date: Optional[str], end_date: Optional[str]):
    """Resolve + validate the date range. Raises a clean 400 error
    instead of letting a bad range reach the Google API."""
    if not start_date or not end_date:
        return gsc_client.default_date_range()

    try:
        s = datetime.date.fromisoformat(start_date)
        e = datetime.date.fromisoformat(end_date)
    except ValueError:
        raise HTTPException(status_code=400, detail="Dates must be in YYYY-MM-DD format")

    if s > e:
        raise HTTPException(status_code=400, detail="Start date must be before end date")

    return start_date, end_date


def _call(fn, *args, **kwargs):
    """Run a gsc_client function and translate errors into the real
    HTTP status instead of always returning 403."""
    try:
        return fn(*args, **kwargs)
    except HttpError as ex:
        status = ex.resp.status if ex.resp is not None else 502
        raise HTTPException(status_code=status, detail=str(ex))
    except GoogleAPICallError as ex:
        status = ex.code if isinstance(ex.code, int) else 502
        raise HTTPException(status_code=status, detail=ex.message or str(ex))
    except Exception as ex:
        raise HTTPException(status_code=500, detail=str(ex))


def get_client_site(authorization: Optional[str] = Header(None)) -> str:
    """Auth dependency for every /api/client/* endpoint. Resolves the
    caller's site_url strictly from their JWT — the client never gets
    to pass site_url themselves, so they physically cannot request
    another client's data."""
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")
    token = authorization.split(" ", 1)[1].strip()
    try:
        payload = client_auth.decode_token(token)
    except pyjwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Session expired, please log in again")
    except pyjwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Invalid session token")
    return payload["site_url"]

def get_current_admin(authorization: Optional[str] = Header(None)) -> str:
    """Auth dependency for every /api/admin/* endpoint."""
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")
    token = authorization.split(" ", 1)[1].strip()
    try:
        payload = admin_auth.decode_token(token)
    except pyjwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Session expired, please log in again")
    except pyjwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Invalid admin session")
    return payload["sub"]
    

# ---------------- Client login + client-scoped data (site locked to their own) ----------------

class ClientLoginBody(BaseModel):
    client_id: str
    password: str


@app.post("/api/client/login")
def api_client_login(body: ClientLoginBody):
    record = client_auth.authenticate(body.client_id, body.password)
    if not record:
        raise HTTPException(status_code=401, detail="Invalid client ID or password")
    token = client_auth.issue_token(body.client_id, record["site_url"])
    return {
        "token": token,
        "site_url": record["site_url"],
        "name": record.get("name"),
        "ga4_property_id": record.get("ga4_property_id"),
    }
    
class AdminLoginBody(BaseModel):
    username: str
    password: str
    
@app.post("/api/admin/login")
def api_admin_login(body: AdminLoginBody):
    if not admin_auth.authenticate(body.username, body.password):
        raise HTTPException(status_code=401, detail="Invalid admin credentials")
    return {"token": admin_auth.issue_token(body.username)}


@app.get("/api/client/report-link")
def api_client_report_link(site_url: str = Depends(get_client_site)):
    return {"site_url": site_url, "drive_link": client_auth.get_report_link(site_url)}


@app.get("/api/client/summary")
def api_client_summary(start_date: Optional[str] = None, end_date: Optional[str] = None,
                        site_url: str = Depends(get_client_site)):
    s, e = _dates(start_date, end_date)
    data = _call(gsc_client.get_summary, site_url, s, e)
    return {"site_url": site_url, "start_date": s, "end_date": e, **data}


@app.get("/api/client/queries")
def api_client_queries(start_date: Optional[str] = None, end_date: Optional[str] = None,
                        limit: int = Query(25, le=1000), site_url: str = Depends(get_client_site)):
    s, e = _dates(start_date, end_date)
    rows = _call(gsc_client.get_queries, site_url, s, e, limit)
    return {"site_url": site_url, "start_date": s, "end_date": e, "rows": rows}


@app.get("/api/client/pages")
def api_client_pages(start_date: Optional[str] = None, end_date: Optional[str] = None,
                      limit: int = Query(25, le=1000), site_url: str = Depends(get_client_site)):
    s, e = _dates(start_date, end_date)
    rows = _call(gsc_client.get_pages, site_url, s, e, limit)
    return {"site_url": site_url, "start_date": s, "end_date": e, "rows": rows}


@app.get("/api/client/devices")
def api_client_devices(start_date: Optional[str] = None, end_date: Optional[str] = None,
                        site_url: str = Depends(get_client_site)):
    s, e = _dates(start_date, end_date)
    rows = _call(gsc_client.get_devices, site_url, s, e)
    return {"site_url": site_url, "start_date": s, "end_date": e, "rows": rows}


@app.get("/api/client/countries")
def api_client_countries(start_date: Optional[str] = None, end_date: Optional[str] = None,
                          limit: int = Query(15, le=250), site_url: str = Depends(get_client_site)):
    s, e = _dates(start_date, end_date)
    rows = _call(gsc_client.get_countries, site_url, s, e, limit)
    return {"site_url": site_url, "start_date": s, "end_date": e, "rows": rows}


@app.get("/api/client/trend")
def api_client_trend(start_date: Optional[str] = None, end_date: Optional[str] = None,
                      site_url: str = Depends(get_client_site)):
    s, e = _dates(start_date, end_date)
    rows = _call(gsc_client.get_trend, site_url, s, e)
    return {"site_url": site_url, "start_date": s, "end_date": e, "rows": rows}


@app.get("/api/client/movers")
def api_client_movers(start_date: Optional[str] = None, end_date: Optional[str] = None,
                       limit: int = Query(10, le=50), site_url: str = Depends(get_client_site)):
    s, e = _dates(start_date, end_date)
    return _call(gsc_client.get_movers, site_url, s, e, limit)


@app.get("/api/client/sitemaps")
def api_client_sitemaps(site_url: str = Depends(get_client_site)):
    return {"site_url": site_url, "sitemaps": _call(gsc_client.get_sitemaps, site_url)}


@app.get("/api/client/comparison")
def api_client_comparison(start_date: Optional[str] = None, end_date: Optional[str] = None,
                           site_url: str = Depends(get_client_site)):
    s, e = _dates(start_date, end_date)
    return _call(gsc_client.get_comparison, site_url, s, e)


@app.get("/api/client/rank-tracker")
def api_client_rank_tracker(start_date: Optional[str] = None, end_date: Optional[str] = None,
                             site_url: str = Depends(get_client_site)):
    s, e = _dates(start_date, end_date)
    keywords = gsc_client.get_tracked_keywords(site_url)
    if not keywords:
        return {"site_url": site_url, "start_date": s, "end_date": e, "rows": []}
    rows = _call(gsc_client.get_rank_tracker_summary, site_url, keywords, s, e)
    return {"site_url": site_url, "start_date": s, "end_date": e, "rows": rows}


@app.get("/api/client/rank-tracker/history")
def api_client_rank_tracker_history(keyword: str, start_date: Optional[str] = None,
                                     end_date: Optional[str] = None, site_url: str = Depends(get_client_site)):
    s, e = _dates(start_date, end_date)
    rows = _call(gsc_client.get_keyword_position_history, site_url, keyword, s, e)
    return {"site_url": site_url, "keyword": keyword, "start_date": s, "end_date": e, "rows": rows}


@app.get("/api/client/serper/rankings")
def api_client_serper_rankings(site_url: str = Depends(get_client_site)):
    """Read-only cached view — clients never trigger a paid Serper refresh themselves."""
    return {"site_url": site_url, "rows": serper_client.get_cached_rankings(site_url)}


@app.get("/api/client/tracked-keywords")
def api_client_tracked_keywords(site_url: str = Depends(get_client_site)):
    """Read-only — the SEO team manages which keywords are tracked, via the admin dashboard."""
    return {"site_url": site_url, "keywords": gsc_client.get_tracked_keywords(site_url)}


@app.get("/api/client/export/csv")
def api_client_export_csv(data_type: str = Query(..., pattern="^(queries|pages|devices|countries|trend)$"),
                           start_date: Optional[str] = None, end_date: Optional[str] = None,
                           limit: int = Query(1000, le=5000), site_url: str = Depends(get_client_site)):
    import csv
    import io

    s, e = _dates(start_date, end_date)
    fetchers = {
        "queries": lambda: gsc_client.get_queries(site_url, s, e, limit),
        "pages": lambda: gsc_client.get_pages(site_url, s, e, limit),
        "devices": lambda: gsc_client.get_devices(site_url, s, e),
        "countries": lambda: gsc_client.get_countries(site_url, s, e, limit),
        "trend": lambda: gsc_client.get_trend(site_url, s, e),
    }
    rows = _call(fetchers[data_type])

    buf = io.StringIO()
    if rows:
        writer = csv.DictWriter(buf, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    buf.seek(0)

    filename = f"{data_type}_{s}_to_{e}.csv"
    return StreamingResponse(
        buf, media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@app.get("/api/client/ga4/summary")
def api_client_ga4_summary(property_id: str, start_date: Optional[str] = None, end_date: Optional[str] = None,
                            site_url: str = Depends(get_client_site)):
    s, e = _dates(start_date, end_date)
    data = _call(ga4_client.get_summary, property_id, s, e)
    return {"site_url": site_url, "property_id": property_id, "start_date": s, "end_date": e, **data}


@app.get("/api/client/ga4/trend")
def api_client_ga4_trend(property_id: str, start_date: Optional[str] = None, end_date: Optional[str] = None,
                          site_url: str = Depends(get_client_site)):
    s, e = _dates(start_date, end_date)
    rows = _call(ga4_client.get_trend, property_id, s, e)
    return {"site_url": site_url, "property_id": property_id, "start_date": s, "end_date": e, "rows": rows}


@app.get("/api/client/ga4/traffic")
def api_client_ga4_traffic(property_id: str, start_date: Optional[str] = None, end_date: Optional[str] = None,
                            site_url: str = Depends(get_client_site)):
    s, e = _dates(start_date, end_date)
    rows = _call(ga4_client.get_traffic_sources, property_id, s, e)
    return {"site_url": site_url, "property_id": property_id, "start_date": s, "end_date": e, "rows": rows}


@app.get("/api/client/ga4/pages")
def api_client_ga4_pages(property_id: str, start_date: Optional[str] = None, end_date: Optional[str] = None,
                          limit: int = Query(15, le=200), site_url: str = Depends(get_client_site)):
    s, e = _dates(start_date, end_date)
    rows = _call(ga4_client.get_top_pages, property_id, s, e, limit)
    return {"site_url": site_url, "property_id": property_id, "start_date": s, "end_date": e, "rows": rows}


# ---------------- Admin: manage client logins + report links ----------------
# NOTE: these are unauthenticated, matching the rest of this internal-only
# admin app (index.html itself has no login either). Keep this app behind
# a VPN / IP allowlist / reverse-proxy auth if it's reachable from the
# public internet.

class AdminClientBody(BaseModel):
    client_id: str
    site_url: str
    password: Optional[str] = None  # omit when just updating name/site/ga4 id
    name: Optional[str] = None
    ga4_property_id: Optional[str] = None


@app.post("/api/admin/clients")
def api_admin_create_client(body: AdminClientBody):
    try:
        return client_auth.create_or_update_client(
            body.client_id, body.site_url, body.password, body.name, body.ga4_property_id
        )
    except ValueError as ex:
        raise HTTPException(status_code=400, detail=str(ex))


@app.get("/api/admin/clients")
def api_admin_list_clients():
    return {"clients": client_auth.list_clients()}


@app.delete("/api/admin/clients/{client_id}")
def api_admin_delete_client(client_id: str):
    if not client_auth.delete_client(client_id):
        raise HTTPException(status_code=404, detail="No such client_id")
    return {"status": "deleted", "client_id": client_id}


class ReportLinkBody(BaseModel):
    site_url: str
    drive_link: str


@app.post("/api/admin/report-link")
def api_admin_set_report_link(body: ReportLinkBody):
    client_auth.set_report_link(body.site_url, body.drive_link)
    return {"site_url": body.site_url, "drive_link": body.drive_link}


@app.get("/api/admin/report-link")
def api_admin_get_report_link(site_url: str):
    return {"site_url": site_url, "drive_link": client_auth.get_report_link(site_url)}


class ReportEmailBody(BaseModel):
    site_url: str
    email: str


@app.post("/api/admin/report-email")
def api_admin_set_report_email(body: ReportEmailBody):
    """Save the client's owner email once per site — the workflow re-uses
    it every time so you don't have to re-type it for each new report."""
    client_auth.set_report_email(body.site_url, body.email)
    return {"site_url": body.site_url, "email": body.email}


@app.get("/api/admin/report-email")
def api_admin_get_report_email(site_url: str):
    return {"site_url": site_url, "email": client_auth.get_report_email(site_url)}


# ---------------- Email report workflow: send + 24h-spaced reminders (x3) ----------------

class WorkflowCreateBody(BaseModel):
    site_url: str
    email: str
    scheduled_time: str  # ISO datetime, e.g. "2026-08-10T09:00:00"
    ga4_property_id: Optional[str] = None
    drive_link: Optional[str] = None       # the client-facing report link included in the email
    custom_message: Optional[str] = None   # optional — replaces the default email intro text
    login_id: Optional[str] = None         # optional — included in the email as "Login ID"
    login_password: Optional[str] = None   # optional — included in the email as "Password"
    recurrence: str = "once"               # "once" | "monthly"
    send_reminders: bool = True            # 3x/24h-apart reminders after each send


@app.post("/api/admin/workflow/create")
def api_create_workflow(body: WorkflowCreateBody):
    if body.recurrence not in ("once", "monthly"):
        raise HTTPException(status_code=400, detail="recurrence must be 'once' or 'monthly'")
    if not email_scheduler.email_client.is_configured():
        raise HTTPException(
            status_code=400,
            detail="GMAIL_USER / GMAIL_APP_PASSWORD not set in .env — see email_client.py",
        )
    try:
        run_date = datetime.datetime.fromisoformat(body.scheduled_time)
    except ValueError:
        raise HTTPException(status_code=400, detail="scheduled_time must be an ISO datetime, e.g. 2026-08-10T09:00:00")

    # remember the owner email + report link against this site for next time
    client_auth.set_report_email(body.site_url, body.email)
    if body.drive_link:
        client_auth.set_report_link(body.site_url, body.drive_link)

    wf = workflow_store.create_workflow(
        site_url=body.site_url, email=body.email,
        scheduled_time=body.scheduled_time, ga4_property_id=body.ga4_property_id,
        drive_link=body.drive_link, custom_message=body.custom_message,
        login_id=body.login_id, login_password=body.login_password,
        recurrence=body.recurrence, send_reminders=body.send_reminders,
    )
    email_scheduler.schedule_workflow(wf["workflow_id"], run_date)
    return wf


# ---------------- Bulk / monthly scheduling across many clients (anti-spam stagger) ----------------
# Sending all clients' reports at the exact same instant looks like a spam
# blast to mail providers. This spreads sends across several days and
# staggers them by a few minutes within each day, e.g. 100 clients at
# daily_batch_size=25 -> 4 days, ~15 min apart within each day.

class BulkWorkflowItem(BaseModel):
    site_url: str
    email: Optional[str] = None        # falls back to the site's saved report email
    ga4_property_id: Optional[str] = None
    drive_link: Optional[str] = None   # falls back to the site's saved report link
    login_id: Optional[str] = None
    login_password: Optional[str] = None


class BulkWorkflowCreateBody(BaseModel):
    items: List[BulkWorkflowItem]
    start_date: str                    # "YYYY-MM-DD" — first send day
    start_hour: int = 9                # hour (0-23, Asia/Kolkata) of the first send each day
    daily_batch_size: int = 25         # how many clients get sent to per day
    minutes_between_sends: int = 15    # stagger between individual sends within a day
    recurrence: str = "monthly"        # "once" | "monthly"
    send_reminders: bool = False       # off by default for bulk/monthly — avoids extra nagging
    custom_message: Optional[str] = None


@app.post("/api/admin/workflow/bulk-create")
def api_bulk_create_workflow(body: BulkWorkflowCreateBody):
    if body.recurrence not in ("once", "monthly"):
        raise HTTPException(status_code=400, detail="recurrence must be 'once' or 'monthly'")
    if body.daily_batch_size < 1:
        raise HTTPException(status_code=400, detail="daily_batch_size must be at least 1")
    if not email_scheduler.email_client.is_configured():
        raise HTTPException(
            status_code=400,
            detail="GMAIL_USER / GMAIL_APP_PASSWORD not set in .env — see email_client.py",
        )
    try:
        start_date = datetime.date.fromisoformat(body.start_date)
    except ValueError:
        raise HTTPException(status_code=400, detail="start_date must be YYYY-MM-DD")

    created, skipped = [], []
    for i, item in enumerate(body.items):
        email = item.email or client_auth.get_report_email(item.site_url)
        if not email:
            skipped.append({"site_url": item.site_url, "reason": "No email on file — set one first (via Client Access or a single workflow)."})
            continue

        drive_link = item.drive_link or client_auth.get_report_link(item.site_url)

        day_offset = i // body.daily_batch_size
        slot_in_day = i % body.daily_batch_size
        send_dt = datetime.datetime.combine(
            start_date + datetime.timedelta(days=day_offset),
            datetime.time(hour=body.start_hour),
        ) + datetime.timedelta(minutes=body.minutes_between_sends * slot_in_day)

        client_auth.set_report_email(item.site_url, email)
        if drive_link:
            client_auth.set_report_link(item.site_url, drive_link)

        wf = workflow_store.create_workflow(
            site_url=item.site_url, email=email, scheduled_time=send_dt.isoformat(),
            ga4_property_id=item.ga4_property_id, drive_link=drive_link,
            custom_message=body.custom_message, recurrence=body.recurrence,
            send_reminders=body.send_reminders,
            login_id=item.login_id, login_password=item.login_password,
        )
        email_scheduler.schedule_workflow(wf["workflow_id"], send_dt)
        created.append({
            "workflow_id": wf["workflow_id"], "site_url": item.site_url,
            "email": email, "scheduled_time": send_dt.isoformat(),
        })

    total_days = (len(body.items) + body.daily_batch_size - 1) // body.daily_batch_size if body.items else 0
    return {"created": created, "skipped": skipped, "total_days": total_days}


@app.get("/api/admin/workflow/list")
def api_list_workflows(site_url: Optional[str] = None):
    return {"workflows": workflow_store.list_workflows(site_url)}


@app.delete("/api/admin/workflow/{workflow_id}")
def api_cancel_workflow(workflow_id: str):
    wf = workflow_store.get_workflow(workflow_id)
    if not wf:
        raise HTTPException(status_code=404, detail="No such workflow_id")
    email_scheduler.cancel_workflow_jobs(workflow_id)
    workflow_store.update_workflow(workflow_id, status="cancelled", next_run_at=None)
    return {"status": "cancelled", "workflow_id": workflow_id}


@app.get("/")
def client_login_page():
    return FileResponse("static/client-login.html")


# ---------------- Admin: switch which Google account GSC/GA4 calls use ----------------
# Each Google account (my@gmail.com, clientjson@gmail.com, etc.) has its own
# OAuth client_secret.json + token.json under accounts/<account_id>/.
# Only one account is "active" at a time; every /api/sites, /api/summary,
# /api/ga4/* etc. call below uses whichever account is currently active.

@app.get("/api/admin/accounts")
def api_list_accounts():
    return {"accounts": gsc_client.list_accounts()}


@app.post("/api/admin/accounts")
def api_add_account(
    account_id: str = Form(...),
    label: str = Form(...),
    client_secret_file: UploadFile = File(...),
):
    """Registers a new Google account. Upload the OAuth client_secret.json
    you downloaded from Google Cloud Console for that account — it gets
    saved to accounts/<account_id>/client_secret.json. The account isn't
    logged in yet; switching to it (below) will trigger the OAuth consent
    screen the first time it's actually used."""
    try:
        content = client_secret_file.file.read()
        return gsc_client.add_account(account_id, label, content)
    except ValueError as ex:
        raise HTTPException(status_code=400, detail=str(ex))


@app.delete("/api/admin/accounts/{account_id}")
def api_delete_account(account_id: str):
    if not gsc_client.delete_account(account_id):
        raise HTTPException(status_code=404, detail="No such account_id")
    return {"status": "deleted", "account_id": account_id}


class AccountSwitchBody(BaseModel):
    account_id: str


@app.post("/api/admin/accounts/switch")
def api_switch_account(body: AccountSwitchBody):
    try:
        gsc_client.set_active_account(body.account_id)
    except ValueError as ex:
        raise HTTPException(status_code=400, detail=str(ex))
    # Touch the API right away so the switch either confirms working sites,
    # or surfaces an auth problem immediately instead of on the next click.
    sites = _call(gsc_client.list_sites)
    return {"active_account": body.account_id, "sites": sites}


@app.get("/api/admin/accounts/active")
def api_active_account():
    return {"active_account": gsc_client.get_active_account_id()}


@app.get("/api/sites")
def api_sites():
    return {"sites": _call(gsc_client.list_sites)}


@app.get("/api/site-ga4-map")
def api_site_ga4_map():
    """Returns the {site_url: ga4_property_id} mapping so the frontend
    can auto-fill the GA4 Property ID when a site is selected."""
    import json
    import os
    path = "site_ga4_map.json"
    if not os.path.exists(path):
        return {}
    with open(path, "r") as f:
        return json.load(f)


@app.get("/api/summary")
def api_summary(site_url: str, start_date: Optional[str] = None, end_date: Optional[str] = None):
    s, e = _dates(start_date, end_date)
    data = _call(gsc_client.get_summary, site_url, s, e)
    return {"site_url": site_url, "start_date": s, "end_date": e, **data}

@app.get("/Client")
def client():
    return FileResponse("static/Clientview.html")


@app.get("/api/queries")
def api_queries(site_url: str, start_date: Optional[str] = None, end_date: Optional[str] = None,
                 limit: int = Query(25, le=1000)):
    s, e = _dates(start_date, end_date)
    rows = _call(gsc_client.get_queries, site_url, s, e, limit)
    return {"site_url": site_url, "start_date": s, "end_date": e, "rows": rows}


@app.get("/api/pages")
def api_pages(site_url: str, start_date: Optional[str] = None, end_date: Optional[str] = None,
              limit: int = Query(25, le=1000)):
    s, e = _dates(start_date, end_date)
    rows = _call(gsc_client.get_pages, site_url, s, e, limit)
    return {"site_url": site_url, "start_date": s, "end_date": e, "rows": rows}


@app.get("/api/devices")
def api_devices(site_url: str, start_date: Optional[str] = None, end_date: Optional[str] = None):
    s, e = _dates(start_date, end_date)
    rows = _call(gsc_client.get_devices, site_url, s, e)
    return {"site_url": site_url, "start_date": s, "end_date": e, "rows": rows}


@app.get("/api/countries")
def api_countries(site_url: str, start_date: Optional[str] = None, end_date: Optional[str] = None,
                   limit: int = Query(15, le=250)):
    s, e = _dates(start_date, end_date)
    rows = _call(gsc_client.get_countries, site_url, s, e, limit)
    return {"site_url": site_url, "start_date": s, "end_date": e, "rows": rows}


@app.get("/api/trend")
def api_trend(site_url: str, start_date: Optional[str] = None, end_date: Optional[str] = None):
    s, e = _dates(start_date, end_date)
    rows = _call(gsc_client.get_trend, site_url, s, e)
    return {"site_url": site_url, "start_date": s, "end_date": e, "rows": rows}


@app.get("/api/comparison")
def api_comparison(site_url: str, start_date: Optional[str] = None, end_date: Optional[str] = None):
    s, e = _dates(start_date, end_date)
    return _call(gsc_client.get_comparison, site_url, s, e)


@app.get("/api/movers")
def api_movers(site_url: str, start_date: Optional[str] = None, end_date: Optional[str] = None,
                limit: int = Query(10, le=50)):
    s, e = _dates(start_date, end_date)
    return _call(gsc_client.get_movers, site_url, s, e, limit)


@app.get("/api/sitemaps")
def api_sitemaps(site_url: str):
    return {"site_url": site_url, "sitemaps": _call(gsc_client.get_sitemaps, site_url)}


@app.get("/api/export/csv")
def api_export_csv(site_url: str, data_type: str = Query(..., pattern="^(queries|pages|devices|countries|trend)$"),
                    start_date: Optional[str] = None, end_date: Optional[str] = None,
                    limit: int = Query(1000, le=5000)):
    import csv
    import io
    from fastapi.responses import StreamingResponse

    s, e = _dates(start_date, end_date)

    fetchers = {
        "queries": lambda: gsc_client.get_queries(site_url, s, e, limit),
        "pages": lambda: gsc_client.get_pages(site_url, s, e, limit),
        "devices": lambda: gsc_client.get_devices(site_url, s, e),
        "countries": lambda: gsc_client.get_countries(site_url, s, e, limit),
        "trend": lambda: gsc_client.get_trend(site_url, s, e),
    }
    rows = _call(fetchers[data_type])

    buf = io.StringIO()
    if rows:
        writer = csv.DictWriter(buf, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    buf.seek(0)

    filename = f"{data_type}_{s}_to_{e}.csv"
    return StreamingResponse(
        buf,
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


# ---------------- Rank Tracker (GSC-based) endpoints ----------------

class TrackedKeywordsBody(BaseModel):
    site_url: str
    keywords: List[str]


@app.get("/api/tracked-keywords")
def api_get_tracked_keywords(site_url: str):
    return {"site_url": site_url, "keywords": gsc_client.get_tracked_keywords(site_url)}


@app.post("/api/tracked-keywords")
def api_set_tracked_keywords(body: TrackedKeywordsBody):
    keywords = gsc_client.set_tracked_keywords(body.site_url, body.keywords)
    return {"site_url": body.site_url, "keywords": keywords}


@app.get("/api/rank-tracker")
def api_rank_tracker(site_url: str, start_date: Optional[str] = None, end_date: Optional[str] = None):
    s, e = _dates(start_date, end_date)
    keywords = gsc_client.get_tracked_keywords(site_url)
    if not keywords:
        return {"site_url": site_url, "start_date": s, "end_date": e, "rows": []}
    rows = _call(gsc_client.get_rank_tracker_summary, site_url, keywords, s, e)
    return {"site_url": site_url, "start_date": s, "end_date": e, "rows": rows}


@app.get("/api/rank-tracker/history")
def api_rank_tracker_history(site_url: str, keyword: str,
                              start_date: Optional[str] = None, end_date: Optional[str] = None):
    s, e = _dates(start_date, end_date)
    rows = _call(gsc_client.get_keyword_position_history, site_url, keyword, s, e)
    return {"site_url": site_url, "keyword": keyword, "start_date": s, "end_date": e, "rows": rows}


# ---------------- True Rank Tracker (Serper.dev — live Google position) ----------------

class SerperKeywordsBody(BaseModel):
    site_url: str
    keywords: List[str]


class SerperRefreshBody(BaseModel):
    site_url: str
    location: Optional[str] = None
    gl: str = "us"


@app.get("/api/serper/tracked-keywords")
def api_serper_get_keywords(site_url: str):
    return {"site_url": site_url, "keywords": serper_client.get_tracked_keywords(site_url)}


@app.post("/api/serper/tracked-keywords")
def api_serper_set_keywords(body: SerperKeywordsBody):
    keywords = serper_client.set_tracked_keywords(body.site_url, body.keywords)
    return {"site_url": body.site_url, "keywords": keywords}


@app.get("/api/serper/rankings")
def api_serper_rankings(site_url: str):
    """Read-only — returns the last cached check. Does NOT call Serper, so it's free to load."""
    return {"site_url": site_url, "rows": serper_client.get_cached_rankings(site_url)}


@app.post("/api/serper/refresh")
def api_serper_refresh(body: SerperRefreshBody):
    """Actually queries Serper for every tracked keyword on this site.
    Each keyword tracked = 1 paid API credit — only call this on demand."""
    keywords = serper_client.get_tracked_keywords(body.site_url)
    if not keywords:
        return {"site_url": body.site_url, "rows": []}
    try:
        rows = serper_client.refresh_rankings(body.site_url, keywords, location=body.location, gl=body.gl)
    except RuntimeError as ex:
        raise HTTPException(status_code=400, detail=str(ex))
    except requests.exceptions.RequestException as ex:
        raise HTTPException(status_code=502, detail=f"Serper API error: {ex}")
    return {"site_url": body.site_url, "rows": rows}


# ---------------- GA4 endpoints ----------------

@app.get("/api/ga4/summary")
def api_ga4_summary(property_id: str, start_date: Optional[str] = None, end_date: Optional[str] = None):
    s, e = _dates(start_date, end_date)
    data = _call(ga4_client.get_summary, property_id, s, e)
    return {"property_id": property_id, "start_date": s, "end_date": e, **data}


@app.get("/api/ga4/traffic")
def api_ga4_traffic(property_id: str, start_date: Optional[str] = None, end_date: Optional[str] = None):
    s, e = _dates(start_date, end_date)
    rows = _call(ga4_client.get_traffic_sources, property_id, s, e)
    return {"property_id": property_id, "start_date": s, "end_date": e, "rows": rows}


@app.get("/api/ga4/trend")
def api_ga4_trend(property_id: str, start_date: Optional[str] = None, end_date: Optional[str] = None):
    s, e = _dates(start_date, end_date)
    rows = _call(ga4_client.get_trend, property_id, s, e)
    return {"property_id": property_id, "start_date": s, "end_date": e, "rows": rows}


@app.get("/api/ga4/pages")
def api_ga4_pages(property_id: str, start_date: Optional[str] = None, end_date: Optional[str] = None,
                   limit: int = Query(15, le=200)):
    s, e = _dates(start_date, end_date)
    rows = _call(ga4_client.get_top_pages, property_id, s, e, limit)
    return {"property_id": property_id, "start_date": s, "end_date": e, "rows": rows}


# Serve the dashboard HTML + static assets last, so /api/* routes take priority
app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/admin")
def serve_dashboard():
    return FileResponse("static/index.html")

@app.get("/admin-login")
def admin_login_page():
    return FileResponse("static/admin-login.html")

@app.get("/report/pdf")
def pdf_report(
    site_url: str,
    property_id: str,
    start_date: str,
    end_date: str
):

    pdf = generate_pdf(
        site_url,
        property_id,
        start_date,
        end_date
    )
    return StreamingResponse(
        pdf,
        media_type="application/pdf",
        headers={
            "Content-Disposition": "inline; filename=seo_report.pdf"
        }
    )
@app.get("/api/ga4/list-properties")
def api_ga4_list_properties():
    return {"properties": _call(ga4_client.list_all_properties)}

@app.get("/client-login")
def client_login_page():
    return FileResponse("static/client-login.html")