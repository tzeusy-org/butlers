/**
 * BriefingStatus -- pill button with four states from useBriefing() /
 * useHealthBriefing().
 *
 * States (priority order):
 *   composing...   amber dot  while isFetching
 *   unavailable    red dot    when isError and not fetching
 *   llm · cached 5m  green dot  when data.source === "llm"
 *   templated        dim dot    when data.source === "fallback"
 *
 * `isError` exists so a failed briefing fetch never renders as the
 * indefinite "composing..."/loading copy (bu-86c4c.2, JARVIS audit move 1b:
 * a killed backend must announce itself, not impersonate "still working on it").
 *
 * Clicking triggers refetch().
 * Geometry: 9px mono, dot + label + refresh icon.
 *
 * Motion: the refresh icon rotates continuously while isFetching using
 * CSS @keyframes spin (transform-only, linear; continuous rotation must be
 * linear to avoid per-loop stutter; ease-out-quart applies to state transitions
 * only).
 *
 * Topology: about/lay-and-land/frontend.md §Status pill
 * Doctrine: about/heart-and-soul/design-language.md §The status pill
 */

import type { BriefingSource } from "@/api/types";

interface BriefingStatusProps {
  source: BriefingSource | undefined;
  generatedAt: string | undefined;
  isFetching: boolean;
  /** True when the underlying briefing query has errored (no cached data assumed absent). */
  isError?: boolean;
  onRefetch: () => void;
}

function ageLabel(generatedAt: string): string {
  const ts = new Date(generatedAt).getTime();
  if (isNaN(ts)) return "cached";
  const ageMs = Date.now() - ts;
  const minutes = Math.floor(ageMs / 60_000);
  if (minutes < 1) return "cached <1m";
  return `cached ${minutes}m`;
}

/**
 * Derive pill label from state.
 */
function pillContent(
  isFetching: boolean,
  isError: boolean,
  source: BriefingSource | undefined,
  generatedAt: string | undefined,
): { dot: "amber" | "green" | "dim" | "red"; label: string } {
  if (isFetching) return { dot: "amber", label: "composing…" };
  if (isError) return { dot: "red", label: "unavailable" };
  if (source === "llm") {
    const age = generatedAt ? ageLabel(generatedAt) : "cached";
    return { dot: "green", label: `llm · ${age}` };
  }
  return { dot: "dim", label: "templated" };
}

const DOT_COLORS: Record<"amber" | "green" | "dim" | "red", string> = {
  amber: "var(--severity-medium)", // oklch(0.769 0.189 84.0)
  green: "var(--severity-low)",    // oklch(0.723 0.198 148.2)
  dim: "var(--muted-foreground)",
  red: "var(--destructive)",
};

export function BriefingStatus({
  source,
  generatedAt,
  isFetching,
  isError = false,
  onRefetch,
}: BriefingStatusProps) {
  const { dot, label } = pillContent(isFetching, isError, source, generatedAt);
  const dotColor = DOT_COLORS[dot];
  // Announce the degraded state without stripping the button's native
  // interactive semantics (role="alert" on the <button> itself would replace
  // its exposed "button" role for assistive tech). A wrapping alert region
  // announces the change; the button underneath stays a button.
  const announceError = isError && !isFetching;

  const pill = (
    <button
      type="button"
      onClick={onRefetch}
      aria-label={`Briefing status: ${label}. Click to refresh.`}
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: "4px",
        fontFamily: "var(--font-mono)",
        fontSize: "9px",
        lineHeight: 1,
        color: "var(--muted-foreground)",
        border: "1px solid var(--border)",
        borderRadius: "var(--radius-sm)",
        padding: "2px 6px",
        background: "transparent",
        cursor: "pointer",
        userSelect: "none",
      }}
    >
      {/* Status dot */}
      <span
        aria-hidden="true"
        style={{
          display: "inline-block",
          width: "5px",
          height: "5px",
          borderRadius: "50%",
          backgroundColor: dotColor,
          flexShrink: 0,
        }}
      />
      {/* Label */}
      <span className="tnum">{label}</span>
      {/* Refresh icon: rotates while fetching */}
      <svg
        aria-hidden="true"
        viewBox="0 0 12 12"
        width="9"
        height="9"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
        style={{
          flexShrink: 0,
          animation: isFetching ? "spin 1s linear infinite" : undefined,
          transformOrigin: "center",
        }}
      >
        <path d="M10 6A4 4 0 1 1 6 2" />
        <path d="M10 2v4H6" />
      </svg>
    </button>
  );

  if (!announceError) return pill;

  return (
    <span role="alert" style={{ display: "inline-flex" }}>
      {pill}
    </span>
  );
}
