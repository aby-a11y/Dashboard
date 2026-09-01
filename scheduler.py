"""
Runs the "send report → wait 24h → remind (x3)" workflow on schedule.

Jobs are persisted to jobs.sqlite (SQLAlchemyJobStore), so if the EC2
server restarts, uvicorn crashes, or you redeploy — scheduled sends and
pending reminders are NOT lost. APScheduler picks them back up as soon
as scheduler.start() runs again (see main.py's startup event).

Flow per workflow:
  1. At scheduled_time -> send the report (initial send)
  2. +24h -> reminder 1
  3. +48h -> reminder 2
  4. +72h -> reminder 3, then:
     - recurrence == "once": mark the workflow "done" and stop.
     - recurrence == "monthly": reset the reminder counter and schedule
       the next initial send exactly one month after this cycle's send
       date (same day-of-month + time), repeating indefinitely until the
       admin cancels it.

If a send fails (bad email, Gmail creds missing, etc.) the workflow is
marked "error" and nothing further is scheduled — it won't retry forever
and silently spam later. Check the admin panel and re-create it after
fixing the issue.
"""

import os
import datetime
import calendar
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore

import workflow_store
import email_client
import share_auth

REMINDER_GAP_HOURS = 24
MAX_REMINDERS = 3

# Used to build the full https://... link that goes in report emails —
# set this in your .env / systemd unit to your real domain in production
# (e.g. https://dashboard.pixelglobalit.com). Falls back to localhost so
# local testing still works without extra config.
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "http://localhost:8000").rstrip("/")

# How long an auto-generated report-email share link stays valid. Generous
# on purpose: it covers the initial send + up to 3 reminders (72h) with
# plenty of room for the client to actually open it, and for "monthly"
# workflows a fresh link is minted on every cycle anyway.
SHARE_LINK_EXPIRY_HOURS = 24 * 45

scheduler = BackgroundScheduler(
    jobstores={"default": SQLAlchemyJobStore(url="sqlite:///jobs.sqlite")},
    timezone="Asia/Kolkata",
)


