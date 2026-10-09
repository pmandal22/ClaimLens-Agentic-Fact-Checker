# ClaimLens — Agentic Reel Fact Checker

Oct 7, 2026 · @Pooja Mandal

## Overview

Build the reel fact checker on **LangGraph**: it is a fixed pipeline with a few controlled loops, and LangGraph gives explicit control over each step. The system takes a short-form video, pulls out its factual claims from speech, on-screen text and caption, verifies each claim against evidence, and returns a cited verdict per claim plus an overall rating.

The design goal is auditability: every verdict traces back to a claim, its evidence and the reasoning that linked them.

## Why LangGraph

The flow should be deterministic, not left to the LLM, and LangGraph gives explicit control over each step.

| Need in this project | How LangGraph handles it |
| --- | --- |
| Check each claim in parallel | `Send` API fan-out, results merged by reducers |
| Retry weak evidence (bounded loop) | Conditional edges with a max-retry counter |
| Human review of low-confidence verdicts | `interrupt()` plus checkpointing |
| Resume a long video job after failure | Checkpointer persists state per step |
| Audit trail claim → evidence → verdict | Typed state, traced per node |
| Deployment runtime | LangGraph Platform or your own FastAPI |

## Architecture

The pipeline runs in five stages: ingest, extract claims, verify each claim in parallel, aggregate, and route uncertain results to a human.

&#91;embedded content: reel fact checker architecture · 3 stages plus per-claim subgraph\]

Weak evidence loops back to query rewriting at most twice, then the claim gets "not enough evidence" rather than a forced verdict. Gemini can replace the separate ASR and OCR steps by taking the video directly.

**Media forensics (phase 4)** adds reverse image search on keyframes as another evidence source, catching real footage reused out of context.

## Graph skeleton

The top-level graph is linear except for the per-claim fan-out; the retry loop and human review live inside the `verify_claim` subgraph.

```python
import operator
from typing import Annotated, Literal, TypedDict
from langgraph.graph import StateGraph, START, END
from langgraph.types import Send

class ClaimResult(TypedDict):
    claim: str
    evidence: list[dict]
    verdict: Literal["supported", "refuted", "misleading", "nei"]
    confidence: float

class ReelState(TypedDict):
    reel_url: str
    transcript: str
    ocr_text: str
    claims: list[str]
    results: Annotated[list[ClaimResult], operator.add]  # merged from fan-out
    report: str

def fan_out(state: ReelState):
    return [Send("verify_claim", {"claim": c}) for c in state["claims"]]

g = StateGraph(ReelState)
g.add_node("ingest", ingest)                 # download, ASR, OCR
g.add_node("extract_claims", extract_claims)
g.add_node("verify_claim", verify_subgraph)  # retrieve → judge → retry loop
g.add_node("aggregate", aggregate)
g.add_edge(START, "ingest")
g.add_edge("ingest", "extract_claims")
g.add_conditional_edges("extract_claims", fan_out, ["verify_claim"])
g.add_edge("verify_claim", "aggregate")
g.add_edge("aggregate", END)
```

## Key risks and success factors

These three matter more to the outcome than the framework choice.

**Video acquisition.** Scraping Instagram breaks its terms and breaks often. Default: accept user-uploaded videos, or links the user downloads themselves.

**Evaluation.** Build a labeled set of 30–50 real reels with known verdicts, and benchmark the verdict step against a public dataset such as AVeriTeC. Track claim-extraction recall separately from verdict accuracy, since they fail differently. Use LangSmith tracing to inspect failures per node.

**Calibrated verdicts.** Return "not enough evidence" rather than forcing true or false. The worst failure is a confident wrong verdict, so low-confidence results go to human review.

## Roadmap and next steps

Build in four phases, each gated by a measurable exit check.

1. **Core pipeline (text only).** Ingest a transcript, extract claims, verify with web search, return verdicts. Exit: runs end to end on 10 reels.
2. **Multimodal ingestion.** Add ASR on audio, keyframe OCR and caption parsing. Exit: claims captured from all three channels on the labeled set.
3. **Verification quality.** Add the Fact Check Tools API, evidence ranking, the bounded retry loop and human review. Exit: verdict accuracy measured on 30–50 labeled reels.
4. **Media forensics.** Add reverse image search on keyframes for out-of-context footage. Exit: catches reused footage in test cases.

- [ ] Choose the primary LLM for ingestion and verdicts
- [ ] Collect and label the first 30 reels
- [ ] Build the `verify_claim` subgraph with its retry counter
- [ ] Set up LangSmith tracing
- [ ] Safety check

Step-by-step build instructions: Build guide
