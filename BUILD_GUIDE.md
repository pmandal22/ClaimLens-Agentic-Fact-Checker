# ClaimLens build guide

Build ClaimLens in nine steps, each ending with a check you can run before moving on. Defaults are noted where the design left a choice open.

## Production project structure

The production layout separates the agent logic (`graph/`) from how it runs (`apps/`) and where it runs (`infra/`), so each can change without touching the others. Step 1's tree is the starting subset of this.

```text
claimlens/
  pyproject.toml  uv.lock  README.md  Makefile  .env.example  .pre-commit-config.yaml

  src/claimlens/                  # the installable package: pure logic, no web framework
    config/
      settings.py                 # pydantic-settings: model, keys, limits, per environment
      logging.py                  # structured JSON logs with thread_id on every line
    domain/
      schemas.py                  # Claim, Evidence, Verdict
      errors.py                   # typed errors: IngestError, RetrievalError, ...
    llm/
      factory.py                  # init_chat_model wrapper: provider, retries, timeouts
      prompts/                    # versioned prompt files, never inline strings
        extract_claims.v1.md
        write_queries.v1.md
        judge.v1.md
    ingest/
      audio.py  asr.py  keyframes.py  ocr.py
      video_llm.py                # Gemini direct-video path
      pipeline.py                 # the ingest node; picks a path from config
    tools/
      web_search.py               # Tavily client
      factcheck_api.py            # Google Fact Check Tools API client
      image_search.py             # reverse image search (phase 4)
      cache.py                    # response cache keyed by query
    graph/
      state.py                    # ReelState, ClaimState
      main_graph.py               # build_graph(checkpointer) -> compiled app
      checkpointer.py             # Sqlite locally, Postgres in prod
      nodes/
        extract_claims.py  aggregate.py  human_review.py  report.py
      verify/
        subgraph.py  write_queries.py  retrieve.py  rank.py  judge.py  routing.py
    safety/
      guards.py                   # sensitive-topic routing, untrusted-text wrapping
      policies.yaml               # topics that always need human review
    services/
      jobs.py                     # start, get status, resume a run
      storage.py                  # GCS / S3 / local behind one interface
    observability/
      tracing.py  metrics.py      # LangSmith setup, cost and latency counters

  apps/                           # thin entry points that call services/
    api/
      main.py                     # FastAPI app factory
      routes/checks.py            # POST /checks, GET /checks/{id}, POST /checks/{id}/review
      dto.py                      # request/response models (not domain schemas)
      deps.py                     # auth, rate limits, injected services
    worker/
      main.py                     # pulls jobs from the queue, runs the graph
    ui/
      app.py                      # Streamlit demo

  evals/
    datasets/reels.jsonl          # labeled claims and verdicts
    run_eval.py  judges.py
    reports/                      # one results file per run, committed

  tests/
    unit/                         # nodes with a fake LLM, routing, schemas
    integration/                  # subgraph against cached search responses
    e2e/                          # full run on 2-3 short sample reels
    fixtures/                     # sample reels, recorded API responses

  infra/
    docker/api.Dockerfile  docker/worker.Dockerfile
    docker-compose.yml            # api + worker + postgres + redis locally
    terraform/                    # Cloud Run, Cloud SQL, GCS, Secret Manager, queue

  docs/
    architecture.md
    adr/0001-langgraph.md         # one file per architecture decision
    runbook.md                    # how to replay a failed run, rotate keys

  .github/workflows/
    ci.yml                        # lint (ruff), types (mypy), unit + integration tests
    eval.yml                      # eval suite on prompt or model changes
    deploy.yml                    # build images, deploy on main
```

| Decision | Why it matters in production |
| --- | --- |
| API and worker are separate processes | A reel takes too long for a request; the API enqueues, the worker runs the graph and survives API restarts |
| `build_graph(checkpointer)` takes its dependencies | Tests pass a memory checkpointer and fake LLM; prod passes Postgres |
| Prompts are versioned files | Eval reports and traces record which prompt version produced a verdict |
| `dto.py` is separate from `domain/schemas.py` | The public API can stay stable while internal schemas evolve |
| Recorded API responses in `fixtures/` | Integration tests run offline, fast and free |
| ADRs in `docs/adr/` | Each design choice has a written reason, which reviewers and interviewers ask for |

## Step 1 — Set up the repo and stack

Start with a clean Python 3.11+ repo managed by `uv`, so every later step drops into a known place.

