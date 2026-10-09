"""Tavily web search client (the fallback). Needs TAVILY_API_KEY."""

from urllib.parse import urlsplit

import httpx

from claimlens.config.settings import get_settings
from claimlens.domain.schemas import Evidence

URL = "https://api.tavily.com/search"


def search(query: str, limit: int = 3, include_domains: list[str] | None = None) -> list[Evidence]:
    api_key = get_settings().tavily_api_key
    if not api_key:
        return []

    body: dict[str, object] = {"query": query, "max_results": limit, "search_depth": "basic"}
    if include_domains:
        body["include_domains"] = include_domains
    headers = {"Authorization": f"Bearer {api_key}"}
    response = httpx.post(URL, json=body, headers=headers, timeout=20)
    response.raise_for_status()
    return [
        Evidence(
            url=hit["url"],
            title=hit.get("title", ""),
            snippet=hit.get("content", ""),
            publisher=urlsplit(hit["url"]).netloc.removeprefix("www."),
        )
        for hit in response.json().get("results", [])
    ]
