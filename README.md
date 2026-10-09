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