| Layer | Default choice | Why |
| --- | --- | --- |
| Orchestration | `langgraph`, `langchain-core` | Graph, `Send`, `interrupt()`, checkpointers |
| LLM | Gemini, OpenAI or Claude via `init_chat_model` | One config value switches provider |
| Schemas | `pydantic` | Structured LLM output and typed state |
| Media | `ffmpeg`, `faster-whisper`, `scenedetect`, `easyocr` | Audio, multilingual ASR, keyframes, OCR |
| Retrieval | `tavily-python`, `httpx` for the Google Fact Check Tools API | Web evidence and existing fact-checks |
| Serving | `fastapi`, `streamlit` | API and demo UI |
| Quality | `langsmith`, `pytest` | Tracing and tests |

Keep the provider in config so you can compare models in Step 8:

```python
# src/claimlens/config.py
import os
from langchain.chat_models import init_chat_model

# e.g. "google_genai:<gemini-model>", "openai:<gpt-model>", "anthropic:<claude-model>"
LLM_MODEL = os.getenv("CLAIMLENS_MODEL")
llm = init_chat_model(LLM_MODEL, temperature=0)
```

Install only the matching integration: `langchain-google-genai`, `langchain-openai` or `langchain-anthropic`. All three support `with_structured_output`, so Steps 4–6 need no changes.

```text
claimlens/
  src/claimlens/
    schemas.py        # Claim, Evidence, Verdict, ReelState
    ingest/           # audio.py, asr.py, keyframes.py, ocr.py
    nodes/            # extract_claims.py, aggregate.py, report.py
    verify/           # subgraph.py, queries.py, retrieve.py, rank.py, verdict.py
    graph.py          # main graph wiring
    config.py         # model names, retry limits, keys
  api/main.py         # FastAPI
  ui/app.py           # Streamlit
  evals/              # labeled reels + eval scripts
  tests/
  .env.example        # CLAIMLENS_MODEL, GOOGLE_API_KEY | OPENAI_API_KEY | ANTHROPIC_API_KEY, TAVILY_API_KEY, FACTCHECK_API_KEY, LANGSMITH_API_KEY
  pyproject.toml
```

**Done when:** `uv run pytest` passes a smoke test that loads config and calls the LLM once.

## Step 2 — Define schemas and graph state

Write the data contracts before any node: every node reads and writes these, and the LLM returns them as structured output.

```python
# src/claimlens/schemas.py
import operator
from typing import Annotated, Literal, TypedDict
from pydantic import BaseModel, Field

class Claim(BaseModel):
    id: str
    text: str = Field(description="One atomic, checkable factual statement")
    source: Literal["speech", "on_screen", "caption"]
    timestamp_s: float | None = None

class Claims(BaseModel):
    claims: list[Claim]

class Evidence(BaseModel):
    url: str
    title: str
    snippet: str
    publisher: str | None = None
    stance: Literal["supports", "refutes", "neutral"] | None = None

class Verdict(BaseModel):
    claim_id: str
    label: Literal["supported", "refuted", "misleading", "nei"]
    confidence: float = Field(ge=0, le=1)
    rationale: str
    citations: list[str]

class ReelState(TypedDict, total=False):
    video_path: str
    caption: str
    transcript: str
    ocr_text: str
    claims: list[Claim]
    verdicts: Annotated[list[Verdict], operator.add]  # merged from parallel branches
    overall: str
    report: str

class ClaimState(TypedDict, total=False):
    claim: Claim
    queries: list[str]
    evidence: list[Evidence]
    attempts: int
    verdicts: list[Verdict]  # same key as ReelState, so results flow back up
```

**Done when:** unit tests construct each model and reject invalid values, such as a confidence of 1.5.

## Step 3 — Build ingestion

Turn an uploaded video into three text channels: transcript, on-screen text and caption. The caption arrives as a form field with the upload.

1. **Extract audio** with ffmpeg: `ffmpeg -i reel.mp4 -vn -ac 1 -ar 16000 audio.wav`.
2. **Transcribe** with `faster-whisper` (`small` model to start; language auto-detected, so mixed-language reels work). Keep segment timestamps; claims will point back to them.
3. **Pick keyframes** with `scenedetect` content detection, capped at about 10 frames per reel. Save them; Step 9 reuses them for reverse image search.
4. **OCR** each keyframe with `easyocr`, then de-duplicate lines that repeat across frames (captions burned into video repeat a lot).
5. **Wrap it as one node** that returns state updates:

