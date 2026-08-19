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


def create_workflow(site_url, email, scheduled_time,
                     ga4_property_id=None, drive_link=None, custom_message=None,
                     recurrence="once", send_reminders=True,
                     login_id=None, login_password=None):
    """The email no longer attaches a PDF — it just contains the
    client-facing report link (drive_link) plus, optionally, the client's
    login_id / login_password so they can log into their dashboard
    themselves. custom_message optionally replaces the default intro text.

    recurrence: "once" (send, then done — or + reminders, see below) or
    "monthly" (repeats automatically every month, same day-of-month +
    time, until cancelled).
    send_reminders: if False, skips the 3x/24h-apart reminder chain after
    each send — useful for "monthly" so clients don't get nagged 4x a
    month; if True (default), behaves like the original one-time flow.
    scheduled_time: ISO datetime string (e.g. '2026-08-10T09:00:00').
    login_id / login_password: optional — included in the email body as
    "Your login details" if provided (plaintext password only exists here
    because the admin typed it in when creating the client login; it is
    never re-derived from the stored hash)."""
    data = _load()
    workflow_id = secrets.token_hex(6)
    data[workflow_id] = {
        "workflow_id": workflow_id,
        "site_url": site_url,
        "ga4_property_id": ga4_property_id,
        "email": email,
        "drive_link": drive_link,
        "custom_message": custom_message,
        "login_id": login_id,
        "login_password": login_password,
        "recurrence": recurrence if recurrence in ("once", "monthly") else "once",
        "send_reminders": bool(send_reminders),
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