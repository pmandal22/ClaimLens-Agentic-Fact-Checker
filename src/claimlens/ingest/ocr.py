"""OCR keyframes with easyocr and de-duplicate repeated lines."""

import re
from collections.abc import Iterable, Sequence
from functools import lru_cache
from pathlib import Path
from typing import Any

MIN_CONFIDENCE = 0.4
MIN_ALNUM_CHARS = 3


@lru_cache(maxsize=4)
def _reader(languages: tuple[str, ...]) -> Any:
    try:
        import easyocr
    except Exception as exc:  # pragma: no cover - environment-dependent
        raise RuntimeError("easyocr is not installed; install the 'pipeline' dependency group") from exc
    return easyocr.Reader(list(languages), gpu=False, verbose=False)


def _normalize(line: str) -> str:
    return re.sub(r"\W+", " ", line.casefold()).strip()


def dedupe_lines(lines: Iterable[str]) -> list[str]:
    """Drop repeats and fragments so burned-in captions that persist across frames appear once.

    A line already contained in a kept line is skipped; a kept line contained in a newer,
    longer line (a caption revealed word by word) is replaced by it.
    """
    kept: list[tuple[str, str]] = []  # (normalized, original)
    for line in lines:
        text = " ".join(line.split())
        norm = _normalize(text)
        if sum(ch.isalnum() for ch in norm) < MIN_ALNUM_CHARS:
            continue
        if any(norm in existing for existing, _ in kept):
            continue
        kept = [(n, o) for n, o in kept if n not in norm]
        kept.append((norm, text))
    return [original for _, original in kept]


def sample_frames(frames: Sequence[Path], max_frames: int) -> list[Path]:
    """Pick at most max_frames, evenly spread across the video."""
    if max_frames <= 0 or len(frames) <= max_frames:
        return list(frames)
    step = len(frames) / max_frames
    return [frames[int(i * step)] for i in range(max_frames)]


def ocr_frame(frame: Path, languages: tuple[str, ...] = ("en",)) -> list[str]:
    """Return the confidently read text lines of one image."""
    results = _reader(languages).readtext(str(frame), detail=1, paragraph=False)
    return [text for _box, text, confidence in results if confidence >= MIN_CONFIDENCE]


def ocr_frames(
    frames: Sequence[Path], max_frames: int = 30, languages: tuple[str, ...] = ("en",)
) -> str:
    """OCR evenly sampled frames and return the de-duplicated text, one line per row."""
    lines: list[str] = []
    for frame in sample_frames(frames, max_frames):
        lines.extend(ocr_frame(frame, languages))
    return "\n".join(dedupe_lines(lines))
