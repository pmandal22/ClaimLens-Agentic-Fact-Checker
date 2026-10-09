"""Worker logic: take a queued job, fetch its video into storage, advance its status."""

import logging
import tempfile
import threading
import time
from collections.abc import Callable
from pathlib import Path

from claimlens.ingest.download import download_video
from claimlens.services import results
from claimlens.services.jobs import Job, JobNotFoundError, JobStatus, JobStore
from claimlens.services.queue import JobQueue
from claimlens.services.storage import Storage

logger = logging.getLogger(__name__)

MAX_ERROR_LENGTH = 500

Downloader = Callable[[str, Path], Path]


def process_job(
    job: Job,
    store: JobStore,
    storage: Storage,
    downloader: Downloader = download_video,
) -> Job:
    """Run one claimed job (status DOWNLOADING) as far as the pipeline is built.

    On success the job ends DONE, or NEEDS_REVIEW if a verdict must be checked by a person;
    claims, evidence and verdicts are in storage under
    claims/, evidence/ and verdicts/ keyed by job id.
    Any failure marks the job FAILED with a short message instead of crashing the worker.
    """
    try:
        # The local copy must outlive the pipeline step, so one temp dir covers both.
        with tempfile.TemporaryDirectory(prefix="claimlens-") as work_dir:
            local_path = Path(work_dir) / "video.mp4"
            if storage.exists(job.video_key):
                # Same video was fetched before; skip the slow, failure-prone download.
                logger.info("job=%s video already stored, skipping download", job.id)
                storage.get_file(job.video_key, local_path)
            else:
                local_path = downloader(job.url, local_path)
                storage.put_file(local_path, job.video_key)
                logger.info("job=%s video stored as %s", job.id, job.video_key)

            store.set_status(job.id, JobStatus.INGESTING)

            # Run the pipeline: audio extraction, ASR, keyframes, and upload artifacts.
            try:
                from claimlens.ingest.pipeline import process_video

                artifacts = process_video(job.id, local_path, storage)
                logger.info("job=%s pipeline produced: %s", job.id, artifacts)
                # Verification runs inside process_video, so VERIFYING is a brief marker here.
                store.set_status(job.id, JobStatus.VERIFYING)
                # Uncertain or sensitive verdicts wait for a person (POST /checks/{id}/review).
                if results.needs_review(storage, job.id):
                    return store.set_status(job.id, JobStatus.NEEDS_REVIEW)
                return store.set_status(job.id, JobStatus.DONE)
            except Exception as err:
                logger.exception("job=%s pipeline failed", job.id)
                return store.set_status(
                    job.id, JobStatus.FAILED, error=str(err)[:MAX_ERROR_LENGTH]
                )
    except Exception as error:
        logger.exception("job=%s failed", job.id)
        return store.set_status(job.id, JobStatus.FAILED, error=str(error)[:MAX_ERROR_LENGTH])


def _heartbeat_loop(
    store: JobStore, job_id: str, worker_id: str, lease_seconds: int, done: threading.Event
) -> None:
    """Extend the job's claim lease about every lease_seconds/2 until `done` is set."""
    interval = max(lease_seconds / 2, 1)
    while not done.wait(interval):
        try:
            if not store.heartbeat(job_id, worker_id, lease_seconds):
                return  # we no longer own the job
        except Exception:
            logger.exception("job=%s heartbeat failed", job_id)


def run_once(
    store: JobStore,
    storage: Storage,
    queue: JobQueue,
    downloader: Downloader = download_video,
    block_ms: int = 2000,
    worker_id: str | None = None,
    lease_seconds: int = 900,
) -> bool:
    """Handle at most one queue message. Returns False if the queue was empty."""
    message = queue.receive(block_ms)
    if message is None:
        return False

    try:
        store.get(message.job_id)
    except JobNotFoundError:
        logger.warning("dropping message for unknown job=%s", message.job_id)
        queue.ack(message)
        return True

    # A redelivered message means a previous worker died mid-job, so allow re-claiming it.
    job = store.claim(
        message.job_id,
        resume=message.redelivered,
        worker_id=worker_id,
        lease_seconds=lease_seconds,
    )
    if job is None:
        # Already claimed or finished: this is a duplicate message.
        logger.info("job=%s not claimable, dropping duplicate message", message.job_id)
        queue.ack(message)
        return True

    done = threading.Event()
    heartbeat = None
    if worker_id:
        heartbeat = threading.Thread(
            target=_heartbeat_loop,
            args=(store, job.id, worker_id, lease_seconds, done),
            daemon=True,
        )
        heartbeat.start()
    try:
        process_job(job, store, storage, downloader)
    finally:
        done.set()
        if heartbeat:
            heartbeat.join(timeout=5)
    # Ack only after processing; if we crash before this, another worker gets the message.
    queue.ack(message)
    return True


def run_forever(
    store: JobStore,
    storage: Storage,
    queue: JobQueue,
    block_ms: int = 2000,
    should_stop: Callable[[], bool] = lambda: False,
    downloader: Downloader = download_video,
    error_backoff_s: float = 5.0,
    worker_id: str | None = None,
    lease_seconds: int = 900,
) -> None:
    """Process jobs until should_stop() is true.

    Waiting for work happens inside queue.receive(), which blocks for up to block_ms, so
    the loop doesn't need its own sleep and still notices should_stop() every block_ms.
    """
    while not should_stop():
        try:
            run_once(store, storage, queue, downloader, block_ms, worker_id=worker_id, lease_seconds=lease_seconds)
        except Exception:
            # Redis or the database is unreachable, say. Keep the worker alive and retry.
            logger.exception("worker loop error; retrying in %ss", error_backoff_s)
            time.sleep(error_backoff_s)
