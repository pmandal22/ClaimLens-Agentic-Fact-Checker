import { STEPS, STEP_TEXT } from "../constants";
import type { Check } from "../types";
import { ClaimList } from "./ClaimList";
import type { Filter } from "./Summary";

export function CheckView({ check, filter }: { check: Check; filter: Filter }) {
  const { status } = check;
  return (
    <>
      {status === "done" && <ClaimList check={check} filter={filter} />}
      {status === "failed" && (
        <>
          <p className="notice error">This check failed: {check.error || "unknown error"}</p>
          <p className="caption">Submit the same URL again to retry.</p>
        </>
      )}
      {STEPS.includes(status) && (
        <div role="status" className="panel progress-panel">
          <ol className="stepper">
            {STEPS.map((step, i) => {
              const at = STEPS.indexOf(status);
              return (
                <li key={step} className={i < at ? "done" : i === at ? "active" : ""}>
                  <span className="step-dot">{i < at ? "✓" : i + 1}</span>
                  <span className="step-name">{step}</span>
                </li>
              );
            })}
          </ol>
          <p className="progress-text">{STEP_TEXT[status]}…</p>
          <p className="caption">A reel usually takes a minute or two. This page updates by itself.</p>
        </div>
      )}
      {STEPS.includes(status) &&
        [0, 1].map((i) => (
          <div key={i} className="claim skeleton" aria-hidden="true">
            <span className="bone w-20" />
            <span className="bone w-80 tall" />
            <span className="bone w-60" />
          </div>
        ))}
    </>
  );
}
