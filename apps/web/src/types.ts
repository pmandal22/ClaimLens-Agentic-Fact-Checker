// Mirrors the API's CheckResponse (apps/api/dto.py).
export type Label = "supported" | "refuted" | "misleading" | "nei";
export type Rating = "mostly_supported" | "mixed" | "misleading" | "inconclusive";
export type JobStatus =
  | "queued"
  | "downloading"
  | "ingesting"
  | "verifying"
  | "done"
  | "failed";

export interface Claim {
  id: string;
  text: string;
  source: "speech" | "on_screen" | "caption";
  category: string;
  timestamp_s: number | null;
}

export interface Evidence {
  url: string;
  title: string;
  snippet: string;
  publisher: string | null;
  stance: "supports" | "refutes" | "neutral" | null;
}

export interface Verdict {
  claim_id: string;
  label: Label;
  confidence: number;
  rationale: string;
  citations: string[];
}

export interface ClaimResult {
  claim: Claim;
  evidence: Evidence[];
  verdict: Verdict | null;
}

export interface Overall {
  rating: Rating;
  summary: string;
  counts: Partial<Record<Label, number>>;
}

export interface Check {
  id: string;
  url: string;
  status: JobStatus;
  error: string | null;
  created_at: string;
  updated_at: string;
  results: ClaimResult[] | null;
  overall: Overall | null;
}
