import os
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import psycopg
import pytest

from claimlens.services.jobs import (
    InvalidTransitionError,
    JobNotFoundError,
    JobStatus,
    JobStore,
    SQLiteJobStore,
)
from claimlens.services.jobs_postgres import PostgresJobStore

URL = "https://www.instagram.com/reel/abc/"


TEST_POSTGRES_URL = os.environ.get(
    "TEST_POSTGRES_URL", "postgresql://claimlens:claimlens@localhost:5432/claimlens_test"
)


def _postgres_store() -> JobStore:
    try:
        store = PostgresJobStore(TEST_POSTGRES_URL)
        with psycopg.connect(TEST_POSTGRES_URL) as conn:
            conn.execute("TRUNCATE jobs")
    except psycopg.OperationalError:
        pytest.skip("Postgres test database not reachable (docker compose up -d postgres)")
    return store


# Every test below runs against both implementations, so they must behave identically.
@pytest.fixture(params=["sqlite", "postgres"])
def store(request, tmp_path: Path) -> JobStore:
    if request.param == "sqlite":
        return SQLiteJobStore(tmp_path / "jobs.db")
    return _postgres_store()


def test_create_starts_queued(store: JobStore):
    job = store.create(URL)
    assert job.status == JobStatus.QUEUED
    assert store.get(job.id) == job


def test_create_same_video_returns_existing_job(store: JobStore):
    first = store.create(URL)
    again = store.create(URL + "#share")
    assert again.id == first.id


def test_failed_job_does_not_block_resubmission(store: JobStore):
    first = store.create(URL)
    store.claim(first.id)
    store.set_status(first.id, JobStatus.FAILED, error="boom")
    assert store.create(URL).id != first.id


def test_get_unknown_job_raises(store: JobStore):
    with pytest.raises(JobNotFoundError):
        store.get("nope")


def test_claim_moves_queued_job_to_downloading_once(store: JobStore):
    job = store.create(URL)

    claimed = store.claim(job.id)
    second_attempt = store.claim(job.id)

    assert claimed and claimed.status == JobStatus.DOWNLOADING
    assert second_attempt is None


def test_claim_unknown_job_returns_none(store: JobStore):
    assert store.claim("nope") is None


def test_claim_resume_accepts_a_job_stuck_in_downloading(store: JobStore):
    job = store.create(URL)
    store.claim(job.id)

    assert store.claim(job.id) is None
    resumed = store.claim(job.id, resume=True)

    assert resumed and resumed.status == JobStatus.DOWNLOADING


@pytest.mark.parametrize("stuck_in", [JobStatus.INGESTING, JobStatus.VERIFYING])
def test_claim_resume_restarts_a_job_stuck_after_download(store: JobStore, stuck_in):
    job = store.create(URL)
    store.claim(job.id, worker_id="dead-worker", lease_seconds=-1)
    store.set_status(job.id, JobStatus.INGESTING)
    if stuck_in == JobStatus.VERIFYING:
        store.set_status(job.id, JobStatus.VERIFYING)

    resumed = store.claim(job.id, resume=True, worker_id="worker-2")

    assert resumed and resumed.status == JobStatus.DOWNLOADING
    assert resumed.claimed_by == "worker-2"


def test_claim_resume_waits_for_an_active_lease_after_download(store: JobStore):
    job = store.create(URL)
    store.claim(job.id, worker_id="slow-worker", lease_seconds=900)
    store.set_status(job.id, JobStatus.INGESTING)

    assert store.claim(job.id, resume=True, worker_id="worker-2") is None
    assert store.get(job.id).status == JobStatus.INGESTING


@pytest.mark.parametrize("final", [JobStatus.DONE, JobStatus.FAILED])
def test_claim_resume_does_not_touch_finished_jobs(store: JobStore, final):
    job = store.create(URL)
    store.claim(job.id, worker_id="worker-1", lease_seconds=-1)
    if final == JobStatus.FAILED:
        store.set_status(job.id, JobStatus.FAILED, error="boom")
    else:
        for status in (JobStatus.INGESTING, JobStatus.VERIFYING, final):
            store.set_status(job.id, status)

    assert store.claim(job.id, resume=True, worker_id="worker-2") is None
    assert store.get(job.id).status == final


def test_jobs_left_waiting_for_review_become_done(store: JobStore):
    job = store.create(URL)
    if isinstance(store, SQLiteJobStore):
        with sqlite3.connect(store.db_path) as conn:
            conn.execute("UPDATE jobs SET status = 'needs_review' WHERE id = ?", (job.id,))
        reopened: JobStore = SQLiteJobStore(store.db_path)
    else:
        with psycopg.connect(TEST_POSTGRES_URL) as conn:
            conn.execute("UPDATE jobs SET status = 'needs_review' WHERE id = %s", (job.id,))
        reopened = PostgresJobStore(TEST_POSTGRES_URL)

    assert reopened.get(job.id).status == JobStatus.DONE


def test_full_happy_path(store: JobStore):
    job = store.create(URL)
    store.claim(job.id)
    for status in (JobStatus.INGESTING, JobStatus.VERIFYING, JobStatus.DONE):
        job = store.set_status(job.id, status)
    assert job.status == JobStatus.DONE


def test_invalid_transition_is_rejected(store: JobStore):
    job = store.create(URL)
    with pytest.raises(InvalidTransitionError):
        store.set_status(job.id, JobStatus.DONE)


def test_error_is_stored_only_for_failed(store: JobStore):
    job = store.create(URL)
    store.claim(job.id)
    failed = store.set_status(job.id, JobStatus.FAILED, error="video is private")
    assert failed.error == "video is private"
    requeued = store.set_status(job.id, JobStatus.QUEUED)
    assert requeued.error is None


def test_concurrent_creates_of_same_video_make_one_job(store: JobStore):
    with ThreadPoolExecutor(max_workers=8) as pool:
        jobs = list(pool.map(lambda _: store.create(URL), range(16)))

    assert len({job.id for job in jobs}) == 1


def test_concurrent_claims_have_exactly_one_winner(store: JobStore):
    job = store.create(URL)

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: store.claim(job.id), range(16)))

    assert sum(r is not None for r in results) == 1


def test_claim_records_the_worker_and_its_lease(store: JobStore):
    job = store.create(URL)

    claimed = store.claim(job.id, worker_id="worker-1", lease_seconds=900)

    assert claimed and claimed.claimed_by == "worker-1" and claimed.claim_expires_at


def test_claim_resume_waits_for_an_active_lease(store: JobStore):
    job = store.create(URL)
    store.claim(job.id, worker_id="slow-worker", lease_seconds=900)

    assert store.claim(job.id, resume=True, worker_id="worker-2") is None
    assert store.get(job.id).claimed_by == "slow-worker"


def test_claim_resume_takes_over_an_expired_lease(store: JobStore):
    job = store.create(URL)
    store.claim(job.id, worker_id="dead-worker", lease_seconds=-1)

    resumed = store.claim(job.id, resume=True, worker_id="worker-2")

    assert resumed and resumed.claimed_by == "worker-2"
