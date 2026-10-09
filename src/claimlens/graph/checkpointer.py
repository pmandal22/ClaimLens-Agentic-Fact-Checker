"""Checkpointer factory: Sqlite locally, Postgres in prod."""

import sqlite3
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.checkpoint.sqlite import SqliteSaver

from claimlens.config.settings import get_settings
from claimlens.domain.schemas import Claim, Evidence, Review, ReviewDecision, Verdict
from claimlens.graph.nodes.aggregate import OverallVerdict

# Our types stored in ReelState. LangGraph warns on (and will soon refuse) restoring
# classes it wasn't told about, which would leave paused runs unresumable.
STATE_TYPES = (Claim, Evidence, Verdict, Review, ReviewDecision, OverallVerdict)


def make_serde() -> JsonPlusSerializer:
    return JsonPlusSerializer(allowed_msgpack_modules=STATE_TYPES)


@contextmanager
def open_checkpointer() -> Generator[BaseCheckpointSaver]:
    """Postgres when POSTGRES_URL is set (shared by many containers), else a local SQLite file.

    A context manager so connections close on exit: open it once per process (worker loop,
    API lifespan) and pass the saver to build_graph(checkpointer).
    """
    settings = get_settings()
    if settings.postgres_url:
        with _postgres(settings.postgres_url) as saver:
            yield saver
    else:
        with _sqlite(settings.checkpoint_db) as saver:
            yield saver


@contextmanager
def _sqlite(db_path: str | Path) -> Generator[SqliteSaver]:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    # SqliteSaver serialises access with its own lock, so sharing across threads is safe.
    conn = sqlite3.connect(db_path, check_same_thread=False)
    try:
        saver = SqliteSaver(conn, serde=make_serde())
        saver.setup()  # creates tables and switches to WAL, so the API can read while the worker writes
        yield saver
    finally:
        conn.close()


@contextmanager
def _postgres(url: str) -> Generator[BaseCheckpointSaver]:
    # Imported here so local runs don't need the optional Postgres saver installed.
    from langgraph.checkpoint.postgres import PostgresSaver
    from psycopg import Connection
    from psycopg.rows import DictRow, dict_row
    from psycopg_pool import ConnectionPool

    # A pool rather than one connection: API request threads and the worker's heartbeat
    # thread use the saver concurrently. These kwargs are what PostgresSaver requires.
    with ConnectionPool(
        url,
        connection_class=Connection[DictRow],
        min_size=1,
        max_size=10,
        kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
    ) as pool:
        saver = PostgresSaver(pool, serde=make_serde())
        saver.setup()  # idempotent: creates or migrates the checkpoint tables
        yield saver
