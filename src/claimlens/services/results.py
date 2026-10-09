"""Save and load a job's claims, evidence, verdicts, report and human review in storage."""

import json
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from claimlens.config.settings import get_settings
from claimlens.domain.schemas import (
    Claim,
    Claims,
    Evidence,
    Label,
    Review,
    ReviewDecision,
    Verdict,
)
from claimlens.safety.guards import review_reasons
from claimlens.services.storage import Storage


class HumanReview(BaseModel):
    """What a reviewer decided; the verdict's label is already replaced by theirs."""

    model_label: Label | None  # the model's label before review; None if it gave no verdict
    note: str


class ClaimResult(BaseModel):
    claim: Claim
    evidence: list[Evidence]
    verdict: Verdict | None
    review_reasons: list[str] = []  # non-empty: a person must decide before publishing
    review: HumanReview | None = None


def review_key(job_id: str) -> str:
    return f"reviews/{job_id}.json"


def _read_json(storage: Storage, key: str) -> Any:
    with tempfile.TemporaryDirectory(prefix="claimlens-results-") as tmp:
        path = storage.get_file(key, Path(tmp) / "artifact.json")
        return json.loads(path.read_text(encoding="utf-8"))


def report_key(job_id: str) -> str:
    return f"reports/{job_id}.md"


def put_text(storage: Storage, key: str, text: str) -> None:
    with tempfile.TemporaryDirectory(prefix="claimlens-artifact-") as tmp:
        path = Path(tmp) / Path(key).name
        path.write_text(text, encoding="utf-8")
        storage.put_file(path, key)


def save_review(storage: Storage, job_id: str, review: Review) -> None:
    put_text(storage, review_key(job_id), review.model_dump_json(indent=2))


def save_run(storage: Storage, job_id: str, state: Mapping[str, Any]) -> None:
    """Write a graph run's results under the keys load_results reads; idempotent.

    The report is written only once the run has one (not while it waits for review).
    """
    claims = Claims(claims=state.get("claims", []))
    evidence = {
        claim_id: [e.model_dump() for e in items]
        for claim_id, items in state.get("evidence", {}).items()
    }
    put_text(storage, f"claims/{job_id}.json", claims.model_dump_json(indent=2))
    put_text(storage, f"evidence/{job_id}.json", json.dumps(evidence, indent=2))
    put_text(
        storage,
        f"verdicts/{job_id}.json",
        json.dumps([v.model_dump() for v in state.get("verdicts", [])], indent=2),
    )
    if report := state.get("report"):
        put_text(storage, report_key(job_id), report)


def _apply_review(result: ClaimResult, decision: ReviewDecision) -> None:
    verdict = result.verdict
    result.review = HumanReview(model_label=verdict.label if verdict else None, note=decision.note)
    result.verdict = decision.apply_to(verdict)


def load_results(
    storage: Storage, job_id: str, min_confidence_score: float | None = None
) -> list[ClaimResult]:
    """Join the pipeline's claims, evidence and verdicts artifacts by claim id.

    If a reviewer has decided on the job, their labels replace the model's.
    Raises FileNotFoundError if a pipeline artifact is missing.
    """
    if min_confidence_score is None:
        min_confidence_score = get_settings().min_confidence_score
    claims = _read_json(storage, f"claims/{job_id}.json")["claims"]
    evidence = _read_json(storage, f"evidence/{job_id}.json")
    verdicts = {
        item["claim_id"]: Verdict.model_validate(item)
        for item in _read_json(storage, f"verdicts/{job_id}.json")
    }
    decisions = {}
    if storage.exists(review_key(job_id)):
        review = Review.model_validate(_read_json(storage, review_key(job_id)))
        decisions = {d.claim_id: d for d in review.decisions}

    results = []
    for raw in claims:
        claim = Claim.model_validate(raw)
        verdict = verdicts.get(claim.id)
        result = ClaimResult(
            claim=claim,
            evidence=[Evidence.model_validate(e) for e in evidence.get(claim.id, [])],
            verdict=verdict,
            review_reasons=review_reasons(claim, verdict, min_confidence_score),
        )
        if decision := decisions.get(claim.id):
            _apply_review(result, decision)
        results.append(result)
    return results
