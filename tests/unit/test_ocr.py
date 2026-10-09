from pathlib import Path

from claimlens.ingest import ocr


def test_dedupe_removes_repeats_fragments_and_noise():
    lines = [
        "Humans once had",
        "humans once had!",
        "Humans once had a tail",
        "Humans once had a tail",
        "ab",
        "—",
        "Source: NIH",
    ]

    assert ocr.dedupe_lines(lines) == ["Humans once had a tail", "Source: NIH"]


def test_sample_frames_spreads_evenly_and_caps():
    frames = [Path(f"f{i}.jpg") for i in range(10)]

    assert ocr.sample_frames(frames, 5) == [frames[0], frames[2], frames[4], frames[6], frames[8]]
    assert ocr.sample_frames(frames, 20) == frames
    assert ocr.sample_frames(frames, 0) == frames


def test_ocr_frames_filters_low_confidence_and_dedupes(monkeypatch):
    class FakeReader:
        def readtext(self, path, detail=1, paragraph=False):
            return [
                ([], "Water boils at 100C", 0.95),
                ([], "blurry garbage", 0.1),
            ]

    monkeypatch.setattr(ocr, "_reader", lambda languages: FakeReader())

    text = ocr.ocr_frames([Path("a.jpg"), Path("b.jpg")], max_frames=2)

    assert text == "Water boils at 100C"
