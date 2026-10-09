"""init_chat_model wrapper: one config value switches provider."""

from typing import Any

from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel

from claimlens.config.settings import get_settings


def get_llm(temperature: float | None = None) -> BaseChatModel:
    # Retries go to the provider client (exponential backoff on 429/5xx) rather than
    # .with_retry(), which would return a Runnable without with_structured_output.
    settings = get_settings()
    if not settings.claimlens_model:
        raise RuntimeError("CLAIMLENS_MODEL is not set, e.g. 'openai:<model>'")
    kwargs: dict[str, Any] = {
        "max_retries": settings.llm_max_retries,
        "timeout": settings.llm_timeout_s,
    }
    temperature = settings.llm_temperature if temperature is None else temperature
    if temperature is not None:
        kwargs["temperature"] = temperature
    return init_chat_model(settings.claimlens_model, **kwargs)
