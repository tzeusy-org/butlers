import { ButlerMark } from "@/components/ui/ButlerMark";
import { Section } from "./Section";
import type { OverviewButlerIndexRow } from "./model";

interface ButlerIndexProps {
  butlers: OverviewButlerIndexRow[];
}

export function ButlerIndex({ butlers }: ButlerIndexProps) {
  return (
    <Section eyebrow="Operations">
      <div role="list" aria-label="Operations">
        {butlers.map((butler, i) => (
          <div
            key={butler.name}
            role="listitem"
            style={{
              display: "grid",
              gridTemplateColumns: "16px minmax(0, 1fr) auto minmax(86px, auto)",
              alignItems: "center",
              gap: "8px",
              paddingTop: "10px",
              paddingBottom: "10px",
              borderTop: i === 0 ? "1px solid var(--border)" : undefined,
              borderBottom: "1px solid var(--border)",
            }}
          >
            <ButlerMark name={butler.name} tone="neutral" />

            <div style={{ minWidth: 0 }}>
              <p
                style={{
                  fontFamily: "var(--font-sans)",
                  fontSize: "13px",
                  fontWeight: 400,
                  color: "var(--foreground)",
                  lineHeight: 1.4,
                  margin: 0,
                }}
              >
                {butler.name}
              </p>
              <p
                style={{
                  fontFamily: "var(--font-serif)",
                  fontSize: "12px",
                  color: "var(--muted-foreground)",
                  lineHeight: 1.4,
                  margin: 0,
                  marginTop: "2px",
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                  whiteSpace: "nowrap",
                }}
              >
                {runtimeLabel(butler)}
                {butler.costUsd > 0 ? ` · ${formatCost(butler.costUsd)} today` : ""}
              </p>
            </div>

            <span
              className="tnum"
              style={{
                fontFamily: "var(--font-mono)",
                fontSize: "11px",
                color: "var(--muted-foreground)",
                lineHeight: 1.4,
              }}
              aria-label={`${butler.sessions24h} sessions in the last 24 hours`}
            >
              {butler.sessions24h}
            </span>

            <span
              className="tnum"
              style={{
                fontFamily: "var(--font-mono)",
                fontSize: "11px",
                color: "var(--muted-foreground)",
                lineHeight: 1.4,
                textAlign: "right",
              }}
            >
              {lastActivityLabel(butler)}
            </span>
          </div>
        ))}
        {butlers.length === 0 && (
          <p
            style={{
              fontFamily: "var(--font-serif)",
              fontSize: "14px",
              fontStyle: "italic",
              color: "var(--muted-foreground)",
              paddingTop: "10px",
              paddingBottom: "10px",
            }}
          >
            No butlers active.
          </p>
        )}
      </div>
    </Section>
  );
}

function runtimeLabel(butler: OverviewButlerIndexRow): string {
  if (butler.runtimeState === "active") {
    return `${butler.activeSessionCount} active`;
  }
  if (butler.runtimeState === "stale" && butler.heartbeatAgeSeconds != null) {
    return `stale ${formatDuration(butler.heartbeatAgeSeconds)}`;
  }
  return butler.runtimeState;
}

function lastActivityLabel(butler: OverviewButlerIndexRow): string {
  if (butler.lastSessionAt) {
    return `last ${formatDateTime(butler.lastSessionAt)}`;
  }
  if (butler.heartbeatAgeSeconds != null) {
    return `heartbeat ${formatDuration(butler.heartbeatAgeSeconds)}`;
  }
  return "no session";
}

function formatCost(value: number): string {
  return `$${value.toFixed(3)}`;
}

function formatDateTime(iso: string): string {
  const parsed = new Date(iso);
  if (Number.isNaN(parsed.getTime())) return "unknown";
  return new Intl.DateTimeFormat(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(parsed);
}

function formatDuration(seconds: number): string {
  if (seconds < 60) return `${Math.max(0, Math.round(seconds))}s ago`;
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.round(minutes / 60);
  return `${hours}h ago`;
}
