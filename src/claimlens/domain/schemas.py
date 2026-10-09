"""Data contracts every node reads and writes; also the LLM's structured output."""

from typing import Literal

from pydantic import BaseModel, Field

Category = Literal[
    "health", "science", "history", "politics", "economy", "technology", "sports", "general"
]


Label = Literal["supported", "refuted", "misleading", "nei"]


class Claim(BaseModel):
    id: str
    text: str = Field(description="One atomic, checkable factual statement")
    source: Literal["speech", "on_screen", "caption"]
    category: Category = Field(
        default="general", description="Topic area, used to choose which trusted sources to search"
    )
    india_related: bool = Field(
        default=False, description="True if the claim is about India, Indian people, laws or places"
    )
    search_terms: str = Field(
        default="", description="3-5 key words for a search engine, most important first"
    )
    timestamp_s: float | None = None


class Claims(BaseModel):
    claims: list[Claim]


class Evidence(BaseModel):
    url: str
    title: str
    snippet: str
    publisher: str | None = None
    stance: Literal["supports", "refutes", "neutral"] | None = None


class Verdict(BaseModel):
    claim_id: str
    label: Label
    confidence: float = Field(ge=0, le=1)
    rationale: str
    citations: list[str]


class ReviewDecision(BaseModel):
    """A human reviewer's final label for one claim."""

    claim_id: str
    label: Label
    note: str = Field(default="", max_length=1000)

    def apply_to(self, verdict: Verdict | None) -> Verdict:
        """The claim's published verdict: the reviewer's label replaces the model's."""
        if verdict:
            return verdict.model_copy(update={"label": self.label})
        return Verdict(
            claim_id=self.claim_id,
            label=self.label,
            confidence=1.0,
            rationale=self.note or "Labelled by a human reviewer.",
            citations=[],
        )


class Review(BaseModel):
    """Stored at reviews/{job_id}.json once a reviewer approves a paused check."""

    decisions: list[ReviewDecision]
    reviewed_at: str
