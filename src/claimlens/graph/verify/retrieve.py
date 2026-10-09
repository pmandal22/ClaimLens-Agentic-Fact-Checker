"""Find evidence for a claim with Tavily, preferring the category's trusted domains."""

import logging
from collections.abc import Callable
from functools import partial

from claimlens.config.settings import get_settings
from claimlens.domain.schemas import Claim, Evidence
from claimlens.tools import web_search
from claimlens.tools.cache import get_cache
from claimlens.tools.trusted_sources import domains_for

logger = logging.getLogger(__name__)


def dedupe(items: list[Evidence]) -> list[Evidence]:
    seen: set[str] = set()
    unique = []
    for item in items:
        if item.url not in seen:
            seen.add(item.url)
            unique.append(item)
    return unique


def _cached(
    name: str, query: str, params: dict[str, object], load: Callable[[], list[Evidence]]
) -> list[Evidence]:
    """Serve a search from the cache when possible. Only successful responses are stored, so
    a rate-limited or failed call is retried next time. Cache errors fall back to the live call."""
    cache = get_cache()
    if cache is None:
        return load()
    try:
        hit = cache.get(query, namespace=name, params=params)
    except Exception:
        logger.warning("cache read failed for %s", name, exc_info=True)
        return load()
    if hit is not None:
        return hit
    found = load()
    try:
        cache.set(query, found, namespace=name, params=params)
    except Exception:
        logger.warning("cache write failed for %s", name, exc_info=True)
    return found


def retrieve_evidence(
    claim: Claim,
    search: Callable[..., list[Evidence]] | None = None,
    queries: list[str] | None = None,
) -> list[Evidence]:
    """Search Tavily within the category's trusted domains; open the search up only if empty.

    Queries in `queries` (default: the claim's search terms) are searched in order until
    there is enough evidence. A failing query is logged and the search moves on.
    """
    settings = get_settings()
    search = web_search.search if search is None else search
    domains = domains_for(claim.category, claim.india_related)
    limit = settings.evidence_per_source
    queries = queries or [claim.search_terms or claim.text]

    evidence: list[Evidence] = []
    for query in queries:
        try:
            scoped = {"limit": limit, "domains": list(domains)}
            found = _cached(
                "tavily", query, scoped, partial(search, query, limit, include_domains=domains)
            )
            if not found:
                found = _cached("tavily", query, {"limit": limit}, partial(search, query, limit))
            evidence = dedupe(evidence + found)
        except Exception:
            logger.exception("claim=%s Tavily search failed", claim.id)
        if len(evidence) >= settings.min_trusted_evidence:
            break
    return evidence
