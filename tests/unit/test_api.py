import json
from pathlib import Path
from types import SimpleNamespace

import fakeredis
import pytest
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver
from redis.exceptions import ConnectionError as RedisConnectionError

from apps.api.deps import job_queue, job_store, reel_graph, storage
from apps.api.main import create_app
from claimlens.domain.schemas import Claim, Verdict
from claimlens.graph.checkpointer import make_serde
from claimlens.graph.main_graph import build_graph, thread_config
from claimlens.services.jobs import JobStatus, SQLiteJobStore
from claimlens.services.queue import RedisStreamQueue
from claimlens.services.results import save_run
from claimlens.services.storage import LocalStorage

URL = "https://example.com/reel"


@pytest.fixture(autouse=True)
def public_urls(monkeypatch):
    # Avoid real DNS: pretend every URL is public except localhost-style ones.
    def fake(url: str) -> None:
        if "127.0.0.1" in url:
            raise ValueError("URL must point to a public internet address")

    monkeypatch.setattr("apps.api.routes.checks.ensure_public_url", fake)


@pytest.fixture
def redis_client():
    return fakeredis.FakeRedis(decode_responses=True)


@pytest.fixture
def local_storage(tmp_path: Path) -> LocalStorage:
    return LocalStorage(tmp_path / "store")


@pytest.fixture
def client(tmp_path: Path, redis_client, local_storage):
    app = create_app()
    store = SQLiteJobStore(tmp_path / "jobs.db")
    queue = RedisStreamQueue(redis_client, consumer="api-test")
    app.dependency_overrides[job_store] = lambda: store
    app.dependency_overrides[job_queue] = lambda: queue
    app.dependency_overrides[storage] = lambda: local_storage
    graph = build_graph(InMemorySaver(serde=make_serde()))
    app.dependency_overrides[reel_graph] = lambda: graph
    return TestClient(app)


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_post_creates_job_and_enqueues(client, redis_client):
    response = client.post("/checks", json={"url": URL})

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "queued"
    assert redis_client.xlen("claimlens:jobs") == 1


def test_get_returns_job(client):
    job_id = client.post("/checks", json={"url": URL}).json()["id"]

    response = client.get(f"/checks/{job_id}")

    assert response.status_code == 200
    assert response.json()["id"] == job_id
    assert response.json()["results"] is None


def write_artifacts(local_storage: LocalStorage, job_id: str, tmp_path: Path) -> None:
    claim = {"id": "c1", "text": "Water boils at 100C.", "source": "speech"}
    evidence = {"c1": [{"url": "https://e.test/1", "title": "Boiling", "snippet": "s",
                        "stance": "supports"}], "c2": []}
    verdict = {"claim_id": "c1", "label": "supported", "confidence": 0.9,
               "rationale": "Agrees.", "citations": ["https://e.test/1"]}
    for key, payload in {
        f"claims/{job_id}.json": {"claims": [claim]},
        f"evidence/{job_id}.json": evidence,
        f"verdicts/{job_id}.json": [verdict],
    }.items():
        source = tmp_path / "artifact.json"
        source.write_text(json.dumps(payload), encoding="utf-8")
        local_storage.put_file(source, key)


def test_done_job_returns_claims_evidence_and_verdicts(client, local_storage, tmp_path):
    job_id = client.post("/checks", json={"url": URL}).json()["id"]
    store = client.app.dependency_overrides[job_store]()
    for step in (JobStatus.DOWNLOADING, JobStatus.INGESTING, JobStatus.VERIFYING, JobStatus.DONE):
        store.set_status(job_id, step)
    write_artifacts(local_storage, job_id, tmp_path)

    body = client.get(f"/checks/{job_id}").json()

    assert body["status"] == "done"
    [result] = body["results"]
    assert result["claim"]["id"] == "c1"
    assert result["evidence"][0]["url"] == "https://e.test/1"
    assert result["verdict"]["label"] == "supported"
    assert body["overall"]["rating"] == "mostly_supported"


