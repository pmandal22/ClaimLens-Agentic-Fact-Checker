"""Typed errors raised by pipeline stages."""


class ClaimLensError(Exception):
    """Base error for ClaimLens."""


class IngestError(ClaimLensError):
    """Audio extraction, ASR, keyframe or OCR failure."""


class ExtractionError(ClaimLensError):
    """Claim extraction failure."""


class RetrievalError(ClaimLensError):
    """Web search or Fact Check Tools API failure."""


class JudgeError(ClaimLensError):
    """Verdict generation failure."""
