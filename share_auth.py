"""
Shareable dashboard links.

Design:
- Admin (from the existing /admin panel, already logged in) picks a
  site + a single date + how long the link should stay valid, and
  calls POST /api/admin/share/create.
- That issues a signed, self-contained JWT with
  {site_url, date, ga4_property_id, exp} and no user/password behind
  it — anyone holding the link can open it, nobody needs to log in.
- The token's own "exp" claim IS the expiry: there is nothing to
  revoke or clean up. Once exp passes, decode_share_token() raises
  jwt.ExpiredSignatureError and the link simply stops working —
  it "disappears" on its own, as asked.
- /api/shared/* endpoints (see main.py) resolve site_url + date
  strictly from this token, never from a query param the visitor
  could edit, so a shared link can only ever show the one
  site+date it was minted for.

Storage: none needed — the JWT is the entire state. Consistent in
spirit with client_auth.py / admin_auth.py's secret-file pattern.
"""

import os
import datetime
import secrets
import jwt  # PyJWT — already a dependency via client_auth.py

SHARE_JWT_SECRET_FILE = "share_jwt_secret.txt"
SHARE_JWT_ALGO = "HS256"

MIN_EXPIRY_HOURS = 1
MAX_EXPIRY_HOURS = 24 * 30  # 30 days — sane upper bound so a link can't be minted "forever"
DEFAULT_EXPIRY_HOURS = 24


def _get_jwt_secret():
    if os.path.exists(SHARE_JWT_SECRET_FILE):
        with open(SHARE_JWT_SECRET_FILE, "r") as f:
            return f.read().strip()
    secret = secrets.token_hex(32)
    with open(SHARE_JWT_SECRET_FILE, "w") as f:
        f.write(secret)
    return secret


_SHARE_SECRET = _get_jwt_secret()


def issue_share_token(site_url: str, date: str, ga4_property_id: str = None,
                       expires_in_hours: int = DEFAULT_EXPIRY_HOURS) -> dict:
    """Mints a share token. Returns {token, expires_at} — expires_at is
    an ISO timestamp the caller can show in the admin UI."""
    hours = max(MIN_EXPIRY_HOURS, min(int(expires_in_hours), MAX_EXPIRY_HOURS))
    now = datetime.datetime.utcnow()
    expires_at = now + datetime.timedelta(hours=hours)
    payload = {
        "site_url": site_url,
        "date": date,
        "ga4_property_id": ga4_property_id,
        "iat": now,
        "exp": expires_at,
        "scope": "shared_snapshot",  # so a share token can never be mistaken for an admin/client one
    }
    token = jwt.encode(payload, _SHARE_SECRET, algorithm=SHARE_JWT_ALGO)
    return {"token": token, "expires_at": expires_at.isoformat() + "Z"}


def decode_share_token(token: str) -> dict:
    """Raises jwt.PyJWTError (expired / invalid signature / malformed) on failure."""
    payload = jwt.decode(token, _SHARE_SECRET, algorithms=[SHARE_JWT_ALGO])
    if payload.get("scope") != "shared_snapshot":
        raise jwt.InvalidTokenError("Not a share token")
    return payload