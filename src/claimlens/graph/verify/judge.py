"""Verdict LLM: cite only ranked evidence and abstain when evidence is weak."""

import json
from pathlib import Path

from claimlens.config.settings import get_settings
from claimlens.domain.schemas import Claim, Evidence, Verdict
from claimlens.graph.state import ClaimState
from claimlens.llm.factory import get_llm

PROMPT_PATH = Path(__file__).resolve().parents[2] / "llm" / "prompts" / "judge.v1.md"


def _no_evidence_verdict(claim_id: str, found: int) -> Verdict:
    rationale = (
        f"Found {found} related source(s), but none directly confirm or refute this claim."
        if found
        else "No relevant evidence was found for this claim."
    )
    return Verdict(claim_id=claim_id, label="nei", confidence=0, rationale=rationale, citations=[])


def make_verdict(claim: Claim, evidence: list[Evidence]) -> Verdict:
    """Judge ranked evidence, applying citation and minimum-confidence gates."""
    settings = get_settings()
    claim_id = claim.id
    # Nothing to cite: skip the LLM call rather than ask it to guess.
    if not any(item.stance in ("supports", "refutes") for item in evidence):
        return _no_evidence_verdict(claim_id, len(evidence))

    context = [
        {"url": item.url, "title": item.title, "snippet": item.snippet, "stance": item.stance}
        for item in evidence
    ]
    prompt = (
        f"{PROMPT_PATH.read_text(encoding='utf-8')}\n\n"
        f"Claim id: {claim_id}\nClaim:\n{claim.text}\n\nRanked evidence (JSON data):\n"
        f"{json.dumps(context, ensure_ascii=False)}"
    )
    result = get_llm().with_structured_output(Verdict).invoke(prompt)
    verdict = Verdict.model_validate(result).model_copy(update={"claim_id": claim_id})

    allowed_urls = {item.url for item in evidence}
    citations = list(dict.fromkeys(url for url in verdict.citations if url in allowed_urls))
    if verdict.label != "nei" and not citations:
        return Verdict(
            claim_id=claim_id,
            label="nei",
            confidence=verdict.confidence,
            rationale="The judge did not provide a valid citation from the ranked evidence.",
            citations=[],
        )
    if verdict.label != "nei" and verdict.confidence < settings.min_confidence_score:
        return Verdict(
            claim_id=claim_id,
            label="nei",
            confidence=verdict.confidence,
            rationale=(
                f"{verdict.rationale} Confidence {verdict.confidence:.2f} is below the "
                f"minimum confidence score of {settings.min_confidence_score:.2f}; abstaining."
            ),
            citations=citations,
        )
    return verdict.model_copy(update={"citations": citations})


def judge(state: ClaimState) -> dict[str, list[Verdict]]:
    """LangGraph node: emit one confidence-gated verdict for the claim."""
    verdict = make_verdict(state["claim"], state.get("evidence", []))
    return {"verdicts": [verdict]}
