"""Redis-backed cache for search responses."""

import hashlib
import json
import logging
from collections.abc import Callable, Mapping, Sequence
from functools import lru_cache
from typing import Protocol

from pydantic import TypeAdapter

from claimlens.config.settings import get_settings
from claimlens.domain.schemas import Evidence

logger = logging.getLogger(__name__)

DEFAULT_TTL_SECONDS = 24 * 60 * 60
_EVIDENCE_LIST = TypeAdapter(list[Evidence])


class RedisCacheClient(Protocol):
    def get(self, name: str) -> str | bytes | None: ...

    def set(self, name: str, value: str, ex: int) -> object: ...


class ResponseCache:
    """Cache evidence by query and optional request details.

    Pass a Redis client configured with ``decode_responses=True``. ``namespace`` and
    ``params`` let callers keep results from different providers or search options apart.
    """

    def __init__(
        self,
        client: RedisCacheClient,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
        key_prefix: str = "claimlens:search-cache",
    ) -> None:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be greater than zero")
        if not key_prefix:
            raise ValueError("key_prefix must not be empty")
        self._client = client
        self._ttl_seconds = ttl_seconds
        self._key_prefix = key_prefix

    def get(
        self,
        query: str,
        *,
        namespace: str = "search",
        params: Mapping[str, object] | None = None,
    ) -> list[Evidence] | None:
        """Return cached evidence, or ``None`` when this request has not been cached."""
        payload = self._client.get(self._key(query, namespace, params))
        if payload is None:
            return None
        return _EVIDENCE_LIST.validate_python(json.loads(payload))

    def set(
        self,
        query: str,
        evidence: Sequence[Evidence],
        *,
        namespace: str = "search",
        params: Mapping[str, object] | None = None,
    ) -> None:
        """Store evidence for a query until the configured TTL expires."""
        payload = json.dumps(
            [item.model_dump(mode="json") for item in evidence],
            separators=(",", ":"),
        )
        self._client.set(
            self._key(query, namespace, params),
            payload,
            ex=self._ttl_seconds,
        )

    def get_or_set(
        self,
        query: str,
        loader: Callable[[], Sequence[Evidence]],
        *,
        namespace: str = "search",
        params: Mapping[str, object] | None = None,
    ) -> list[Evidence]:
        """Load and cache evidence only when no cached response exists."""
        cached = self.get(query, namespace=namespace, params=params)
        if cached is not None:
            return cached

        evidence = list(loader())
        self.set(query, evidence, namespace=namespace, params=params)
        return evidence

    def _key(
        self,
        query: str,
        namespace: str,
        params: Mapping[str, object] | None,
    ) -> str:
        request = json.dumps(
            {"query": query, "namespace": namespace, "params": params or {}},
            sort_keys=True,
            separators=(",", ":"),
        )
        digest = hashlib.sha256(request.encode("utf-8")).hexdigest()
        return f"{self._key_prefix}:{digest}"


@lru_cache
def get_cache() -> ResponseCache | None:
    """Shared cache from settings, or None when disabled or Redis is unreachable.

    Caching is an optimisation, so a missing Redis must never stop a check.
    """
    settings = get_settings()
    if not settings.search_cache_enabled:
        return None
    try:
        import redis

        client = redis.Redis.from_url(
            settings.redis_url, decode_responses=True, socket_connect_timeout=2, socket_timeout=2
        )
        client.ping()
    except Exception:
        logger.warning("Redis unavailable; search caching is off", exc_info=True)
        return None
    return ResponseCache(client, ttl_seconds=settings.search_cache_ttl_s)
