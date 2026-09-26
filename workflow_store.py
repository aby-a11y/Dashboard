"""
Storage for the "send report -> wait 24h -> remind (x3)" email workflows.

Now Postgres-backed (email_workflows table, see models.py) instead of a
flat email_workflows.json — same reasoning as client_auth.py. The actual
timing/triggering is still handled by scheduler.py (APScheduler); this
module only tracks *state* so the admin UI can show what's scheduled,
sent, or done.

One workflow per site at a time is expected (creating a new one for a
site_url that already has an active workflow will just add a second
entry — the admin UI is responsible for cancelling the old one first if
that's not wanted).

Function signatures are unchanged from the JSON version, so main.py and
scheduler.py needed no changes.
"""

import secrets

from db import get_session
from models import EmailWorkflow


def _to_dict(w):
    return {
        "workflow_id": w.workflow_id,
        "site_url": w.site_url,
        "ga4_property_id": w.ga4_property_id,
        "email": w.email,
        "drive_link": w.drive_link,
        "report_start_date": w.report_start_date,
        "report_end_date": w.report_end_date,
        "custom_message": w.custom_message,
        "custom_subject": w.custom_subject,
        "login_id": w.login_id,
        "login_password": w.login_password,
        "recurrence": w.recurrence,
        "send_reminders": w.send_reminders,
        "scheduled_time": w.scheduled_time,
        "status": w.status,
        "reminder_count": w.reminder_count,
        "sent_at": w.sent_at,
        "next_run_at": w.next_run_at,
        "history": w.history or [],
    }


def create_workflow(site_url, email, scheduled_time,
                     ga4_property_id=None, drive_link=None, custom_message=None,
                     custom_subject=None,
                     recurrence="once", send_reminders=True,
                     login_id=None, login_password=None,
                     report_start_date=None, report_end_date=None):
    """See module docstring / original JSON version for the full field
    explanation — behaviour is identical, only the storage changed."""
    workflow_id = secrets.token_hex(6)
    with get_session() as session:
        w = EmailWorkflow(
            workflow_id=workflow_id,
            site_url=site_url,
            ga4_property_id=ga4_property_id,
            email=email,
            drive_link=drive_link,
            report_start_date=report_start_date,
            report_end_date=report_end_date,
            custom_message=custom_message,
            custom_subject=custom_subject,
            login_id=login_id,
            login_password=login_password,
            recurrence=recurrence if recurrence in ("once", "monthly") else "once",
            send_reminders=bool(send_reminders),
            scheduled_time=scheduled_time,
            status="scheduled",
            reminder_count=0,
            sent_at=None,
            next_run_at=scheduled_time,
            history=[],
        )
        session.add(w)
        session.flush()
        return _to_dict(w)


def update_workflow(workflow_id, **fields):
    with get_session() as session:
        w = session.get(EmailWorkflow, workflow_id)
        if w is None:
            return None
        for key, value in fields.items():
            setattr(w, key, value)
        session.add(w)
        session.flush()
        return _to_dict(w)


def get_workflow(workflow_id):
    with get_session() as session:
        w = session.get(EmailWorkflow, workflow_id)
        return _to_dict(w) if w else None


def list_workflows(site_url=None):
    with get_session() as session:
        query = session.query(EmailWorkflow)
        if site_url:
            query = query.filter(EmailWorkflow.site_url == site_url)
        rows = query.all()
        return sorted((_to_dict(w) for w in rows), key=lambda w: w["scheduled_time"])


def delete_workflow(workflow_id):
    with get_session() as session:
        w = session.get(EmailWorkflow, workflow_id)
        if w is None:
            return False
        session.delete(w)
        return True
