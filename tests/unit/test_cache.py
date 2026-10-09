import fakeredis
import pytest

from claimlens.domain.schemas import Evidence
from claimlens.tools.cache import ResponseCache


def test_cache_round_trips_evidence_and_applies_ttl():
    client = fakeredis.FakeRedis(decode_responses=True)
    cache = ResponseCache(client, ttl_seconds=60)
    evidence = [Evidence(url="https://example.test/a", title="A", snippet="Snippet")]

    assert cache.get("claim") is None
    cache.set("claim", evidence)

    assert cache.get("claim") == evidence
    key = client.keys("claimlens:search-cache:*")[0]
    assert client.ttl(key) == 60


def test_get_or_set_loads_once_and_caches_empty_responses():
    cache = ResponseCache(fakeredis.FakeRedis(decode_responses=True))
    calls = 0

    def load() -> list[Evidence]:
        nonlocal calls
        calls += 1
        return []

    assert cache.get_or_set("claim", load) == []
    assert cache.get_or_set("claim", load) == []
    assert calls == 1


def test_cache_request_scope_separates_providers_and_options():
    cache = ResponseCache(fakeredis.FakeRedis(decode_responses=True))
    evidence = [Evidence(url="https://example.test/a", title="A", snippet="Snippet")]
    cache.set("claim", evidence, namespace="provider-a", params={"limit": 2})

    assert cache.get("claim", namespace="provider-a", params={"limit": 2}) == evidence
    assert cache.get("claim", namespace="provider-b", params={"limit": 2}) is None
    assert cache.get("claim", namespace="provider-a", params={"limit": 3}) is None


def test_cache_requires_positive_ttl():
    with pytest.raises(ValueError, match="ttl_seconds"):
        ResponseCache(fakeredis.FakeRedis(), ttl_seconds=0)
