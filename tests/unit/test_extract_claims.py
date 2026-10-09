from claimlens.domain.schemas import Claim, Claims
from claimlens.graph.nodes import extract_claims as module


class FakeLLM:
    def __init__(self, claims):
        self.claims = claims
        self.prompt = None

    def with_structured_output(self, schema):
        return self

    def invoke(self, prompt):
        self.prompt = prompt
        return Claims(claims=self.claims)


def make(n):
    return [Claim(id="x", text=f"Fact {i}.", source="speech") for i in range(n)]


def test_claims_get_stable_ids_and_are_capped(monkeypatch):
    llm = FakeLLM(make(9))
    monkeypatch.setattr(module, "get_extract_llm", lambda: llm)

    claims = module.extract_claims("Some transcript")

    assert [c.id for c in claims] == ["c1", "c2", "c3", "c4", "c5"]
    assert "Some transcript" in llm.prompt


def test_empty_text_skips_the_llm(monkeypatch):
    def boom():
        raise AssertionError("LLM must not be called")

    monkeypatch.setattr(module, "get_extract_llm", boom)

    assert module.extract_claims("  ", caption="") == []
