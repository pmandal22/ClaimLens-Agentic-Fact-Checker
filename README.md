# ClaimLens

Agentic fact-checker for short-form video. Paste a public Instagram Reel, YouTube Short or TikTok link; ClaimLens pulls the factual claims out of the speech, on-screen text and post caption, checks each one against trusted sources, and returns a cited verdict per claim plus a rating for the whole reel.

## How it works

Each check is a job. The API queues it, a worker runs it through a [LangGraph](https://langchain-ai.github.io/langgraph/) graph, and the web UI polls for results.

```
download video ─► ingest ─► extract claims ─► verify each claim (in parallel) ─► rate the reel ─► report
                    │                              │
   speech (Whisper) + on-screen text (EasyOCR)     search trusted sources (Tavily)
   + post caption, or one Gemini video call        → rank evidence → judge with citations
                                                   → weak evidence? rewrite the search and retry
```

- **Ingest** reads the video's text: Whisper transcribes speech and EasyOCR reads sampled keyframes (`INGEST_PATH=asr_ocr`), or Gemini reads the whole video in one call (`INGEST_PATH=video_llm`). Videos without audio still work from on-screen text and the caption.
- **Search** stays within trusted sites per topic (health, science, history, politics, economy, technology, sports, general), with Indian sources first for India-related claims. The lists live in [`src/claimlens/tools/trusted_sources.py`](src/claimlens/tools/trusted_sources.py).
- **Rank** scores each result's relevance and stance (supports, refutes, neutral) and keeps the best five.
- **Judge** labels the claim and must cite the evidence. It abstains rather than guess: no valid citation, confidence below `MIN_CONFIDENCE_SCORE`, or a "refuted"/"misleading" call with no refuting evidence all become "not enough evidence".

### Verdicts

| Claim label | Meaning |
| --- | --- |
| Supported | The evidence confirms the claim's core assertion |
| Refuted | The evidence contradicts it |
| Misleading | True or partly true, but the evidence shows it creates a false impression |
| Not enough evidence | The evidence doesn't settle it |

The reel's overall rating counts only decided claims: **Mostly supported**, **Contains false claims** (some refuted or misleading, at most half), **Mostly false** (more than half), or **Inconclusive** (nothing decided).

## Quick start

You need [uv](https://docs.astral.sh/uv/), ffmpeg, Docker and Node.js 20+, plus API keys for an LLM provider and [Tavily](https://tavily.com).

```bash
brew install ffmpeg                  # or: apt-get install ffmpeg
uv sync                              # Python deps, including the ML and LLM packages
source .venv/bin/activate            # the make targets call pytest, uvicorn and python directly
cp .env.example .env                 # set CLAIMLENS_MODEL, your provider's key and TAVILY_API_KEY
```

**Run everything in Docker** (API, workers, Postgres, Redis), then start the UI:

```bash
make up                              # API on http://localhost:8000; WORKER_COUNT workers
make ui                              # web UI on http://localhost:5173
make down                            # stop; job data stays in Docker volumes
```

The worker image ships the Whisper and EasyOCR models for `OCR_LANGUAGES`, so containers never download them at runtime; change `OCR_LANGUAGES` and run `make up` to rebuild.

**Or run the Python services locally**, with Redis running (`docker compose -f infra/docker-compose.yml up -d redis`). Jobs go to a local SQLite file unless `POSTGRES_URL` is set.

```bash
make api                             # uvicorn with reload
python -m apps.worker.main           # one worker
make ui
```

## Configuration

Settings come from `.env`; [`.env.example`](.env.example) lists them all. Each LLM step can use its own model, written as `provider:model` (for example `openai:<model>`, `anthropic:<model>`, `google_genai:<model>`):

| Setting | Used for | When empty |
| --- | --- | --- |
| `CLAIMLENS_MODEL` | Writing search queries; default for the rest | Required |
| `EXTRACT_MODEL` | Extracting claims | `CLAIMLENS_MODEL` |
| `VERIFY_MODEL` | Ranking evidence and judging | `CLAIMLENS_MODEL` |
| `VIDEO_MODEL` | Reading the video with `INGEST_PATH=video_llm` (Gemini only) | `CLAIMLENS_MODEL` |

`LLM_TEMPERATURE` applies to `CLAIMLENS_MODEL`. The others have their own `*_TEMPERATURE`, and leaving one empty sends no temperature, since some models accept only their default.

Other settings you are likely to change:

| Setting | Default | What it does |
| --- | --- | --- |
| `MAX_CLAIMS_PER_REEL` | 10 | Most claims checked per reel; each adds search and LLM calls |
| `MIN_CONFIDENCE_SCORE` | 0.7 | Verdicts below this become "not enough evidence" |
| `EVIDENCE_RELEVANCE_THRESHOLD` | 0.5 | Search results below this relevance are dropped |
| `OCR_LANGUAGES` | `en` | EasyOCR languages, comma separated (e.g. `en,hi`) |
| `WORKER_COUNT` | 1 | Worker containers started by `make up` |
| `GCS_BUCKET` | empty | Store videos and results in Google Cloud Storage instead of local disk |
| `LANGSMITH_TRACING` | `true` | Trace runs to LangSmith; tests always run with tracing off |

## API

| Method | Path | Returns |
| --- | --- | --- |
| `POST` | `/checks` with `{"url": "..."}` | `202` and the job. Submitting a URL again returns its existing job unless that job failed. |
| `GET` | `/checks/{id}` | Status (`queued`, `downloading`, `ingesting`, `verifying`, `done`, `failed`); once `done`, per-claim results (claim, ranked evidence, verdict) and the overall rating |
| `GET` | `/health` | `{"status": "ok"}` |

Only public URLs are accepted, and private network addresses are rejected.

## Tests and evals

```bash
make test                            # Python unit and integration tests
make lint                            # ruff and mypy
cd apps/web && npm test              # web UI tests
make eval                            # claim eval: rank + judge on 24 labelled claims
```

The eval caches retrieved evidence in `evals/datasets/.evidence_cache.json` so runs compare the same inputs; add `--refresh` after changing search or trusted domains (it repeats the Tavily searches), or `--limit 5` for a quick run. Reports land in `evals/reports/` and name the model that ranked and judged.

## Project layout

```
apps/
  api/           FastAPI: POST /checks, GET /checks/{id}
  worker/        Queue consumer that runs the graph
  web/           React + TypeScript UI (Vite)
src/claimlens/
  ingest/        Download, audio, Whisper, keyframes, OCR, Gemini video path
  graph/         Main graph, per-claim verify subgraph, checkpointer
  llm/           Per-task model factory and prompts
  services/      Jobs (SQLite/Postgres), Redis queue, storage, results
  tools/         Trusted source lists
evals/           Labelled claims, eval runner, reports
infra/           Docker Compose and Dockerfiles
```

Workers take jobs with a lease and heartbeat, so a crashed worker's job moves to another one, and the graph is checkpointed so a retried job continues where it stopped.

More detail: [BUILD_GUIDE.md](BUILD_GUIDE.md) (build plan), [WIP_TRACKER.md](WIP_TRACKER.md) (status) and the [design doc](Reel%20Fact%20Checker%20%E2%80%94%20Agentic%20AI%20Design.md).
