import shutil
import subprocess
from pathlib import Path

import pytest

from claimlens.ingest.keyframes import extract_keyframes

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")


def make_video(path: Path, colors: list[str], seconds: int = 6) -> Path:
    inputs = []
    for color in colors:
        inputs += ["-f", "lavfi", "-i", f"color={color}:s=160x120:d={seconds}"]
    streams = "".join(f"[{i}]" for i in range(len(colors)))
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *inputs,
         "-filter_complex", f"{streams}concat=n={len(colors)}:v=1",
         "-pix_fmt", "yuv420p", str(path)],
        check=True,
    )
    return path


def test_static_shots_and_repeats_yield_one_frame_each(tmp_path):
    # 18s sampled every 2s is 9 frames, but only two distinct images.
    video = make_video(tmp_path / "v.mp4", ["red", "blue", "red"])

    frames = extract_keyframes(video, tmp_path / "frames")

    assert len(frames) == 2
    assert sorted((tmp_path / "frames").glob("frame_*.jpg")) == list(frames)


def test_frames_from_an_earlier_run_are_not_returned(tmp_path):
    out_dir = tmp_path / "frames"
    out_dir.mkdir()
    (out_dir / "frame_0099.jpg").write_bytes(b"stale")
    video = make_video(tmp_path / "v.mp4", ["green"])

    frames = extract_keyframes(video, out_dir)

    assert [f.name for f in frames] == ["frame_0001.jpg"]
