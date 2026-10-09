"""Load a finished job's claims, evidence, verdicts and human review from storage."""

import json
import tempfile
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from claimlens.config.settings import get_settings
from claimlens.domain.schemas import Claim, Evidence, Label, Review, Verdict
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


def save_review(storage: Storage, job_id: str, review: Review) -> None:
    with tempfile.TemporaryDirectory(prefix="claimlens-review-") as tmp:
        path = Path(tmp) / "review.json"
        path.write_text(review.model_dump_json(indent=2), encoding="utf-8")
        storage.put_file(path, review_key(job_id))


def _apply_review(result: ClaimResult, label: Label, note: str) -> None:
    verdict = result.verdict
    result.review = HumanReview(model_label=verdict.label if verdict else None, note=note)
    if verdict:
        result.verdict = verdict.model_copy(update={"label": label})
    else:
        result.verdict = Verdict(
            claim_id=result.claim.id,
            label=label,
            confidence=1.0,
            rationale=note or "Labelled by a human reviewer.",
            citations=[],
        )


def load_results(
    storage: Storage, job_id: str, confidence_floor: float | None = None
) -> list[ClaimResult]:
    """Join the pipeline's claims, evidence and verdicts artifacts by claim id.

    If a reviewer has decided on the job, their labels replace the model's.
    Raises FileNotFoundError if a pipeline artifact is missing.
    """
    if confidence_floor is None:
        confidence_floor = get_settings().confidence_floor
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
            review_reasons=review_reasons(claim, verdict, confidence_floor),
        )
        if decision := decisions.get(claim.id):
            _apply_review(result, decision.label, decision.note)
        results.append(result)
    return results


def needs_review(storage: Storage, job_id: str) -> bool:
    return any(result.review_reasons for result in load_results(storage, job_id))
