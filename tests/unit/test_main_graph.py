import logging
from pathlib import Path

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from claimlens.config.settings import get_settings
from claimlens.domain.schemas import Claim, Evidence, Verdict
from claimlens.graph import main_graph
from claimlens.graph.checkpointer import make_serde, open_checkpointer

CLAIMS = [
    Claim(id="c1", text="The Eiffel Tower is in Paris.", source="speech"),
    Claim(id="c2", text="Vitamin C cures colds.", source="caption", category="health"),
]


def ev(url: str, stance: str = "supports") -> Evidence:
    return Evidence(url=url, title=f"Title {url}", snippet="...", stance=stance)


VERDICTS = {
    "c1": Verdict(
        claim_id="c1",
        label="supported",
        confidence=0.95,
        rationale="Encyclopedias agree.",
        citations=["https://a.test"],
    ),
    "c2": Verdict(
        claim_id="c2",
        label="refuted",
        confidence=0.9,
        rationale="Trials show no cure.",
        citations=["https://b.test"],
    ),
}


@pytest.fixture
def fakes(monkeypatch):
    """Replace ingest, the claim extractor and the verify subgraph; returns the claims to emit."""
    claims: list[Claim] = list(CLAIMS)
    monkeypatch.setattr(
        "claimlens.ingest.pipeline.read_video_text", lambda path, tmp, job_id="": ("speech", "text", [])
    )
    monkeypatch.setattr(
        "claimlens.graph.nodes.extract_claims.extract_claims", lambda *a, **k: claims
    )

    class FakeSubgraph:
        def invoke(self, state):
            claim = state["claim"]
            url = VERDICTS[claim.id].citations[0]
            return {"verdicts": [VERDICTS[claim.id]], "evidence": [ev(url)]}

    monkeypatch.setattr("claimlens.graph.verify.subgraph.verify_claim", FakeSubgraph())
    return claims


def config(thread: str) -> dict:
    return {"configurable": {"thread_id": thread}}


def test_unflagged_reel_runs_to_report(fakes):
    fakes[:] = [CLAIMS[0]]
    app = main_graph.build_graph(InMemorySaver(serde=make_serde()))

    state = app.invoke({"video_path": "reel.mp4"}, config("t1"))

    assert app.get_state(config("t1")).next == ()
    assert state["verdicts"] == [VERDICTS["c1"]]
    assert state["evidence"] == {"c1": [ev("https://a.test")]}
    assert state["overall"].rating == "mostly_supported"
    assert "[Title https://a.test](https://a.test)" in state["report"]


def test_no_claims_skips_verification(fakes):
    fakes[:] = []
    app = main_graph.build_graph(InMemorySaver(serde=make_serde()))

    state = app.invoke({"video_path": "reel.mp4"}, config("t2"))

    assert state["overall"].rating == "inconclusive"
    assert "verdicts" not in state or state["verdicts"] == []
    assert state["report"].startswith("# Overall: inconclusive")


def test_sensitive_claim_pauses_and_resumes_after_restart(
    fakes, tmp_path: Path, monkeypatch, caplog
):
    monkeypatch.setenv("CHECKPOINT_DB", str(tmp_path / "cp.db"))
    monkeypatch.delenv("POSTGRES_URL", raising=False)
    get_settings.cache_clear()
    cfg = config("t3")

    with open_checkpointer() as cp:
        app = main_graph.build_graph(cp)
        app.invoke({"video_path": "reel.mp4"}, cfg)
        snapshot = app.get_state(cfg)
        assert snapshot.next == ("human_review",)
        (pending,) = snapshot.interrupts
        assert [item["claim"]["id"] for item in pending.value["review"]] == ["c2"]
        assert pending.value["review"][0]["reasons"] == ["Sensitive topic: health"]

    # A fresh checkpointer stands in for a restarted worker.
    with caplog.at_level(logging.WARNING), open_checkpointer() as cp:
        app = main_graph.build_graph(cp)
        review = {
            "decisions": [{"claim_id": "c2", "label": "misleading", "note": "Overstated."}],
            "reviewed_at": "2026-10-09T10:00:00Z",
        }
        state = app.invoke(Command(resume=review), cfg)
    get_settings.cache_clear()

    assert "unregistered type" not in caplog.text
    assert state["review"].decisions[0].label == "misleading"
    assert state["overall"].counts["misleading"] == 1
    assert "**Misleading**" in state["report"]
    assert "Reviewed by a person: Overstated." in state["report"]


def test_review_missing_a_flagged_claim_is_rejected(fakes):
    app = main_graph.build_graph(InMemorySaver(serde=make_serde()))
    cfg = config("t4")
    app.invoke({"video_path": "reel.mp4"}, cfg)

    with pytest.raises(ValueError, match="missing decisions"):
        app.invoke(Command(resume={"decisions": [], "reviewed_at": "now"}), cfg)
    assert app.get_state(cfg).next == ("human_review",)  # still waiting, can be retried
