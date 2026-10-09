"""Injected services. Tests replace these with fakes via app.dependency_overrides."""

from functools import lru_cache

from claimlens.services.jobs import JobStore, get_job_store
from claimlens.services.queue import JobQueue, get_queue
from claimlens.services.storage import Storage, get_storage


@lru_cache
def job_store() -> JobStore:
    return get_job_store()


@lru_cache
def job_queue() -> JobQueue:
    return get_queue()


@lru_cache
def storage() -> Storage:
    return get_storage()

