import shutil
import subprocess
from pathlib import Path

import pytest

from claimlens.ingest import audio
from claimlens.ingest import pipeline as pipeline_module
from claimlens.ingest.pipeline import read_video_text
from claimlens.ingest.video_llm import VideoText


@pytest.fixture
def tmp_video(tmp_path: Path) -> Path:
    # Create a tiny fake "video" file; pipeline's ffmpeg/whisper calls are monkeypatched below.
    p = tmp_path / "video.mp4"
    p.write_bytes(b"not a real mp4")
    return p


def fake_frames(video, out_dir, every_n_seconds=2):
    out_dir.mkdir(parents=True, exist_ok=True)
    a = out_dir / "frame_0001.jpg"
    a.write_bytes(b"jpg1")
    b = out_dir / "frame_0002.jpg"
    b.write_bytes(b"jpg2")
    return [a, b]


def fake_extract_audio(video, out):
    out.write_bytes(b"audio")
    return out


def test_asr_ocr_path_returns_transcript_ocr_and_keyframes(tmp_video, tmp_path, monkeypatch):
    seen = {}

    def fake_ocr(frames, *args, **kwargs):
        seen["frames"] = frames
        return "ON SCREEN"

    monkeypatch.setattr("claimlens.ingest.audio.has_audio", lambda v: True)
    monkeypatch.setattr("claimlens.ingest.audio.extract_audio", fake_extract_audio)
    monkeypatch.setattr("claimlens.ingest.asr.transcribe", lambda a: "hello world")
    monkeypatch.setattr("claimlens.ingest.keyframes.extract_keyframes", fake_frames)
    monkeypatch.setattr("claimlens.ingest.ocr.ocr_frames", fake_ocr)
    work = tmp_path / "work"
    work.mkdir()

    transcript, ocr_text, frames = read_video_text(tmp_video, work)

    assert transcript == "hello world"
    assert ocr_text == "ON SCREEN"
    assert [f.name for f in frames] == ["frame_0001.jpg", "frame_0002.jpg"]
    assert all(f.parent == work / "frames" and f.exists() for f in frames)
    assert seen["frames"] == frames


def test_ocr_failure_keeps_the_transcript(tmp_video, tmp_path, monkeypatch):
    def broken_ocr(*args, **kwargs):
        raise RuntimeError("model download failed")

    monkeypatch.setattr("claimlens.ingest.audio.has_audio", lambda v: True)
    monkeypatch.setattr("claimlens.ingest.audio.extract_audio", fake_extract_audio)
    monkeypatch.setattr("claimlens.ingest.asr.transcribe", lambda a: "spoken")
    monkeypatch.setattr("claimlens.ingest.keyframes.extract_keyframes", fake_frames)
    monkeypatch.setattr("claimlens.ingest.ocr.ocr_frames", broken_ocr)

    transcript, ocr_text, _ = read_video_text(tmp_video, tmp_path)

    assert (transcript, ocr_text) == ("spoken", "")


def test_video_without_audio_skips_asr_and_keeps_ocr(tmp_video, tmp_path, monkeypatch):
    def fail(*args, **kwargs):
        raise AssertionError("ffmpeg/ASR must not run on a video with no audio track")

    monkeypatch.setattr("claimlens.ingest.audio.has_audio", lambda v: False)
    monkeypatch.setattr("claimlens.ingest.audio.extract_audio", fail)
    monkeypatch.setattr("claimlens.ingest.asr.transcribe", fail)
    monkeypatch.setattr("claimlens.ingest.keyframes.extract_keyframes", fake_frames)
    monkeypatch.setattr("claimlens.ingest.ocr.ocr_frames", lambda *a, **k: "ON SCREEN")

    transcript, ocr_text, _ = read_video_text(tmp_video, tmp_path)

    assert (transcript, ocr_text) == ("", "ON SCREEN")


def _make_video(path: Path, with_audio: bool) -> Path:
    inputs = ["-f", "lavfi", "-i", "testsrc=duration=1:size=64x64:rate=5"]
    if with_audio:
        inputs += ["-f", "lavfi", "-i", "sine=duration=1", "-shortest"]
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *inputs, str(path)]
    subprocess.run(cmd, check=True)
    return path


@pytest.mark.skipif(not shutil.which("ffprobe"), reason="ffmpeg is not installed")
def test_has_audio_detects_audio_track(tmp_path):
    assert audio.has_audio(_make_video(tmp_path / "sound.mp4", with_audio=True)) is True
    assert audio.has_audio(_make_video(tmp_path / "silent.mp4", with_audio=False)) is False


def test_video_llm_path_skips_asr_and_ocr(tmp_video, tmp_path, monkeypatch):
    def fail(*args, **kwargs):
        raise AssertionError("ASR/OCR must not run on the video_llm path")

    settings = pipeline_module.get_settings().model_copy(update={"ingest_path": "video_llm"})
    monkeypatch.setattr(pipeline_module, "get_settings", lambda: settings)
    monkeypatch.setattr("claimlens.ingest.audio.extract_audio", fail)
    monkeypatch.setattr("claimlens.ingest.asr.transcribe", fail)
    monkeypatch.setattr("claimlens.ingest.ocr.ocr_frames", fail)
    monkeypatch.setattr(
        "claimlens.ingest.video_llm.analyze_video",
        lambda path: VideoText(transcript="said words", on_screen_text="shown words"),
    )
    monkeypatch.setattr("claimlens.ingest.keyframes.extract_keyframes", fake_frames)

    transcript, ocr_text, frames = read_video_text(tmp_video, tmp_path)

    assert (transcript, ocr_text) == ("said words", "shown words")
    assert len(frames) == 2
