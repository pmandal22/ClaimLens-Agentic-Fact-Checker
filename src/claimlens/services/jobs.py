"""Job records: what the API creates and the worker advances. SQLite locally, Postgres shared."""

import sqlite3
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Protocol

from claimlens.config.settings import get_settings
from claimlens.services.storage import video_key


class JobStatus(StrEnum):
    QUEUED = "queued"
    DOWNLOADING = "downloading"
    INGESTING = "ingesting"
    VERIFYING = "verifying"
    NEEDS_REVIEW = "needs_review"
    DONE = "done"
    FAILED = "failed"


# Which status may follow which; blocks bugs like moving a finished job back to "queued".
ALLOWED_TRANSITIONS: dict[JobStatus, set[JobStatus]] = {
    JobStatus.QUEUED: {JobStatus.DOWNLOADING, JobStatus.FAILED},
    JobStatus.DOWNLOADING: {JobStatus.INGESTING, JobStatus.FAILED},
    JobStatus.INGESTING: {JobStatus.VERIFYING, JobStatus.FAILED},
    JobStatus.VERIFYING: {JobStatus.NEEDS_REVIEW, JobStatus.DONE, JobStatus.FAILED},
    JobStatus.NEEDS_REVIEW: {JobStatus.VERIFYING, JobStatus.DONE, JobStatus.FAILED},
    JobStatus.DONE: set(),
    # A failed job can be retried by re-queueing it.
    JobStatus.FAILED: {JobStatus.QUEUED},
}


@dataclass(frozen=True)
class Job:
    id: str
    url: str
    video_key: str
    status: JobStatus
    error: str | None
    created_at: str
    updated_at: str


class JobNotFoundError(KeyError):
    pass


class InvalidTransitionError(ValueError):
    pass


class JobStore(Protocol):
    def create(self, url: str) -> Job: ...

    def get(self, job_id: str) -> Job: ...

    def claim(self, job_id: str, resume: bool = False, worker_id: str | None = None, lease_seconds: int = 900) -> Job | None: ...

    def heartbeat(self, job_id: str, worker_id: str, lease_seconds: int = 900) -> bool: ...

    def set_status(self, job_id: str, status: JobStatus, error: str | None = None) -> Job: ...


_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    url TEXT NOT NULL,
    video_key TEXT NOT NULL,
    status TEXT NOT NULL,
    error TEXT,
    claimed_by TEXT,
    claim_expires_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_jobs_status_created ON jobs (status, created_at);
