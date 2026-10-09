"""Worker logic: take a queued job, fetch its video into storage, advance its status."""

import logging
import tempfile
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from claimlens.ingest.download import download_video
from claimlens.services import results
from claimlens.services.jobs import IN_PROGRESS, Job, JobNotFoundError, JobStatus, JobStore
from claimlens.services.queue import JobQueue, Message
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
    store: JobStore,
    queue: JobQueue,
    message: Message,
    worker_id: str | None,
    lease_seconds: int,
    done: threading.Event,
    lost: threading.Event,
) -> None:
    """Every lease_seconds/2 until `done`, tell Redis and the DB this job is still being worked on.

    Redis only sees the message's idle time, so without this a job slower than the reclaim
    window would be handed to a second worker while the first is still running it.
    Sets `lost` and stops if another worker has taken the job over.
    """
    interval = max(lease_seconds / 2, 1)
    while not done.wait(interval):
        try:
            still_ours = queue.touch(message)
            if worker_id:
                still_ours = store.heartbeat(message.job_id, worker_id, lease_seconds) and still_ours
        except Exception:
            # Redis or the DB is briefly unreachable; try again next interval.
            logger.exception("job=%s heartbeat failed", message.job_id)
            continue
        if not still_ours:
            logger.warning("job=%s was taken over by another worker", message.job_id)
            lost.set()
            return


def _lease_is_active(job: Job) -> bool:
    if job.status not in IN_PROGRESS or job.claim_expires_at is None:
        return False
    return datetime.fromisoformat(job.claim_expires_at) > datetime.now(UTC)


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
        previous = store.get(message.job_id)
    except JobNotFoundError:
        logger.warning("dropping message for unknown job=%s", message.job_id)
        queue.ack(message)
        return True

    if message.redelivered:
        logger.warning(
            "job=%s redelivered; previous worker=%s status=%s lease_expires=%s",
            previous.id, previous.claimed_by, previous.status, previous.claim_expires_at,
        )

    # A redelivered message means a previous worker died mid-job, so allow re-claiming it
    # once that worker's lease has run out. The job restarts from the download step.
    job = store.claim(
        message.job_id,
        resume=message.redelivered,
        worker_id=worker_id,
        lease_seconds=lease_seconds,
    )
    if job is None:
        if message.redelivered and _lease_is_active(store.get(message.job_id)):
            # The previous worker is still heartbeating the DB. Leave the message pending so
            # it is retried after another reclaim window, in case that worker dies after all.
            logger.info("job=%s still leased by another worker, leaving it pending", message.job_id)
            return True
        # Already claimed or finished: this is a duplicate message.
        logger.info("job=%s not claimable, dropping duplicate message", message.job_id)
        queue.ack(message)
        return True

    done = threading.Event()
    lost = threading.Event()
    heartbeat = threading.Thread(
        target=_heartbeat_loop,
        args=(store, queue, message, worker_id, lease_seconds, done, lost),
        daemon=True,
    )
    heartbeat.start()
    try:
        process_job(job, store, storage, downloader)
    finally:
        done.set()
        heartbeat.join(timeout=5)
    if lost.is_set():
        # Another worker owns the message now; acking would delete it out from under them.
        logger.warning("job=%s finished after takeover; not acknowledging", job.id)
        return True
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
