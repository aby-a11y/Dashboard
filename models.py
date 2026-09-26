"""
ORM models — one table per old JSON file (see db.py for why).
"""

import datetime
from sqlalchemy import Column, String, Integer, Boolean, DateTime, Text, JSON
from db import Base


class Client(Base):
    """Replaces clients.json."""
    __tablename__ = "clients"

    client_id = Column(String(128), primary_key=True)
    password_hash = Column(String(256), nullable=False)
    salt = Column(String(64), nullable=False)
    site_url = Column(String(512), nullable=False, index=True)
    name = Column(String(256), nullable=True)
    ga4_property_id = Column(String(128), nullable=True)
    gmb_location_id = Column(String(256), nullable=True)


class ReportLink(Base):
    """Replaces report_links.json. site_url -> Google Drive link."""
    __tablename__ = "report_links"

    site_url = Column(String(512), primary_key=True)
    drive_link = Column(Text, nullable=False)


class ReportEmail(Base):
    """Replaces report_emails.json. site_url -> owner email."""
    __tablename__ = "report_emails"

    site_url = Column(String(512), primary_key=True)
    email = Column(String(256), nullable=False)


class SiteGA4Map(Base):
    """Replaces site_ga4_map.json."""
    __tablename__ = "site_ga4_map"

    site_url = Column(String(512), primary_key=True)
    ga4_property_id = Column(String(128), nullable=False)


class SiteGMBMap(Base):
    """Replaces site_gmb_map.json."""
    __tablename__ = "site_gmb_map"

    site_url = Column(String(512), primary_key=True)
    gmb_location_id = Column(String(256), nullable=False)


class EmailWorkflow(Base):
    """Replaces email_workflows.json."""
    __tablename__ = "email_workflows"

    workflow_id = Column(String(32), primary_key=True)
    site_url = Column(String(512), nullable=False, index=True)
    ga4_property_id = Column(String(128), nullable=True)
    email = Column(String(256), nullable=False)
    drive_link = Column(Text, nullable=True)
    report_start_date = Column(String(32), nullable=True)
    report_end_date = Column(String(32), nullable=True)
    custom_message = Column(Text, nullable=True)
    custom_subject = Column(Text, nullable=True)
    login_id = Column(String(128), nullable=True)
    login_password = Column(String(256), nullable=True)
    recurrence = Column(String(16), default="once")
    send_reminders = Column(Boolean, default=True)
    scheduled_time = Column(String(64), nullable=False)
    status = Column(String(32), default="scheduled")
    reminder_count = Column(Integer, default=0)
    sent_at = Column(String(64), nullable=True)
    next_run_at = Column(String(64), nullable=True)
    history = Column(JSON, default=list)  # [{sent_at, type, success}, ...]


class GscTrackedKeyword(Base):
    """Replaces tracked_keywords.json (gsc_client.py's list)."""
    __tablename__ = "gsc_tracked_keywords"

    site_url = Column(String(512), primary_key=True)
    keywords = Column(JSON, default=list)


class SerperTrackedKeyword(Base):
    """Replaces serper_keywords.json."""
    __tablename__ = "serper_tracked_keywords"

    site_url = Column(String(512), primary_key=True)
    keywords = Column(JSON, default=list)


class SiteAccountMap(Base):
    """Which registered Google account (gsc_client account_id — one of your
    5 agency Gmail logins) actually has GSC/GA4/GMB access to a given site.

    Auto-discovered by scanning every registered account's GSC site list
    (gsc_client.list_all_sites_across_accounts()) — not manually entered.
    Once a site is in here, every GSC/GA4/GMB data call for that site
    automatically uses the right account's credentials, so you never have
    to manually flip the account switcher before viewing a client whose
    access happens to live under a different one of the 5 Gmail accounts."""
    __tablename__ = "site_account_map"

    site_url = Column(String(512), primary_key=True)
    gsc_account_id = Column(String(128), nullable=False)
