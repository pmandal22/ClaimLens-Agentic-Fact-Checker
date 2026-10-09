"""Graph state for the main graph and the verify_claim subgraph."""

import operator
from typing import Annotated, TypedDict

from claimlens.domain.schemas import Claim, Evidence, Review, Verdict
from claimlens.graph.nodes.aggregate import OverallVerdict


def merge_dicts(left: dict, right: dict) -> dict:
    return {**left, **right}


class ReelState(TypedDict, total=False):
    video_path: str
    work_dir: str  # optional: ingest writes here, so its keyframes outlive the node
    caption: str
    transcript: str
    ocr_text: str
    keyframes: list[str]  # paths under work_dir; empty when it wasn't given
    claims: list[Claim]
    verdicts: Annotated[list[Verdict], operator.add]  # merged from parallel branches
    evidence: Annotated[dict[str, list[Evidence]], merge_dicts]  # claim id -> ranked evidence
    review: Review  # set when a person decided on flagged claims
    overall: OverallVerdict
    report: str


class ClaimState(TypedDict, total=False):
    claim: Claim
    queries: list[str]
    evidence: list[Evidence]
    attempts: int
    verdicts: list[Verdict]  # same key as ReelState, so results flow back up
