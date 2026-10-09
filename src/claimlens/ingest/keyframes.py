"""Extract keyframes from a video using ffmpeg to sample frames.

This is a simple but robust approach: extract one frame every N seconds and keep only the
frames that differ from the previous kept one, so a static shot (e.g. a text slide held for
ten seconds) yields one keyframe instead of five. A more advanced approach would use
scenedetect to find shot boundaries.
"""

import hashlib
import subprocess
from collections.abc import Sequence
from pathlib import Path


def extract_keyframes(video_path: Path, out_dir: Path, every_n_seconds: int = 2) -> Sequence[Path]:
    """Extract unique frames and return the list of file paths written.

    Uses ffmpeg's fps filter to sample frames at 1/every_n_seconds fps, then mpdecimate to
    drop samples that look nearly the same as the last kept frame. Exact duplicates that are
    not adjacent (a shot that comes back later) are removed by content hash.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    # Frames left by an earlier run would be picked up by the glob below.
    for stale in out_dir.glob("frame_*.jpg"):
        stale.unlink()
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
        f"fps={fps},mpdecimate",
        # Write only the frames mpdecimate keeps; constant frame rate would re-duplicate them.
        "-fps_mode",
        "vfr",
        out_pattern,
    ]
    subprocess.run(cmd, check=True)
    # Collect the files in a deterministic order, dropping byte-identical repeats
    files = []
    seen: set[str] = set()
    for frame in sorted(out_dir.glob("frame_*.jpg")):
        digest = hashlib.sha256(frame.read_bytes()).hexdigest()
        if digest in seen:
            frame.unlink()
            continue
        seen.add(digest)
        files.append(frame)
    return files
