"""Turn a claim into 2-3 new search queries; increments attempts."""

import json
from pathlib import Path

from pydantic import BaseModel, Field

from claimlens.domain.schemas import Claim
from claimlens.graph.state import ClaimState
from claimlens.llm.factory import get_llm

PROMPT_PATH = Path(__file__).resolve().parents[2] / "llm" / "prompts" / "write_queries.v1.md"
MAX_QUERIES = 3


class SearchQueries(BaseModel):
    queries: list[str] = Field(description="2-3 distinct search queries")


def make_queries(claim: Claim, previous: list[str]) -> list[str]:
    """Ask the LLM for fresh queries that differ from the ones already tried."""
    tried = previous or [claim.search_terms or claim.text]
    prompt = (
        f"{PROMPT_PATH.read_text(encoding='utf-8')}\n\n"
        f"Claim:\n{claim.text}\n\nQueries already tried (JSON data):\n"
        f"{json.dumps(tried, ensure_ascii=False)}"
    )
    result = get_llm().with_structured_output(SearchQueries).invoke(prompt)
    seen = {query.casefold() for query in tried}
    fresh: list[str] = []
    for query in SearchQueries.model_validate(result).queries:
        query = " ".join(query.split())
        if query and query.casefold() not in seen:
            seen.add(query.casefold())
            fresh.append(query)
    if not fresh:
        raise ValueError("The query writer returned no new queries")
    return fresh[:MAX_QUERIES]


def write_queries(state: ClaimState) -> dict:
    queries = make_queries(state["claim"], state.get("queries", []))
    return {"queries": queries, "attempts": state.get("attempts", 0) + 1}
