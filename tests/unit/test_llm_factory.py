"""VERIFY_MODEL and EXTRACT_MODEL pick per-task models and fall back to CLAIMLENS_MODEL."""

import pytest

from claimlens.config.settings import Settings
from claimlens.llm import factory


@pytest.fixture
def chosen(monkeypatch):
    calls: list[tuple[str, object]] = []
    monkeypatch.setattr(
        factory,
        "init_chat_model",
        lambda model, **kwargs: calls.append((model, kwargs.get("temperature", "default"))),
    )
    return calls


def use(monkeypatch, **values):
    # Explicit values so model settings loaded into os.environ by other tests don't leak in.
    unset = dict.fromkeys(
        ["verify_model", "verify_temperature", "extract_model", "extract_temperature"]
    )
    settings = Settings(_env_file=None, **{**unset, **values})
    monkeypatch.setattr(factory, "get_settings", lambda: settings)


def test_verify_llm_uses_verify_model_when_set(monkeypatch, chosen):
    use(monkeypatch, claimlens_model="openai:small", verify_model="openai:big", llm_temperature=0)
    factory.get_verify_llm()
    factory.get_llm()
    # The verify model does not inherit LLM_TEMPERATURE.
    assert chosen == [("openai:big", "default"), ("openai:small", 0)]


def test_verify_temperature_is_sent_when_set(monkeypatch, chosen):
    use(monkeypatch, claimlens_model="openai:small", verify_model="openai:big", verify_temperature=0.2)
    factory.get_verify_llm()
    assert chosen == [("openai:big", 0.2)]


def test_extract_llm_uses_extract_model_when_set(monkeypatch, chosen):
    use(monkeypatch, claimlens_model="openai:small", extract_model="openai:big", llm_temperature=0)
    factory.get_extract_llm()
    factory.get_verify_llm()
    assert chosen == [("openai:big", "default"), ("openai:small", 0)]


def test_verify_llm_falls_back_to_claimlens_model(monkeypatch, chosen):
    use(monkeypatch, claimlens_model="openai:small", verify_model=None, llm_temperature=0)
    factory.get_verify_llm()
    assert chosen == [("openai:small", 0)]
