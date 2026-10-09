# ClaimLens: WIP Tracker

Last updated: 2026-10-09T14:33:00+05:30

Status key: ✅ done · 🚧 in progress · ⬜ not started

## Flow today
```
Client → POST /checks → API validates URL → job row (Postgres) + message (Redis)
Worker ← Redis → claims job (lease + heartbeat) → downloads video → saves to storage → status "ingesting"
  → LangGraph reel graph (thread id = job id, checkpointed):
    ingest → extract claims → verify each claim in parallel
      [retrieve → rank → (weak? write_queries → retrieve, max 3 tries) → judge]
    → aggregate → human_review (pause) → report
  → status "done", or "needs_review" if a verdict needs a person
Reviewer → POST /checks/{id}/review → resumes the paused run → report → "done"
Client → GET /checks/{id} → status, plus overall rating and per-claim results (claim, ranked evidence, verdict)
```
Artifacts: `transcripts/{id}.txt`, `ocr/{id}.txt`, `claims/{id}.json`, `evidence/{id}.json`, `verdicts/{id}.json`, `keyframes/{id}/`, `reports/{id}.md`.
Live check (1oMfwA4cSLs): retries recovered one claim; two still abstain (no evidence) because snippets are titles only.

## Done ✅

### Ingestion plumbing
- ✅ Video downloader (yt-dlp), any public URL, 100 MB / 180 s limits (`ingest/download.py`)
- ✅ SSRF protection: public-address check in API and worker
- ✅ Storage: `LocalStorage` and `GCSStorage`, URL-hash keys (`services/storage.py`)
- ✅ Job tracking: statuses, allowed transitions, dedupe (`services/jobs.py`)
- ✅ `SQLiteJobStore` (local) and `PostgresJobStore` (shared) (`services/jobs_postgres.py`)
- ✅ Redis Streams queue: consumer group, ack, crash recovery (`services/queue.py`)
- ✅ `submit_check()` (`services/submit.py`)
- ✅ Worker loop with graceful shutdown (`services/worker.py`, `apps/worker/main.py`)
- ✅ Claim lease + heartbeat: jobs now record `claimed_by` and `claim_expires_at`, and the worker heartbeats while processing to extend the lease (safer reclaiming and diagnostics) (`services/jobs.py`, `services/jobs_postgres.py`, `services/worker.py`)
- ✅ Safe job recovery (PR #1, merged): heartbeat also refreshes the Redis message idle time (XCLAIM to self); `claim(resume=True)` takes over only expired leases, for any in-progress status; a worker that lost its job no longer acks the new owner's message; still-leased redeliveries stay pending; worker id = Redis consumer name, stale consumers pruned on startup; DB lease ends before the Redis reclaim window

### API
- ✅ `POST /checks` (202), `GET /checks/{id}`, `GET /health` (`apps/api/`)
- ✅ `POST /checks/{id}/review`: resumes the run paused at `human_review`, claims the review first (concurrent reviewers get 409), saves `reports/{id}.md`
- ✅ 422 for bad input, 503 when the queue is down

### Infra
- ✅ Docker compose: postgres, redis, api, worker (`infra/docker-compose.yml`)
- ✅ `api.Dockerfile`, `worker.Dockerfile`, `postgres.Dockerfile`, `.dockerignore`
- ✅ Dependencies split into `dev`, `pipeline`, `ui` groups to keep images small
- ✅ Verified end to end in containers with 2 workers

### Quality
- ✅ 193 unit tests passing, ruff clean
- ✅ Job-store tests run against both SQLite and Postgres

- ✅ Pipeline wired into the worker: ffmpeg audio, faster-whisper ASR (`asr` dep group), keyframes
- ✅ Claim extraction node (`graph/nodes/extract_claims.py`, `llm` dep group); worker gets `CLAIMLENS_MODEL` + `OPENAI_API_KEY` via compose (`--env-file .env`)
- ✅ Overall rating (`graph/nodes/aggregate.py`): `aggregate()` rates the reel `mostly_supported` / `mixed` / `misleading` / `inconclusive` from claim verdicts (nei ignored for rating; missing verdict = nei); `GET /checks/{id}` returns it as `overall`
- ✅ `ResponseCache` wired into retrieval (`retrieve._cached`, `tools/cache.get_cache`): per-source and Tavily searches cached in Redis (24 h, `SEARCH_CACHE_ENABLED`, `SEARCH_CACHE_TTL_S`); failures are never cached; Redis down or cache errors fall back to live calls. Live: second identical retrieval 3.6 s -> 0.0 s
- ✅ Claim-level eval (`evals/run_eval.py`, `make eval`): 24 labeled claims in `evals/datasets/claims.jsonl`; retrieves once (cached in `.evidence_cache.json`) and reports accuracy, abstain rate, confident-wrong and time for the LLM ranker. First results: accuracy 0.54-0.62 overall, ~0.92 when answering, confident-wrong 0.04; results swing by 1-2 claims between identical runs. Abstentions are mostly retrieval gaps (title-only snippets, Wikipedia 429s cached into evidence). Not yet covered: claim recall, citation validity (LLM judges), video-level `reels.jsonl`
- ✅ Claims carry a `category` and `search_terms` (same LLM call). Evidence retrieval (`graph/verify/retrieve.py`): Tavily only, limited first to the category's trusted domains (map in `tools/trusted_sources.py`), then the open web if that finds nothing; queries stop once `min_trusted_evidence` (2) results are found. Saved to `evidence/{id}.json`
- ✅ Redis-backed query response cache with TTL, provider/options scoping, and `Evidence` serialization (`tools/cache.py`)
- ✅ Per-claim verification graph: LLM relevance/stance ranking, relevance threshold (`EVIDENCE_RELEVANCE_THRESHOLD`, default 0.5), and a minimum confidence score (`MIN_CONFIDENCE_SCORE`, default 0.7; replaced the 2-strong-evidence rule `MIN_VERDICT_EVIDENCE`). The judge runs once any evidence supports or refutes the claim. Low-confidence or unsupported-citation results abstain as `nei`; ranked evidence and verdicts are saved by the worker.
- ✅ Live pipeline run on YouTube Short `c1b6adSrK3g`: downloaded and processed successfully; extracted 5 claims, retrieved 36 evidence results, and produced 76 keyframes. Temporary video and artifacts were cleaned up after the run.
- ✅ Main LangGraph reel graph + checkpointer (PR #2, merged) (`graph/main_graph.py`, `graph/checkpointer.py`): SQLite locally, pooled Postgres when `POSTGRES_URL` is set; worker runs each job as one thread and retried jobs continue from their last checkpoint instead of re-transcribing; graph nodes import ML/LLM code lazily so the API image stays light; `process_video` and `results.needs_review` removed
- ✅ React + TypeScript UI (`apps/web`, `make ui`): submit a URL, poll the job, show overall rating and per-claim verdicts
- ✅ Fixed: missing `run_once`, SQLite column mismatch, YouTube format selection, temp dir lifetime

## In progress 🚧
- Evals: claim recall, citation validity (LLM judges), video-level `reels.jsonl` (see Next up #10)

## Next up ⬜
1. ✅ Ingestion pipeline: `INGEST_PATH=asr_ocr` (faster-whisper + easyocr on sampled keyframes, de-duplicated) or `video_llm` (Gemini Files API returns transcript + on-screen text); on-screen text feeds claim extraction and is stored at `ocr/{id}.txt`. Live-tested both on one reel: OCR is noisier (~2 min on CPU for 30 frames incl. first-run model download); Gemini is cleaner (~1 min).
2. ✅ Move jobs past `ingesting` (worker runs the pipeline, then `verifying` → `done`)
3. ✅ Strict expiry-based reclaiming: `claim()` refuses active claims until `claim_expires_at` is past
4. ⬜ DB reaper: background task to re-queue jobs with expired claims whose Redis message is gone (Redis redelivery already covers crashed workers)
5. ✅ Claim extraction node
6. ✅ Per-claim verification: relevance ranking, thresholds, citation validation, query rewrite and retry (`write_queries`, `routing`, `MAX_ATTEMPTS`), verdict persistence. Results returned by `GET /checks/{id}` once the job is `done`.
6a. ⬜ Richer snippets for ranking (Wikipedia/web hits are short)
7. ✅ Main LangGraph graph + checkpointer (`graph/*`): worker runs it per job (thread id = job id) and continues retried jobs from their last checkpoint; review endpoint resumes the paused run; report saved to `reports/{id}.md`
8. ✅ Human review: `POST /checks/{id}/review`
9. ✅ Streamlit UI (`apps/ui/app.py`)
10. 🚧 Evals (`evals/`): claim-level eval done; still to do: claim recall, citation validity (LLM judges), video-level `reels.jsonl`
11. ⬜ Deployment (cloud, GCS storage, secrets)

## Known limitations / tech debt
- **Ingest:** easyocr reads only `OCR_LANGUAGES` (default `en`; set `en,hi` for Hindi) and is slow on CPU; OCR failures are logged and skipped. The image-keyframes path for non-Gemini models is not built. Wikipedia still rate-limits (429) during retries; the search cache cuts repeat calls but not first-time ones.
- **Evidence relevance:** direct sources can return loosely related hits; an LLM relevance score threshold filters results before judging, but should be evaluated against labeled claims. Tavily fallback and Google Fact Check each require their respective `TAVILY_API_KEY` / `FACTCHECK_API_KEY`.
- **Schema migrations:** runtime `ALTER TABLE` adds new columns on startup for convenience; add Alembic before production schema changes.
- **Postgres connections:** the job store opens a new connection per call (the checkpointer uses a pool). Switch the job store to a pool under load.
- **Dedupe key:** tracking params in URLs (`utm_*`) give different storage keys for the same video.
- **SSRF:** DNS rebinding is still possible. The real fix is network egress rules in production.
- **Queue timeout / lease tuning:** `QUEUE_RECLAIM_AFTER_S` (default 900) drives both timers; the DB lease is `max(reclaim - 60, reclaim / 2)` so it expires before Redis redelivers. Heartbeat runs at ~lease_seconds/2. A takeover restarts the job and overwrites its artifacts.
- **Local defaults:** the Postgres password is `claimlens`. Set real values in `.env` before sharing.
- **Images:** ML packages (PyTorch, easyocr, whisper) are not in the API or worker images yet.
- **macOS:** hidden `.pth` file, so run with `PYTHONPATH=src:.` outside pytest.
- **Placeholders:** `config/logging.py`, `observability/*`, `evals/judges.py`, `tools/image_search.py` are docstring-only.
- **Real GCS** is untested (fake client only).

## Handy commands
```bash
# Whole stack
docker compose -f infra/docker-compose.yml up -d --build
docker compose -f infra/docker-compose.yml up -d --scale worker=3
docker compose -f infra/docker-compose.yml logs -f worker
docker compose -f infra/docker-compose.yml down

# Local dev without Docker for the app (Postgres/Redis in Docker)
docker compose -f infra/docker-compose.yml up -d postgres redis
PYTHONPATH=src:. uv run uvicorn apps.api.main:app --reload
# Run a worker with a stable id (useful to inspect claimed_by)
WORKER_ID=my-worker-1 PYTHONPATH=src:. uv run python -m apps.worker.main

# Checks
uv run ruff check src apps tests/unit
uv run pytest tests/unit -q
```
API docs: http://localhost:8000/docs

## Update: more trusted sources + India focus
- Wikipedia and Google Fact Check removed from retrieval (`tools/wikipedia.py`, `tools/factcheck_api.py` and `FACTCHECK_API_KEY` deleted); retrieval is Tavily only.
- Europe PMC and PubMed are excluded from retrieval (removed from `trusted_sources` and `SEARCHERS`; `tools/pubmed.py` and `tools/europepmc.py` kept but unused). Earlier: added Europe PMC searcher (`tools/europepmc.py`); Wikidata rejected (label-only matching).
- `tools/trusted_sources.py`: expanded global domains per category and added `india_domains` (PIB, RBI, ICMR, ISRO, Indian fact-checkers, etc.).
- `Claim.india_related` set by the LLM; when true, Tavily fallback searches Indian domains first (`domains_for`).
- Tavily verified live with the key.
