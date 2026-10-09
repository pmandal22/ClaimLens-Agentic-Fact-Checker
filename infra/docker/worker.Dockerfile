# Worker image: pulls jobs from Redis, downloads videos, stores them.
# Build from the project root:  docker build -f infra/docker/worker.Dockerfile .
FROM python:3.12-slim

# ffmpeg: yt-dlp needs it to merge separate audio/video streams (and later for audio extraction).
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH" \
    HF_HOME=/data/hf

WORKDIR /app

# Dependencies first: this layer is cached until pyproject.toml or uv.lock change.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --group asr --group llm --no-group pipeline --no-group ui --no-install-project

COPY src ./src
COPY apps ./apps
RUN uv sync --frozen --no-dev --group asr --group llm --no-group pipeline --no-group ui

# Run as a non-root user; /data is where local video storage is mounted.
RUN useradd --create-home app && mkdir -p /data && chown app /data
USER app

CMD ["python", "-m", "apps.worker.main"]
