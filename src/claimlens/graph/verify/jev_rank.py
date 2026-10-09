"""Assess evidence with TypeSafe's Jev decision model: one cheap structured call per item."""

import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from claimlens.domain.schemas import Claim, Evidence

RELEVANCE_LEVELS = [
    "Unrelated to the claim",
    "Same topic but does not address the claim",
    "Partly addresses the claim",
    "Directly addresses the claim",
]
STANCES = {
    "supports": "The evidence confirms the claim",
    "refutes": "The evidence contradicts the claim",
    "neutral": "The evidence neither confirms nor contradicts the claim",
}
MAX_WORKERS = 8
logger = logging.getLogger(__name__)


def _client() -> Any:
    try:
        from typesafe_sdk import TypeSafeClient
    except Exception as exc:  # pragma: no cover - environment-dependent
        raise RuntimeError(
            "typesafe-sdk is not installed; install the 'llm' dependency group"
        ) from exc
    return TypeSafeClient()


def _questions() -> dict[str, Any]:
    from typesafe_sdk import Choice, Score

    return {
        "relevance": Score(
            instructions="How directly does the evidence address the claim?",
            criteria=RELEVANCE_LEVELS,
        ),
        "stance": Choice(
            instructions="Does the evidence support or refute the claim?", criteria=STANCES
        ),
    }


def _state(claim: Claim, item: Evidence) -> str:
    return f"Claim: {claim.text}\n\nEvidence title: {item.title}\nEvidence snippet: {item.snippet}"


def assess_one(client: Any, claim: Claim, item: Evidence) -> tuple[float, str, float]:
    """Return (relevance 0-1, stance, confidence) for one evidence item.

    Relevance is the probability-weighted level, so uncertainty lowers the score rather than
    being hidden behind the top label. Confidence is the weaker of the two answers.
    """
    result = client.system_one(_state(claim, item), _questions())
    score = result.scores["relevance"]
    choice = result.choices["stance"]
    top = len(RELEVANCE_LEVELS) - 1
    relevance = sum(float(level) * p for level, p in score.probabilities.items()) / top
    return relevance, choice.choice, min(score.confidence, choice.confidence)


def assess_evidence(
    claim: Claim, evidence: list[tuple[int, Evidence]]
) -> dict[int, tuple[float, str, float]]:
    """Assess (index, evidence) pairs in parallel; a failed call is simply left out of the result."""
    if not evidence:
        return {}
    client = _client()

    def run(entry: tuple[int, Evidence]) -> tuple[int, tuple[float, str, float] | None]:
        index, item = entry
        try:
            return index, assess_one(client, claim, item)
        except Exception:
            logger.warning("Jev assessment failed for %s", item.url, exc_info=True)
            return index, None

    with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(evidence))) as pool:
        return {index: out for index, out in pool.map(run, evidence) if out is not None}
