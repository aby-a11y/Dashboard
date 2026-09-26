"""
One-time migration: reads the old flat JSON files (if they exist in the
current directory) and loads their data into Postgres (via models.py)
and Redis (for the two pure-cache files). Safe to re-run — every insert
is an upsert, so running this twice just re-syncs, never duplicates.

Usage (run once, from the project directory, after setting DATABASE_URL
and REDIS_URL and starting Postgres/Redis):

    python migrate_json_to_db.py

Leaves the original .json files untouched — delete them yourself once
you've confirmed the dashboard works against the DB.
"""

import json
import os

import cache
from db import get_session, init_db
from models import (
    Client, ReportLink, ReportEmail, SiteGA4Map, SiteGMBMap,
    EmailWorkflow, GscTrackedKeyword, SerperTrackedKeyword,
)


def _load(path):
    if not os.path.exists(path):
        return None
    with open(path, "r") as f:
        return json.load(f)


def migrate_clients():
    data = _load("clients.json")
    if not data:
        print("clients.json: nothing to migrate")
        return
    with get_session() as session:
        for client_id, rec in data.items():
            row = session.get(Client, client_id) or Client(client_id=client_id)
            row.password_hash = rec["password_hash"]
            row.salt = rec["salt"]
            row.site_url = rec["site_url"]
            row.name = rec.get("name")
            row.ga4_property_id = rec.get("ga4_property_id")
            row.gmb_location_id = rec.get("gmb_location_id")
            session.add(row)
    print(f"clients.json: migrated {len(data)} client(s)")


def migrate_simple_map(json_path, model, key_field, value_field):
    """Handles report_links.json, report_emails.json, site_ga4_map.json,
    site_gmb_map.json — all {site_url: value} shaped files."""
    data = _load(json_path)
    if not data:
        print(f"{json_path}: nothing to migrate")
        return
    with get_session() as session:
        for site_url, value in data.items():
            row = session.get(model, site_url)
            if row is None:
                row = model(**{key_field: site_url})
            setattr(row, value_field, value)
            session.add(row)
    print(f"{json_path}: migrated {len(data)} row(s)")


def migrate_workflows():
    data = _load("email_workflows.json")
    if not data:
        print("email_workflows.json: nothing to migrate")
        return
    with get_session() as session:
        for workflow_id, w in data.items():
            row = session.get(EmailWorkflow, workflow_id) or EmailWorkflow(workflow_id=workflow_id)
            row.site_url = w["site_url"]
            row.ga4_property_id = w.get("ga4_property_id")
            row.email = w["email"]
            row.drive_link = w.get("drive_link")
            row.report_start_date = w.get("report_start_date")
            row.report_end_date = w.get("report_end_date")
            row.custom_message = w.get("custom_message")
            row.custom_subject = w.get("custom_subject")
            row.login_id = w.get("login_id")
            row.login_password = w.get("login_password")
            row.recurrence = w.get("recurrence", "once")
            row.send_reminders = w.get("send_reminders", True)
            row.scheduled_time = w["scheduled_time"]
            row.status = w.get("status", "scheduled")
            row.reminder_count = w.get("reminder_count", 0)
            row.sent_at = w.get("sent_at")
            row.next_run_at = w.get("next_run_at")
            row.history = w.get("history", [])
            session.add(row)
    print(f"email_workflows.json: migrated {len(data)} workflow(s)")


def migrate_tracked_keywords(json_path, model):
    data = _load(json_path)
    if not data:
        print(f"{json_path}: nothing to migrate")
        return
    with get_session() as session:
        for site_url, keywords in data.items():
            row = session.get(model, site_url) or model(site_url=site_url)
            row.keywords = keywords
            session.add(row)
    print(f"{json_path}: migrated {len(data)} site(s)")


def migrate_gsc_query_cache():
    data = _load("gsc_query_cache.json")
    if not data:
        print("gsc_query_cache.json: nothing to migrate")
        return
    from gsc_client import GSC_CACHE_KEY_PREFIX, GSC_CACHE_TTL_SECONDS
    n = 0
    for cache_key, entry in data.items():
        cache.set_json(GSC_CACHE_KEY_PREFIX + cache_key, {"rows": entry["rows"]},
                        ttl_seconds=GSC_CACHE_TTL_SECONDS)
        n += 1
    print(f"gsc_query_cache.json: loaded {n} entries into Redis")


def migrate_serper_rank_cache():
    data = _load("serper_rank_cache.json")
    if not data:
        print("serper_rank_cache.json: nothing to migrate")
        return
    from serper_client import _cache_key
    for site_url, site_cache in data.items():
        cache.set_json(_cache_key(site_url), site_cache)
    print(f"serper_rank_cache.json: loaded {len(data)} site(s) into Redis")


if __name__ == "__main__":
    init_db()
    migrate_clients()
    migrate_simple_map("report_links.json", ReportLink, "site_url", "drive_link")
    migrate_simple_map("report_emails.json", ReportEmail, "site_url", "email")
    migrate_simple_map("site_ga4_map.json", SiteGA4Map, "site_url", "ga4_property_id")
    migrate_simple_map("site_gmb_map.json", SiteGMBMap, "site_url", "gmb_location_id")
    migrate_workflows()
    migrate_tracked_keywords("tracked_keywords.json", GscTrackedKeyword)
    migrate_tracked_keywords("serper_keywords.json", SerperTrackedKeyword)
    migrate_gsc_query_cache()
    migrate_serper_rank_cache()
    if not cache.is_available():
        print("\nNOTE: Redis wasn't reachable — the two cache files above were "
              "skipped. Start Redis and re-run this script to load them; "
              "the dashboard works without it, just slower (every query hits "
              "Google/Serper's API instead of the cache).")
    print("\nDone. Verify the dashboard against the DB, then you can delete "
          "the old .json files.")
