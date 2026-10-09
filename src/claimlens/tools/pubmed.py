"""PubMed search (NCBI E-utilities, no key needed at low volume)."""

import re

import httpx

from claimlens.domain.schemas import Evidence

BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
MAX_KEYWORDS = 5
MIN_KEYWORDS = 3
STOPWORDS = frozenset(
    ["about", "after", "around", "because", "before", "between", "become", "being", "from", "have", "into", "more", "most", "over", "than", "that", "their", "them", "then", "there", "these", "they", "this", "those", "together", "under", "very", "were", "what", "when", "where", "which", "while", "will", "with", "would"]
)


def _keywords(text: str) -> list[str]:
    words = dict.fromkeys(
        w for w in re.findall(r"[a-z][a-z-]{2,}", text.lower()) if w not in STOPWORDS
    )
    return list(words)[:MAX_KEYWORDS]


def _find_ids(query: str, limit: int) -> list[str]:
    """PubMed ANDs every term, so a whole sentence matches nothing: drop the last (least
    important) keyword until something is found. Callers pass the key terms most important first."""
    keywords = _keywords(query)
    for count in range(len(keywords), min(MIN_KEYWORDS, len(keywords)) - 1, -1):
        response = httpx.get(
            f"{BASE}/esearch.fcgi",
            params={
                "db": "pubmed",
                "term": " ".join(keywords[:count]),
                "retmax": limit,
                "retmode": "json",
                "sort": "relevance",
            },
            timeout=10,
        )
        response.raise_for_status()
        ids = response.json().get("esearchresult", {}).get("idlist", [])
        if ids:
            return ids
    return []


def search(query: str, limit: int = 3) -> list[Evidence]:
    ids = _find_ids(query, limit)
    if not ids:
        return []

    summary = httpx.get(
        f"{BASE}/esummary.fcgi",
        params={"db": "pubmed", "id": ",".join(ids), "retmode": "json"},
        timeout=10,
    )
    summary.raise_for_status()
    records = summary.json().get("result", {})
    evidence = []
    for pubmed_id in ids:
        record = records.get(pubmed_id)
        if not record:
            continue
        title = record.get("title", "").strip()
        journal = record.get("source", "")
        evidence.append(
            Evidence(
                url=f"https://pubmed.ncbi.nlm.nih.gov/{pubmed_id}/",
                title=title,
                snippet=f"{title} ({journal}, {record.get('pubdate', '')})",
                publisher="PubMed",
            )
        )
    return evidence
