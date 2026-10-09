import time
from contextlib import contextmanager
from pathlib import Path

import fakeredis
import pytest
from langgraph.checkpoint.memory import InMemorySaver

from claimlens.domain.schemas import Claim, Verdict
from claimlens.graph.checkpointer import make_serde
from claimlens.graph.main_graph import build_graph
from claimlens.ingest.download import caption_path
from claimlens.services.jobs import JobStatus, SQLiteJobStore
from claimlens.services.queue import RedisStreamQueue
from claimlens.services.storage import LocalStorage
from claimlens.services.submit import submit_check
from claimlens.services.worker import process_job, run_forever, run_once

URL = "https://example.com/reel"


class FakePipeline:
    """Stands in for ingest and the LLM steps; the graph, checkpoints and worker logic are real."""

    def __init__(self):
        self.claims: list[Claim] = []
        self.ingest_calls = 0
        self.extract_error: Exception | None = None
        self.captions: list[str] = []

    def read_video_text(self, video: Path, work_dir: Path, job_id: str = ""):
        self.ingest_calls += 1
        frame = work_dir / "frames" / "frame_0001.jpg"
        frame.parent.mkdir(parents=True, exist_ok=True)
        frame.write_bytes(b"jpg")
        return "spoken words", "on screen", [frame]

    def extract_claims(self, *args, **kwargs):
        self.captions.append(kwargs.get("caption", ""))
        if self.extract_error:
            raise self.extract_error
        return list(self.claims)


class FakeVerify:
    def invoke(self, state):
        claim = state["claim"]
        verdict = Verdict(
            claim_id=claim.id, label="supported", confidence=0.9, rationale="ok", citations=[]
        )
        return {"verdicts": [verdict], "evidence": []}


@pytest.fixture(autouse=True)
def pipeline(monkeypatch) -> FakePipeline:
    # Worker tests cover queue/claim/status logic; real ingest needs ffmpeg and Whisper.
    fake = FakePipeline()
    monkeypatch.setattr("claimlens.ingest.pipeline.read_video_text", fake.read_video_text)
    monkeypatch.setattr(
        "claimlens.graph.nodes.extract_claims.extract_claims", fake.extract_claims
    )
    monkeypatch.setattr("claimlens.graph.verify.subgraph.verify_claim", FakeVerify())
    saver = InMemorySaver(serde=make_serde())  # shared across jobs, like the real database

    @contextmanager
    def open_graph():
        yield build_graph(saver)

    monkeypatch.setattr("claimlens.services.worker.open_graph", open_graph)
    return fake


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


def test_downloaded_caption_reaches_claim_extraction_and_is_stored(
    store, storage, queue, pipeline
):
    def downloader_with_caption(url: str, destination: Path) -> Path:
        caption_path(destination).write_text("Water cures flu", encoding="utf-8")
        return fake_downloader(url, destination)

    job = submit_check(URL, store, queue)
    run(store, storage, queue, downloader_with_caption)

    assert pipeline.captions == ["Water cures flu"]
    assert storage.exists(job.video_key.removesuffix(".mp4") + ".caption.txt")


def test_already_stored_video_keeps_its_caption(store, storage, queue, pipeline, tmp_path: Path):
    job = submit_check(URL, store, queue)
    video = tmp_path / "existing.mp4"
    video.write_bytes(b"old")
    storage.put_file(video, job.video_key)
    caption_path(video).write_text("Stored caption", encoding="utf-8")
    storage.put_file(caption_path(video), job.video_key.removesuffix(".mp4") + ".caption.txt")

    run(store, storage, queue, failing_downloader)

    assert pipeline.captions == ["Stored caption"]


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


def test_flagged_verdicts_park_the_job_for_review(store, storage, queue, pipeline):
    pipeline.claims = [Claim(id="c1", text="Garlic cures flu.", source="speech", category="health")]
    job = submit_check(URL, store, queue)

    run(store, storage, queue)

    assert store.get(job.id).status == JobStatus.NEEDS_REVIEW
    assert storage.exists(f"verdicts/{job.id}.json")
    assert not storage.exists(f"reports/{job.id}.md")  # written once the review resumes the run


def test_finished_job_stores_every_artifact(store, storage, queue, pipeline):
    pipeline.claims = [Claim(id="c1", text="Paris is in France.", source="speech")]
    job = submit_check(URL, store, queue)

    run(store, storage, queue)

    assert store.get(job.id).status == JobStatus.DONE
    for key in ("transcripts/{}.txt", "ocr/{}.txt", "keyframes/{}/frame_0001.jpg",
                "claims/{}.json", "evidence/{}.json", "verdicts/{}.json", "reports/{}.md"):
        assert storage.exists(key.format(job.id)), key


def test_retried_job_continues_from_its_checkpoint(store, storage, queue, pipeline):
    pipeline.extract_error = RuntimeError("LLM rate limited")
    job = submit_check(URL, store, queue)
    run(store, storage, queue)
    assert store.get(job.id).status == JobStatus.FAILED

    pipeline.extract_error = None
    store.set_status(job.id, JobStatus.QUEUED)
    queue.enqueue(job.id)
    run(store, storage, queue)

    assert store.get(job.id).status == JobStatus.DONE
    assert pipeline.ingest_calls == 1  # the transcript came from the first attempt's checkpoint


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
