import pytest

from claimlens.domain.schemas import Claim, Evidence, Verdict
from claimlens.graph.verify import judge as judge_module
from claimlens.graph.verify import rank as rank_module
from claimlens.graph.verify import routing as routing_module
from claimlens.graph.verify import subgraph as subgraph_module
from claimlens.graph.verify import write_queries as wq_module


def claim() -> Claim:
    return Claim(id="c1", text="Water boils at 100°C at sea level.", source="speech")


def evidence(index: int, stance: str | None = None) -> Evidence:
    return Evidence(
        url=f"https://example.test/{index}",
        title=f"Source {index}",
        snippet=f"Evidence snippet {index}",
        stance=stance,
    )


class StructuredModel:
    def __init__(self, result):
        self.result = result

    def with_structured_output(self, _schema):
        return self

    def invoke(self, _prompt):
        return self.result


def test_rank_discards_low_relevance_and_sorts_by_score(monkeypatch):
    assessments = [
        rank_module.EvidenceAssessment(index=0, relevance=0.49, stance="supports"),
        rank_module.EvidenceAssessment(index=1, relevance=0.9, stance="refutes"),
        rank_module.EvidenceAssessment(index=2, relevance=0.7, stance="neutral"),
    ]
    monkeypatch.setattr(
        rank_module, "get_verify_llm", lambda: StructuredModel({"assessments": assessments})
    )

    ranked = rank_module.score_evidence(claim(), [evidence(0), evidence(1), evidence(2)])

    assert [item.url for item in ranked] == ["https://example.test/1", "https://example.test/2"]
    assert [item.stance for item in ranked] == ["refutes", "neutral"]


def test_rank_keeps_at_most_five(monkeypatch):
    inputs = [evidence(index) for index in range(6)]
    assessments = [
        rank_module.EvidenceAssessment(index=index, relevance=1, stance="supports")
        for index in range(6)
    ]
    monkeypatch.setattr(
        rank_module, "get_verify_llm", lambda: StructuredModel({"assessments": assessments})
    )

    assert len(rank_module.score_evidence(claim(), inputs)) == 5


def test_rank_rejects_incomplete_assessments(monkeypatch):
    monkeypatch.setattr(
        rank_module,
        "get_verify_llm",
        lambda: StructuredModel(
            {"assessments": [{"index": 0, "relevance": 1, "stance": "supports"}]}
        ),
    )

    with pytest.raises(ValueError, match="every evidence item"):
        rank_module.score_evidence(claim(), [evidence(0), evidence(1)])


def test_judge_returns_nei_without_calling_llm_when_no_evidence_takes_a_stance(monkeypatch):
    monkeypatch.setattr(
        judge_module,
        "get_verify_llm",
        lambda: pytest.fail("judge must not be called without supporting or refuting evidence"),
    )

    verdict = judge_module.make_verdict(claim(), [evidence(0, "neutral"), evidence(1, "neutral")])

    assert verdict.label == "nei"
    assert verdict.citations == []
    assert verdict.rationale.startswith("Found 2 related source(s)")


def test_judge_accepts_a_single_confident_piece_of_evidence(monkeypatch):
    monkeypatch.setattr(
        judge_module,
        "get_verify_llm",
        lambda: StructuredModel(
            Verdict(
                claim_id="c1",
                label="supported",
                confidence=0.9,
                rationale="The source confirms it.",
                citations=["https://example.test/0"],
            )
        ),
    )

    verdict = judge_module.make_verdict(claim(), [evidence(0, "supports")])

    assert verdict.label == "supported"


def test_judge_converts_below_min_confidence_score_to_nei(monkeypatch):
    settings = judge_module.get_settings()
    monkeypatch.setattr(
        judge_module,
        "get_settings",
        lambda: settings.model_copy(update={"min_confidence_score": 0.7}),
    )
    ranked = [evidence(0, "supports"), evidence(1, "refutes")]
    monkeypatch.setattr(
        judge_module,
        "get_verify_llm",
        lambda: StructuredModel(
            Verdict(
                claim_id="c1",
                label="supported",
                confidence=0.6,
                rationale="Some evidence agrees.",
                citations=["https://example.test/0"],
            )
        ),
    )

    verdict = judge_module.make_verdict(claim(), ranked)

    assert verdict.label == "nei"
    assert verdict.citations == ["https://example.test/0"]
    assert "below the minimum confidence score" in verdict.rationale


def judged_as(label: str) -> StructuredModel:
    return StructuredModel(
        Verdict(
            claim_id="c1",
            label=label,
            confidence=0.95,
            rationale="The breakdown by country is not in the evidence.",
            citations=["https://example.test/0"],
        )
    )


@pytest.mark.parametrize("label", ["misleading", "refuted"])
def test_judge_abstains_on_misleading_or_refuted_without_refuting_evidence(monkeypatch, label):
    monkeypatch.setattr(judge_module, "get_verify_llm", lambda: judged_as(label))

    verdict = judge_module.make_verdict(claim(), [evidence(0, "supports"), evidence(1, "neutral")])

    assert verdict.label == "nei"
    assert verdict.citations == ["https://example.test/0"]
    assert f"not marked {label}" in verdict.rationale


@pytest.mark.parametrize("label", ["misleading", "refuted"])
def test_judge_keeps_misleading_or_refuted_with_refuting_evidence(monkeypatch, label):
    monkeypatch.setattr(judge_module, "get_verify_llm", lambda: judged_as(label))

    verdict = judge_module.make_verdict(claim(), [evidence(0, "refutes")])

    assert verdict.label == label


