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
        rank_module, "get_llm", lambda: StructuredModel({"assessments": assessments})
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
        rank_module, "get_llm", lambda: StructuredModel({"assessments": assessments})
    )

    assert len(rank_module.score_evidence(claim(), inputs)) == 5


def test_rank_rejects_incomplete_assessments(monkeypatch):
    monkeypatch.setattr(
        rank_module,
        "get_llm",
        lambda: StructuredModel(
            {"assessments": [{"index": 0, "relevance": 1, "stance": "supports"}]}
        ),
    )

    with pytest.raises(ValueError, match="every evidence item"):
        rank_module.score_evidence(claim(), [evidence(0), evidence(1)])


def test_judge_returns_nei_without_calling_llm_when_evidence_is_insufficient(monkeypatch):
    monkeypatch.setattr(
        judge_module,
        "get_llm",
        lambda: pytest.fail("judge must not be called for insufficient evidence"),
    )

    verdict = judge_module.make_verdict(claim(), [evidence(0, "supports"), evidence(1, "neutral")])

    assert verdict.label == "nei"
    assert verdict.citations == []


def test_judge_converts_below_floor_confidence_to_nei(monkeypatch):
    settings = judge_module.get_settings()
    monkeypatch.setattr(
        judge_module,
        "get_settings",
        lambda: settings.model_copy(update={"confidence_floor": 0.7}),
    )
    ranked = [evidence(0, "supports"), evidence(1, "refutes")]
    monkeypatch.setattr(
        judge_module,
        "get_llm",
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
    assert "below the required floor" in verdict.rationale


def test_judge_never_returns_citations_outside_ranked_evidence(monkeypatch):
    monkeypatch.setattr(
        judge_module,
        "get_llm",
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
        "get_llm",
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
        rank_module, "get_llm", lambda: StructuredModel({"assessments": assessments})
    )
    monkeypatch.setattr(
        judge_module,
        "get_llm",
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
    strong = {"claim": claim(), "evidence": [evidence(0, "supports"), evidence(1, "refutes")]}

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
        "get_llm",
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


class FakeJevResult:
    def __init__(self, probabilities, stance, confidence):
        from types import SimpleNamespace

        self.scores = {
            "relevance": SimpleNamespace(
                probabilities=probabilities,
                confidence=confidence,
                score=max(probabilities, key=probabilities.get),
            )
        }
        self.choices = {"stance": SimpleNamespace(choice=stance, confidence=confidence)}


def jev_settings(monkeypatch):
    from claimlens.config.settings import get_settings

    monkeypatch.setattr(
        rank_module, "get_settings", lambda: get_settings().model_copy(update={"ranker": "jev"})
    )


def test_jev_probability_weighted_relevance_and_threshold(monkeypatch):
    jev_settings(monkeypatch)
    by_text = {
        "Evidence snippet 0": FakeJevResult({0: 0, 1: 0, 2: 0, 3: 1.0}, "supports", 1.0),
        "Evidence snippet 1": FakeJevResult({0: 1.0, 1: 0, 2: 0, 3: 0}, "neutral", 1.0),
    }

    class Client:
        def system_one(self, state, _questions):
            return next(r for text, r in by_text.items() if text in state)

    monkeypatch.setattr("claimlens.graph.verify.jev_rank._client", lambda: Client())
    monkeypatch.setattr("claimlens.graph.verify.jev_rank._questions", dict)
    monkeypatch.setattr(rank_module, "get_llm", lambda: pytest.fail("LLM must not be called"))

    ranked = rank_module.score_evidence(claim(), [evidence(0), evidence(1)])

    assert [(e.url, e.stance) for e in ranked] == [("https://example.test/0", "supports")]


def test_jev_low_confidence_and_failures_fall_back_to_llm(monkeypatch):
    jev_settings(monkeypatch)

    class Client:
        def system_one(self, state, _questions):
            if "snippet 1" in state:
                return FakeJevResult({0: 0, 1: 0.5, 2: 0.5, 3: 0}, "supports", 0.4)
            if "snippet 2" in state:
                raise RuntimeError("api down")
            return FakeJevResult({0: 0, 1: 0, 2: 0, 3: 1.0}, "refutes", 0.95)

    monkeypatch.setattr("claimlens.graph.verify.jev_rank._client", lambda: Client())
    monkeypatch.setattr("claimlens.graph.verify.jev_rank._questions", dict)
    llm_items = [
        rank_module.EvidenceAssessment(index=1, relevance=0.8, stance="supports"),
        rank_module.EvidenceAssessment(index=2, relevance=0.6, stance="neutral"),
    ]
    monkeypatch.setattr(rank_module, "get_llm", lambda: StructuredModel({"assessments": llm_items}))

    ranked = rank_module.score_evidence(claim(), [evidence(0), evidence(1), evidence(2)])

    assert [(e.url[-1], e.stance) for e in ranked] == [
        ("0", "refutes"),
        ("1", "supports"),
        ("2", "neutral"),
    ]
