"""
Redis cache wrapper — replaces gsc_query_cache.json and
serper_rank_cache.json.

Why Redis instead of a DB table for these two: they're pure cache (TTL,
throwaway, rebuilt from the Google/Serper APIs on a miss), not source
data — a proper TTL store is a better fit than rows you'd have to prune
yourself, and it takes the read/write load off Postgres for the highest
-frequency lookups once you're at 200-300 clients.

If Redis is unreachable (not configured yet, or briefly down), every
function here degrades to "always a cache miss" instead of raising —
the dashboard should stay up and just hit the live API a bit more, not
500 because the cache is down.
"""

import os
import json
import logging
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger("cache")

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

try:
    import redis
    _client = redis.from_url(REDIS_URL, decode_responses=True, socket_connect_timeout=2)
    _client.ping()
except Exception as exc:  # noqa: BLE001 — genuinely want to catch anything here
    logger.warning("Redis unavailable (%s) — caching disabled, running cache-miss-only.", exc)
    _client = None


def get_json(key):
    """Returns the cached value (already json.loads'd), or None on a
    miss / when Redis is unavailable."""
    if _client is None:
        return None
    try:
        raw = _client.get(key)
        return json.loads(raw) if raw is not None else None
    except Exception:  # noqa: BLE001
        return None


def set_json(key, value, ttl_seconds=None):
    """Stores value (json-serializable) under key, optionally with a TTL.
    No-ops silently if Redis is unavailable."""
    if _client is None:
        return
    try:
        payload = json.dumps(value)
        if ttl_seconds:
            _client.setex(key, ttl_seconds, payload)
        else:
            _client.set(key, payload)
    except Exception:  # noqa: BLE001
        pass


def delete(key):
    if _client is None:
        return
    try:
        _client.delete(key)
    except Exception:  # noqa: BLE001
        pass


def is_available():
    return _client is not None
