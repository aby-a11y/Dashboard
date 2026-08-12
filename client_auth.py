"""
Client authentication + per-site access control.

Design:
- Admin (Abhi / senior SEO team, using the existing unauthenticated
  index.html) creates one login per client via POST /api/admin/clients,
  bound to exactly one site_url.
- The client logs in at /static/client-login.html with client_id +
  password and gets back a JWT whose payload is {client_id, site_url, exp}.
- Every client-facing data endpoint (/api/client/...) resolves site_url
  strictly from that JWT — never from a query param — so a client can
  never see another client's data, even by editing the URL.
- Google Drive report links are stored per site_url (report_links.json)
  and set by the admin via POST /api/admin/report-link. The client
  dashboard's "Get More Info" button just opens that link.

Storage is flat JSON files, consistent with the rest of this project
(tracked_keywords.json, serper_rank_cache.json etc). Fine for ~100
clients; if this grows much past that, move clients.json to sqlite.
"""

import os
import json
import hashlib
import hmac
import secrets
import datetime
import jwt  # PyJWT — add to requirements.txt

CLIENTS_FILE = "clients.json"            # {client_id: {password_hash, salt, site_url, name, ga4_property_id}}
REPORT_LINKS_FILE = "report_links.json"  # {site_url: drive_link}
REPORT_EMAILS_FILE = "report_emails.json"  # {site_url: owner_email} — used by the email workflow feature
JWT_SECRET_FILE = "jwt_secret.txt"
JWT_ALGO = "HS256"
TOKEN_TTL_HOURS = 24 * 7  # 7 days — client stays logged in for a week


# ---------------- JSON helpers ----------------

def _load_json(path):
    if not os.path.exists(path):
        return {}
    with open(path, "r") as f:
        return json.load(f)


def _save_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


# ---------------- JWT secret (generated once, persisted to disk) ----------------

def _get_jwt_secret():
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

def create_or_update_client(client_id, site_url, password=None, name=None, ga4_property_id=None):
    """Admin call. Creates a new client login, or updates an existing one.
    Password is only changed if a new one is provided — lets the admin
    update the site/name/ga4 id without resetting the password."""
    clients = _load_json(CLIENTS_FILE)
    client_id = client_id.strip()
    record = clients.get(client_id, {})

    if password:
        digest, salt = _hash_password(password)
        record["password_hash"] = digest
        record["salt"] = salt

    record["site_url"] = site_url
    if name is not None:
        record["name"] = name
    if ga4_property_id is not None:
        record["ga4_property_id"] = ga4_property_id

    if "password_hash" not in record:
        raise ValueError("A password is required when creating a new client login")

    clients[client_id] = record
    _save_json(CLIENTS_FILE, clients)
    return {"client_id": client_id, "site_url": record["site_url"], "name": record.get("name")}


def delete_client(client_id):
    clients = _load_json(CLIENTS_FILE)
    if client_id in clients:
        del clients[client_id]
        _save_json(CLIENTS_FILE, clients)
        return True
    return False


def list_clients():
    """Admin call — never returns password hashes."""
    clients = _load_json(CLIENTS_FILE)
    return [
        {
            "client_id": cid,
            "site_url": rec.get("site_url"),
            "name": rec.get("name"),
            "ga4_property_id": rec.get("ga4_property_id"),
        }
        for cid, rec in clients.items()
    ]


# ---------------- client: login + tokens ----------------

def authenticate(client_id, password):
    clients = _load_json(CLIENTS_FILE)
    record = clients.get((client_id or "").strip())
    if not record:
        return None
    if not _verify_password(password or "", record["salt"], record["password_hash"]):
        return None
    return record


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


# ---------------- admin: per-site Google Drive report link ----------------

def set_report_link(site_url, drive_link):
    links = _load_json(REPORT_LINKS_FILE)
    links[site_url] = drive_link
    _save_json(REPORT_LINKS_FILE, links)
    return drive_link


def get_report_link(site_url):
    return _load_json(REPORT_LINKS_FILE).get(site_url)


# ---------------- admin: per-site owner email (for the email workflow feature) ----------------

def set_report_email(site_url, email):
    emails = _load_json(REPORT_EMAILS_FILE)
    emails[site_url] = email
    _save_json(REPORT_EMAILS_FILE, emails)
    return email


def get_report_email(site_url):
    return _load_json(REPORT_EMAILS_FILE).get(site_url)