def test_judge_never_returns_citations_outside_ranked_evidence(monkeypatch):
    monkeypatch.setattr(
        judge_module,
        "get_verify_llm",
        lambda: StructuredModel(
            Verdict(
                claim_id="c1",
                label="supported",
                confidence=0.9,
                rationale="The evidence supports it.",
                citations=["https://example.test/0", "https://unranked.test/claim"],
            )
        ),
    )

    verdict = judge_module.make_verdict(claim(), [evidence(0, "supports"), evidence(1, "refutes")])

    assert verdict.label == "supported"
    assert verdict.citations == ["https://example.test/0"]


def test_judge_overrides_claim_id_invented_by_model(monkeypatch):
    monkeypatch.setattr(
        judge_module,
        "get_verify_llm",
        lambda: StructuredModel(
            Verdict(
                claim_id="made_up_slug",
                label="supported",
                confidence=0.9,
                rationale="The evidence supports it.",
                citations=["https://example.test/0"],
            )
        ),
    )

    verdict = judge_module.make_verdict(claim(), [evidence(0, "supports"), evidence(1, "refutes")])

    assert verdict.claim_id == "c1"


def test_verify_subgraph_retrieves_ranks_and_judges(monkeypatch):
    found = [evidence(0), evidence(1)]
    assessments = [
        rank_module.EvidenceAssessment(index=0, relevance=0.9, stance="supports"),
        rank_module.EvidenceAssessment(index=1, relevance=0.8, stance="refutes"),
    ]
    monkeypatch.setattr(subgraph_module, "retrieve_evidence", lambda _claim, queries=None: found)
    monkeypatch.setattr(
        rank_module, "get_verify_llm", lambda: StructuredModel({"assessments": assessments})
    )
    monkeypatch.setattr(
        judge_module,
        "get_verify_llm",
        lambda: StructuredModel(
            Verdict(
                claim_id="c1",
                label="supported",
                confidence=0.9,
                rationale="The cited source supports the claim.",
                citations=["https://example.test/0"],
            )
        ),
    )

    result = subgraph_module.verify_claim.invoke({"claim": claim()})

    assert [item.stance for item in result["evidence"]] == ["supports", "refutes"]
    assert result["verdicts"][0].label == "supported"


def test_routing_retries_until_attempts_are_exhausted():
    weak = {"claim": claim(), "evidence": [evidence(0, "neutral")]}
    strong = {"claim": claim(), "evidence": [evidence(0, "supports"), evidence(1, "neutral")]}

    assert routing_module.route_after_rank({**weak, "attempts": 0}) == "write_queries"
    assert routing_module.route_after_rank({**weak, "attempts": 1}) == "write_queries"
    assert routing_module.route_after_rank({**weak, "attempts": 2}) == "judge"
    assert routing_module.route_after_rank({**strong, "attempts": 0}) == "judge"


def test_write_queries_drops_repeats_and_counts_attempt(monkeypatch):
    result = {"queries": ["spine height  sleep", "Old Query", "disc rehydration overnight"]}
    monkeypatch.setattr(wq_module, "get_llm", lambda: StructuredModel(result))

    update = wq_module.write_queries({"claim": claim(), "queries": ["old query"], "attempts": 1})

    assert update == {
        "queries": ["spine height sleep", "disc rehydration overnight"],
        "attempts": 2,
    }


def test_write_queries_fails_when_nothing_new(monkeypatch):
    monkeypatch.setattr(wq_module, "get_llm", lambda: StructuredModel({"queries": ["x"]}))

    with pytest.raises(ValueError, match="no new queries"):
        wq_module.make_queries(claim(), ["x"])


def test_verify_subgraph_rewrites_queries_when_first_search_is_empty(monkeypatch):
    calls = []

    def fake_retrieve(_claim, queries=None):
        calls.append(queries)
        return [evidence(0), evidence(1)] if queries else []

    def fake_rank(_claim, items):
        return [item.model_copy(update={"stance": "supports"}) for item in items]

    monkeypatch.setattr(subgraph_module, "retrieve_evidence", fake_retrieve)
    monkeypatch.setattr(rank_module, "score_evidence", fake_rank)
    monkeypatch.setattr(
        wq_module, "get_llm", lambda: StructuredModel({"queries": ["better query"]})
    )
    monkeypatch.setattr(
        judge_module,
        "get_verify_llm",
        lambda: StructuredModel(
            Verdict(
                claim_id="c1",
                label="supported",
                confidence=0.9,
                rationale="Supported.",
                citations=["https://example.test/0"],
            )
        ),
    )

    result = subgraph_module.verify_claim.invoke({"claim": claim()})

    assert calls == [None, ["better query"]]
    assert result["attempts"] == 1
    assert result["verdicts"][0].label == "supported"


def test_verify_subgraph_gives_up_after_max_attempts(monkeypatch):
    calls = []
    monkeypatch.setattr(
        subgraph_module, "retrieve_evidence", lambda _c, queries=None: calls.append(queries) or []
    )
    monkeypatch.setattr(wq_module, "get_llm", lambda: StructuredModel({"queries": ["q1", "q2"]}))
    # Make each retry produce a distinct query so the writer never errors.
    counter = iter(range(10))
    monkeypatch.setattr(wq_module, "make_queries", lambda _c, _p: [f"query {next(counter)}"])

    result = subgraph_module.verify_claim.invoke({"claim": claim()})

    assert len(calls) == 3  # first try + two retries
    assert result["verdicts"][0].label == "nei"

