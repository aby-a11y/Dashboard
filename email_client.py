"""
Sends report emails via Gmail SMTP using an App Password.

Setup (one-time, on your Google account that will send the reports):
1. Turn on 2-Step Verification: https://myaccount.google.com/security
2. Create an App Password: https://myaccount.google.com/apppasswords
   (choose "Mail" as the app — Google gives you a 16-character password)
3. Add to your .env file (same folder as main.py):
     GMAIL_USER=yourname@gmail.com
     GMAIL_APP_PASSWORD=the16charapppassword   # no spaces
     GMAIL_SENDER_NAME=Pixel Global IT — SEO Reports   # optional, shown as the "From" display name instead of the raw email

Do NOT use your normal Gmail password here — App Passwords are the
only thing Google allows for SMTP login now, and they can be revoked
independently of your real password if this server is ever compromised.
"""

import os
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr
from dotenv import load_dotenv

load_dotenv()

GMAIL_USER = os.getenv("GMAIL_USER")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD")
GMAIL_SENDER_NAME = os.getenv("GMAIL_SENDER_NAME", "Pixel Global IT — SEO Reports")

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 465  # SSL


def send_report_email(to_email, subject, body_html, pdf_bytes=None, pdf_filename="seo_report.pdf"):
    """Sends one email. Raises RuntimeError if Gmail creds aren't configured,
    or smtplib.SMTPException on send failure — caller decides how to handle it
    (the scheduler marks the workflow as 'error' and stops instead of retrying
    forever). pdf_bytes is optional — the standard workflow email no longer
    attaches a PDF (just the report link + login details), but this stays
    supported for anywhere that still wants to attach one."""
    if not GMAIL_USER or not GMAIL_APP_PASSWORD:
        raise RuntimeError(
            "GMAIL_USER / GMAIL_APP_PASSWORD not set. Add them to your .env file — "
            "see the docstring at the top of email_client.py."
        )

    msg = EmailMessage()
    msg["From"] = formataddr((GMAIL_SENDER_NAME, GMAIL_USER))
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.set_content("This email contains an HTML report. Please view it in an HTML-capable email client.")
    msg.add_alternative(body_html, subtype="html")

    if pdf_bytes:
        msg.add_attachment(pdf_bytes, maintype="application", subtype="pdf", filename=pdf_filename)

    context = ssl.create_default_context()
    with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, context=context) as server:
        server.login(GMAIL_USER, GMAIL_APP_PASSWORD)
        server.send_message(msg)


def is_configured():
    return bool(GMAIL_USER and GMAIL_APP_PASSWORD)