import time
from pathlib import Path

import fakeredis
import pytest

from claimlens.services.jobs import JobStatus, SQLiteJobStore
from claimlens.services.queue import RedisStreamQueue
from claimlens.services.storage import LocalStorage
from claimlens.services.submit import submit_check
from claimlens.services.worker import process_job, run_forever, run_once

URL = "https://example.com/reel"


@pytest.fixture(autouse=True)
def stub_pipeline(monkeypatch):
    # Worker tests cover queue/claim/status logic; the real pipeline needs ffmpeg and Whisper.
    monkeypatch.setattr(
        "claimlens.ingest.pipeline.process_video", lambda job_id, path, storage: {}
    )
    # The stub writes no verdicts, so there is nothing to review unless a test says so.
    monkeypatch.setattr("claimlens.services.results.needs_review", lambda storage, job_id: False)


@pytest.fixture
def store(tmp_path: Path) -> SQLiteJobStore:
    return SQLiteJobStore(tmp_path / "jobs.db")


@pytest.fixture
def storage(tmp_path: Path) -> LocalStorage:
    return LocalStorage(tmp_path / "store")


@pytest.fixture
def client():
    return fakeredis.FakeRedis(decode_responses=True)


@pytest.fixture
def queue(client) -> RedisStreamQueue:
    return RedisStreamQueue(client, consumer="worker-1", reclaim_after_ms=0)


def fake_downloader(url: str, destination: Path) -> Path:
    destination.write_bytes(b"video")
    return destination


def failing_downloader(url: str, destination: Path) -> Path:
    raise ValueError("video is private")


def run(store, storage, queue, downloader=fake_downloader) -> bool:
    return run_once(store, storage, queue, downloader, block_ms=1)


def test_run_once_returns_false_on_empty_queue(store, storage, queue):
    assert run(store, storage, queue) is False


def test_successful_job_is_stored_and_finishes_done(store, storage, queue):
    job = submit_check(URL, store, queue)

    assert run(store, storage, queue) is True

    assert store.get(job.id).status == JobStatus.DONE
    assert storage.exists(job.video_key)


def test_message_is_acknowledged_after_processing(store, storage, queue, client):
    submit_check(URL, store, queue)

    run(store, storage, queue)

    assert client.xlen("claimlens:jobs") == 0
    assert run(store, storage, queue) is False


def test_failed_download_marks_job_failed_and_still_acknowledges(store, storage, queue, client):
    job = submit_check(URL, store, queue)

    run(store, storage, queue, failing_downloader)

    updated = store.get(job.id)
    assert updated.status == JobStatus.FAILED
    assert updated.error == "video is private"
    assert client.xlen("claimlens:jobs") == 0


def test_already_stored_video_skips_download(store, storage, queue, tmp_path: Path):
    job = submit_check(URL, store, queue)
    existing = tmp_path / "existing.mp4"
    existing.write_bytes(b"old")
    storage.put_file(existing, job.video_key)

    run(store, storage, queue, failing_downloader)  # would fail if it were called

    assert store.get(job.id).status == JobStatus.DONE


def test_duplicate_message_is_dropped_without_reprocessing(store, storage, queue):
    job = submit_check(URL, store, queue)
    queue.enqueue(job.id)  # a second message for the same job

    run(store, storage, queue)
    run(store, storage, queue, failing_downloader)  # must not be called for the duplicate

    assert store.get(job.id).status == JobStatus.DONE


def test_message_for_unknown_job_is_dropped(store, storage, queue, client):
    queue.enqueue("does-not-exist")

    assert run(store, storage, queue) is True
    assert client.xlen("claimlens:jobs") == 0


def test_crashed_worker_job_is_recovered_by_another_worker(store, storage, queue, client):
    job = submit_check(URL, store, queue)
    # Worker 1 receives and claims the job, then "dies" before finishing or acknowledging.
    message = queue.receive(block_ms=1)
    assert message is not None
    store.claim(message.job_id)
    assert store.get(job.id).status == JobStatus.DOWNLOADING

    worker_2 = RedisStreamQueue(client, consumer="worker-2", reclaim_after_ms=0)
    run(store, storage, worker_2)

    assert store.get(job.id).status == JobStatus.DONE
    assert storage.exists(job.video_key)


def test_resubmitting_a_queued_job_enqueues_again(store, queue, client):
    submit_check(URL, store, queue)
    submit_check(URL, store, queue)

    assert client.xlen("claimlens:jobs") == 2


def test_resubmitting_a_running_job_does_not_enqueue(store, queue, client):
    job = submit_check(URL, store, queue)
    store.claim(job.id)

    submit_check(URL, store, queue)

    assert client.xlen("claimlens:jobs") == 1


