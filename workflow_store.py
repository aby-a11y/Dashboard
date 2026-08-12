"""
Storage for the "send report → wait 24h → remind (x3)" email workflows.

Flat JSON file, same pattern as clients.json / report_links.json elsewhere
in this project. The actual timing/triggering is handled by scheduler.py
(APScheduler) — this module only tracks *state* so the admin UI can show
what's scheduled, sent, or done.

One workflow per site at a time is expected (creating a new one for a
site_url that already has an active workflow will just add a second
entry — the admin UI is responsible for cancelling the old one first if
that's not wanted).
"""

import os
import json
import secrets

WORKFLOWS_FILE = "email_workflows.json"


def _load():
    if not os.path.exists(WORKFLOWS_FILE):
        return {}
    with open(WORKFLOWS_FILE, "r") as f:
        return json.load(f)


def _save(data):
    with open(WORKFLOWS_FILE, "w") as f:
        json.dump(data, f, indent=2)


def create_workflow(site_url, email, report_type, scheduled_time,
                     ga4_property_id=None, drive_link=None):
    """report_type: 'dashboard_pdf' or 'external_link'.
    scheduled_time: ISO datetime string (e.g. '2026-08-10T09:00:00')."""
    data = _load()
    workflow_id = secrets.token_hex(6)
    data[workflow_id] = {
        "workflow_id": workflow_id,
        "site_url": site_url,
        "ga4_property_id": ga4_property_id,
        "email": email,
        "report_type": report_type,
        "drive_link": drive_link,
        "scheduled_time": scheduled_time,
        "status": "scheduled",       # scheduled -> sent -> ... -> done | cancelled | error
        "reminder_count": 0,         # how many of the 3 reminders have gone out
        "sent_at": None,             # last successful send
        "next_run_at": scheduled_time,
        "history": [],               # [{sent_at, type, success}, ...]
    }
    _save(data)
    return data[workflow_id]


def update_workflow(workflow_id, **fields):
    data = _load()
    if workflow_id not in data:
        return None
    data[workflow_id].update(fields)
    _save(data)
    return data[workflow_id]


def get_workflow(workflow_id):
    return _load().get(workflow_id)


def list_workflows(site_url=None):
    data = _load()
    values = list(data.values())
    if site_url:
        values = [w for w in values if w["site_url"] == site_url]
    return sorted(values, key=lambda w: w["scheduled_time"])


def delete_workflow(workflow_id):
    data = _load()
    if workflow_id in data:
        del data[workflow_id]
        _save(data)
        return True
    return False