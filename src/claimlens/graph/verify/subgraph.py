"""Per-claim verification: retrieve, rank, retry with new queries, then judge.

retrieve -> rank -> (enough strong evidence, or attempts exhausted) -> judge
                 -> otherwise write_queries -> retrieve ...
"""

from langgraph.graph import END, START, StateGraph

from claimlens.graph.state import ClaimState
from claimlens.graph.verify.judge import judge
from claimlens.graph.verify.rank import rank
from claimlens.graph.verify.retrieve import dedupe, retrieve_evidence
from claimlens.graph.verify.routing import route_after_rank
from claimlens.graph.verify.write_queries import write_queries


def retrieve(state: ClaimState) -> dict:
    found = retrieve_evidence(state["claim"], queries=state.get("queries") or None)
    # Keep what earlier attempts already found; rank re-scores everything together.
    return {"evidence": dedupe(state.get("evidence", []) + found)}


builder = StateGraph(ClaimState)
builder.add_node("retrieve", retrieve)
builder.add_node("rank", rank)
builder.add_node("write_queries", write_queries)
builder.add_node("judge", judge)
builder.add_edge(START, "retrieve")
builder.add_edge("retrieve", "rank")
builder.add_conditional_edges("rank", route_after_rank, ["write_queries", "judge"])
builder.add_edge("write_queries", "retrieve")
builder.add_edge("judge", END)

verify_claim = builder.compile()
