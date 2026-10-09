# ClaimLens: WIP Tracker

Last updated: 2026-10-08T21:20:00+05:30

Status key: ✅ done · 🚧 in progress · ⬜ not started

## Flow today
```
Client → POST /checks → API validates URL → job row (Postgres) + message (Redis)
Worker ← Redis → downloads video → saves to storage → status "ingesting"
  → audio → transcript → claims → [retrieve → rank → (weak? write_queries → retrieve, max 3 tries) → judge]
  → keyframes → status "verifying" → "done"
Client → GET /checks/{id} → status, plus per-claim results (claim, ranked evidence, verdict) once "done"
```
Artifacts: `transcripts/{id}.txt`, `ocr/{id}.txt`, `claims/{id}.json`, `evidence/{id}.json`, `verdicts/{id}.json`, `keyframes/{id}/`.
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

### API
- ✅ `POST /checks` (202), `GET /checks/{id}`, `GET /health` (`apps/api/`)
- ✅ 422 for bad input, 503 when the queue is down

### Infra
- ✅ Docker compose: postgres, redis, api, worker (`infra/docker-compose.yml`)
- ✅ `api.Dockerfile`, `worker.Dockerfile`, `postgres.Dockerfile`, `.dockerignore`
- ✅ Dependencies split into `dev`, `pipeline`, `ui` groups to keep images small
- ✅ Verified end to end in containers with 2 workers

### Quality
- ✅ 93 unit tests passing, ruff clean
- ✅ Job-store tests run against both SQLite and Postgres

- ✅ Pipeline wired into the worker: ffmpeg audio, faster-whisper ASR (`asr` dep group), keyframes
- ✅ Claim extraction node (`graph/nodes/extract_claims.py`, `llm` dep group); worker gets `CLAIMLENS_MODEL` + `OPENAI_API_KEY` via compose (`--env-file .env`)
- ✅ Overall rating (`graph/nodes/aggregate.py`): `aggregate()` rates the reel `mostly_supported` / `mixed` / `misleading` / `inconclusive` from claim verdicts (nei ignored for rating; missing verdict = nei); `GET /checks/{id}` returns it as `overall`
- ✅ Optional Jev ranker (`graph/verify/jev_rank.py`, `RANKER=jev`): per-item relevance (probability-weighted score) + stance via TypeSafe; items below `JEV_MIN_CONFIDENCE` or failed calls fall back to the LLM ranker. Live on 2 claims it agreed with the LLM on kept items (~2 s each); needs evals before changing the default
- ✅ `ResponseCache` wired into retrieval (`retrieve._cached`, `tools/cache.get_cache`): per-source and Tavily searches cached in Redis (24 h, `SEARCH_CACHE_ENABLED`, `SEARCH_CACHE_TTL_S`); failures are never cached; Redis down or cache errors fall back to live calls. Live: second identical retrieval 3.6 s -> 0.0 s
- ✅ Claim-level eval (`evals/run_eval.py`, `make eval`): 24 labeled claims in `evals/datasets/claims.jsonl`; retrieves once (cached in `.evidence_cache.json`) and compares `--rankers llm,jev` on identical evidence (accuracy, abstain rate, confident-wrong, time). First results: accuracy 0.54-0.62 overall, ~0.92 when answering, confident-wrong 0.04 for both; llm vs jev differ by 1-2 claims and swing between identical runs, so no winner yet. Abstentions are mostly retrieval gaps (title-only snippets, Wikipedia 429s cached into evidence). Not yet covered: claim recall, citation validity (LLM judges), video-level `reels.jsonl`
- ✅ Claims carry a `category` and `search_terms` (same LLM call). Evidence retrieval (`graph/verify/retrieve.py`): trusted sources per category first (Wikipedia, PubMed, Google Fact Check if key set; map in `tools/trusted_sources.py`), Tavily only if fewer than `min_trusted_evidence` (2) results, first limited to the category's trusted domains. Saved to `evidence/{id}.json`
- ✅ Redis-backed query response cache with TTL, provider/options scoping, and `Evidence` serialization (`tools/cache.py`)
- ✅ Per-claim verification graph: LLM relevance/stance ranking, relevance threshold (`EVIDENCE_RELEVANCE_THRESHOLD`, default 0.5), minimum strong evidence threshold (`MIN_VERDICT_EVIDENCE`, default 2), and confidence floor (`CONFIDENCE_FLOOR`, default 0.7). Low-confidence or unsupported-citation results abstain as `nei`; ranked evidence and verdicts are saved by the worker.
- ✅ Live pipeline run on YouTube Short `c1b6adSrK3g`: downloaded and processed successfully; extracted 5 claims, retrieved 36 evidence results, and produced 76 keyframes. Temporary video and artifacts were cleaned up after the run.
- ✅ Fixed: missing `run_once`, SQLite column mismatch, YouTube format selection, temp dir lifetime

