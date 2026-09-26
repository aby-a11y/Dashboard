"""Shared helper — normalizes a GSC-style site URL / GA4 stream URL / GMB
website URL down to a bare comparable hostname, so auto-discovery can match
"https://www.pixelglobalit.com/" (GSC) against "pixelglobalit.com" (GA4
stream default_uri) against "www.pixelglobalit.com" (GMB websiteUri)."""


def extract_domain(url_or_domain):
    d = (url_or_domain or "").strip().lower()
    d = d.replace("sc-domain:", "")
    d = d.replace("https://", "").replace("http://", "")
    d = d.split("/")[0]
    if d.startswith("www."):
        d = d[4:]
    return d
