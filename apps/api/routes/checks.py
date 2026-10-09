"""POST /checks, GET /checks/{id}."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from redis.exceptions import RedisError

from apps.api.deps import job_queue, job_store, storage
from apps.api.dto import CheckRequest, CheckResponse
from claimlens.ingest.download import ensure_public_url
from claimlens.services.jobs import Job, JobNotFoundError, JobStatus, JobStore
from claimlens.services.queue import JobQueue
from claimlens.services.results import ClaimResult, load_results
from claimlens.services.storage import Storage
from claimlens.services.submit import submit_check

router = APIRouter(prefix="/checks", tags=["checks"])

Store = Annotated[JobStore, Depends(job_store)]
Queue = Annotated[JobQueue, Depends(job_queue)]
ResultStorage = Annotated[Storage, Depends(storage)]


def _get_job(store: JobStore, job_id: str) -> Job:
    try:
        return store.get(job_id)
    except JobNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Check not found") from exc


def _load(storage: Storage, job_id: str) -> list[ClaimResult]:
    try:
        return load_results(storage, job_id)
    except FileNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Results not found") from exc


# Plain `def` (not `async def`): FastAPI runs it in a thread pool, so the blocking
# DNS, database and Redis calls inside don't freeze the server.
@router.post("", status_code=status.HTTP_202_ACCEPTED, response_model=CheckResponse)
def create_check(body: CheckRequest, store: Store, queue: Queue) -> CheckResponse:
    url = str(body.url)
    try:
        ensure_public_url(url)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc

    try:
        job = submit_check(url, store, queue)
    except RedisError as exc:
        # The job row exists; submitting the same URL again will enqueue it.
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "Queue unavailable, please retry"
        ) from exc
    return CheckResponse.from_job(job)


@router.get("/{job_id}", response_model=CheckResponse)
def get_check(job_id: str, store: Store, storage: ResultStorage) -> CheckResponse:
    job = _get_job(store, job_id)
    if job.status != JobStatus.DONE:
        return CheckResponse.from_job(job)
    return CheckResponse.from_job(job, _load(storage, job.id))

