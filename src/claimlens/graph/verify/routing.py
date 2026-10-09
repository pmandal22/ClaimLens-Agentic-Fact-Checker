"""Route on attempts and evidence strength, not an LLM guess."""

from typing import Literal

from claimlens.config.settings import get_settings
from claimlens.graph.state import ClaimState


def route_after_rank(state: ClaimState) -> Literal["write_queries", "judge"]:
    """Retry with new queries until there is enough evidence or attempts run out."""
    settings = get_settings()
    strong = [e for e in state.get("evidence", []) if e.stance in ("supports", "refutes")]
    if len(strong) >= settings.min_verdict_evidence:
        return "judge"
    # max_attempts counts the first try, so retries allowed = max_attempts - 1.
    if state.get("attempts", 0) >= settings.max_attempts - 1:
        return "judge"
    return "write_queries"