CREATE INDEX IF NOT EXISTS idx_jobs_video_key ON jobs (video_key);
"""


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _to_job(row: sqlite3.Row) -> Job:
    return Job(
        id=row["id"],
        url=row["url"],
        video_key=row["video_key"],
        status=JobStatus(row["status"]),
        error=row["error"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


class SQLiteJobStore:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = str(db_path)
        with self._connect() as conn:
            conn.executescript(_SCHEMA)
            # Add new columns if they aren't present yet (safe to run repeatedly).
            try:
                conn.execute("ALTER TABLE jobs ADD COLUMN claimed_by TEXT")
            except sqlite3.OperationalError:
                # Column already exists or other harmless error; ignore
                pass
            try:
                conn.execute("ALTER TABLE jobs ADD COLUMN claim_expires_at TEXT")
            except sqlite3.OperationalError:
                pass

    def _connect(self) -> sqlite3.Connection:
        # One short-lived connection per call keeps this safe across threads and processes.
        # isolation_level=None lets us control transactions explicitly (BEGIN IMMEDIATE).
        conn = sqlite3.connect(self.db_path, timeout=30, isolation_level=None)
        conn.row_factory = sqlite3.Row
        return conn

    def create(self, url: str) -> Job:
        """Create a job, or return the existing one if this video is already queued or done."""
        key = video_key(url)
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                "SELECT * FROM jobs WHERE video_key = ? AND status != ? "
                "ORDER BY created_at DESC LIMIT 1",
                (key, JobStatus.FAILED),
            ).fetchone()
            if existing:
                conn.execute("COMMIT")
                return _to_job(existing)

            now = _now()
            job_id = uuid.uuid4().hex
            conn.execute(
                "INSERT INTO jobs (id, url, video_key, status, error, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, NULL, ?, ?)",
                (job_id, url, key, JobStatus.QUEUED, now, now),
            )
            conn.execute("COMMIT")
        except BaseException:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()
        return self.get(job_id)

    def get(self, job_id: str) -> Job:
        conn = self._connect()
        try:
            row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        finally:
            conn.close()
        if row is None:
            raise JobNotFoundError(job_id)
        return _to_job(row)

    def claim(self, job_id: str, resume: bool = False, worker_id: str | None = None, lease_seconds: int = 900) -> Job | None:
        """Atomically move a queued job to DOWNLOADING and set a claim lease.

        If worker_id is provided, the job's claimed_by and claim_expires_at fields are set so
        other workers can reclaim after the lease expires. resume=True allows re-claiming a
        previously-downloading job (redelivery).
        """
        claimable = [JobStatus.QUEUED, *([JobStatus.DOWNLOADING] if resume else [])]
        placeholders = ", ".join("?" for _ in claimable)
        conn = self._connect()
        try:
            now = _now()
            expires = (datetime.now(UTC) + timedelta(seconds=lease_seconds)).isoformat()
            if worker_id:
                row = conn.execute(
                    f"UPDATE jobs SET status = ?, updated_at = ?, claimed_by = ?, claim_expires_at = ? "
                    f"WHERE id = ? AND status IN ({placeholders}) RETURNING *",
                    (JobStatus.DOWNLOADING, now, worker_id, expires, job_id, *claimable),
                ).fetchone()
            else:
                row = conn.execute(
                    f"UPDATE jobs SET status = ?, updated_at = ? "
                    f"WHERE id = ? AND status IN ({placeholders}) RETURNING *",
                    (JobStatus.DOWNLOADING, now, job_id, *claimable),
                ).fetchone()
        finally:
            conn.close()
        return _to_job(row) if row else None

    def heartbeat(self, job_id: str, worker_id: str, lease_seconds: int = 900) -> bool:
        """Extend the claim lease for a job when called by the claiming worker.

        Returns True when the heartbeat extended the lease, False if the job wasn't claimed by
        the given worker (so the caller should stop heartbeating).
        """
        conn = self._connect()
        try:
            # Only extend if claimed_by matches worker_id
            new_exp = (datetime.now(UTC) + timedelta(seconds=lease_seconds)).isoformat()
            cursor = conn.execute(
                "UPDATE jobs SET claim_expires_at = ?, updated_at = ? "
                "WHERE id = ? AND claimed_by = ?",
                (new_exp, _now(), job_id, worker_id),
            )
        finally:
            conn.close()
        return cursor.rowcount > 0

    def set_status(self, job_id: str, status: JobStatus, error: str | None = None) -> Job:
        current = self.get(job_id).status
        if status not in ALLOWED_TRANSITIONS[current]:
            raise InvalidTransitionError(f"Cannot move job from {current} to {status}")
        conn = self._connect()
        try:
            # "AND status = ?" guards against another process changing it since we read it.
            cursor = conn.execute(
                "UPDATE jobs SET status = ?, error = ?, updated_at = ? WHERE id = ? AND status = ?",
                (status, error if status == JobStatus.FAILED else None, _now(), job_id, current),
            )
        finally:
            conn.close()
        if cursor.rowcount == 0:
            raise InvalidTransitionError(f"Job {job_id} changed while updating; retry")
        return self.get(job_id)


def get_job_store() -> JobStore:
    """Postgres when POSTGRES_URL is set (shared by many containers), else a local SQLite file."""
    settings = get_settings()
    if settings.postgres_url:
        # Imported here because jobs_postgres itself imports from this module.
        from claimlens.services.jobs_postgres import PostgresJobStore

        return PostgresJobStore(settings.postgres_url)
    return SQLiteJobStore(settings.jobs_db)
