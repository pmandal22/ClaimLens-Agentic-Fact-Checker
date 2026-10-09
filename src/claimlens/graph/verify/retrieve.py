"""Find evidence for a claim: category's trusted sources first, Tavily as the fallback."""

import logging
from collections.abc import Callable
from functools import partial

from claimlens.config.settings import get_settings
from claimlens.domain.schemas import Claim, Evidence
from claimlens.tools import factcheck_api, web_search, wikipedia
from claimlens.tools.cache import get_cache
from claimlens.tools.trusted_sources import domains_for, sources_for

logger = logging.getLogger(__name__)

Searcher = Callable[[str, int], list[Evidence]]
SEARCHERS: dict[str, Searcher] = {
    "factcheck": factcheck_api.search,
    "wikipedia": wikipedia.search,
}


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
    searchers: dict[str, Searcher] | None = None,
    fallback: Callable[..., list[Evidence]] | None = None,
    queries: list[str] | None = None,
) -> list[Evidence]:
    """Search the trusted sources for claim.category; use Tavily only if that's not enough.

    Every query in `queries` (default: the claim's search terms) is searched.
    One failing source never stops the others: errors are logged and the search moves on.
    """
    settings = get_settings()
    searchers = SEARCHERS if searchers is None else searchers
    fallback = web_search.search if fallback is None else fallback
    sources = sources_for(claim.category)
    domains = domains_for(claim.category, claim.india_related)
    limit = settings.evidence_per_source
    queries = queries or [claim.search_terms or claim.text]

    evidence: list[Evidence] = []
    for query in queries:
        for name in sources.searchers:
            try:
                evidence += _cached(
                    name, query, {"limit": limit}, partial(searchers[name], query, limit)
                )
            except Exception:
                logger.exception("claim=%s source=%s failed", claim.id, name)
    evidence = dedupe(evidence)
    if len(evidence) >= settings.min_trusted_evidence:
        return evidence

    logger.info("claim=%s: %d trusted result(s), falling back to Tavily", claim.id, len(evidence))
    for query in queries:
        try:
            # Stay on the category's trusted domains first; open the search up only if empty.
            scoped = {"limit": limit, "domains": list(domains)}
            extra = _cached(
                "tavily", query, scoped, partial(fallback, query, limit, include_domains=domains)
            )
            if not extra:
                extra = _cached("tavily", query, {"limit": limit}, partial(fallback, query, limit))
            evidence = dedupe(evidence + extra)
        except Exception:
            logger.exception("claim=%s Tavily fallback failed", claim.id)
        if len(evidence) >= settings.min_trusted_evidence:
            break
    return evidence
