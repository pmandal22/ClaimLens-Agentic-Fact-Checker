"""Postgres-backed job store: lets the API and several workers share one database."""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import psycopg
from psycopg.rows import dict_row

from claimlens.services.jobs import (
    ALLOWED_TRANSITIONS,
    IN_PROGRESS,
    InvalidTransitionError,
    Job,
    JobNotFoundError,
    JobStatus,
)
from claimlens.services.storage import video_key

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    url TEXT NOT NULL,
    video_key TEXT NOT NULL,
    status TEXT NOT NULL,
    error TEXT,
    claimed_by TEXT,
    claim_expires_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_jobs_status_created ON jobs (status, created_at);
CREATE INDEX IF NOT EXISTS idx_jobs_video_key ON jobs (video_key);
"""


def _to_job(row: dict[str, Any]) -> Job:
    return Job(
        id=row["id"],
        url=row["url"],
        video_key=row["video_key"],
        status=JobStatus(row["status"]),
        error=row["error"],
        created_at=row["created_at"].isoformat(),
        updated_at=row["updated_at"].isoformat(),
        claimed_by=row["claimed_by"],
        claim_expires_at=row["claim_expires_at"].isoformat() if row["claim_expires_at"] else None,
    )


class PostgresJobStore:
    def __init__(self, dsn: str) -> None:
        self.dsn = dsn
        with self._connect() as conn:
            # Two processes starting together could both try to create the table; the lock
            # makes them take turns.
            conn.execute("SELECT pg_advisory_xact_lock(727001)")
            conn.execute(_SCHEMA)
            # Add columns if missing (safe to run repeatedly).
            conn.execute("ALTER TABLE jobs ADD COLUMN IF NOT EXISTS claimed_by TEXT")
            conn.execute("ALTER TABLE jobs ADD COLUMN IF NOT EXISTS claim_expires_at TIMESTAMPTZ")

    def _connect(self) -> psycopg.Connection[dict[str, Any]]:
        # One short-lived connection per call. `with conn:` commits on success and rolls
        # back on error. A connection pool is the upgrade if this becomes a bottleneck.
        return psycopg.connect(self.dsn, row_factory=dict_row)

    def create(self, url: str) -> Job:
        """Create a job, or return the existing one if this video is already queued or done."""
        key = video_key(url)
        with self._connect() as conn:
            # Serialize concurrent creates of the same video so two requests can't both insert.
            conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (key,))
            existing = conn.execute(
                "SELECT * FROM jobs WHERE video_key = %s AND status != %s "
                "ORDER BY created_at DESC LIMIT 1",
                (key, JobStatus.FAILED.value),
            ).fetchone()
            if existing:
                return _to_job(existing)

            now = datetime.now(UTC)
            row = conn.execute(
                "INSERT INTO jobs (id, url, video_key, status, error, created_at, updated_at) "
                "VALUES (%s, %s, %s, %s, NULL, %s, %s) RETURNING *",
                (uuid.uuid4().hex, url, key, JobStatus.QUEUED.value, now, now),
            ).fetchone()
        assert row is not None
        return _to_job(row)

    def get(self, job_id: str) -> Job:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE id = %s", (job_id,)).fetchone()
        if row is None:
            raise JobNotFoundError(job_id)
        return _to_job(row)

    def claim(self, job_id: str, resume: bool = False, worker_id: str | None = None, lease_seconds: int = 900) -> Job | None:
        """Atomically move a queued job to DOWNLOADING and set a claim lease.

        resume=True also takes a job a dead worker left in progress, once its lease has run out.
        """
        with self._connect() as conn:
            now = datetime.now(UTC)
            expires = (now + timedelta(seconds=lease_seconds)) if worker_id else None
            # A NULL lease means no worker id was given, so there is nothing to wait for.
            condition = (
                "(status = %s OR (status = ANY(%s) "
                "AND (claim_expires_at IS NULL OR claim_expires_at < %s)))"
                if resume
                else "status = %s"
            )
            params = (
                (JobStatus.QUEUED.value, [status.value for status in IN_PROGRESS], now)
                if resume
                else (JobStatus.QUEUED.value,)
            )
            row = conn.execute(
                "UPDATE jobs SET status = %s, updated_at = %s, claimed_by = %s, claim_expires_at = %s "
                f"WHERE id = %s AND {condition} RETURNING *",
                (JobStatus.DOWNLOADING.value, now, worker_id, expires, job_id, *params),
            ).fetchone()
        return _to_job(row) if row else None

    def heartbeat(self, job_id: str, worker_id: str, lease_seconds: int = 900) -> bool:
        """Extend the claim lease for a job when called by the claiming worker.

        Returns True when the heartbeat extended the lease, False if the job wasn't claimed by
        the given worker (so the caller should stop heartbeating).
        """
        with self._connect() as conn:
            new_exp = datetime.now(UTC) + timedelta(seconds=lease_seconds)
            cursor = conn.execute(
                "UPDATE jobs SET claim_expires_at = %s, updated_at = %s WHERE id = %s AND claimed_by = %s",
                (new_exp, datetime.now(UTC), job_id, worker_id),
            )
        return cursor.rowcount > 0

    def set_status(self, job_id: str, status: JobStatus, error: str | None = None) -> Job:
        current = self.get(job_id).status
        if status not in ALLOWED_TRANSITIONS[current]:
            raise InvalidTransitionError(f"Cannot move job from {current} to {status}")
        with self._connect() as conn:
            # "AND status = %s" guards against another process changing it since we read it.
            row = conn.execute(
                "UPDATE jobs SET status = %s, error = %s, updated_at = %s "
                "WHERE id = %s AND status = %s RETURNING *",
                (
                    status.value,
                    error if status == JobStatus.FAILED else None,
                    datetime.now(UTC),
                    job_id,
                    current.value,
                ),
            ).fetchone()
        if row is None:
            raise InvalidTransitionError(f"Job {job_id} changed while updating; retry")
        return _to_job(row)
