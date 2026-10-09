"""Graph state for the main graph and the verify_claim subgraph."""

import operator
from typing import Annotated, TypedDict

from claimlens.domain.schemas import Claim, Evidence, Verdict


class ReelState(TypedDict, total=False):
    video_path: str
    caption: str
    transcript: str
    ocr_text: str
    claims: list[Claim]
    verdicts: Annotated[list[Verdict], operator.add]  # merged from parallel branches
    overall: str
    report: str


class ClaimState(TypedDict, total=False):
    claim: Claim
    queries: list[str]
    evidence: list[Evidence]
    attempts: int
    verdicts: list[Verdict]  # same key as ReelState, so results flow back up
