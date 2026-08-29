"""
Admin authentication for the internal SEO dashboard.
Single shared admin login (username+password from env vars) -> JWT.
"""
import os
import datetime
import jwt as pyjwt

ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD")  # set this in your environment / systemd unit
ADMIN_JWT_SECRET = os.environ.get("ADMIN_JWT_SECRET")  # set this too, long random string
ADMIN_JWT_ALGO = "HS256"
ADMIN_TOKEN_HOURS = 12


def _check_env():
    if not ADMIN_PASSWORD or not ADMIN_JWT_SECRET:
        raise RuntimeError(
            "ADMIN_PASSWORD and ADMIN_JWT_SECRET env vars must be set "
            "before the admin API can be used."
        )


def authenticate(username: str, password: str) -> bool:
    _check_env()
    return username == ADMIN_USERNAME and password == ADMIN_PASSWORD


def issue_token(username: str) -> str:
    _check_env()
    payload = {
        "sub": username,
        "role": "admin",
        "exp": datetime.datetime.utcnow() + datetime.timedelta(hours=ADMIN_TOKEN_HOURS),
    }
    return pyjwt.encode(payload, ADMIN_JWT_SECRET, algorithm=ADMIN_JWT_ALGO)


def decode_token(token: str) -> dict:
    _check_env()
    payload = pyjwt.decode(token, ADMIN_JWT_SECRET, algorithms=[ADMIN_JWT_ALGO])
    if payload.get("role") != "admin":
        raise pyjwt.PyJWTError("Not an admin token")
    return payload