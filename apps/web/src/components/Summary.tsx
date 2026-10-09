import { LABELS, LABEL_KEYS, RATINGS } from "../constants";
import type { Check, Label } from "../types";
import { ToneIcon } from "./Badge";

export type Filter = Label | "all";

export function Summary({
  check,
  filter,
  onFilter,
}: {
  check: Check;
  filter: Filter;
  onFilter: (f: Filter) => void;
}) {
  const { overall } = check;
  if (!overall) return null;
  const total = LABEL_KEYS.reduce((n, k) => n + (overall.counts[k] ?? 0), 0);
  return (
    <section className={`panel summary tone-${RATINGS[overall.rating].tone}`}>
      <p className="eyebrow">Overall assessment</p>
      <div className="rating">
        <span className="rating-icon">
          <ToneIcon tone={RATINGS[overall.rating].tone} size={18} />
        </span>
        <h2>{RATINGS[overall.rating].text}</h2>
      </div>
      <p className="summary-text">{overall.summary}</p>
      {total > 0 && (
        <div className="distribution" role="img" aria-label="Verdict distribution">
          {LABEL_KEYS.filter((k) => (overall.counts[k] ?? 0) > 0).map((k) => (
            <span key={k} className={`seg seg-${LABELS[k].tone}`} style={{ flexGrow: overall.counts[k] }} />
          ))}
        </div>
      )}
      <div className="filters" role="group" aria-label="Filter claims by verdict">
        <button type="button" className={filter === "all" ? "on" : ""} aria-pressed={filter === "all"} onClick={() => onFilter("all")}>
          <span>All claims</span>
          <b>{total}</b>
        </button>
        {LABEL_KEYS.map((k) => (
          <button
            key={k}
            type="button"
            className={filter === k ? "on" : ""}
            aria-pressed={filter === k}
            disabled={(overall.counts[k] ?? 0) === 0}
            onClick={() => onFilter(k)}
          >
            <span>
              <i className={`dot seg-${LABELS[k].tone}`} />
              {LABELS[k].text}
            </span>
            <b>{overall.counts[k] ?? 0}</b>
          </button>
        ))}
      </div>
    </section>
  );
}
