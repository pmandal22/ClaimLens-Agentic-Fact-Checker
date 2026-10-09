"""Score evidence stance and relevance; keep the top five relevant results."""

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from claimlens.config.settings import get_settings
from claimlens.domain.schemas import Claim, Evidence
from claimlens.graph.state import ClaimState
from claimlens.llm.factory import get_verify_llm

PROMPT_PATH = Path(__file__).resolve().parents[2] / "llm" / "prompts" / "rank.v1.md"
MAX_RANKED_EVIDENCE = 5


class EvidenceAssessment(BaseModel):
    index: int = Field(ge=0, description="Zero-based index of the evidence being assessed")
    relevance: float = Field(
        ge=0, le=1, description="How directly the evidence addresses the claim"
    )
    stance: Literal["supports", "refutes", "neutral"]


class EvidenceAssessments(BaseModel):
    assessments: list[EvidenceAssessment]


def _llm_assess(claim: Claim, items: list[tuple[int, Evidence]]) -> dict[int, tuple[float, str]]:
    """Ask the LLM to assess the given (index, evidence) pairs; every index must come back once."""
    material = [
        {"index": index, "url": item.url, "title": item.title, "snippet": item.snippet}
        for index, item in items
    ]
    prompt = (
        f"{PROMPT_PATH.read_text(encoding='utf-8')}\n\n"
        f"Claim:\n{claim.text}\n\nEvidence (JSON data):\n"
        f"{json.dumps(material, ensure_ascii=False)}"
    )
    result = get_verify_llm().with_structured_output(EvidenceAssessments).invoke(prompt)
    assessments = EvidenceAssessments.model_validate(result).assessments

    expected = {index for index, _ in items}
    by_index: dict[int, tuple[float, str]] = {}
    for assessment in assessments:
        if assessment.index not in expected:
            raise ValueError(f"Evidence assessment index {assessment.index} is out of range")
        if assessment.index in by_index:
            raise ValueError(f"Evidence index {assessment.index} was assessed more than once")
        by_index[assessment.index] = (assessment.relevance, assessment.stance)
    if set(by_index) != expected:
        raise ValueError("The ranker must assess every evidence item exactly once")
    return by_index


def score_evidence(claim: Claim, evidence: list[Evidence]) -> list[Evidence]:
    """Assess each result, discard low-relevance items, and return the best five."""
    if not evidence:
        return []

    threshold = get_settings().evidence_relevance_threshold
    ranked = [
        (relevance, index, evidence[index].model_copy(update={"stance": stance}))
        for index, (relevance, stance) in _llm_assess(claim, list(enumerate(evidence))).items()
        if relevance >= threshold
    ]
    ranked.sort(key=lambda item: (-item[0], item[1]))
    return [item for _score, _index, item in ranked[:MAX_RANKED_EVIDENCE]]


def rank(state: ClaimState) -> dict[str, list[Evidence]]:
    """LangGraph node: replace retrieved evidence with the ranked, relevant subset."""
    return {"evidence": score_evidence(state["claim"], state.get("evidence", []))}
