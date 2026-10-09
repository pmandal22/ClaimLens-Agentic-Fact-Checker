"""Pause low-confidence runs with interrupt() for human review."""

from collections.abc import Sequence

from langgraph.types import interrupt

from claimlens.config.settings import get_settings
from claimlens.domain.schemas import Claim, Review, Verdict
from claimlens.graph.nodes.aggregate import aggregate
from claimlens.graph.state import ReelState
from claimlens.safety.guards import review_reasons


def verdicts_by_claim(verdicts: Sequence[Verdict]) -> dict[str, Verdict]:
    # Parallel branches append in completion order, so look verdicts up by claim id.
    return {v.claim_id: v for v in verdicts}


def flagged_claims(claims: Sequence[Claim], verdicts: Sequence[Verdict]) -> dict[str, list[str]]:
    """Claim id -> why a person must decide on it; claims that need no review are left out."""
    floor = get_settings().min_confidence_score
    by_id = verdicts_by_claim(verdicts)
    reasons = {c.id: review_reasons(c, by_id.get(c.id), floor) for c in claims}
    return {claim_id: r for claim_id, r in reasons.items() if r}


def apply_review(
    claims: Sequence[Claim], verdicts: Sequence[Verdict], review: Review | None
) -> list[Verdict | None]:
    """Final verdict per claim, in claim order: the reviewer's label wins over the model's."""
    by_id = verdicts_by_claim(verdicts)
    decisions = {d.claim_id: d for d in review.decisions} if review else {}
    final: list[Verdict | None] = []
    for claim in claims:
        verdict = by_id.get(claim.id)
        if decision := decisions.get(claim.id):
            verdict = decision.apply_to(verdict)
        final.append(verdict)
    return final


def human_review(state: ReelState) -> dict:
    """LangGraph node: pause until a reviewer labels every flagged claim.

    Resume with Command(resume=Review(...)) or its dict form, as POST /checks/{id}/review takes.
    """
    claims = state.get("claims", [])
    verdicts = state.get("verdicts", [])
    flagged = flagged_claims(claims, verdicts)
    by_id = verdicts_by_claim(verdicts)
    payload = {
        "review": [
            {
                "claim": claim.model_dump(),
                "verdict": by_id[claim.id].model_dump() if claim.id in by_id else None,
                "reasons": flagged[claim.id],
            }
            for claim in claims
            if claim.id in flagged
        ]
    }
    review = Review.model_validate(interrupt(payload))

    missing = set(flagged) - {d.claim_id for d in review.decisions}
    if missing:
        raise ValueError(f"Review is missing decisions for claims: {sorted(missing)}")
    # The overall rating follows the reviewed labels, not the model's.
    return {"review": review, "overall": aggregate(apply_review(claims, verdicts, review))}
