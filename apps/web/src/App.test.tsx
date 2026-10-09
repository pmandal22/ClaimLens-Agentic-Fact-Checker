import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, vi } from "vitest";
import App from "./App";
import type { Check } from "./types";

const base: Check = {
  id: "job1",
  url: "https://example.com/reel",
  status: "done",
  error: null,
  created_at: "2026-10-08T00:00:00+00:00",
  updated_at: "2026-10-08T00:00:00+00:00",
  results: null,
  overall: null,
};

const claimResult = {
  claim: { id: "c1", text: "The Eiffel Tower is 330 m tall.", source: "speech", category: "general", timestamp_s: 75 },
  evidence: [{ url: "https://ex.org/eiffel", title: "Eiffel Tower facts", snippet: "330 metres", publisher: "Ex", stance: "supports" }],
  verdict: { claim_id: "c1", label: "supported", confidence: 0.9, rationale: "Official height is 330 m.", citations: ["https://ex.org/eiffel"] },
};

function mockCheck(check: Check) {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: async () => check }));
  window.history.replaceState(null, "", "/?job=job1");
}

afterEach(() => vi.unstubAllGlobals());

test("shows verdict, confidence and sources for a finished check", async () => {
  mockCheck({
    ...base,
    results: [claimResult] as Check["results"],
    overall: { rating: "mostly_supported", summary: "ok", counts: { supported: 1 } },
  });
  render(<App />);
  expect(await screen.findByText(/The Eiffel Tower is 330 m tall/)).toBeInTheDocument();
  expect(screen.getByText("Mostly supported")).toBeInTheDocument();
  expect(screen.getByText("Confidence 90%")).toBeInTheDocument();
  expect(screen.getAllByRole("link", { name: "Eiffel Tower facts" })[0]).toHaveAttribute("href", "https://ex.org/eiffel");
});

test("shows progress while running", async () => {
  mockCheck({ ...base, status: "ingesting" });
  render(<App />);
  expect(await screen.findByText(/Reading speech/)).toBeInTheDocument();
});

test("shows the error for a failed check", async () => {
  mockCheck({ ...base, status: "failed", error: "download failed" });
  render(<App />);
  expect(await screen.findByText(/download failed/)).toBeInTheDocument();
});

test("Clear removes the recent checks", async () => {
  localStorage.setItem("claimlens.recent", JSON.stringify([{ id: "job1", url: "https://ex.com/reel", at: 1 }]));
  mockCheck({ ...base, status: "failed", error: "boom" });
  render(<App />);

  fireEvent.click(await screen.findByRole("button", { name: "Clear" }));

  expect(screen.queryByText("Recent checks")).not.toBeInTheDocument();
  expect(localStorage.getItem("claimlens.recent")).toBeNull();
});
