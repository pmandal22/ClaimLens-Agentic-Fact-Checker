import type { Check } from "./types";

const API_BASE = (import.meta.env.VITE_CLAIMLENS_API_URL ?? "/api").replace(/\/$/, "");

export class ApiError extends Error {}

async function request<T>(method: string, path: string, body?: unknown, headers?: HeadersInit) {
  let resp: Response;
  try {
    resp = await fetch(`${API_BASE}${path}`, {
      method,
      headers: { ...(body ? { "Content-Type": "application/json" } : {}), ...headers },
      body: body ? JSON.stringify(body) : undefined,
    });
  } catch {
    throw new ApiError("Can't reach the ClaimLens API. Is it running?");
  }
  if (!resp.ok) throw new ApiError(await errorDetail(resp));
  return (await resp.json()) as T;
}

async function errorDetail(resp: Response): Promise<string> {
  try {
    const { detail } = await resp.json();
    if (Array.isArray(detail)) return detail.map((d) => d.msg ?? String(d)).join("; ");
    return typeof detail === "string" ? detail : `Request failed (${resp.status})`;
  } catch {
    return `Request failed (${resp.status})`;
  }
}

export const createCheck = (url: string) => request<Check>("POST", "/checks", { url });

export const getCheck = (id: string) => request<Check>("GET", `/checks/${encodeURIComponent(id)}`);
