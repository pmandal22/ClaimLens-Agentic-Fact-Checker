import pytest


@pytest.fixture(autouse=True)
def no_search_cache(monkeypatch):
    """Keep tests off any real Redis; cache tests install their own."""
    monkeypatch.setattr("claimlens.graph.verify.retrieve.get_cache", lambda: None)
