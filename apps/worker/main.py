"""Worker entry point: python -m apps.worker.main"""

import logging
import os
import signal
import threading
import uuid

from claimlens.config.settings import get_settings
from claimlens.services.jobs import get_job_store
from claimlens.services.queue import get_queue
from claimlens.services.storage import get_storage
from claimlens.services.worker import run_forever

logger = logging.getLogger(__name__)

# Redis consumers idle this long with nothing pending belong to workers that are gone.
STALE_CONSUMER_MS = 24 * 60 * 60 * 1000


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    stop = threading.Event()
    # On Ctrl+C or a container stop, finish the current job, then exit cleanly.
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())

    # Worker identifier (use env WORKER_ID to override for reproducible names in containers)
    worker_id = os.environ.get("WORKER_ID") or uuid.uuid4().hex

    # The same id names this worker in Redis and in the jobs table, so the two can be matched.
    queue = get_queue(consumer=worker_id)
    try:
        removed = queue.prune_consumers(STALE_CONSUMER_MS)
        if removed:
            logger.info("removed stale Redis consumers: %s", ", ".join(removed))
    except Exception:
        logger.exception("could not prune stale Redis consumers; continuing")

    logger.info("worker started id=%s", worker_id)
    # One setting drives both timers. The DB lease must run out before Redis hands the job to
    # another worker; otherwise that worker finds the lease still active and has to wait a
    # whole extra reclaim window.
    reclaim_s = get_settings().queue_reclaim_after_s
    lease_seconds = max(reclaim_s - 60, reclaim_s // 2)
    run_forever(
        get_job_store(),
        get_storage(),
        queue,
        should_stop=stop.is_set,
        worker_id=worker_id,
        lease_seconds=lease_seconds,
    )
    logger.info("worker stopped")


if __name__ == "__main__":
    main()
