export interface RecentCheck {
  id: string;
  url: string;
  at: number;
}

const KEY = "claimlens.recent";
const MAX = 8;

// Newest first, one entry per reel URL: resubmitting a reel creates a new job id.
function uniqueByUrl(items: RecentCheck[]): RecentCheck[] {
  const seen = new Set<string>();
  return items.filter((r) => !seen.has(r.url) && seen.add(r.url));
}

export function loadRecent(): RecentCheck[] {
  try {
    const raw = JSON.parse(localStorage.getItem(KEY) ?? "[]");
    return Array.isArray(raw)
      ? uniqueByUrl(raw.filter((r) => r && typeof r.id === "string" && typeof r.url === "string"))
      : [];
  } catch {
    return [];
  }
}

export function clearRecent(): void {
  try {
    localStorage.removeItem(KEY);
  } catch {
    // Storage can be unavailable (private mode); nothing to clear then.
  }
}

export function remember(item: RecentCheck): RecentCheck[] {
  const next = uniqueByUrl([item, ...loadRecent()]).slice(0, MAX);
  try {
    localStorage.setItem(KEY, JSON.stringify(next));
  } catch {
    // Storage can be unavailable (private mode); history is a convenience only.
  }
  return next;
}
