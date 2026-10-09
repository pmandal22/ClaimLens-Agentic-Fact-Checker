"""Injected services. Tests replace these with fakes via app.dependency_overrides."""

import secrets
from collections.abc import Iterator
from functools import lru_cache
from typing import Annotated

from fastapi import Header, HTTPException, status
from langgraph.graph.state import CompiledStateGraph

from claimlens.config.settings import get_settings
from claimlens.graph.checkpointer import open_checkpointer
from claimlens.graph.main_graph import build_graph
from claimlens.services.jobs import JobStore, get_job_store
from claimlens.services.queue import JobQueue, get_queue
from claimlens.services.storage import Storage, get_storage


@lru_cache
def job_store() -> JobStore:
    return get_job_store()


@lru_cache
def job_queue() -> JobQueue:
    return get_queue()


@lru_cache
def storage() -> Storage:
    return get_storage()


def reel_graph() -> Iterator[CompiledStateGraph]:
    """The reel graph on the workers' checkpointer. Opened per request: only reviews use it."""
    with open_checkpointer() as checkpointer:
        yield build_graph(checkpointer)


def require_reviewer(x_review_token: Annotated[str | None, Header()] = None) -> None:
    """Gate publishing a review behind REVIEW_TOKEN; open when it isn't set (local dev)."""
    expected = get_settings().review_token
    if expected and not secrets.compare_digest(x_review_token or "", expected):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "A valid reviewer token is required")
