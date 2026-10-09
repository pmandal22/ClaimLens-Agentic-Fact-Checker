"""Submitting a URL: record the job, then put it on the queue. The API will call this."""

from claimlens.services.jobs import Job, JobStatus, JobStore
from claimlens.services.queue import JobQueue


def submit_check(url: str, store: JobStore, queue: JobQueue) -> Job:
    job = store.create(url)
    # create() returns an existing job when the same video was already submitted. Enqueue only
    # if it is still waiting. If an earlier enqueue failed, resubmitting the URL repairs it, and
    # a duplicate message is harmless because only one worker can claim the job.
    if job.status == JobStatus.QUEUED:
        queue.enqueue(job.id)
    return job
