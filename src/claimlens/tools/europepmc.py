"""Europe PMC search (biomedical and life-science literature, no key needed)."""

import httpx

from claimlens.domain.schemas import Evidence

URL = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"


def search(query: str, limit: int = 3) -> list[Evidence]:
    response = httpx.get(
        URL,
        params={"query": query, "format": "json", "pageSize": limit, "resultType": "lite"},
        timeout=10,
    )
    response.raise_for_status()
    evidence = []
    for hit in response.json().get("resultList", {}).get("result", []):
        title = hit.get("title", "").strip()
        journal = hit.get("journalTitle") or hit.get("source", "")
        evidence.append(
            Evidence(
                url=f"https://europepmc.org/article/{hit['source']}/{hit['id']}",
                title=title,
                snippet=f"{title} ({journal}, {hit.get('pubYear', '')})",
                publisher="Europe PMC",
            )
        )
    return evidence
