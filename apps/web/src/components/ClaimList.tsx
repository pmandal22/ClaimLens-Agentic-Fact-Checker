import type { Check } from "../types";
import { ClaimCard } from "./ClaimCard";
import type { Filter } from "./Summary";

export function ClaimList({ check, filter }: { check: Check; filter: Filter }) {
  const all = check.results ?? [];
  const shown = filter === "all" ? all : all.filter((r) => r.verdict?.label === filter);
  return (
    <>
      <div className="section-head">
        <h2>Claims examined</h2>
        <span className="count">
          {filter === "all" ? all.length : `${shown.length} of ${all.length}`}
        </span>
      </div>
      {all.length === 0 && <p className="notice info">No checkable factual claims were found in this reel.</p>}
      {shown.map((r) => (
        <ClaimCard key={r.claim.id} result={r} />
      ))}
    </>
  );
}
