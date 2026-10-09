import os

import pytest

# Never send test runs to LangSmith. The app loads .env without overriding variables that are
# already set, so this wins over LANGSMITH_TRACING=true there.
os.environ["LANGSMITH_TRACING"] = "false"
os.environ["LANGCHAIN_TRACING_V2"] = "false"


@pytest.fixture(autouse=True)
def no_search_cache(monkeypatch):
    """Keep tests off any real Redis; cache tests install their own."""
    monkeypatch.setattr("claimlens.graph.verify.retrieve.get_cache", lambda: None)
