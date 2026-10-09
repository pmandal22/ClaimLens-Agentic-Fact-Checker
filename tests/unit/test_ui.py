"""Render the Streamlit page against a fake API and check what each job state shows."""

from pathlib import Path

import httpx
import pytest
from streamlit.testing.v1 import AppTest

APP = str(Path(__file__).parents[2] / "apps" / "ui" / "app.py")
URL = "https://example.com/reel"


def check(status: str, **extra) -> dict:
    return {
        "id": "job1", "url": URL, "status": status, "error": None,
        "created_at": "2026-10-08T00:00:00+00:00", "updated_at": "2026-10-08T00:00:00+00:00",
        "results": None, "overall": None, **extra,
    }


DONE = check(
    "done",
    results=[
        {
            "claim": {"id": "c1", "text": "The Eiffel Tower is 330 m tall.", "source": "speech",
                      "category": "general", "timestamp_s": 75.0},
            "evidence": [{"url": "https://ex.org/eiffel", "title": "Eiffel Tower facts",
                          "snippet": "330 metres", "publisher": "Ex", "stance": "supports"}],
            "verdict": {"claim_id": "c1", "label": "supported", "confidence": 0.9,
                        "rationale": "Official height is 330 m.",
                        "citations": ["https://ex.org/eiffel"]},
        }
    ],
    overall={"rating": "mostly_supported", "summary": "1 claim(s)",
             "counts": {"supported": 1, "refuted": 0, "misleading": 0, "nei": 0}},
)


@pytest.fixture
def fake_api(monkeypatch):
    """Serve `state["check"]` for GET and record POSTs."""
    state: dict = {"check": DONE, "posts": [], "reviews": []}

    def request(method, url, **kwargs):
        req = httpx.Request(method, url)
        if state.get("down"):
            raise httpx.ConnectError("refused", request=req)
        if method == "POST" and url.endswith("/review"):
            state["reviews"].append((kwargs["json"], kwargs.get("headers")))
            state["check"] = DONE
            return httpx.Response(200, json=DONE, request=req)
        if method == "POST":
            state["posts"].append(kwargs["json"])
            return httpx.Response(202, json=check("queued"), request=req)
        return httpx.Response(200, json=state["check"], request=req)

    monkeypatch.setattr(httpx, "request", request)
    return state


def run(job: str | None = None) -> AppTest:
    at = AppTest.from_file(APP, default_timeout=10)
    if job:
        at.query_params["job"] = job
    return at.run()


def all_text(at: AppTest) -> str:
    parts = [e.value for e in [*at.markdown, *at.caption, *at.error, *at.warning, *at.info]]
    return "\n".join(str(p) for p in parts)


def test_empty_page_shows_form(fake_api):
    at = run()
    assert not at.exception
    assert at.text_input[0].label == "Reel URL"


def test_submit_posts_url_and_tracks_job(fake_api):
    at = run()
    at.text_input[0].input(URL)
    at.button[0].click().run()
    assert not at.exception
    assert fake_api["posts"] == [{"url": URL}]
    assert at.query_params["job"] == "job1"


def test_done_shows_verdict_and_sources(fake_api):
    at = run("job1")
    assert not at.exception
    text = all_text(at)
    assert "The Eiffel Tower is 330 m tall." in text
    assert "Speech · at 1:15" in text
    assert "[Eiffel Tower facts](https://ex.org/eiffel)" in text
    assert [m.value for m in at.metric] == ["1", "0", "0", "0"]


def test_running_job_shows_progress(fake_api):
    fake_api["check"] = check("ingesting")
    at = run("job1")
    assert not at.exception
    assert "updates by itself" in all_text(at)
    assert "Reading speech" in at.get("progress")[0].proto.text


def test_failed_and_review_states(fake_api):
    fake_api["check"] = check("failed", error="Download blocked")
    assert "Download blocked" in all_text(run("job1"))


def test_api_down_shows_error(fake_api):
    fake_api["down"] = True
    at = run("job1")
    assert not at.exception
    assert "Can't reach the ClaimLens API" in all_text(at)


def needs_review() -> dict:
    flagged = {**DONE["results"][0], "review_reasons": ["Low confidence (40%)"]}
    return {**DONE, "status": "needs_review", "results": [flagged]}


def test_needs_review_shows_provisional_results_and_form(fake_api):
    fake_api["check"] = needs_review()
    at = run("job1")
    assert not at.exception
    text = all_text(at)
    assert "human reviewer" in text
    assert "Waiting for review: Low confidence (40%)" in text
    assert "Overall (before review)" in [h.value for h in at.subheader]
    assert at.selectbox(key="label_c1").value == "supported"


def test_publishing_review_posts_decisions_and_token(fake_api):
    fake_api["check"] = needs_review()
    at = run("job1")
    at.selectbox(key="label_c1").select("misleading")
    at.text_input(key="note_c1").input("Only at sea level.")
    at.text_input[-1].input("s3cret")  # reviewer token
    at.button[-1].click().run()
    assert not at.exception
    assert fake_api["reviews"] == [(
        {"decisions": [{"claim_id": "c1", "label": "misleading", "note": "Only at sea level."}]},
        {"X-Review-Token": "s3cret"},
    )]


def test_reviewed_claim_says_what_changed(fake_api):
    reviewed = {**DONE["results"][0], "review": {"model_label": "supported", "note": "Too vague."}}
    reviewed["verdict"] = {**reviewed["verdict"], "label": "misleading"}
    fake_api["check"] = {**DONE, "results": [reviewed]}
    text = all_text(run("job1"))
    assert "Reviewed by a person: changed from Supported. Note: Too vague." in text


def test_done_job_never_says_waiting_for_review(fake_api):
    # Jobs finished before review existed can still have flagged claims.
    flagged = {**DONE["results"][0], "review_reasons": ["Low confidence (40%)"]}
    fake_api["check"] = {**DONE, "results": [flagged]}
    assert "Waiting for review" not in all_text(run("job1"))
