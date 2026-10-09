"""Audio extraction helpers using ffmpeg."""

import subprocess
from pathlib import Path


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
