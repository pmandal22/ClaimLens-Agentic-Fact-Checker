# ClaimLens

Agentic fact checker for short-form video: extracts factual claims from speech, on-screen text and caption, verifies each against evidence, and returns a cited verdict per claim.

## Quick start

```bash
brew install ffmpeg            # or apt-get install ffmpeg
python -m venv .venv && source .venv/bin/activate
make install
cp .env.example .env           # fill in keys
make test
```

See [Build guide.md](Build%20guide.md) and the design doc for details.

## Web UI

React + TypeScript (Vite) in [apps/web](apps/web). With the API and a worker running:

```bash
make ui          # http://localhost:5173; /api is proxied to CLAIMLENS_API_URL (default http://localhost:8000)
cd apps/web && npm test
```
