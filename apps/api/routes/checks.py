"""POST /checks, GET /checks/{id}, POST /checks/{id}/review."""

from collections import Counter
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from redis.exceptions import RedisError

from apps.api.deps import job_queue, job_store, require_reviewer, storage
from apps.api.dto import CheckRequest, CheckResponse, ReviewRequest
from claimlens.domain.schemas import Review
from claimlens.ingest.download import ensure_public_url
from claimlens.services.jobs import (
    InvalidTransitionError,
    Job,
    JobNotFoundError,
    JobStatus,
    JobStore,
)
from claimlens.services.queue import JobQueue
from claimlens.services.results import ClaimResult, load_results, save_review
from claimlens.services.storage import Storage
from claimlens.services.submit import submit_check

router = APIRouter(prefix="/checks", tags=["checks"])

Store = Annotated[JobStore, Depends(job_store)]
Queue = Annotated[JobQueue, Depends(job_queue)]
ResultStorage = Annotated[Storage, Depends(storage)]

# Statuses whose claims and verdicts are in storage.
HAS_RESULTS = {JobStatus.DONE, JobStatus.NEEDS_REVIEW}


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
    if job.status not in HAS_RESULTS:
        return CheckResponse.from_job(job)
    return CheckResponse.from_job(job, _load(storage, job.id))


@router.post(
    "/{job_id}/review",
    response_model=CheckResponse,
    dependencies=[Depends(require_reviewer)],
)
def review_check(
    job_id: str, body: ReviewRequest, store: Store, storage: ResultStorage
) -> CheckResponse:
    """Record a reviewer's labels for a paused check and publish it."""
    job = _get_job(store, job_id)
    if job.status != JobStatus.NEEDS_REVIEW:
        raise HTTPException(status.HTTP_409_CONFLICT, "This check is not waiting for review")

    results = _load(storage, job.id)
    known = {r.claim.id for r in results}
    flagged = {r.claim.id for r in results if r.review_reasons}
    decided = Counter(d.claim_id for d in body.decisions)
    problems = []
    if unknown := sorted(set(decided) - known):
        problems.append(f"unknown claim ids: {', '.join(unknown)}")
    if repeated := sorted(c for c, n in decided.items() if n > 1):
        problems.append(f"more than one decision for: {', '.join(repeated)}")
    if missing := sorted(flagged - set(decided)):
        problems.append(f"no decision for flagged claims: {', '.join(missing)}")
    if problems:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "; ".join(problems))

    # Two reviewers submitting at once both get here; set_status lets only one move the job
    # to DONE, but the other's file may already have replaced the winner's.
    save_review(
        storage,
        job.id,
        Review(decisions=body.decisions, reviewed_at=datetime.now(UTC).isoformat()),
    )
    try:
        job = store.set_status(job.id, JobStatus.DONE)
    except InvalidTransitionError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, "This check was reviewed already") from exc
    return CheckResponse.from_job(job, _load(storage, job.id))