## In progress 🚧
- Add stricter reclaim policy and a reaper for expired claims (planned next)

## Next up ⬜
1. ✅ Ingestion pipeline: `INGEST_PATH=asr_ocr` (faster-whisper + easyocr on sampled keyframes, de-duplicated) or `video_llm` (Gemini Files API returns transcript + on-screen text); on-screen text feeds claim extraction and is stored at `ocr/{id}.txt`. Live-tested both on one reel: OCR is noisier (~2 min on CPU for 30 frames incl. first-run model download); Gemini is cleaner (~1 min).
2. ✅ Move jobs past `ingesting` (worker runs the pipeline, then `verifying` → `done`)
3. ⬜ Strict expiry-based reclaiming: make claim() refuse to steal active claims unless `claim_expires_at` is past
4. ⬜ DB reaper: background task to release or re-queue jobs with expired claims
5. ✅ Claim extraction node
6. ✅ Per-claim verification: relevance ranking, thresholds, citation validation, query rewrite and retry (`write_queries`, `routing`, `MAX_ATTEMPTS`), verdict persistence. Results returned by `GET /checks/{id}` once the job is `done`.
6a. ⬜ Richer snippets for ranking (Wikipedia/web hits are short)
7. ✅ Main LangGraph graph + checkpointer (`graph/*`): worker runs it per job (thread id = job id) and continues retried jobs from their last checkpoint; review endpoint resumes the paused run; report saved to `reports/{id}.md`
8. ⬜ Human review: `POST /checks/{id}/review`
9. ⬜ Streamlit UI (`apps/ui/app.py`)
10. ⬜ Evals (`evals/`)
11. ⬜ Deployment (cloud, GCS storage, secrets)

## Known limitations / tech debt
- **Ingest:** easyocr reads only `OCR_LANGUAGES` (default `en`; set `en,hi` for Hindi) and is slow on CPU; OCR failures are logged and skipped. The image-keyframes path for non-Gemini models is not built. Wikipedia still rate-limits (429) during retries; the cache is not wired in yet.
- **Evidence relevance:** direct sources can return loosely related hits; an LLM relevance score threshold filters results before judging, but should be evaluated against labeled claims. Tavily fallback and Google Fact Check each require their respective `TAVILY_API_KEY` / `FACTCHECK_API_KEY`.
- **Schema migrations:** runtime `ALTER TABLE` adds new columns on startup for convenience; add Alembic before production schema changes.
- **Postgres connections:** a new connection per call. Switch to a pool under load.
- **Dedupe key:** tracking params in URLs (`utm_*`) give different storage keys for the same video.
- **SSRF:** DNS rebinding is still possible. The real fix is network egress rules in production.
- **Queue timeout / lease tuning:** `QUEUE_RECLAIM_AFTER_S` (default 900) must exceed the slowest job and `lease_seconds` should be tuned. Heartbeat runs at ~lease_seconds/2.
- **Local defaults:** the Postgres password is `claimlens`. Set real values in `.env` before sharing.
- **Images:** ML packages (PyTorch, easyocr, whisper) are not in the API or worker images yet.
- **macOS:** hidden `.pth` file, so run with `PYTHONPATH=src:.` outside pytest.
- **Placeholders:** `apps/ui/app.py`, `config/logging.py` are docstring-only.
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
- Europe PMC and PubMed are excluded from retrieval (removed from `trusted_sources` and `SEARCHERS`; `tools/pubmed.py` and `tools/europepmc.py` kept but unused). Earlier: added Europe PMC searcher (`tools/europepmc.py`); Wikidata rejected (label-only matching).
- `tools/trusted_sources.py`: expanded global domains per category and added `india_domains` (PIB, RBI, ICMR, ISRO, Indian fact-checkers, etc.).
- `Claim.india_related` set by the LLM; when true, Tavily fallback searches Indian domains first (`domains_for`).
- 95 unit tests pass; Tavily verified live with the key.
