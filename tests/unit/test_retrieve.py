import pytest

from claimlens.domain.schemas import Claim, Evidence
from claimlens.graph.verify.retrieve import retrieve_evidence
from claimlens.tools.trusted_sources import domains_for


def ev(url: str) -> Evidence:
    return Evidence(url=url, title=url, snippet="s")


def claim(category: str) -> Claim:
    return Claim(id="c1", text="Humans have a tail.", source="speech", category=category)


class Recorder:
    """Stands in for Tavily and records how it was called."""

    def __init__(self, results):
        self.results = results
        self.calls = []
        self.queries = []

    def __call__(self, query, limit, include_domains=None):
        self.calls.append(include_domains)
        self.queries.append(query)
        return self.results if include_domains or self.results else []


def test_search_stays_on_trusted_domains():
    tavily = Recorder([ev("https://who.int/tail")])

    result = retrieve_evidence(claim("health"), tavily)

    assert [e.url for e in result] == ["https://who.int/tail"]
    assert tavily.calls == [domains_for("health", False)]


def test_search_opens_up_when_trusted_domains_have_nothing():
    tavily = Recorder([])

    retrieve_evidence(claim("health"), tavily)

    assert tavily.calls == [domains_for("health", False), None]


def test_a_failing_query_does_not_stop_the_others():
    calls = []

    def flaky(query, limit, include_domains=None):
        calls.append(query)
        if query == "a":
            raise RuntimeError("down")
        return [ev("https://x/1"), ev("https://x/2")]

    result = retrieve_evidence(claim("science"), flaky, queries=["a", "b"])

    assert calls == ["a", "b"]
    assert len(result) == 2


def test_duplicate_urls_are_removed():
    tavily = Recorder([ev("https://same")])

    result = retrieve_evidence(claim("general"), tavily, queries=["a", "b", "c"])

    assert [e.url for e in result] == ["https://same"]


@pytest.mark.parametrize("category", ["health", "science", "history", "politics", "economy", "technology", "sports", "general", "unknown"])
def test_every_category_has_domains(category):
    assert domains_for(category, False)


def test_search_terms_are_used_instead_of_the_full_sentence():
    tavily = Recorder([ev("https://a"), ev("https://b")])
    c = Claim(id="c1", text="Long sentence.", source="speech", search_terms="tail embryo")

    retrieve_evidence(c, tavily)

    assert tavily.queries == ["tail embryo"]


def test_india_related_claims_search_indian_domains_first():
    tavily = Recorder([ev("https://pib.gov.in/x")])
    c = Claim(id="c1", text="t", source="speech", category="health", india_related=True)

    retrieve_evidence(c, tavily)

    assert tavily.calls[0][0] == "mohfw.gov.in"
    assert "who.int" in tavily.calls[0]


def test_global_claims_do_not_get_indian_domains():
    assert "mohfw.gov.in" not in domains_for("health", False)
    assert domains_for("health", True)[0] == "mohfw.gov.in"
    assert len(domains_for("health", True)) == len(set(domains_for("health", True)))


def test_queries_are_searched_until_there_is_enough_evidence():
    def search(query, limit, include_domains=None):
        return [ev("https://x/shared"), ev(f"https://x/{query}")] if query != "a" else []

    result = retrieve_evidence(claim("health"), search, queries=["a", "b", "c"])

    assert [e.url for e in result] == ["https://x/shared", "https://x/b"]


def fake_cache(monkeypatch):
    import fakeredis

    from claimlens.tools.cache import ResponseCache

    cache = ResponseCache(fakeredis.FakeRedis(decode_responses=True))
    monkeypatch.setattr("claimlens.graph.verify.retrieve.get_cache", lambda: cache)
    return cache


def test_repeated_searches_are_served_from_the_cache(monkeypatch):
    fake_cache(monkeypatch)
    tavily = Recorder([ev("https://x/1"), ev("https://x/2")])

    first = retrieve_evidence(claim("history"), tavily)
    second = retrieve_evidence(claim("history"), tavily)

    assert tavily.queries == ["Humans have a tail."]
    assert [e.url for e in second] == [e.url for e in first]


def test_failed_searches_are_not_cached(monkeypatch):
    fake_cache(monkeypatch)
    attempts = []

    def flaky(query, limit, include_domains=None):
        attempts.append(query)
        if len(attempts) == 1:
            raise RuntimeError("429")
        return [ev("https://x/1"), ev("https://x/2")]

    assert retrieve_evidence(claim("history"), flaky) == []
    assert len(retrieve_evidence(claim("history"), flaky)) == 2


def test_a_broken_cache_does_not_stop_retrieval(monkeypatch):
    class Broken:
        def get(self, *a, **k):
            raise ConnectionError("redis down")

        def set(self, *a, **k):
            raise ConnectionError("redis down")

    monkeypatch.setattr("claimlens.graph.verify.retrieve.get_cache", lambda: Broken())
    tavily = Recorder([ev("https://x/1"), ev("https://x/2")])

    assert len(retrieve_evidence(claim("history"), tavily)) == 2
