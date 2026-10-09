import httpx

from claimlens.tools import wikipedia


def test_search_retries_after_rate_limit(monkeypatch):
    responses = iter(
        [
            httpx.Response(429, headers={"Retry-After": "1"}, request=httpx.Request("GET", "x")),
            httpx.Response(
                200,
                json={"query": {"search": [{"title": "Spine", "snippet": "a <span>spine</span>"}]}},
                request=httpx.Request("GET", "x"),
            ),
        ]
    )
    sleeps = []
    monkeypatch.setattr(wikipedia.httpx, "get", lambda *a, **k: next(responses))
    monkeypatch.setattr(wikipedia.time, "sleep", sleeps.append)

    [item] = wikipedia.search("spine")

    assert item.url == "https://en.wikipedia.org/wiki/Spine"
    assert sleeps == [1.0]
