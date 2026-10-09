import pytest

from claimlens.domain.schemas import Claim, Evidence
from claimlens.graph.verify.retrieve import retrieve_evidence
from claimlens.tools.trusted_sources import domains_for, sources_for


def ev(url: str) -> Evidence:
    return Evidence(url=url, title=url, snippet="s")


def claim(category: str) -> Claim:
    return Claim(id="c1", text="Humans have a tail.", source="speech", category=category)


class Recorder:
    """Stands in for Tavily and records how it was called."""

    def __init__(self, results):
        self.results = results
        self.calls = []

    def __call__(self, query, limit, include_domains=None):
        self.calls.append(include_domains)
        return self.results if include_domains or self.results else []


def test_enough_trusted_results_skips_tavily():
    tavily = Recorder([ev("https://tavily.example/x")])
    searchers = {
        "factcheck": lambda q, n: [],
        "wikipedia": lambda q, n: [ev("https://wiki/1"), ev("https://wiki/2")],
    }

    result = retrieve_evidence(claim("health"), searchers, tavily)

    assert [e.url for e in result] == ["https://wiki/1", "https://wiki/2"]
    assert tavily.calls == []


def test_category_selects_which_sources_are_searched():
    called = []
    searchers = {
        name: (lambda q, n, name=name: called.append(name) or [ev(f"https://{name}/1")] * 2)
        for name in ("factcheck", "wikipedia")
    }

    retrieve_evidence(claim("history"), searchers, Recorder([]))

    assert called == ["wikipedia"]


def test_thin_results_fall_back_to_tavily_on_trusted_domains():
    tavily = Recorder([ev("https://who.int/tail")])
    searchers = {name: (lambda q, n: []) for name in ("factcheck", "wikipedia")}

    result = retrieve_evidence(claim("health"), searchers, tavily)

    assert [e.url for e in result] == ["https://who.int/tail"]
    assert tavily.calls == [domains_for("health", False)]


def test_tavily_opens_up_when_trusted_domains_have_nothing():
    tavily = Recorder([])
    searchers = {name: (lambda q, n: []) for name in ("factcheck", "wikipedia")}

    retrieve_evidence(claim("health"), searchers, tavily)

    assert tavily.calls == [domains_for("health", False), None]


def test_a_failing_source_does_not_stop_the_others():
    def broken(q, n):
        raise RuntimeError("down")

    searchers = {
        "factcheck": broken,
        "wikipedia": lambda q, n: [ev("https://wiki/1"), ev("https://wiki/2")],
    }

    result = retrieve_evidence(claim("science"), searchers, Recorder([]))

    assert len(result) == 2


def test_duplicate_urls_are_removed():
    searchers = {
        "factcheck": lambda q, n: [ev("https://same")],
        "wikipedia": lambda q, n: [ev("https://same"), ev("https://other")],
    }

    assert len(retrieve_evidence(claim("general"), searchers, Recorder([]))) == 2


@pytest.mark.parametrize("category", ["health", "science", "history", "politics", "economy", "technology", "sports", "general", "unknown"])
def test_every_category_has_sources(category):
    sources = sources_for(category)
    assert sources.searchers and sources.domains


def test_search_terms_are_used_instead_of_the_full_sentence():
    queries = []
    searchers = {
        name: (lambda q, n: queries.append(q) or [ev("https://a"), ev("https://b")])
        for name in ("factcheck", "wikipedia")
    }
    c = Claim(id="c1", text="Long sentence.", source="speech", search_terms="tail embryo")

    retrieve_evidence(c, searchers, Recorder([]))

    assert set(queries) == {"tail embryo"}


def test_india_related_claims_search_indian_domains_first():
    tavily = Recorder([ev("https://pib.gov.in/x")])
    searchers = {name: (lambda q, n: []) for name in ("factcheck", "wikipedia")}
    c = Claim(id="c1", text="t", source="speech", category="health", india_related=True)

    retrieve_evidence(c, searchers, tavily)

    assert tavily.calls[0][0] == "mohfw.gov.in"
    assert "who.int" in tavily.calls[0]


def test_global_claims_do_not_get_indian_domains():
    assert "mohfw.gov.in" not in domains_for("health", False)
    assert domains_for("health", True)[0] == "mohfw.gov.in"
    assert len(domains_for("health", True)) == len(set(domains_for("health", True)))


def test_every_query_is_searched_and_results_are_deduplicated():
    queried = []

    def wikipedia(query, limit):
        queried.append(query)
        return [ev("https://wiki/shared"), ev(f"https://wiki/{query}")]

    searchers = {
        "factcheck": lambda q, n: [],
        "wikipedia": wikipedia,
    }

    result = retrieve_evidence(claim("health"), searchers, Recorder([]), queries=["a", "b"])

    assert queried == ["a", "b"]
    assert [e.url for e in result] == [
        "https://wiki/shared", "https://wiki/a", "https://wiki/b"
    ]


def fake_cache(monkeypatch):
    import fakeredis

    from claimlens.tools.cache import ResponseCache

    cache = ResponseCache(fakeredis.FakeRedis(decode_responses=True))
    monkeypatch.setattr("claimlens.graph.verify.retrieve.get_cache", lambda: cache)
    return cache


def test_repeated_searches_are_served_from_the_cache(monkeypatch):
    fake_cache(monkeypatch)
    calls = []

    def wiki(q, n):
        calls.append(q)
        return [ev("https://wiki/1"), ev("https://wiki/2")]

    searchers = {"factcheck": lambda q, n: [], "wikipedia": wiki}

    first = retrieve_evidence(claim("history"), searchers, Recorder([]))
    second = retrieve_evidence(claim("history"), searchers, Recorder([]))

    assert calls == ["Humans have a tail."]
    assert [e.url for e in second] == [e.url for e in first]


def test_failed_searches_are_not_cached(monkeypatch):
    fake_cache(monkeypatch)
    attempts = []

    def flaky(q, n):
        attempts.append(q)
        if len(attempts) == 1:
            raise RuntimeError("429")
        return [ev("https://wiki/1"), ev("https://wiki/2")]

    searchers = {"factcheck": lambda q, n: [], "wikipedia": flaky}

    assert retrieve_evidence(claim("history"), searchers, Recorder([])) == []
    assert len(retrieve_evidence(claim("history"), searchers, Recorder([]))) == 2


def test_tavily_fallback_is_cached(monkeypatch):
    fake_cache(monkeypatch)
    tavily = Recorder([ev("https://tavily.example/x")])
    searchers = {"factcheck": lambda q, n: [], "wikipedia": lambda q, n: []}

    retrieve_evidence(claim("history"), searchers, tavily)
    calls_after_first = len(tavily.calls)
    retrieve_evidence(claim("history"), searchers, tavily)

    assert len(tavily.calls) == calls_after_first


def test_a_broken_cache_does_not_stop_retrieval(monkeypatch):
    class Broken:
        def get(self, *a, **k):
            raise ConnectionError("redis down")

        def set(self, *a, **k):
            raise ConnectionError("redis down")

    monkeypatch.setattr("claimlens.graph.verify.retrieve.get_cache", lambda: Broken())
    searchers = {
        "factcheck": lambda q, n: [],
        "wikipedia": lambda q, n: [ev("https://wiki/1"), ev("https://wiki/2")],
    }

    assert len(retrieve_evidence(claim("history"), searchers, Recorder([]))) == 2
