from claimlens.domain.schemas import Claim, Verdict
from claimlens.safety.guards import review_reasons


def claim(category="general") -> Claim:
    return Claim(id="c1", text="x", source="speech", category=category)


def verdict(label="supported", confidence=0.9) -> Verdict:
    return Verdict(claim_id="c1", label=label, confidence=confidence, rationale="", citations=[])


def test_confident_general_claim_is_not_flagged():
    assert review_reasons(claim(), verdict(), 0.7) == []


def test_low_confidence_verdict_is_flagged():
    assert review_reasons(claim(), verdict(confidence=0.5), 0.7) == ["Low confidence (50%)"]


def test_abstentions_are_not_flagged_for_confidence():
    assert review_reasons(claim(), verdict("nei", 0.2), 0.7) == []
    assert review_reasons(claim(), None, 0.7) == []


def test_sensitive_topics_are_always_flagged():
    assert review_reasons(claim("health"), verdict(), 0.7) == ["Sensitive topic: health"]
    assert review_reasons(claim("politics"), None, 0.7) == ["Sensitive topic: politics"]
