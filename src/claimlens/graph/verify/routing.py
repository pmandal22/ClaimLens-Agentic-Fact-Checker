"""Route on attempts and evidence strength, not an LLM guess."""

from typing import Literal

from claimlens.config.settings import get_settings
from claimlens.graph.state import ClaimState


def route_after_rank(state: ClaimState) -> Literal["write_queries", "judge"]:
    """Retry with new queries until some evidence takes a stance or attempts run out.

    How much that evidence settles the claim is left to the judge's confidence score.
    """
    settings = get_settings()
    if any(e.stance in ("supports", "refutes") for e in state.get("evidence", [])):
        return "judge"
    # max_attempts counts the first try, so retries allowed = max_attempts - 1.
    if state.get("attempts", 0) >= settings.max_attempts - 1:
        return "judge"
    return "write_queries"
