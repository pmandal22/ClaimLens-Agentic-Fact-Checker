import type { Tone } from "../constants";

// One glyph per verdict tone, so the label is readable without relying on color.
const TONE_PATHS: Record<Tone, string> = {
  green: "M5 12.5l4.2 4.2L19 7",
  red: "M7 7l10 10M17 7L7 17",
  orange: "M12 6.5v7M12 17.5h.01",
  gray: "M9.3 9.3a2.7 2.7 0 1 1 3.8 2.5c-.7.3-1.1 1-1.1 1.7v.2M12 17.5h.01",
};

export function ToneIcon({ tone, size = 14 }: { tone: Tone; size?: number }) {
  return (
    <svg
      viewBox="0 0 24 24"
      width={size}
      height={size}
      fill="none"
      stroke="currentColor"
      strokeWidth="2.6"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d={TONE_PATHS[tone]} />
    </svg>
  );
}

export function Badge({ tone, children }: { tone: Tone; children: React.ReactNode }) {
  return (
    <span className={`badge badge-${tone}`}>
      <span className="badge-icon">
        <ToneIcon tone={tone} size={11} />
      </span>
      {children}
    </span>
  );
}

// Evidence URLs come from the web; only link http(s) ones.
export function SafeLink({ url, children }: { url: string; children: React.ReactNode }) {
  const ok = /^https?:\/\//i.test(url);
  return ok ? (
    <a href={url} target="_blank" rel="noopener noreferrer">
      {children}
    </a>
  ) : (
    <span>{children}</span>
  );
}

export function host(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return url;
  }
}
