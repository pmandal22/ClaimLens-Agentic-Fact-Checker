import { LABELS, SOURCES, fmtTime } from "../constants";
import type { ClaimResult, Evidence } from "../types";
import { Badge, SafeLink, host } from "./Badge";

function SourceAvatar({ url }: { url: string }) {
  return (
    <span className="avatar" aria-hidden="true">
      {host(url).charAt(0).toUpperCase()}
    </span>
  );
}

function StanceTag({ stance }: { stance: Evidence["stance"] }) {
  return <span className={`stance stance-${stance ?? "unrated"}`}>{stance ?? "unrated"}</span>;
}

export function ClaimCard({ result }: { result: ClaimResult }) {
  const { claim, verdict, evidence } = result;
  const titles = new Map(evidence.map((e) => [e.url, e.title || e.url]));
  const time = fmtTime(claim.timestamp_s);
  const meta = [SOURCES[claim.source] ?? claim.source, time && `at ${time}`, claim.category];
  // An abstention with no stance-taking evidence was never scored by the judge.
  const judged =
    verdict && (verdict.label !== "nei" || evidence.some((e) => e.stance === "supports" || e.stance === "refutes"));

  return (
    <article className={`claim tone-${verdict ? LABELS[verdict.label].tone : "gray"}`}>
      <div className="claim-head">
        {verdict ? (
          <Badge tone={LABELS[verdict.label].tone}>{LABELS[verdict.label].text}</Badge>
        ) : (
          <Badge tone="gray">No verdict</Badge>
        )}
        {judged && (
          <div className="confidence">
            <progress value={verdict.confidence} max={1} />
            <span>Confidence {Math.round(verdict.confidence * 100)}%</span>
          </div>
        )}
      </div>

      <h3>“{claim.text}”</h3>
      <p className="chips">
        {meta.filter(Boolean).map((m) => (
          <span key={String(m)} className="chip">
            {m}
          </span>
        ))}
      </p>

      {verdict ? (
        <>
          <p className="rationale">{verdict.rationale}</p>
          {verdict.citations.length > 0 && (
            <>
              <p className="eyebrow">Sources</p>
              <ul className="sources">
                {verdict.citations.map((url) => (
                  <li key={url}>
                    <SourceAvatar url={url} />
                    <span className="source-text">
                      <SafeLink url={url}>{titles.get(url) ?? url}</SafeLink>
                      <span className="host">{host(url)}</span>
                    </span>
                  </li>
                ))}
              </ul>
            </>
          )}
        </>
      ) : (
        <p className="caption">The check did not reach a verdict for this claim.</p>
      )}

      {evidence.length > 0 && (
        <details>
          <summary>All evidence found ({evidence.length})</summary>
          <ul className="evidence-list">
            {evidence.map((e) => (
              <li key={e.url} className="evidence">
                <div className="evidence-head">
                  <SafeLink url={e.url}>{e.title || e.url}</SafeLink>
                  <StanceTag stance={e.stance} />
                </div>
                <span className="host">{e.publisher || host(e.url)}</span>
                {e.snippet && <p className="snippet">{e.snippet}</p>}
              </li>
            ))}
          </ul>
        </details>
      )}
    </article>
  );
}
