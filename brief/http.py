"""HTTPS GET with bounded retries (from the legacy app.get)."""
from urllib.parse import urlparse

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .config import USER_AGENT


def safe_url(value):
    parsed = urlparse(str(value))
    return str(value) if parsed.scheme == "https" and parsed.hostname and not parsed.username else ""


HTML = "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8"
JSON = "application/json"
GEOJSON = "application/geo+json, application/json;q=0.9"


def get(url, *, params=None, accept=HTML, timeout=(4, 10)):
    """Ask for one content type per request: some servers answer a mixed Accept with 406."""
    if not safe_url(url):
        raise ValueError("Feed requires an HTTPS URL")
    # Each worker owns its Session; connection retries are bounded.
    with requests.Session() as session:
        session.mount("https://", HTTPAdapter(max_retries=Retry(
            total=1, backoff_factor=0.3, status_forcelist=[429, 502, 503, 504],
            allowed_methods=["GET"], respect_retry_after_header=False)))
        headers = {"User-Agent": USER_AGENT,
                   "Accept": accept}
        response = session.get(url, params=params, headers=headers, timeout=timeout)
        response.raise_for_status()
        return response
