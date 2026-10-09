"""Wikipedia search (MediaWiki API, no key needed)."""

import re
import time
from urllib.parse import quote

import httpx

from claimlens.domain.schemas import Evidence

API = "https://en.wikipedia.org/w/api.php"
# Wikimedia asks API clients to identify themselves.
HEADERS = {"User-Agent": "ClaimLens/0.1 (fact-checking research project)"}
MAX_RATE_LIMIT_RETRIES = 2
MAX_BACKOFF_S = 5.0


def _get(params: dict) -> httpx.Response:
    """GET with a short backoff when Wikipedia answers 429 Too Many Requests."""
    for attempt in range(MAX_RATE_LIMIT_RETRIES + 1):
        response = httpx.get(API, params=params, headers=HEADERS, timeout=10)
        if response.status_code != 429 or attempt == MAX_RATE_LIMIT_RETRIES:
            return response
        retry_after = response.headers.get("Retry-After", "")
        delay = float(retry_after) if retry_after.isdigit() else 1.0 * 2**attempt
        time.sleep(min(delay, MAX_BACKOFF_S))
    return response


def search(query: str, limit: int = 3) -> list[Evidence]:
    response = _get(
        {
            "action": "query",
            "list": "search",
            "srsearch": query,
            "srlimit": limit,
            "format": "json",
        }
    )
    response.raise_for_status()
    hits = response.json().get("query", {}).get("search", [])
    return [
        Evidence(
            url="https://en.wikipedia.org/wiki/" + quote(hit["title"].replace(" ", "_")),
            title=hit["title"],
            # The API wraps matched words in <span> tags and escapes entities.
            snippet=re.sub(r"<[^>]+>", "", hit["snippet"]).replace("&quot;", '"'),
            publisher="Wikipedia",
        )
        for hit in hits
    ]
