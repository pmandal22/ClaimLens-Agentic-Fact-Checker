import contextlib
from types import SimpleNamespace

import pytest
from langchain_core.messages import HumanMessage

from claimlens.ingest import video_llm


class FakeModel:
    def __init__(self, result):
        self.result = result
        self.messages = None

    def with_structured_output(self, _schema):
        return self

    def invoke(self, messages):
        self.messages = messages
        return self.result


def use_model(monkeypatch, name):
    settings = video_llm.get_settings().model_copy(update={"claimlens_model": name})
    monkeypatch.setattr(video_llm, "get_settings", lambda: settings)


def test_analyze_video_sends_uploaded_video_and_returns_both_channels(monkeypatch, tmp_path):
    video = tmp_path / "v.mp4"
    video.write_bytes(b"video-bytes")
    model = FakeModel({"transcript": " spoken ", "on_screen_text": "shown\n"})
    use_model(monkeypatch, "google_genai:gemini-test")
    monkeypatch.setattr(video_llm, "get_llm", lambda: model)
    monkeypatch.setattr(
        video_llm,
        "_uploaded",
        contextlib.contextmanager(lambda path: (yield SimpleNamespace(uri="files/abc"))),
    )

    result = video_llm.analyze_video(video)

    assert (result.transcript, result.on_screen_text) == ("spoken", "shown")
    [message] = model.messages
    assert isinstance(message, HumanMessage)
    assert message.content[1] == {"type": "media", "file_uri": "files/abc", "mime_type": "video/mp4"}


def test_non_gemini_model_is_rejected(monkeypatch, tmp_path):
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")
    use_model(monkeypatch, "openai:gpt-test")

    with pytest.raises(RuntimeError, match="Gemini"):
        video_llm.analyze_video(video)


def test_uploaded_file_is_deleted_even_when_the_model_fails(monkeypatch, tmp_path):
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")
    deleted = []
    active = SimpleNamespace(name="files/abc", uri="u", state=SimpleNamespace(name="ACTIVE"))
    files = SimpleNamespace(
        upload=lambda file, config: active, delete=lambda name: deleted.append(name)
    )
    monkeypatch.setattr("google.genai.Client", lambda: SimpleNamespace(files=files))

    with pytest.raises(ValueError), video_llm._uploaded(video):
        raise ValueError("model failed")

    assert deleted == ["files/abc"]
