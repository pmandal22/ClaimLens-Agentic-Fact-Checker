"""Read a video's text: ASR + OCR, or Gemini video analysis. The graph's ingest node calls this.

INGEST_PATH picks how text is read from the video:
- asr_ocr (default): faster-whisper on the audio, easyocr on sampled keyframes
- video_llm: one Gemini call returns the transcript and on-screen text (Gemini models only)
"""

import logging
from collections.abc import Sequence
from pathlib import Path

from claimlens.config.settings import get_settings
from claimlens.ingest import asr, audio, keyframes, ocr, video_llm

logger = logging.getLogger(__name__)


def read_video_text(
    local_video_path: Path, workdir: Path, job_id: str = ""
) -> tuple[str, str, Sequence[Path]]:
    """Transcript, on-screen text and keyframe paths (written under workdir), per INGEST_PATH."""
    settings = get_settings()
    frames_dir = workdir / "frames"
    if settings.ingest_path == "video_llm":
        # 1-2) Transcript and on-screen text from one Gemini call.
        video_text = video_llm.analyze_video(local_video_path)
        transcript, ocr_text = video_text.transcript, video_text.on_screen_text
        frames = keyframes.extract_keyframes(local_video_path, frames_dir)
    else:
        # 1) Extract audio, 2) transcribe. A video with no audio track (text-only reels)
        # makes ffmpeg fail, so skip ASR and rely on on-screen text and the caption.
        if audio.has_audio(local_video_path):
            audio_path = workdir / "audio.wav"
            audio.extract_audio(local_video_path, audio_path)
            transcript = asr.transcribe(audio_path)
        else:
            logger.info("job=%s video has no audio track; skipping speech-to-text", job_id)
            transcript = ""

        # 2b) OCR sampled keyframes. A failed OCR must not lose the spoken claims.
        frames = keyframes.extract_keyframes(local_video_path, frames_dir)
        try:
            ocr_text = ocr.ocr_frames(
                frames,
                settings.ocr_max_frames,
                tuple(code.strip() for code in settings.ocr_languages.split(",")),
            )
        except Exception:
            logger.exception("job=%s OCR failed; continuing without on-screen text", job_id)
            ocr_text = ""
    return transcript, ocr_text, frames
