from pathlib import Path

import pytest

from claimlens.domain.schemas import Claim, Evidence, Verdict
from claimlens.ingest import pipeline as pipeline_module
from claimlens.ingest.pipeline import process_video
from claimlens.ingest.video_llm import VideoText


class DummyStorage:
    def __init__(self):
        self.files = {}

    def put_file(self, local_path: Path, key: str) -> None:
        self.files[key] = local_path.read_bytes()

    def get_file(self, key: str, destination: Path) -> Path:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(self.files[key])
        return destination

    def exists(self, key: str) -> bool:
        return key in self.files

    def delete(self, key: str) -> None:
        self.files.pop(key, None)


@pytest.fixture
def tmp_video(tmp_path: Path) -> Path:
    # Create a tiny fake "video" file; pipeline's ffmpeg/whisper calls are monkeypatched below.
    p = tmp_path / "video.mp4"
    p.write_bytes(b"not a real mp4")
    return p


def test_process_video_uploads_transcript_and_keyframes(tmp_video, monkeypatch):
    storage = DummyStorage()

    # Monkeypatch audio extract, asr transcribe, and keyframes to avoid calling heavy binaries.
    def fake_extract(v, out):
        out.write_bytes(b"audio")
        return out

    monkeypatch.setattr("claimlens.ingest.audio.extract_audio", fake_extract)
    monkeypatch.setattr("claimlens.ingest.asr.transcribe", lambda a: "hello world\nthis is a test")

    monkeypatch.setattr(
        "claimlens.ingest.pipeline.extract_claims",
        lambda transcript, ocr_text="": [Claim(id="c1", text="A fact.", source="speech")],
    )
    monkeypatch.setattr("claimlens.ingest.ocr.ocr_frames", lambda *a, **k: "ON SCREEN")

    class FakeVerification:
        def invoke(self, state):
            return {
                "evidence": [
                    Evidence(
                        url="https://e.org/a",
                        title="A",
                        snippet="s",
                        stance="supports",
                    )
                ],
                "verdicts": [
                    Verdict(
                        claim_id=state["claim"].id,
                        label="supported",
                        confidence=0.9,
                        rationale="The source directly supports the claim.",
                        citations=["https://e.org/a"],
                    )
                ],
            }

    monkeypatch.setattr("claimlens.ingest.pipeline.verify_claim", FakeVerification())

    def fake_frames(video, out_dir, every_n_seconds=2):
        out_dir.mkdir(parents=True, exist_ok=True)
        a = out_dir / "frame_0001.jpg"
        a.write_bytes(b"jpg1")
        b = out_dir / "frame_0002.jpg"
        b.write_bytes(b"jpg2")
        return [a, b]

    monkeypatch.setattr("claimlens.ingest.keyframes.extract_keyframes", fake_frames)

    artifacts = process_video("job1", tmp_video, storage)

    assert "transcript" in artifacts and len(artifacts["transcript"]) == 1
    tkey = artifacts["transcript"][0]
    assert storage.exists(tkey)
    assert b"hello world" in storage.files[tkey]

    assert "keyframes" in artifacts and len(artifacts["keyframes"]) == 2
    for k in artifacts["keyframes"]:
        assert storage.exists(k)

    ckey = artifacts["claims"][0]
    assert ckey == "claims/job1.json"
    assert b"A fact." in storage.files[ckey]

    assert storage.files["ocr/job1.txt"] == b"ON SCREEN"
    assert b"https://e.org/a" in storage.files["evidence/job1.json"]
    assert b'"label": "supported"' in storage.files["verdicts/job1.json"]


def stub_verification(monkeypatch):
    class Empty:
        def invoke(self, state):
            return {"evidence": [], "verdicts": []}

    monkeypatch.setattr("claimlens.ingest.pipeline.verify_claim", Empty())


def fake_frames(video, out_dir, every_n_seconds=2):
    out_dir.mkdir(parents=True, exist_ok=True)
    frame = out_dir / "frame_0001.jpg"
    frame.write_bytes(b"jpg")
    return [frame]


def test_ocr_failure_does_not_stop_the_pipeline(tmp_video, monkeypatch):
    storage = DummyStorage()
    seen = {}

    def fake_extract_claims(transcript, ocr_text=""):
        seen["ocr_text"] = ocr_text
        return []

    def broken_ocr(*args, **kwargs):
        raise RuntimeError("model download failed")

    monkeypatch.setattr(
        "claimlens.ingest.audio.extract_audio", lambda v, out: out.write_bytes(b"a") or out
    )
    monkeypatch.setattr("claimlens.ingest.asr.transcribe", lambda a: "spoken")
    monkeypatch.setattr("claimlens.ingest.keyframes.extract_keyframes", fake_frames)
    monkeypatch.setattr("claimlens.ingest.ocr.ocr_frames", broken_ocr)
    monkeypatch.setattr("claimlens.ingest.pipeline.extract_claims", fake_extract_claims)
    stub_verification(monkeypatch)

    artifacts = process_video("job2", tmp_video, storage)

    assert seen["ocr_text"] == ""
    assert storage.files["transcripts/job2.txt"] == b"spoken"
    assert "claims" in artifacts


def test_video_llm_path_skips_asr_and_ocr(tmp_video, monkeypatch):
    storage = DummyStorage()
    seen = {}

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

    def fake_extract_claims(transcript, ocr_text=""):
        seen.update(transcript=transcript, ocr_text=ocr_text)
        return []

    monkeypatch.setattr("claimlens.ingest.pipeline.extract_claims", fake_extract_claims)
    stub_verification(monkeypatch)

    process_video("job3", tmp_video, storage)

    assert seen == {"transcript": "said words", "ocr_text": "shown words"}
    assert storage.files["ocr/job3.txt"] == b"shown words"