```python
# src/claimlens/ingest/__init__.py
def ingest(state: ReelState) -> dict:
    wav = extract_audio(state["video_path"])
    frames = pick_keyframes(state["video_path"], max_frames=10)
    return {
        "transcript": transcribe(wav),
        "ocr_text": dedupe(ocr(f) for f in frames),
    }
```

Alternative path: send the whole video to Gemini and ask for transcript plus on-screen text in one structured call. This path is Gemini-only; with OpenAI or Claude, keep ASR and send keyframes as images instead of running OCR. Both are implemented behind `INGEST_PATH` (`asr_ocr` or `video_llm`; the latter uploads via the Gemini Files API, so it handles videos beyond the inline size limit). Compare them in Step 8.

**Done when:** three test reels produce a readable transcript and OCR text in under a minute each on your machine.

## Step 4 — Extract claims

This node sets the ceiling for the whole system: a claim it misses is never checked. Use structured output and a strict prompt.

```python
# src/claimlens/nodes/extract_claims.py
SYSTEM = """Extract every checkable factual claim from this reel.
Rules:
- One atomic claim per item; split compound sentences.
- Rewrite each claim to stand alone (resolve 'this', 'he', 'here').
- Skip opinions, jokes, predictions and calls to action.
- Keep numbers, names, dates and places exactly as stated.
- Translate non-English claims to English; keep the original in parentheses."""

extractor = llm.with_structured_output(Claims)

def extract_claims(state: ReelState) -> dict:
    content = (f"TRANSCRIPT:\n{state['transcript']}\n\n"
               f"ON-SCREEN TEXT:\n{state['ocr_text']}\n\n"
               f"CAPTION:\n{state.get('caption', '')}")
    result = extractor.invoke([("system", SYSTEM), ("user", content)])
    return {"claims": result.claims[:8]}  # cap per reel to bound cost
```

Label claims by hand on your first 10 reels before tuning the prompt, so you can measure recall instead of eyeballing it.

**Done when:** the node finds most hand-labeled claims on those 10 reels and returns no opinions.

## Step 5 — Build the verify\_claim subgraph

Build and test this subgraph on single claims before wiring it into the main graph; it is where most of the quality comes from.

1. **Retrieve:** use the claim's search terms, querying its category's direct trusted sources first and Tavily as a fallback.
2. **Rank:** an LLM assesses each result's relevance (0–1) and stance (supports, refutes or neutral). Discard results below `EVIDENCE_RELEVANCE_THRESHOLD` (default `0.5`) and keep the top five.
3. **Check sufficiency:** require at least `MIN_VERDICT_EVIDENCE` relevant results with a supporting or refuting stance (default `2`). Otherwise return `nei` without asking the judge to guess.
4. **Judge:** see only ranked evidence. Citations are restricted to its URLs; non-`nei` verdicts below `CONFIDENCE_FLOOR` (default `0.7`) are downgraded to `nei`.
5. **Persist:** the worker stores ranked evidence and per-claim verdicts under `evidence/{id}.json` and `verdicts/{id}.json`.

The current worker runs this single-pass verification subgraph for each claim. Query rewriting and retrieval retries remain a possible improvement for claims that do not meet the evidence threshold.

Cache search results by query string during development; you will rerun the same claims many times.

**Done when:** 10 hand-picked claims (true, false and unverifiable) each get a verdict with real, relevant citations.

## Step 6 — Wire the main graph and human review

Connect the pieces, fan out one branch per claim, and pause the run when any verdict is uncertain.

```python
# src/claimlens/graph.py
import sqlite3
from langgraph.types import Send, interrupt, Command
from langgraph.checkpoint.sqlite import SqliteSaver

CONFIDENCE_FLOOR = 0.7

def fan_out(state: ReelState):
    return [Send("verify_claim", {"claim": c, "attempts": 0}) for c in state["claims"]]

def route_review(state: ReelState) -> str:
    shaky = any(v.confidence < CONFIDENCE_FLOOR for v in state["verdicts"])
    return "human_review" if shaky else "report"

def human_review(state: ReelState) -> dict:
    shaky = [v for v in state["verdicts"] if v.confidence < CONFIDENCE_FLOOR]
    decision = interrupt({"review": [v.model_dump() for v in shaky]})  # pauses here
    return {"overall": decision.get("overall", "reviewed")}

g = StateGraph(ReelState)
for name, fn in [("ingest", ingest), ("extract_claims", extract_claims),
                 ("verify_claim", verify_subgraph), ("aggregate", aggregate),
                 ("human_review", human_review), ("report", report)]:
    g.add_node(name, fn)
g.add_edge(START, "ingest")
g.add_edge("ingest", "extract_claims")
g.add_conditional_edges("extract_claims", fan_out, ["verify_claim"])
g.add_edge("verify_claim", "aggregate")
g.add_conditional_edges("aggregate", route_review, ["human_review", "report"])
g.add_edge("human_review", "report")
g.add_edge("report", END)

app = g.compile(checkpointer=SqliteSaver(sqlite3.connect("claimlens.db", check_same_thread=False)))
```

