"""Gemini direct-video path: transcript plus on-screen text in one call."""

import contextlib
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from langchain_core.messages import HumanMessage
from pydantic import BaseModel

from claimlens.config.settings import get_settings
from claimlens.llm.factory import get_video_llm

PROMPT_PATH = Path(__file__).resolve().parents[1] / "llm" / "prompts" / "video_ingest.v1.md"
UPLOAD_TIMEOUT_S = 120
POLL_INTERVAL_S = 2.0


class VideoText(BaseModel):
    transcript: str = ""
    on_screen_text: str = ""


@contextlib.contextmanager
def _uploaded(video_path: Path) -> Iterator[Any]:
    """Upload the video to the Gemini Files API, wait until it is usable, delete it after."""
    from google import genai

    client = genai.Client()  # reads GOOGLE_API_KEY from the environment
    uploaded = client.files.upload(file=str(video_path), config={"mime_type": "video/mp4"})
    try:
        deadline = time.monotonic() + UPLOAD_TIMEOUT_S
        while uploaded.state is not None and uploaded.state.name == "PROCESSING":
            if time.monotonic() > deadline:
                raise TimeoutError("Gemini did not finish processing the video in time")
            time.sleep(POLL_INTERVAL_S)
            uploaded = client.files.get(name=uploaded.name)
        if uploaded.state is not None and uploaded.state.name == "FAILED":
            raise RuntimeError("Gemini could not process the video")
        yield uploaded
    finally:
        with contextlib.suppress(Exception):
            client.files.delete(name=uploaded.name)


def analyze_video(video_path: Path) -> VideoText:
    """Send the whole video to Gemini and return its transcript and on-screen text."""
    settings = get_settings()
    model = settings.video_model or settings.claimlens_model or ""
    if not model.startswith("google_genai:"):
        raise RuntimeError(
            "INGEST_PATH=video_llm needs a Gemini model (VIDEO_MODEL=google_genai:...); "
            "use INGEST_PATH=asr_ocr with other providers"
        )
    with _uploaded(video_path) as uploaded:
        message = HumanMessage(
            content=[
                {"type": "text", "text": PROMPT_PATH.read_text(encoding="utf-8")},
                {"type": "media", "file_uri": uploaded.uri, "mime_type": "video/mp4"},
            ]
        )
        result = get_video_llm().with_structured_output(VideoText).invoke([message])
    video_text = VideoText.model_validate(result)
    return VideoText(
        transcript=video_text.transcript.strip(),
        on_screen_text=video_text.on_screen_text.strip(),
    )
