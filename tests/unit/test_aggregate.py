import pytest

from claimlens.domain.schemas import Verdict
from claimlens.graph.nodes.aggregate import aggregate


def v(label: str) -> Verdict | None:
    if label == "none":
        return None
    return Verdict(claim_id="c", label=label, confidence=0.9, rationale="r", citations=[])


@pytest.mark.parametrize(
    ("labels", "rating"),
    [
        (["supported", "supported", "nei"], "mostly_supported"),
        (["supported", "nei", "nei"], "inconclusive"),
        (["nei", "none"], "inconclusive"),
        ([], "inconclusive"),
        (["supported", "supported", "refuted"], "mixed"),
        (["supported", "refuted"], "misleading"),
        (["misleading", "nei"], "misleading"),
    ],
)
def test_rating(labels, rating):
    assert aggregate([v(label) for label in labels]).rating == rating


def test_counts_treat_missing_verdict_as_nei():
    overall = aggregate([v("supported"), v("none"), v("refuted")])

    assert overall.counts == {"supported": 1, "refuted": 1, "misleading": 0, "nei": 1}
    assert "3 claim(s)" in overall.summary