def test_done_job_without_artifacts_is_404(client):
    job_id = client.post("/checks", json={"url": URL}).json()["id"]
    store = client.app.dependency_overrides[job_store]()
    for step in (JobStatus.DOWNLOADING, JobStatus.INGESTING, JobStatus.VERIFYING, JobStatus.DONE):
        store.set_status(job_id, step)

    assert client.get(f"/checks/{job_id}").status_code == 404

def test_duplicate_post_returns_same_job(client):
    first = client.post("/checks", json={"url": URL}).json()
    second = client.post("/checks", json={"url": URL}).json()

    assert first["id"] == second["id"]


def test_unknown_job_is_404(client):
    assert client.get("/checks/nope").status_code == 404


def test_non_url_is_422(client):
    assert client.post("/checks", json={"url": "not a url"}).status_code == 422


def test_non_http_scheme_is_422(client):
    assert client.post("/checks", json={"url": "file:///etc/passwd"}).status_code == 422


def test_private_address_is_422(client):
    response = client.post("/checks", json={"url": "http://127.0.0.1/x.mp4"})

    assert response.status_code == 422
    assert "public" in response.json()["detail"]


def test_queue_down_is_503(tmp_path: Path):
    class DownQueue:
        def enqueue(self, job_id):
            raise RedisConnectionError("down")

    app = create_app()
    app.dependency_overrides[job_store] = lambda: SQLiteJobStore(tmp_path / "jobs.db")
    app.dependency_overrides[job_queue] = lambda: DownQueue()

    response = TestClient(app).post("/checks", json={"url": URL})

    assert response.status_code == 503


def to_review(client, local_storage, tmp_path, confidence=0.4, category="general") -> str:
    """A job paused for review with one claim the model was unsure about."""
    job_id = client.post("/checks", json={"url": URL}).json()["id"]
    store = client.app.dependency_overrides[job_store]()
    for step in (JobStatus.DOWNLOADING, JobStatus.INGESTING, JobStatus.VERIFYING,
                 JobStatus.NEEDS_REVIEW):
        store.set_status(job_id, step)
    write_artifacts(local_storage, job_id, tmp_path)
    for key, change in ((f"verdicts/{job_id}.json", ("confidence", confidence)),
                        (f"claims/{job_id}.json", ("category", category))):
        path = local_storage._path(key)
        data = json.loads(path.read_text())
        (data[0] if isinstance(data, list) else data["claims"][0])[change[0]] = change[1]
        path.write_text(json.dumps(data))
    return job_id


def test_needs_review_job_returns_results_with_reasons(client, local_storage, tmp_path):
    job_id = to_review(client, local_storage, tmp_path)

    body = client.get(f"/checks/{job_id}").json()

    assert body["status"] == "needs_review"
    assert body["results"][0]["review_reasons"] == ["Low confidence (40%)"]


def test_review_publishes_reviewer_label(client, local_storage, tmp_path):
    job_id = to_review(client, local_storage, tmp_path)

    response = client.post(f"/checks/{job_id}/review", json={"decisions": [
        {"claim_id": "c1", "label": "misleading", "note": "Only at sea level."}]})

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "done"
    [result] = body["results"]
    assert result["verdict"]["label"] == "misleading"
    assert result["review"] == {"model_label": "supported", "note": "Only at sea level."}
    assert body["overall"]["rating"] == "misleading"
    # And it stays published on later reads.
    assert client.get(f"/checks/{job_id}").json()["results"][0]["verdict"]["label"] == "misleading"


def test_review_must_decide_every_flagged_claim(client, local_storage, tmp_path):
    job_id = to_review(client, local_storage, tmp_path)

    response = client.post(f"/checks/{job_id}/review", json={"decisions": [
        {"claim_id": "nope", "label": "supported"}]})

    assert response.status_code == 422
    assert "unknown claim ids: nope" in response.json()["detail"]
    assert "no decision for flagged claims: c1" in response.json()["detail"]
    assert client.get(f"/checks/{job_id}").json()["status"] == "needs_review"


