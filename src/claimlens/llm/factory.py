"""init_chat_model wrapper: one config value switches provider."""

from typing import Any

from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel

from claimlens.config.settings import get_settings


def _build(model: str, temperature: float | None) -> BaseChatModel:
    # Retries go to the provider client (exponential backoff on 429/5xx) rather than
    # .with_retry(), which would return a Runnable without with_structured_output.
    settings = get_settings()
    kwargs: dict[str, Any] = {
        "max_retries": settings.llm_max_retries,
        "timeout": settings.llm_timeout_s,
    }
    if temperature is not None:
        kwargs["temperature"] = temperature
    return init_chat_model(model, **kwargs)


def get_llm(temperature: float | None = None) -> BaseChatModel:
    settings = get_settings()
    if not settings.claimlens_model:
        raise RuntimeError("CLAIMLENS_MODEL is not set, e.g. 'openai:<model>'")
    temperature = settings.llm_temperature if temperature is None else temperature
    return _build(settings.claimlens_model, temperature)


def _task_llm(model: str | None, temperature: float | None) -> BaseChatModel:
    if not model:
        return get_llm()
    # Separate from LLM_TEMPERATURE: stronger models often accept only their default.
    return _build(model, temperature)


def get_verify_llm() -> BaseChatModel:
    """Model for ranking evidence and judging: VERIFY_MODEL if set, else CLAIMLENS_MODEL."""
    settings = get_settings()
    return _task_llm(settings.verify_model, settings.verify_temperature)


def get_extract_llm() -> BaseChatModel:
    """Model for extracting claims: EXTRACT_MODEL if set, else CLAIMLENS_MODEL."""
    settings = get_settings()
    return _task_llm(settings.extract_model, settings.extract_temperature)
