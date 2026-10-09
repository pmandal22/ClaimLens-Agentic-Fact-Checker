"""Save and load a job's claims, evidence, verdicts and report in storage."""

import json
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from claimlens.domain.schemas import Claim, Claims, Evidence, Verdict
from claimlens.services.storage import Storage


class ClaimResult(BaseModel):
    claim: Claim
    evidence: list[Evidence]
    verdict: Verdict | None


def _read_json(storage: Storage, key: str) -> Any:
    with tempfile.TemporaryDirectory(prefix="claimlens-results-") as tmp:
        path = storage.get_file(key, Path(tmp) / "artifact.json")
        return json.loads(path.read_text(encoding="utf-8"))


def report_key(job_id: str) -> str:
    return f"reports/{job_id}.md"


def put_text(storage: Storage, key: str, text: str) -> None:
    with tempfile.TemporaryDirectory(prefix="claimlens-artifact-") as tmp:
        path = Path(tmp) / Path(key).name
        path.write_text(text, encoding="utf-8")
        storage.put_file(path, key)


def save_run(storage: Storage, job_id: str, state: Mapping[str, Any]) -> None:
    """Write a graph run's results under the keys load_results reads; idempotent.

    The report is written only once the run has one.
    """
    claims = Claims(claims=state.get("claims", []))
    evidence = {
        claim_id: [e.model_dump() for e in items]
        for claim_id, items in state.get("evidence", {}).items()
    }
    put_text(storage, f"claims/{job_id}.json", claims.model_dump_json(indent=2))
    put_text(storage, f"evidence/{job_id}.json", json.dumps(evidence, indent=2))
    put_text(
        storage,
        f"verdicts/{job_id}.json",
        json.dumps([v.model_dump() for v in state.get("verdicts", [])], indent=2),
    )
    if report := state.get("report"):
        put_text(storage, report_key(job_id), report)


def load_results(storage: Storage, job_id: str) -> list[ClaimResult]:
    """Join the pipeline's claims, evidence and verdicts artifacts by claim id.

    Raises FileNotFoundError if a pipeline artifact is missing.
    """
    claims = _read_json(storage, f"claims/{job_id}.json")["claims"]
    evidence = _read_json(storage, f"evidence/{job_id}.json")
    verdicts = {
        item["claim_id"]: Verdict.model_validate(item)
        for item in _read_json(storage, f"verdicts/{job_id}.json")
    }
    results = []
    for raw in claims:
        claim = Claim.model_validate(raw)
        results.append(
            ClaimResult(
                claim=claim,
                evidence=[Evidence.model_validate(e) for e in evidence.get(claim.id, [])],
                verdict=verdicts.get(claim.id),
            )
        )
    return results
