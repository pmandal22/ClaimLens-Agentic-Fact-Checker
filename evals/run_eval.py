"""Claim-level eval: compare rankers on the same retrieved evidence.

Evidence is retrieved once per claim and cached, so every variant ranks and judges identical
inputs and differences come only from the ranker. Single pass (no query-rewrite retries).

    python evals/run_eval.py --rankers llm,jev
    python evals/run_eval.py --refresh          # re-run retrieval instead of using the cache
    python evals/run_eval.py --limit 5          # quick smoke run
"""

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

from claimlens.config.settings import get_settings
from claimlens.domain.schemas import Claim, Evidence
from claimlens.graph.verify import rank as rank_module
from claimlens.graph.verify.judge import make_verdict
from claimlens.graph.verify.retrieve import retrieve_evidence

DATASET = ROOT / "evals" / "datasets" / "claims.jsonl"
CACHE = ROOT / "evals" / "datasets" / ".evidence_cache.json"
REPORTS = ROOT / "evals" / "reports"


def load_dataset(limit: int | None) -> list[dict]:
    rows = [
        json.loads(line)
        for line in DATASET.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    return rows[:limit] if limit else rows


def to_claim(row: dict) -> Claim:
    return Claim(
        id=row["id"],
        text=row["text"],
        source="speech",
        category=row["category"],
        india_related=row.get("india_related", False),
        search_terms=row["search_terms"],
    )


def load_evidence(rows: list[dict], refresh: bool) -> dict[str, list[Evidence]]:
    cached = {} if refresh or not CACHE.exists() else json.loads(CACHE.read_text(encoding="utf-8"))
    for row in rows:
        if row["id"] not in cached:
            found = retrieve_evidence(to_claim(row))
            cached[row["id"]] = [e.model_dump() for e in found]
            print(f"  retrieved {len(found):2d} hits for {row['id']}", flush=True)
    CACHE.write_text(json.dumps(cached, ensure_ascii=False), encoding="utf-8")
    return {k: [Evidence.model_validate(e) for e in v] for k, v in cached.items()}


def run_variant(ranker: str, rows: list[dict], evidence: dict[str, list[Evidence]]) -> list[dict]:
    base = get_settings()
    rank_module.get_settings = lambda: base.model_copy(update={"ranker": ranker})
    results = []
    for row in rows:
        claim, hits = to_claim(row), evidence[row["id"]]
        start = time.perf_counter()
        try:
            ranked = rank_module.score_evidence(claim, hits)
            verdict = make_verdict(claim, ranked)
            label, confidence, error = verdict.label, verdict.confidence, None
        except Exception as exc:  # noqa: BLE001
            ranked, label, confidence, error = [], "error", 0.0, str(exc)[:200]
        results.append(
            {
                "id": row["id"],
                "expected": row["expected"],
                "label": label,
                "confidence": confidence,
                "kept": len(ranked),
                "seconds": round(time.perf_counter() - start, 2),
                "error": error,
            }
        )
    return results


def summarize(results: list[dict], confidence_floor: float) -> dict:
    n = len(results)
    answered = [r for r in results if r["label"] not in ("nei", "error")]
    answerable = [r for r in results if r["expected"] != "nei"]
    wrong_confident = [
        r for r in answered if r["label"] != r["expected"] and r["confidence"] >= confidence_floor
    ]
    return {
        "n": n,
        "accuracy": sum(r["label"] == r["expected"] for r in results) / n,
        "answered_accuracy": (sum(r["label"] == r["expected"] for r in answered) / len(answered))
        if answered
        else 0.0,
        "abstain_rate": sum(r["label"] == "nei" for r in results) / n,
        "missed_answerable": sum(r["label"] == "nei" for r in answerable) / max(len(answerable), 1),
        "confident_wrong": len(wrong_confident) / n,
        "errors": sum(r["label"] == "error" for r in results),
        "avg_kept": sum(r["kept"] for r in results) / n,
        "avg_seconds": sum(r["seconds"] for r in results) / n,
    }


def render(summaries: dict[str, dict], per_claim: dict[str, list[dict]], rows: list[dict]) -> str:
    metrics = [
        ("accuracy", "Verdict accuracy (all claims)"),
        ("answered_accuracy", "Accuracy when not abstaining"),
        ("abstain_rate", "Abstain rate (nei)"),
        ("missed_answerable", "Abstained on answerable claims"),
        ("confident_wrong", "Confident-wrong rate"),
        ("errors", "Errors"),
        ("avg_kept", "Avg evidence kept"),
        ("avg_seconds", "Avg seconds per claim"),
    ]
    variants = list(summaries)
    lines = ["| Metric | " + " | ".join(variants) + " |", "| --- |" + " --- |" * len(variants)]
    for key, label in metrics:
        cells = [f"{summaries[v][key]:.2f}" for v in variants]
        lines.append(f"| {label} | " + " | ".join(cells) + " |")
    lines += [
        "",
        "| Claim | Expected | " + " | ".join(variants) + " |",
        "| --- | --- |" + " --- |" * len(variants),
    ]
    for index, row in enumerate(rows):
        cells = []
        for v in variants:
            r = per_claim[v][index]
            mark = "✅" if r["label"] == r["expected"] else ("➖" if r["label"] == "nei" else "❌")
            cells.append(f"{mark} {r['label']} ({r['confidence']:.2f})")
        lines.append(f"| {row['text'][:60]} | {row['expected']} | " + " | ".join(cells) + " |")
    errors = [
        f"- {v} {r['id']}: {r['error']}" for v in variants for r in per_claim[v] if r["error"]
    ]
    return "\n".join(lines + (["", "Errors:", *errors] if errors else []))


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--rankers", default="llm,jev")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()

    rows = load_dataset(args.limit)
    print(f"{len(rows)} claims; retrieving evidence (cached in {CACHE.name})")
    evidence = load_evidence(rows, args.refresh)

    floor = get_settings().confidence_floor
    per_claim, summaries = {}, {}
    for ranker in args.rankers.split(","):
        print(f"ranking + judging with ranker={ranker}", flush=True)
        per_claim[ranker] = run_variant(ranker, rows, evidence)
        summaries[ranker] = summarize(per_claim[ranker], floor)

    report = render(summaries, per_claim, rows)
    print("\n" + report)
    REPORTS.mkdir(exist_ok=True)
    out = REPORTS / f"eval-{datetime.now().astimezone():%Y%m%d-%H%M%S}.md"
    out.write_text(
        f"# Claim eval {datetime.now().astimezone():%Y-%m-%d %H:%M}\n\nModel: `{get_settings().claimlens_model}`, "
        f"relevance threshold {get_settings().evidence_relevance_threshold}, "
        f"confidence floor {floor}.\n\n{report}\n",
        encoding="utf-8",
    )
    print(f"\nreport: {out}")


if __name__ == "__main__":
    main()
