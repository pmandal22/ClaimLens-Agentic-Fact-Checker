import json
from pathlib import Path

import fakeredis
import pytest
from fastapi.testclient import TestClient
from redis.exceptions import ConnectionError as RedisConnectionError

from apps.api.deps import job_queue, job_store, storage
from apps.api.main import create_app
from claimlens.services.jobs import JobStatus, SQLiteJobStore
from claimlens.services.queue import RedisStreamQueue
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



def test_review_endpoint_is_gone(client):
    assert client.post("/checks/any/review", json={"decisions": []}).status_code in (404, 405)
