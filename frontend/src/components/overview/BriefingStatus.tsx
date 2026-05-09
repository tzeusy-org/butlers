/**
 * BriefingStatus -- pill button with three states from useBriefing().
 *
 * States:
 *   composing...   amber dot  while isFetching
 *   llm · cached 5m  green dot  when data.source === "llm"
 *   templated        dim dot    when data.source === "fallback"
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
  source: BriefingSource | undefined,
  generatedAt: string | undefined,
): { dot: "amber" | "green" | "dim"; label: string } {
  if (isFetching) return { dot: "amber", label: "composing…" };
  if (source === "llm") {
    const age = generatedAt ? ageLabel(generatedAt) : "cached";
    return { dot: "green", label: `llm · ${age}` };
  }
  return { dot: "dim", label: "templated" };
}

const DOT_COLORS: Record<"amber" | "green" | "dim", string> = {
  amber: "var(--severity-medium)", // oklch(0.769 0.189 84.0)
  green: "var(--severity-low)",    // oklch(0.723 0.198 148.2)
  dim: "var(--muted-foreground)",
};

export function BriefingStatus({
  source,
  generatedAt,
  isFetching,
  onRefetch,
}: BriefingStatusProps) {
  const { dot, label } = pillContent(isFetching, source, generatedAt);
  const dotColor = DOT_COLORS[dot];

  return (
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
}
