"""
Client authentication + per-site access control.

Design (unchanged from the JSON version):
- Admin (Abhi / senior SEO team, using the existing unauthenticated
  index.html) creates one login per client via POST /api/admin/clients,
  bound to exactly one site_url.
- The client logs in at /static/client-login.html with client_id +
  password and gets back a JWT whose payload is {client_id, site_url, exp}.
- Every client-facing data endpoint (/api/client/...) resolves site_url
  strictly from that JWT — never from a query param — so a client can
  never see another client's data, even by editing the URL.
- Google Drive report links are stored per site_url (report_links table)
  and set by the admin via POST /api/admin/report-link. The client
  dashboard's "Get More Info" button just opens that link.

Storage: Postgres (via db.py/models.py) instead of flat JSON files — the
old clients.json/report_links.json/report_emails.json approach didn't
scale past ~100 clients (whole-file read+rewrite on every single write,
no real concurrency safety). Function signatures are unchanged so
main.py needed no changes for this file.
"""

import os
import hashlib
import hmac
import secrets
import datetime
import jwt  # PyJWT — add to requirements.txt

from db import get_session
from models import Client, ReportLink, ReportEmail

JWT_SECRET_FILE = "jwt_secret.txt"
JWT_ALGO = "HS256"
TOKEN_TTL_HOURS = 24 * 7  # 7 days — client stays logged in for a week


# ---------------- JWT secret (generated once, persisted to disk) ----------------
# Left as a file on purpose: it's a single secret, not per-client data, and
# every app instance needs the same value to validate each other's tokens —
# moving it to an env var (JWT_SECRET) is the natural next step if/when this
# runs on more than one machine.

def _get_jwt_secret():
    env_secret = os.getenv("JWT_SECRET")
    if env_secret:
        return env_secret
    if os.path.exists(JWT_SECRET_FILE):
        with open(JWT_SECRET_FILE, "r") as f:
            return f.read().strip()
    secret = secrets.token_hex(32)
    with open(JWT_SECRET_FILE, "w") as f:
        f.write(secret)
    return secret


_JWT_SECRET = _get_jwt_secret()


# ---------------- password hashing (stdlib pbkdf2, no extra dependency) ----------------

def _hash_password(password, salt_hex=None):
    salt_hex = salt_hex or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), 100_000
    ).hex()
    return digest, salt_hex


def _verify_password(password, salt_hex, expected_digest):
    digest, _ = _hash_password(password, salt_hex)
    return hmac.compare_digest(digest, expected_digest)


# ---------------- admin: manage client logins ----------------

def create_or_update_client(client_id, site_url, password=None, name=None, ga4_property_id=None,
                             gmb_location_id=None):
    """Admin call. Creates a new client login, or updates an existing one.
    Password is only changed if a new one is provided — lets the admin
    update the site/name/ga4 id/gmb location without resetting the password."""
    client_id = client_id.strip()
    with get_session() as session:
        record = session.get(Client, client_id)
        if record is None:
            record = Client(client_id=client_id)

        if password:
            digest, salt = _hash_password(password)
            record.password_hash = digest
            record.salt = salt

        record.site_url = site_url
        if name is not None:
            record.name = name
        if ga4_property_id is not None:
            record.ga4_property_id = ga4_property_id
        if gmb_location_id is not None:
            record.gmb_location_id = gmb_location_id

        if not record.password_hash:
            raise ValueError("A password is required when creating a new client login")

        session.add(record)
        session.flush()
        return {"client_id": client_id, "site_url": record.site_url, "name": record.name}


def delete_client(client_id):
    with get_session() as session:
        record = session.get(Client, client_id)
        if record is None:
            return False
        session.delete(record)
        return True


def list_clients():
    """Admin call — never returns password hashes."""
    with get_session() as session:
        records = session.query(Client).all()
        return [
            {
                "client_id": r.client_id,
                "site_url": r.site_url,
                "name": r.name,
                "ga4_property_id": r.ga4_property_id,
                "gmb_location_id": r.gmb_location_id,
            }
            for r in records
        ]


# ---------------- client: login + tokens ----------------

def authenticate(client_id, password):
    with get_session() as session:
        record = session.get(Client, (client_id or "").strip())
        if not record:
            return None
        if not _verify_password(password or "", record.salt, record.password_hash):
            return None
        return {
            "client_id": record.client_id,
            "site_url": record.site_url,
            "name": record.name,
            "ga4_property_id": record.ga4_property_id,
            "gmb_location_id": record.gmb_location_id,
        }


def issue_token(client_id, site_url):
    now = datetime.datetime.utcnow()
    payload = {
        "client_id": client_id,
        "site_url": site_url,
        "iat": now,
        "exp": now + datetime.timedelta(hours=TOKEN_TTL_HOURS),
    }
    return jwt.encode(payload, _JWT_SECRET, algorithm=JWT_ALGO)


def decode_token(token):
    """Raises jwt.PyJWTError (expired / invalid signature / malformed) on failure."""
    return jwt.decode(token, _JWT_SECRET, algorithms=[JWT_ALGO])


def get_client_record(client_id):
    """Looks up one client's stored record by client_id (e.g. to resolve
    gmb_location_id server-side from the JWT instead of trusting a query
    param — see get_client_gmb_location in main.py)."""
    with get_session() as session:
        record = session.get(Client, client_id)
        if record is None:
            return None
        return {
            "client_id": record.client_id,
            "site_url": record.site_url,
            "name": record.name,
            "ga4_property_id": record.ga4_property_id,
            "gmb_location_id": record.gmb_location_id,
        }


# ---------------- admin: per-site Google Drive report link ----------------

def set_report_link(site_url, drive_link):
    with get_session() as session:
        record = session.get(ReportLink, site_url)
        if record is None:
            record = ReportLink(site_url=site_url, drive_link=drive_link)
        else:
            record.drive_link = drive_link
        session.add(record)
    return drive_link


def get_report_link(site_url):
    with get_session() as session:
        record = session.get(ReportLink, site_url)
        return record.drive_link if record else None


# ---------------- admin: per-site owner email (for the email workflow feature) ----------------

def set_report_email(site_url, email):
    with get_session() as session:
        record = session.get(ReportEmail, site_url)
        if record is None:
            record = ReportEmail(site_url=site_url, email=email)
        else:
            record.email = email
        session.add(record)
    return email


def get_report_email(site_url):
    with get_session() as session:
        record = session.get(ReportEmail, site_url)
        return record.email if record else None
