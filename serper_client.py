"""
Serper.dev client — real Google SERP position checking.

Unlike gsc_client's "average position" (which comes from Search Console and
can be a misleading blended number), this hits live Google search results
through Serper (https://serper.dev) and returns the *actual* rank — 1st,
2nd, "not found in top N", etc.

Because every check costs an API credit, results are cached to disk
(serper_rank_cache.json) and only refreshed when explicitly requested —
see refresh_rankings(). Nothing here calls Serper on a normal page load.
"""

import os
import json
import datetime
import requests
from dotenv import load_dotenv

load_dotenv()  # reads SERPER_API_KEY from a local .env file

SERPER_API_KEY = os.getenv("SERPER_API_KEY")
SERPER_URL = "https://google.serper.dev/search"

KEYWORDS_FILE = "serper_keywords.json"   # {site_url: ["keyword", ...]}  — you decide these manually
CACHE_FILE = "serper_rank_cache.json"    # {site_url: {keyword: {position, url, found, checked_at}}}


# ---------------- keyword list (per-site, manually curated) ----------------

def _load_json(path):
    if not os.path.exists(path):
        return {}
    with open(path, "r") as f:
        return json.load(f)


def _save_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def get_tracked_keywords(site_url):
    return _load_json(KEYWORDS_FILE).get(site_url, [])


def set_tracked_keywords(site_url, keywords):
    data = _load_json(KEYWORDS_FILE)
    cleaned, seen = [], set()
    for kw in keywords:
        kw = (kw or "").strip()
        if kw and kw.lower() not in seen:
            cleaned.append(kw)
            seen.add(kw.lower())
    data[site_url] = cleaned
    _save_json(KEYWORDS_FILE, data)
    return cleaned


# ---------------- domain matching ----------------

def _extract_domain(url_or_domain):
    """Normalize a GSC-style site URL ('sc-domain:example.com', 'https://www.example.com/')
    or a bare domain down to a comparable hostname."""
    d = (url_or_domain or "").strip().lower()
    d = d.replace("sc-domain:", "")
    d = d.replace("https://", "").replace("http://", "")
    d = d.split("/")[0]
    if d.startswith("www."):
        d = d[4:]
    return d


# ---------------- live Serper lookup ----------------

def check_ranking(keyword, site_url, location=None, gl="us", num=100):
    """Query Serper for `keyword` and find where `site_url` ranks in organic
    results. Costs 1 Serper credit per call."""
    if not SERPER_API_KEY:
        raise RuntimeError("SERPER_API_KEY not set — add it to your .env file as SERPER_API_KEY=your_key")

    domain = _extract_domain(site_url)
    payload = {"q": keyword, "num": num, "gl": gl}
    if location:
        payload["location"] = location

    resp = requests.post(
        SERPER_URL,
        headers={"X-API-KEY": SERPER_API_KEY, "Content-Type": "application/json"},
        json=payload,
        timeout=20,
    )
    resp.raise_for_status()
    data = resp.json()

    for result in data.get("organic", []):
        result_domain = _extract_domain(result.get("link", ""))
        if result_domain == domain or result_domain.endswith("." + domain):
            return {
                "keyword": keyword,
                "position": result.get("position"),
                "url": result.get("link"),
                "found": True,
            }

    return {"keyword": keyword, "position": None, "url": None, "found": False}


def refresh_rankings(site_url, keywords, location=None, gl="us"):
    """Actually hits Serper for every keyword and overwrites the cache for
    this site. Call this only when the user explicitly asks to refresh —
    each keyword = 1 paid API credit."""
    cache = _load_json(CACHE_FILE)
    site_cache = cache.get(site_url, {})
    now = datetime.datetime.utcnow().isoformat() + "Z"

    for kw in keywords:
        result = check_ranking(kw, site_url, location=location, gl=gl)
        site_cache[kw] = {
            "position": result["position"],
            "url": result["url"],
            "found": result["found"],
            "checked_at": now,
        }

    cache[site_url] = site_cache
    _save_json(CACHE_FILE, cache)
    return get_cached_rankings(site_url)


def get_cached_rankings(site_url):
    """Read-only — returns whatever was cached last time refresh_rankings()
    ran, merged with the current tracked-keyword list (so newly added
    keywords show as 'not checked yet' instead of disappearing)."""
    cache = _load_json(CACHE_FILE).get(site_url, {})
    keywords = get_tracked_keywords(site_url)

    rows = []
    for kw in keywords:
        cached = cache.get(kw)
        if cached:
            rows.append({"keyword": kw, **cached})
        else:
            rows.append({"keyword": kw, "position": None, "url": None, "found": None, "checked_at": None})
    return rows