"""Extract keyframes from a video using ffmpeg to sample frames.

This is a simple but robust approach: extract one frame every N seconds and let later
components pick the best ones. A more advanced approach would use scenedetect to find
shot boundaries.
"""

import subprocess
from collections.abc import Sequence
from pathlib import Path


def extract_keyframes(video_path: Path, out_dir: Path, every_n_seconds: int = 2) -> Sequence[Path]:
    """Extract frames and return the list of file paths written.

    Uses ffmpeg's fps filter to sample frames at 1/every_n_seconds fps.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    out_pattern = str((out_dir / "frame_%04d.jpg").resolve())
    fps = 1 / every_n_seconds
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(video_path),
        "-vf",
        f"fps={fps}",
        out_pattern,
    ]
    subprocess.run(cmd, check=True)
    # Collect the files in a deterministic order
    files = sorted(out_dir.glob("frame_*.jpg"))
    return files
