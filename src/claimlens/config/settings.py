"""Settings loaded from the environment / .env file."""

from functools import lru_cache
from typing import Literal

from dotenv import load_dotenv
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", env_parse_none_str="none")

    # e.g. "google_genai:<gemini-model>", "openai:<gpt-model>", "anthropic:<claude-model>"
    claimlens_model: str | None = None
    # Optional stronger model for ranking evidence and judging verdicts; defaults to claimlens_model
    verify_model: str | None = None
    # Applies only with verify_model; None sends no temperature (the model's default)
    verify_temperature: float | None = None
    # Optional stronger model for extracting claims; same fallback and temperature rules as verify
    extract_model: str | None = None
    extract_temperature: float | None = None
    ingest_path: Literal["asr_ocr", "video_llm"] = "asr_ocr"  # video_llm is Gemini only
    ocr_max_frames: int = Field(default=30, ge=0)  # frames sampled for OCR; 0 means all
    ocr_languages: str = "en"  # comma-separated easyocr codes, e.g. "en,hi"

    # "none" omits it, for models with fixed sampling (e.g. some Gemini models)
    llm_temperature: float | None = 0
    llm_max_retries: int = 6  # backoff on 429s; free tiers rate-limit hard
    llm_timeout_s: float = 60

    max_claims_per_reel: int = 5
    max_keyframes: int = 10
    max_attempts: int = 3  # first try + 2 retries
    # Verdicts below this confidence abstain as nei and are flagged for review
    min_confidence_score: float = Field(default=0.7, ge=0, le=1)
    # When set, POST /checks/{id}/review needs this in the X-Review-Token header.
    review_token: str | None = None
    min_trusted_evidence: int = 2  # stop searching further queries once this many results are found
    evidence_relevance_threshold: float = Field(default=0.5, ge=0, le=1)
    evidence_per_source: int = 3

    gcs_bucket: str | None = None  # set to store videos in Google Cloud Storage
    local_storage_dir: str = "data/storage"  # used when gcs_bucket is not set

    checkpoint_db: str = "claimlens.db"
    jobs_db: str = "claimlens_jobs.db"

    redis_url: str = "redis://localhost:6379/0"
    search_cache_enabled: bool = True
    search_cache_ttl_s: int = Field(default=24 * 60 * 60, gt=0)
    # Must be longer than the slowest job: a message idle this long is given to another worker.
    queue_reclaim_after_s: int = 900
    postgres_url: str | None = None

    tavily_api_key: str | None = None


@lru_cache
def get_settings() -> Settings:
    # Provider SDKs read their keys (GOOGLE_API_KEY, ...) from os.environ, not from Settings
    load_dotenv()
    return Settings()
