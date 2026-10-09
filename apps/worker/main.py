"""Worker entry point: python -m apps.worker.main"""

import logging
import os
import signal
import threading
import uuid

from claimlens.services.jobs import get_job_store
from claimlens.services.queue import get_queue
from claimlens.services.storage import get_storage
from claimlens.services.worker import run_forever

logger = logging.getLogger(__name__)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    stop = threading.Event()
    # On Ctrl+C or a container stop, finish the current job, then exit cleanly.
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())

    # Worker identifier (use env WORKER_ID to override for reproducible names in containers)
    worker_id = os.environ.get("WORKER_ID") or uuid.uuid4().hex

    logger.info("worker started id=%s", worker_id)
    run_forever(get_job_store(), get_storage(), get_queue(), should_stop=stop.is_set, worker_id=worker_id)
    logger.info("worker stopped")


if __name__ == "__main__":
    main()
