"""Main graph wiring: build_graph(checkpointer) -> compiled app.

ingest -> extract_claims -> verify_claim (one branch per claim) -> aggregate
       -> human_review (interrupt) when any claim is flagged -> report

A reel with no claims skips verification and is rated "inconclusive".

Ingest, claim extraction and verification import their code when they run, so the API can
build this graph to read and resume runs without the ML and LLM packages installed.
"""

import tempfile
from pathlib import Path
from typing import Literal

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Send

from claimlens.graph.nodes.aggregate import aggregate as rate_reel
from claimlens.graph.nodes.human_review import (
    apply_review,
    flagged_claims,
    human_review,
)
from claimlens.graph.nodes.report import render_report
from claimlens.graph.state import ClaimState, ReelState


def thread_config(job_id: str) -> RunnableConfig:
    # One graph thread per job, so the worker and API find the same run by job id.
    return {"configurable": {"thread_id": job_id}}


def ingest(state: ReelState) -> dict:
    from claimlens.ingest.pipeline import read_video_text

    video = Path(state["video_path"])
    if work_dir := state.get("work_dir"):
        transcript, ocr_text, frames = read_video_text(video, Path(work_dir))
        keyframes = [str(f) for f in frames]
    else:
        with tempfile.TemporaryDirectory(prefix="claimlens-graph-ingest-") as tmp:
            transcript, ocr_text, _ = read_video_text(video, Path(tmp))
        keyframes = []
    return {"transcript": transcript, "ocr_text": ocr_text, "keyframes": keyframes}


def extract_claims(state: ReelState) -> dict:
    from claimlens.graph.nodes.extract_claims import extract_claims as find_claims

    claims = find_claims(
        state.get("transcript", ""),
        caption=state.get("caption", ""),
        ocr_text=state.get("ocr_text", ""),
    )
    return {"claims": claims}


def fan_out(state: ReelState) -> list[Send] | Literal["aggregate"]:
    claims = state.get("claims", [])
    if not claims:
        return "aggregate"
    return [Send("verify_claim", {"claim": c}) for c in claims]


def verify_claim(state: ClaimState) -> dict:
    """One branch per claim. Wraps the subgraph so its evidence list is kept per claim id."""
    from claimlens.graph.verify.subgraph import verify_claim as verify_subgraph

    claim = state["claim"]
    result = verify_subgraph.invoke({"claim": claim})
    return {
        "verdicts": result.get("verdicts", []),
        "evidence": {claim.id: result.get("evidence", [])},
    }


def aggregate(state: ReelState) -> dict:
    claims = state.get("claims", [])
    return {"overall": rate_reel(apply_review(claims, state.get("verdicts", []), None))}


def route_review(state: ReelState) -> Literal["human_review", "report"]:
    if flagged_claims(state.get("claims", []), state.get("verdicts", [])):
        return "human_review"
    return "report"


def report(state: ReelState) -> dict:
    text = render_report(
        state.get("claims", []),
        state.get("verdicts", []),
        state.get("evidence", {}),
        state["overall"],
        state.get("review"),
    )
    return {"report": text}


def build_graph(checkpointer: BaseCheckpointSaver | None = None) -> CompiledStateGraph:
    """Compile the reel graph. Human review needs a checkpointer to pause and resume."""
    g = StateGraph(ReelState)
    g.add_node("ingest", ingest)
    g.add_node("extract_claims", extract_claims)
    g.add_node("verify_claim", verify_claim)
    g.add_node("aggregate", aggregate)
    g.add_node("human_review", human_review)
    g.add_node("report", report)
    g.add_edge(START, "ingest")
    g.add_edge("ingest", "extract_claims")
    g.add_conditional_edges("extract_claims", fan_out, ["verify_claim", "aggregate"])
    g.add_edge("verify_claim", "aggregate")
    g.add_conditional_edges("aggregate", route_review, ["human_review", "report"])
    g.add_edge("human_review", "report")
    g.add_edge("report", END)
    return g.compile(checkpointer=checkpointer)