def test_long_error_messages_are_truncated(store, storage, queue):
    job = submit_check(URL, store, queue)
    claimed = store.claim(job.id)
    assert claimed is not None

    def noisy(url: str, destination: Path) -> Path:
        raise ValueError("x" * 5000)

    process_job(claimed, store, storage, noisy)

    assert len(store.get(job.id).error or "") == 500


def test_run_forever_processes_jobs_then_stops(store, storage, queue):
    job = submit_check(URL, store, queue)
    checks = {"n": 0}

    def stop_after_three_checks() -> bool:
        checks["n"] += 1
        return checks["n"] > 3

    run_forever(
        store,
        storage,
        queue,
        block_ms=1,
        should_stop=stop_after_three_checks,
        downloader=fake_downloader,
    )

    assert store.get(job.id).status == JobStatus.DONE
    assert checks["n"] == 4


def test_run_forever_survives_errors_and_keeps_going(store, storage):
    class FlakyQueue:
        def __init__(self):
            self.calls = 0

        def receive(self, block_ms):
            self.calls += 1
            if self.calls == 1:
                raise ConnectionError("redis is down")

        def enqueue(self, job_id): ...

        def ack(self, message): ...

    flaky = FlakyQueue()
    checks = {"n": 0}

    def stop_after_two_checks() -> bool:
        checks["n"] += 1
        return checks["n"] > 2

    run_forever(
        store,
        storage,
        flaky,
        block_ms=1,
        should_stop=stop_after_two_checks,
        error_backoff_s=0,
    )

    assert flaky.calls == 2


def test_flagged_verdicts_park_the_job_for_review(store, storage, queue, monkeypatch):
    monkeypatch.setattr("claimlens.services.results.needs_review", lambda storage, job_id: True)
    job = submit_check(URL, store, queue)

    run(store, storage, queue)

    assert store.get(job.id).status == JobStatus.NEEDS_REVIEW


def test_redelivered_job_with_active_lease_is_left_pending(store, storage, queue, client):
    job = submit_check(URL, store, queue)
    assert queue.receive(block_ms=1) is not None
    store.claim(job.id, worker_id="worker-1", lease_seconds=900)  # still heartbeating

    worker_2 = RedisStreamQueue(client, consumer="worker-2", reclaim_after_ms=0)
    run_once(store, storage, worker_2, failing_downloader, block_ms=1, worker_id="worker-2")

    assert store.get(job.id).status == JobStatus.DOWNLOADING
    assert store.get(job.id).claimed_by == "worker-1"
    assert client.xlen("claimlens:jobs") == 1  # not acked: retried if worker-1 dies after all


def test_redelivered_job_with_expired_lease_is_taken_over(store, storage, queue, client):
    job = submit_check(URL, store, queue)
    assert queue.receive(block_ms=1) is not None
    store.claim(job.id, worker_id="worker-1", lease_seconds=-1)  # stopped heartbeating

    worker_2 = RedisStreamQueue(client, consumer="worker-2", reclaim_after_ms=0)
    run_once(store, storage, worker_2, fake_downloader, block_ms=1, worker_id="worker-2")

    assert store.get(job.id).status == JobStatus.DONE
    assert client.xlen("claimlens:jobs") == 0


def test_worker_that_lost_its_job_does_not_ack_it(store, storage, client):
    slow = RedisStreamQueue(client, consumer="worker-1", reclaim_after_ms=0)
    other = RedisStreamQueue(client, consumer="worker-2", reclaim_after_ms=0)
    submit_check(URL, store, slow)

    def downloader_that_gets_overtaken(url: str, destination: Path) -> Path:
        assert other.receive(block_ms=1) is not None  # worker-2 reclaims the message
        time.sleep(1.3)  # long enough for one heartbeat (lease 2s -> every 1s)
        return fake_downloader(url, destination)

    run_once(store, storage, slow, downloader_that_gets_overtaken, block_ms=1, lease_seconds=2)

    assert client.xlen("claimlens:jobs") == 1  # worker-2's message survives
    pending = client.xpending_range("claimlens:jobs", "workers", min="-", max="+", count=10)
    assert [p["consumer"] for p in pending] == ["worker-2"]


def test_job_abandoned_while_ingesting_is_restarted_and_finishes(store, storage, queue, client):
    job = submit_check(URL, store, queue)
    assert queue.receive(block_ms=1) is not None
    # Worker 1 downloaded the video and started ingesting, then died.
    store.claim(job.id, worker_id="worker-1", lease_seconds=-1)
    store.set_status(job.id, JobStatus.INGESTING)

    worker_2 = RedisStreamQueue(client, consumer="worker-2", reclaim_after_ms=0)
    run_once(store, storage, worker_2, fake_downloader, block_ms=1, worker_id="worker-2")

    assert store.get(job.id).status == JobStatus.DONE
    assert client.xlen("claimlens:jobs") == 0
