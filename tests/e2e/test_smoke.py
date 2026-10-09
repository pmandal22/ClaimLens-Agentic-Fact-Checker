"""Step 1 check: config loads and the configured LLM answers once. Makes one real API call."""
import pytest
from pydantic import ValidationError

from claimlens.config.settings import get_settings
from claimlens.llm.factory import get_llm


@pytest.fixture(scope="module")
def settings():
    try:
        s = get_settings()
    except ValidationError:
        pytest.skip("CLAIMLENS_MODEL not set in .env")
    if not s.claimlens_model:
        pytest.skip("CLAIMLENS_MODEL not set in .env")
    return s


def test_llm_responds(settings):
    reply = get_llm().invoke("Reply with the single word: ok")
    assert "ok" in str(reply.content).lower()