def _add_one_month(dt):
    """Same day-of-month + time, one month later. Clamps the day if the
    next month is shorter (e.g. Jan 31 -> Feb 28/29)."""
    year = dt.year + (dt.month // 12)
    month = dt.month % 12 + 1
    day = min(dt.day, calendar.monthrange(year, month)[1])
    return dt.replace(year=year, month=month, day=day)


def _add_one_month_date(d):
    """Date-only version of _add_one_month — used to roll a workflow's
    report_start_date/report_end_date forward for 'monthly' recurrence,
    same clamping rule (e.g. Jan 31 -> Feb 28/29)."""
    year = d.year + (d.month // 12)
    month = d.month % 12 + 1
    day = min(d.day, calendar.monthrange(year, month)[1])
    return d.replace(year=year, month=month, day=day)


def _build_share_link(wf):
    """Mints a fresh, no-login share link for this workflow's report
    period (report_start_date/report_end_date), if set. A brand-new
    token is minted on every send (initial + each reminder + every
    monthly cycle), so it's always freshly-expiring from *this* send,
    never reused/stale. Returns None if no report period is set on
    this workflow (older workflows created before this feature, or
    ones the admin deliberately left blank to use drive_link instead)."""
    start = wf.get("report_start_date")
    end = wf.get("report_end_date")
    if not start or not end:
        return None
    result = share_auth.issue_share_token(
        wf["site_url"], start, end, wf.get("ga4_property_id"),
        expires_in_hours=SHARE_LINK_EXPIRY_HOURS,
    )
    return f"{PUBLIC_BASE_URL}/shared?token={result['token']}"


def _build_email(wf, is_reminder, reminder_number):
    """No PDF attachment — the email just contains a report link and,
    if the admin set them, the client's login details. A custom_message
    (if the admin wrote one) replaces the default intro text; the link,
    login box, and reminder note are still appended.

    Both links are included when both are available: a freshly-minted,
    no-login share link for report_start_date..report_end_date (see
    _build_share_link) AND the manually-pasted drive_link (client's
    full dashboard / login page). Either one is skipped if not set on
    this workflow — a workflow with only a report period sends just
    the share link, one with only drive_link sends just that."""
    if is_reminder:
        subject = f"Reminder #{reminder_number}: Your SEO Report — {wf['site_url']}"
    else:
        subject = f"Your SEO Report is Ready — {wf['site_url']}"

    custom_message = (wf.get("custom_message") or "").strip()
    if custom_message:
        message_html = "".join(
            f"<p>{line}</p>" for line in custom_message.splitlines() if line.strip()
        )
    else:
        message_html = f"""
        <p>Hi,</p>
        <p>Your latest SEO &amp; Analytics report for <b>{wf['site_url']}</b> is ready.</p>
        """

    # Both links go out together when both are set: the auto-generated
    # share link (quick, no-login snapshot for this exact report period)
    # AND the client-facing drive_link (their full dashboard / login
    # page), so the client can pick whichever they want to open.
    share_link = _build_share_link(wf)
    drive_link = wf.get("drive_link")
    link_html = ""
    if share_link:
        link_html += f'<p>View your report snapshot here: <a href="{share_link}">{share_link}</a></p>'
    if drive_link:
        link_html += f'<p>Full dashboard / login page: <a href="{drive_link}">{drive_link}</a></p>'

    login_id = wf.get("login_id")
    login_password = wf.get("login_password")
    login_html = ""
    if login_id or login_password:
        login_html = "<p>Your login details:<br>"
        if login_id:
            login_html += f"Login ID: <b>{login_id}</b><br>"
        if login_password:
            login_html += f"Password: <b>{login_password}</b><br>"
        login_html += "</p>"

    reminder_html = (
        "<p style='color:#999'>This is a reminder — let us know if you have any questions about the report.</p>"
        if is_reminder else ""
    )

    body_html = message_html + link_html + login_html + reminder_html
    return subject, body_html



def _run_workflow_step(workflow_id):
    wf = workflow_store.get_workflow(workflow_id)
    if not wf or wf["status"] in ("done", "cancelled", "error"):
        return  # workflow was cancelled or already finished — do nothing

    is_reminder = wf["status"] != "scheduled"
    reminder_number = wf["reminder_count"] + 1 if is_reminder else 0

    subject, body_html = _build_email(wf, is_reminder, reminder_number)

    now = datetime.datetime.utcnow().isoformat()
    try:
        email_client.send_report_email(wf["email"], subject, body_html)
        success = True
    except Exception as ex:
        success = False
        wf["history"].append({"sent_at": now, "type": subject, "success": False, "error": str(ex)})
        workflow_store.update_workflow(workflow_id, status="error", history=wf["history"])
        return

    wf["history"].append({"sent_at": now, "type": subject, "success": True})

    send_reminders = wf.get("send_reminders", True)
    if not send_reminders and not is_reminder:
        # Reminders disabled for this workflow — treat the initial send as
        # the whole cycle (skips straight to the done/recurrence check below).
        new_count = MAX_REMINDERS
    elif is_reminder:
        new_count = reminder_number
    else:
        new_count = 0

    if new_count >= MAX_REMINDERS:
        if wf.get("recurrence") == "monthly":
            # Recurring workflow: don't stop — schedule the next monthly
            # send exactly one month after this cycle's original send date,
            # reset the reminder counter, and go back to "scheduled".
            last_scheduled = datetime.datetime.fromisoformat(wf["scheduled_time"])
            next_send = _add_one_month(last_scheduled)

            update_fields = dict(
                status="scheduled", reminder_count=0,
                sent_at=now, scheduled_time=next_send.isoformat(),
                next_run_at=next_send.isoformat(), history=wf["history"],
            )
            # Roll the report period forward by a month too, so next
            # cycle's emailed share link points at next month's data
            # instead of reusing this cycle's dates.
            if wf.get("report_start_date") and wf.get("report_end_date"):
                rs = datetime.date.fromisoformat(wf["report_start_date"])
                re_ = datetime.date.fromisoformat(wf["report_end_date"])
                update_fields["report_start_date"] = _add_one_month_date(rs).isoformat()
                update_fields["report_end_date"] = _add_one_month_date(re_).isoformat()

            workflow_store.update_workflow(workflow_id, **update_fields)
            scheduler.add_job(
                _run_workflow_step, "date", run_date=next_send,
                args=[workflow_id], id=f"{workflow_id}_initial", replace_existing=True,
            )
        else:
            workflow_store.update_workflow(
                workflow_id, status="done", reminder_count=(0 if not send_reminders else new_count),
                sent_at=now, next_run_at=None, history=wf["history"],
            )
        return

    next_run = datetime.datetime.utcnow() + datetime.timedelta(hours=REMINDER_GAP_HOURS)
    workflow_store.update_workflow(
        workflow_id, status="sent", reminder_count=new_count,
        sent_at=now, next_run_at=next_run.isoformat(), history=wf["history"],
    )
    scheduler.add_job(
        _run_workflow_step, "date", run_date=next_run,
        args=[workflow_id], id=f"{workflow_id}_r{new_count + 1}", replace_existing=True,
    )


def schedule_workflow(workflow_id, run_date):
    """run_date: a datetime.datetime (naive or tz-aware)."""
    scheduler.add_job(
        _run_workflow_step, "date", run_date=run_date,
        args=[workflow_id], id=f"{workflow_id}_initial", replace_existing=True,
    )


def cancel_workflow_jobs(workflow_id):
    for job in scheduler.get_jobs():
        if job.id.startswith(workflow_id):
            job.remove()


def start():
    if not scheduler.running:
        scheduler.start()