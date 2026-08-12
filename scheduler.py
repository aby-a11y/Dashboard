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
  4. +72h -> reminder 3, then mark the workflow "done" and stop
     (no more jobs get scheduled after the 3rd reminder)

If a send fails (bad email, Gmail creds missing, etc.) the workflow is
marked "error" and nothing further is scheduled — it won't retry forever
and silently spam later. Check the admin panel and re-create it after
fixing the issue.
"""

import datetime
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore

import workflow_store
import email_client
from pdf_report import generate_pdf

REMINDER_GAP_HOURS = 24
MAX_REMINDERS = 3

scheduler = BackgroundScheduler(
    jobstores={"default": SQLAlchemyJobStore(url="sqlite:///jobs.sqlite")},
    timezone="Asia/Kolkata",
)


def _default_date_range():
    end = datetime.date.today() - datetime.timedelta(days=3)
    start = end - datetime.timedelta(days=28)
    return str(start), str(end)


def _build_email(wf, is_reminder, reminder_number):
    label = f"Reminder #{reminder_number}" if is_reminder else "Report"
    subject = f"{label}: SEO Report — {wf['site_url']}"

    if wf["report_type"] == "dashboard_pdf":
        start, end = _default_date_range()
        pdf_buf = generate_pdf(wf["site_url"], wf.get("ga4_property_id") or "", start, end)
        pdf_bytes = pdf_buf.read()
        body_html = f"""
        <p>Hi,</p>
        <p>Please find attached the latest SEO &amp; Analytics report for
        <b>{wf['site_url']}</b> ({start} to {end}).</p>
        {"<p style='color:#999'>This is a reminder — let us know if you have any questions about the report.</p>" if is_reminder else ""}
        """
        return subject, body_html, pdf_bytes
    else:
        body_html = f"""
        <p>Hi,</p>
        <p>Your SEO report for <b>{wf['site_url']}</b> is ready:</p>
        <p><a href="{wf['drive_link']}">View your report</a></p>
        {"<p style='color:#999'>This is a reminder — let us know if you have any questions.</p>" if is_reminder else ""}
        """
        return subject, body_html, None


def _run_workflow_step(workflow_id):
    wf = workflow_store.get_workflow(workflow_id)
    if not wf or wf["status"] in ("done", "cancelled", "error"):
        return  # workflow was cancelled or already finished — do nothing

    is_reminder = wf["status"] != "scheduled"
    reminder_number = wf["reminder_count"] + 1 if is_reminder else 0

    subject, body_html, pdf_bytes = _build_email(wf, is_reminder, reminder_number)

    now = datetime.datetime.utcnow().isoformat()
    try:
        email_client.send_report_email(wf["email"], subject, body_html, pdf_bytes=pdf_bytes)
        success = True
    except Exception as ex:
        success = False
        wf["history"].append({"sent_at": now, "type": subject, "success": False, "error": str(ex)})
        workflow_store.update_workflow(workflow_id, status="error", history=wf["history"])
        return

    wf["history"].append({"sent_at": now, "type": subject, "success": True})

    if is_reminder:
        new_count = reminder_number
    else:
        new_count = 0

    if new_count >= MAX_REMINDERS:
        workflow_store.update_workflow(
            workflow_id, status="done", reminder_count=new_count,
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