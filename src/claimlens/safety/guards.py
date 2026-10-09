"""Sensitive-topic routing: which verdicts a person must see before they are published."""

from functools import lru_cache
from pathlib import Path

import yaml  # type: ignore[import-untyped]

from claimlens.domain.schemas import Claim, Verdict

POLICIES = Path(__file__).with_name("policies.yaml")


@lru_cache
def always_review_categories() -> frozenset[str]:
    policies = yaml.safe_load(POLICIES.read_text(encoding="utf-8")) or {}
    return frozenset(policies.get("always_review_categories", []))


def review_reasons(claim: Claim, verdict: Verdict | None, confidence_floor: float) -> list[str]:
    """Why this claim needs a human before publishing; empty if it doesn't.

    An abstention ("nei" or no verdict) is not flagged for low confidence: declining to
    judge can't be a confident wrong verdict, which is the failure review exists to catch.
    """
    reasons = []
    if verdict and verdict.label != "nei" and verdict.confidence < confidence_floor:
        reasons.append(f"Low confidence ({verdict.confidence:.0%})")
    if claim.category in always_review_categories():
        reasons.append(f"Sensitive topic: {claim.category}")
    return reasons
