"""Request/response models (kept separate from domain schemas)."""

from pydantic import BaseModel, Field, HttpUrl

from claimlens.domain.schemas import ReviewDecision
from claimlens.graph.nodes.aggregate import OverallVerdict, aggregate
from claimlens.services.jobs import Job, JobStatus
from claimlens.services.results import ClaimResult


class CheckRequest(BaseModel):
    # HttpUrl rejects anything that isn't a well-formed http(s) URL before our code runs.
    url: HttpUrl


class ReviewRequest(BaseModel):
    # One decision per flagged claim; unflagged claims may be relabelled too.
    decisions: list[ReviewDecision] = Field(max_length=100)


class CheckResponse(BaseModel):
    id: str
    url: str
    status: JobStatus
    error: str | None
    created_at: str
    updated_at: str
    results: list[ClaimResult] | None = None  # set once the job is done or needs review
    overall: OverallVerdict | None = None

    @classmethod
    def from_job(cls, job: Job, results: list[ClaimResult] | None = None) -> "CheckResponse":
        return cls(
            id=job.id,
            url=job.url,
            status=job.status,
            error=job.error,
            created_at=job.created_at,
            updated_at=job.updated_at,
            results=results,
            overall=aggregate([r.verdict for r in results]) if results is not None else None,
        )
