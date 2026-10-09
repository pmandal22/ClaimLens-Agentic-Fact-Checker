# Worker image: pulls jobs from Redis, downloads videos, runs the reel graph.
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
    HF_HOME=/opt/models/hf \
    EASYOCR_MODULE_PATH=/opt/models/easyocr

WORKDIR /app

# Dependencies first: this layer is cached until pyproject.toml or uv.lock change.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --group asr --group ocr --group llm --no-group pipeline --no-install-project

# Bake the OCR and Whisper models into the image so workers never download them at runtime.
# Before COPY src, so code changes don't repeat the download. OCR_LANGUAGES comes from .env.
ARG OCR_LANGUAGES=en
RUN useradd --create-home app \
    && python -c "import easyocr; easyocr.Reader('${OCR_LANGUAGES}'.split(','), gpu=False, verbose=False)" \
    && python -c "from faster_whisper import WhisperModel; WhisperModel('small', device='cpu', compute_type='int8')" \
    && chown -R app /opt/models

COPY src ./src
COPY apps ./apps
RUN uv sync --frozen --no-dev --group asr --group ocr --group llm --no-group pipeline

# Run as a non-root user; /data is where local video storage is mounted.
RUN mkdir -p /data && chown app /data
USER app

CMD ["python", "-m", "apps.worker.main"]
