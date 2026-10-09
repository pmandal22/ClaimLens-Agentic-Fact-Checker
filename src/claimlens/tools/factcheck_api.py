"""Google Fact Check Tools API (claims:search) client. Needs FACTCHECK_API_KEY."""

import httpx

from claimlens.config.settings import get_settings
from claimlens.domain.schemas import Evidence

URL = "https://factchecktools.googleapis.com/v1alpha1/claims:search"


def search(query: str, limit: int = 3) -> list[Evidence]:
    api_key = get_settings().factcheck_api_key
    if not api_key:
        return []

    response = httpx.get(
        URL,
        params={"query": query, "pageSize": limit, "languageCode": "en", "key": api_key},
        timeout=10,
    )
    response.raise_for_status()
    evidence = []
    for claim in response.json().get("claims", []):
        for review in claim.get("claimReview", [])[:1]:
            text = claim.get("text", "")
            evidence.append(
                Evidence(
                    url=review["url"],
                    title=review.get("title") or text,
                    snippet=f"Claim: {text} | Rating: {review.get('textualRating', '')}",
                    publisher=review.get("publisher", {}).get("name"),
                )
            )
    return evidence[:limit]
