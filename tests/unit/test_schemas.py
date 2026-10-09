import pytest
from pydantic import ValidationError

from claimlens.domain.schemas import Claim, Verdict


def test_claim_valid():
    claim = Claim(id="c1", text="The Eiffel Tower is 330 m tall.", source="speech")
    assert claim.timestamp_s is None


def test_verdict_rejects_confidence_above_one():
    with pytest.raises(ValidationError):
        Verdict(claim_id="c1", label="supported", confidence=1.5, rationale="", citations=[])
