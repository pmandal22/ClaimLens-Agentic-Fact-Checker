"""Audio extraction helpers using ffmpeg."""

import subprocess
from pathlib import Path


def has_audio(video_path: Path) -> bool:
    """True if the video has at least one audio stream. Uses ffprobe; raises on failure."""
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "a",
        "-show_entries",
        "stream=index",
        "-of",
        "csv=p=0",
        str(video_path),
    ]
    result = subprocess.run(cmd, check=True, capture_output=True, text=True)
    return bool(result.stdout.strip())


def extract_audio(video_path: Path, out_wav: Path) -> Path:
    """Extract a single-channel WAV suitable for ASR.

    Uses ffmpeg; raises CalledProcessError on failure.
    """
    out_wav.parent.mkdir(parents=True, exist_ok=True)
    # -y overwrite, -vn ignore video, -ac 1 mono, -ar 16000 sample rate
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(video_path),
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        str(out_wav),
    ]
    subprocess.run(cmd, check=True)
    return out_wav