Run with a `thread_id` in the config. When the run pauses, resume it with `app.invoke(Command(resume={"overall": "misleading"}), config)`. Handle a reel with zero claims by routing straight to `report`.

**Done when:** one reel runs end to end, pauses on a low-confidence claim, and resumes after a restart of the Python process.

## Step 7 — Add the API and UI

A reel takes longer than a normal web request, so the API starts a job and the UI polls it.

| Endpoint | Does |
| --- | --- |
| `POST /checks` | Accepts the video file and caption, saves the file, starts the graph in a background task, returns `thread_id` |
| `GET /checks/{thread_id}` | Returns status (running, needs review, done), claims and verdicts from the checkpointed state |
| `POST /checks/{thread_id}/review` | Takes the reviewer's decision and resumes the run with `Command(resume=...)` |

Read status with `app.get_state(config)`: a paused run shows `human_review` in its `next` nodes.

The Streamlit UI needs three views: an upload form, a results page with one card per claim (label, confidence, rationale and clickable citations, plus the transcript timestamp), and a review panel that appears only when the run is paused.

**Done when:** you can upload a reel in the browser, watch it finish, and approve a paused verdict without touching the terminal.

## Step 8 — Evaluate, trace and run the safety check

Measure before you tune: set up tracing and an eval set, then change one thing at a time.

**Tracing.** Set `LANGSMITH_TRACING=true` and your key; every node, LLM call and retry then shows up per run. Tag runs with the prompt version.

**Eval set.** Label 30–50 reels in `evals/reels.jsonl`: the expected claims and a verdict for each. Mix true, false, misleading and unverifiable, and include non-English reels.

| Metric | Measures | How |
| --- | --- | --- |
| Claim recall | Claims found vs. hand-labeled | LLM judge matches extracted to expected claims |
| Verdict accuracy | Correct labels on matched claims | Exact match against your labels |
| Citation validity | Cited pages actually support the rationale | LLM judge plus spot checks |
| Confident-wrong rate | Wrong verdicts above the confidence floor | The number to drive toward zero |
| Cost and latency | Per reel | From LangSmith traces |

Run the eval script on every prompt or model change and keep results in a table, so regressions show up.

**Safety check.**

- [ ] Verdicts name sources, never accuse the creator of lying; wording describes the claim, not the person
- [ ] Uploaded videos are deleted after processing, with a stated retention period
- [ ] The UI says verdicts are automated and may be wrong, and links to the evidence
- [ ] Claims about health, elections or named private individuals always go to human review
- [ ] Text from reels and web pages is treated as data in prompts, so a reel cannot instruct the model

**Done when:** you have a baseline score for every metric and the safety checklist is ticked.

## Step 9 — Deploy, add forensics, package it

Ship a public demo, add the visual evidence channel, and make the repo easy for a reviewer to understand in five minutes.

**Deploy on Google Cloud** (default; works the same with any of the three providers):

1. Containerize the API and UI with Docker, with ffmpeg installed in the image.
2. Run both on Cloud Run; store uploads in Cloud Storage with a lifecycle rule that deletes them.
3. Swap `SqliteSaver` for `PostgresSaver` on Cloud SQL, so paused runs survive container restarts.
4. Keep keys in Secret Manager and add a per-user upload limit to cap cost.

**Media forensics.** Send the Step 3 keyframes to Cloud Vision web detection, which returns pages containing matching images. Treat an older page about a different event as `Evidence` with stance `refutes`, so the existing Verdict node can label the claim misleading.

**Package for your portfolio:**

- [ ] README with the architecture diagram, a one-line problem statement and a quick-start
- [ ] A 2-minute demo video: upload, results, a paused review
- [ ] The eval table from Step 8, with what changed between versions
- [ ] A short design write-up on the trade-offs: deterministic graph vs. autonomous agents, retry limits, human review

**Done when:** a stranger can open the live link, check a reel, and follow how each verdict was reached.
