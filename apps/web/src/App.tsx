import { useCallback, useEffect, useState } from "react";
import { ApiError, createCheck, getCheck } from "./api";
import { CheckView } from "./components/CheckView";
import { Summary, type Filter } from "./components/Summary";
import { POLL_MS, STEPS } from "./constants";
import { loadRecent, remember, type RecentCheck } from "./history";
import type { Check } from "./types";

const jobFromUrl = () => new URLSearchParams(window.location.search).get("job");

export default function App() {
  const [url, setUrl] = useState("");
  const [jobId, setJobId] = useState<string | null>(jobFromUrl);
  const [check, setCheck] = useState<Check | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [filter, setFilter] = useState<Filter>("all");
  const [recent, setRecent] = useState<RecentCheck[]>(loadRecent);

  const refresh = useCallback(async () => {
    if (!jobId) return;
    try {
      setCheck(await getCheck(jobId));
      setError(null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Something went wrong.");
    }
  }, [jobId]);

  useEffect(() => {
    setCheck(null);
    setFilter("all");
    void refresh();
  }, [refresh]);

  // Poll while the job is moving; stop once it's done or failed.
  const running = check !== null && STEPS.includes(check.status);
  useEffect(() => {
    if (!running) return;
    const timer = setInterval(() => void refresh(), POLL_MS);
    return () => clearInterval(timer);
  }, [running, refresh]);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!url.trim()) {
      setError("Paste a URL first.");
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      const created = await createCheck(url.trim());
      window.history.replaceState(null, "", `?job=${encodeURIComponent(created.id)}`);
      setRecent(remember({ id: created.id, url: created.url, at: Date.now() }));
      setJobId(created.id); // keeps the result on refresh and in links
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Something went wrong.");
    } finally {
      setSubmitting(false);
    }
  }

  function open(id: string) {
    window.history.replaceState(null, "", `?job=${encodeURIComponent(id)}`);
    setJobId(id);
  }

  const hasSummary = check?.status === "done";

  return (
    <>
      <header className="topbar">
        <div className="topbar-inner">
          <span className="logo" aria-hidden="true">
            <svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
              <circle cx="10.5" cy="10.5" r="6.5" />
              <path d="m7.7 10.6 2 2 3.7-3.9M20 20l-4.9-4.9" />
            </svg>
          </span>
          <span className="brand">ClaimLens</span>
          <span className="tagline">Agentic fact-checking for short-form video</span>
        </div>
      </header>
      <div className="dashboard">
        <aside className="sidebar">
          <form onSubmit={onSubmit} className="panel">
            <p className="eyebrow">New check</p>
            <input
              type="url"
              aria-label="Reel URL"
              placeholder="https://www.youtube.com/shorts/..."
              value={url}
              onChange={(e) => setUrl(e.target.value)}
            />
            <button type="submit" disabled={submitting}>
              {submitting ? "Submitting…" : "Check this reel"}
            </button>
            {error && (
              <p className="notice error" role="alert">
                {error}
              </p>
            )}
          </form>

          {check && (
            <section className="panel">
              <p className="eyebrow">Reel</p>
              <a className="reel-url" href={check.url} target="_blank" rel="noopener noreferrer">
                {check.url}
              </a>
              <p className="caption">
                <span className={`status status-${check.status}`}>{check.status.replace("_", " ")}</span>
              </p>
            </section>
          )}

          {hasSummary && <Summary check={check} filter={filter} onFilter={setFilter} />}

          {recent.length > 0 && (
            <section className="panel">
              <p className="eyebrow">Recent checks</p>
              <ul className="recent">
                {recent.map((r) => (
                  <li key={r.id}>
                    <button type="button" className={r.id === jobId ? "on" : ""} onClick={() => open(r.id)}>
                      {r.url.replace(/^https?:\/\/(www\.)?/, "")}
                    </button>
                  </li>
                ))}
              </ul>
            </section>
          )}
        </aside>

        <main>
          {check ? (
            <CheckView check={check} filter={filter} />
          ) : (
            !error && (
              <section className="empty">
                <p className="hero-kicker">Fact-check short-form video</p>
                <h1>Verify what a reel really claims</h1>
                <p>
                  Paste a link to a short video. ClaimLens extracts the factual claims from speech,
                  on-screen text and caption, then checks each one against published evidence.
                </p>
                <p className="chips hero-chips">
                  <span className="chip">YouTube Shorts</span>
                  <span className="chip">Instagram Reels</span>
                  <span className="chip">TikTok</span>
                </p>
                <ol className="how">
                  <li><b>Extract</b> atomic, checkable claims</li>
                  <li><b>Retrieve</b> evidence from the web</li>
                  <li><b>Judge</b> each claim with citations</li>
                  <li><b>Rate</b> the reel as a whole</li>
                </ol>
              </section>
            )
          )}
        </main>
      </div>
      <footer className="footer">
        Verdicts are produced automatically and can be wrong. They describe the claim, not the
        person who made it. Open the sources to judge the evidence yourself.
      </footer>
    </>
  );
}
