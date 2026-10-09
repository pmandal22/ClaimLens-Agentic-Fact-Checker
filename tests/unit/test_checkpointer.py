import os
from pathlib import Path
from typing import TypedDict

import psycopg
import pytest
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from claimlens.config.settings import get_settings
from claimlens.graph.checkpointer import open_checkpointer

TEST_POSTGRES_URL = os.environ.get(
    "TEST_POSTGRES_URL", "postgresql://claimlens:claimlens@localhost:5432/claimlens_test"
)


class _State(TypedDict, total=False):
    steps: list[str]
    decision: str


def _build(checkpointer):
    """Tiny stand-in for a graph run: one step, a pause (as if interrupted), then done."""

    def first(state: _State) -> dict:
        return {"steps": ["first"]}

    def review(state: _State) -> dict:
        return {"decision": interrupt({"review": state["steps"]})}

    g = StateGraph(_State)
    g.add_node("first", first)
    g.add_node("review", review)
    g.add_edge(START, "first")
    g.add_edge("first", "review")
    g.add_edge("review", END)
    return g.compile(checkpointer=checkpointer)


@pytest.fixture(params=["sqlite", "postgres"])
def backend(request, tmp_path: Path, monkeypatch):
    if request.param == "sqlite":
        monkeypatch.setenv("CHECKPOINT_DB", str(tmp_path / "nested" / "checkpoints.db"))
        monkeypatch.delenv("POSTGRES_URL", raising=False)
    else:
        try:
            with psycopg.connect(TEST_POSTGRES_URL, connect_timeout=2):
                pass
        except psycopg.OperationalError:
            pytest.skip("Postgres test database not reachable (docker compose up -d postgres)")
        monkeypatch.setenv("POSTGRES_URL", TEST_POSTGRES_URL)
    get_settings.cache_clear()
    yield request.param
    get_settings.cache_clear()


def test_paused_run_resumes_after_reopen(backend, request):
    config = {"configurable": {"thread_id": f"t-{request.node.name}-{os.getpid()}"}}

    with open_checkpointer() as cp:
        app = _build(cp)
        app.invoke({}, config)
        assert app.get_state(config).next == ("review",)

    # A fresh saver stands in for a restarted process.
    with open_checkpointer() as cp:
        app = _build(cp)
        assert app.get_state(config).next == ("review",)
        result = app.invoke(Command(resume="misleading"), config)

    assert result == {"steps": ["first"], "decision": "misleading"}


def test_sqlite_used_without_postgres_url(tmp_path: Path, monkeypatch):
    db = tmp_path / "cp.db"
    monkeypatch.setenv("CHECKPOINT_DB", str(db))
    monkeypatch.delenv("POSTGRES_URL", raising=False)
    get_settings.cache_clear()
    try:
        with open_checkpointer() as cp:
            assert type(cp).__name__ == "SqliteSaver"
        assert db.exists()
    finally:
        get_settings.cache_clear()
