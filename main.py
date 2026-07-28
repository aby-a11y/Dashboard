"""
SEO Client Dashboard — FastAPI backend
Serves the dashboard UI + JSON API endpoints backed by Google Search Console.

Run with: uvicorn main:app
Then open: http://127.0.0.1:8000
"""

import datetime
from fastapi import FastAPI, Query, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from typing import Optional
from googleapiclient.errors import HttpError
from google.api_core.exceptions import GoogleAPICallError

import gsc_client
import ga4_client

app = FastAPI(title="SEO Client Dashboard API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


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


@app.get("/")
def serve_dashboard():
    return FileResponse("static/index.html")