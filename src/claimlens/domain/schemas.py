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
