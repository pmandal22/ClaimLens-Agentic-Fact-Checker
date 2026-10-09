"""Combine per-claim verdicts into an overall rating."""

from collections import Counter
from collections.abc import Sequence
from typing import Literal

from pydantic import BaseModel

from claimlens.domain.schemas import Claim, Verdict

Rating = Literal["mostly_supported", "mixed", "misleading", "inconclusive"]

# A reel is rated "misleading" (shown as "Mostly false") when more than this share of its
# decided claims are refuted or misleading; an even split is "mixed" (shown as "Contains false claims").
MISLEADING_SHARE = 0.5
# "mostly_supported" needs supported claims to make up at least this share of all claims,
# so one lucky verdict among many abstentions does not vouch for the reel.
SUPPORTED_SHARE = 0.5


class OverallVerdict(BaseModel):
    rating: Rating
    summary: str
    counts: dict[str, int]


def in_claim_order(claims: Sequence[Claim], verdicts: Sequence[Verdict]) -> list[Verdict | None]:
    """Each claim's verdict, in claim order; None for a claim without one.

    Parallel branches append verdicts in completion order, so they are looked up by claim id.
    """
    by_id = {v.claim_id: v for v in verdicts}
    return [by_id.get(c.id) for c in claims]


def aggregate(verdicts: Sequence[Verdict | None]) -> OverallVerdict:
    """Rate the whole reel from its claim verdicts. Claims without a verdict count as nei."""
    labels = Counter(v.label if v else "nei" for v in verdicts)
    counts = {label: labels.get(label, 0) for label in ("supported", "refuted", "misleading", "nei")}
    total = sum(counts.values())
    bad = counts["refuted"] + counts["misleading"]
    decided = counts["supported"] + bad

    if decided == 0:
        rating: Rating = "inconclusive"
    elif bad / decided > MISLEADING_SHARE:
        rating = "misleading"
    elif bad > 0:
        rating = "mixed"
    elif counts["supported"] / total >= SUPPORTED_SHARE:
        rating = "mostly_supported"
    else:
        rating = "inconclusive"

    summary = (
        f"{total} claim(s): {counts['supported']} supported, {counts['refuted']} refuted, "
        f"{counts['misleading']} misleading, {counts['nei']} without enough evidence."
    )
    return OverallVerdict(rating=rating, summary=summary, counts=counts)