def test_review_of_job_not_waiting_is_409(client, local_storage, tmp_path):
    job_id = to_review(client, local_storage, tmp_path)
    decisions = {"decisions": [{"claim_id": "c1", "label": "supported"}]}
    assert client.post(f"/checks/{job_id}/review", json=decisions).status_code == 200

    assert client.post(f"/checks/{job_id}/review", json=decisions).status_code == 409


def test_review_of_unknown_job_is_404(client):
    assert client.post("/checks/nope/review", json={"decisions": []}).status_code == 404


def test_review_token_is_enforced_when_set(client, local_storage, tmp_path, monkeypatch):
    from claimlens.config.settings import get_settings

    monkeypatch.setenv("REVIEW_TOKEN", "s3cret")
    get_settings.cache_clear()
    try:
        job_id = to_review(client, local_storage, tmp_path)
        decisions = {"decisions": [{"claim_id": "c1", "label": "supported"}]}
        url = f"/checks/{job_id}/review"
        assert client.post(url, json=decisions).status_code == 401
        assert client.post(url, json=decisions, headers={"X-Review-Token": "bad"}).status_code == 401
        assert client.post(url, json=decisions, headers={"X-Review-Token": "s3cret"}).status_code == 200
    finally:
        get_settings.cache_clear()


def pause_graph_run(client, local_storage, monkeypatch) -> str:
    """A job whose real graph run stopped at human_review, as the worker leaves it."""
    claim = Claim(id="c1", text="Garlic cures flu.", source="speech", category="health")
    verdict = Verdict(
        claim_id="c1", label="refuted", confidence=0.9, rationale="Trials.", citations=[]
    )
    monkeypatch.setattr("claimlens.ingest.pipeline.read_video_text", lambda v, w: ("s", "", []))
    monkeypatch.setattr(
        "claimlens.graph.nodes.extract_claims.extract_claims", lambda *a, **k: [claim]
    )
    monkeypatch.setattr(
        "claimlens.graph.verify.subgraph.verify_claim",
        SimpleNamespace(invoke=lambda state: {"verdicts": [verdict], "evidence": []}),
    )
    job_id = client.post("/checks", json={"url": URL}).json()["id"]
    store = client.app.dependency_overrides[job_store]()
    for step in (JobStatus.DOWNLOADING, JobStatus.INGESTING, JobStatus.VERIFYING,
                 JobStatus.NEEDS_REVIEW):
        store.set_status(job_id, step)
    graph = client.app.dependency_overrides[reel_graph]()
    graph.invoke({"video_path": "reel.mp4"}, thread_config(job_id))
    save_run(local_storage, job_id, graph.get_state(thread_config(job_id)).values)
    return job_id


def test_review_resumes_the_paused_graph_run(client, local_storage, monkeypatch):
    job_id = pause_graph_run(client, local_storage, monkeypatch)

    response = client.post(f"/checks/{job_id}/review", json={"decisions": [
        {"claim_id": "c1", "label": "misleading", "note": "Weak evidence."}]})

    assert response.status_code == 200
    assert response.json()["status"] == "done"
    graph = client.app.dependency_overrides[reel_graph]()
    state = graph.get_state(thread_config(job_id))
    assert state.next == ()
    assert state.values["review"].decisions[0].label == "misleading"
    report = local_storage._path(f"reports/{job_id}.md").read_text()
    assert "**Misleading**" in report


def test_failed_resume_leaves_the_check_waiting_for_review(client, local_storage, monkeypatch):
    job_id = pause_graph_run(client, local_storage, monkeypatch)

    def broken(*args, **kwargs):
        raise RuntimeError("checkpoint database down")

    graph = client.app.dependency_overrides[reel_graph]()
    monkeypatch.setattr(graph, "invoke", broken)

    with pytest.raises(RuntimeError):
        client.post(f"/checks/{job_id}/review", json={"decisions": [
            {"claim_id": "c1", "label": "misleading"}]})

    assert client.get(f"/checks/{job_id}").json()["status"] == "needs_review"
    assert not local_storage.exists(f"reviews/{job_id}.json")
