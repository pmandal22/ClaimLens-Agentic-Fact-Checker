import type { JobStatus, Label, Rating } from "./types";

export const POLL_MS = 3000;

// The order a job moves through while it runs; used for the progress bar.
export const STEPS: JobStatus[] = ["queued", "downloading", "ingesting", "verifying"];

export const STEP_TEXT: Record<string, string> = {
  queued: "Waiting for a worker",
  downloading: "Downloading the reel",
  ingesting: "Reading speech, on-screen text and caption",
  verifying: "Checking each claim against evidence",
};

export type Tone = "green" | "red" | "orange" | "gray";

export const LABELS: Record<Label, { text: string; tone: Tone }> = {
  supported: { text: "Supported", tone: "green" },
  refuted: { text: "Refuted", tone: "red" },
  misleading: { text: "Misleading", tone: "orange" },
  nei: { text: "Not enough evidence", tone: "gray" },
};

export const LABEL_KEYS = Object.keys(LABELS) as Label[];

export const RATINGS: Record<Rating, { text: string; tone: Tone }> = {
  mostly_supported: { text: "Mostly supported", tone: "green" },
  mixed: { text: "Mixed", tone: "orange" },
  misleading: { text: "Misleading", tone: "red" },
  inconclusive: { text: "Inconclusive", tone: "gray" },
};

export const SOURCES: Record<string, string> = {
  speech: "Speech",
  on_screen: "On-screen text",
  caption: "Caption",
};

export function fmtTime(seconds: number | null | undefined): string | null {
  if (seconds == null) return null;
  const s = Math.floor(seconds);
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}
