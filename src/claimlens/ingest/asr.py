"""ASR wrapper using faster-whisper when available.

This file keeps the dependency import local so the codebase works without installing the heavy
pipeline extras. Tests monkeypatch the transcribe function where needed.
"""

from pathlib import Path


def transcribe(audio_path: Path) -> str:
    """Return a plain-text transcript for the given audio file.

    Attempts to use faster_whisper (locally). If the import isn't available, raise a
    RuntimeError with a helpful message so tests and the caller can mock/replace it.
    """
    try:
        from faster_whisper import WhisperModel
    except Exception as exc:  # pragma: no cover - environment-dependent
        raise RuntimeError(
            "faster-whisper is not installed; install the 'pipeline' dependency group or "
            "provide another ASR implementation"
        ) from exc

    # Use a small default model if the environment doesn't provide one. The caller may
    # change this by setting environment variables or swapping out this function.
    model = WhisperModel("small", device="cpu", compute_type="int8")
    segments, _info = model.transcribe(str(audio_path), beam_size=5)
    texts: list[str] = []
    for segment in segments:
        # segment.text is the spoken text for that segment
        texts.append(segment.text)
    return "\n".join(t.strip() for t in texts if t.strip())
