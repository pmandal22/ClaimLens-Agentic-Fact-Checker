"""Extract atomic, checkable claims from transcript, OCR text and caption."""

from pathlib import Path

from claimlens.config.settings import get_settings
from claimlens.domain.schemas import Claim, Claims
from claimlens.llm.factory import get_extract_llm

PROMPT_PATH = Path(__file__).resolve().parents[2] / "llm" / "prompts" / "extract_claims.v1.md"


def extract_claims(transcript: str, caption: str = "", ocr_text: str = "") -> list[Claim]:
    """Ask the LLM for claims; returns at most max_claims_per_reel, with ids c1, c2, ..."""
    sources = {"Transcript (speech)": transcript, "On-screen text": ocr_text, "Caption": caption}
    material = "\n\n".join(
        f"{name}:\n{text.strip()}" for name, text in sources.items() if text.strip()
    )
    if not material:
        return []

    max_claims = get_settings().max_claims_per_reel
    rules = PROMPT_PATH.read_text(encoding="utf-8")
    prompt = f"{rules}\nReturn at most {max_claims} claims.\n\n{material}"
    result = get_extract_llm().with_structured_output(Claims).invoke(prompt)
    claims = Claims.model_validate(result).claims[:max_claims]
    # Ids are assigned here so they are stable and unique whatever the model returned.
    return [claim.model_copy(update={"id": f"c{i}"}) for i, claim in enumerate(claims, start=1)]
