export interface RecentCheck {
  id: string;
  url: string;
  at: number;
}

const KEY = "claimlens.recent";
const MAX = 8;

export function loadRecent(): RecentCheck[] {
  try {
    const raw = JSON.parse(localStorage.getItem(KEY) ?? "[]");
    return Array.isArray(raw) ? raw.filter((r) => r && typeof r.id === "string" && typeof r.url === "string") : [];
  } catch {
    return [];
  }
}

export function remember(item: RecentCheck): RecentCheck[] {
  const next = [item, ...loadRecent().filter((r) => r.id !== item.id)].slice(0, MAX);
  try {
    localStorage.setItem(KEY, JSON.stringify(next));
  } catch {
    // Storage can be unavailable (private mode); history is a convenience only.
  }
  return next;
}
