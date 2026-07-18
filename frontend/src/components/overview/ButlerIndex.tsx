import { ButlerMark } from "@/components/ui/ButlerMark";
import { RowLink } from "@/components/ui/RowLink";
import { formatCostUsd } from "@/lib/format-cost";
import { Section } from "./Section";
import type { OverviewButlerIndexRow } from "./model";

interface ButlerIndexProps {
  butlers: OverviewButlerIndexRow[];
  /**
   * True when the butler-health source (`GET /api/butlers`) failed to load.
   * When set, the empty state surfaces an explicit "source unreachable"
   * message instead of the misleading "No butlers active." — an empty list
   * over a dead source must not read as a healthy, quiet system.
   */
  butlersError?: boolean;
}

export function ButlerIndex({ butlers, butlersError = false }: ButlerIndexProps) {
  return (
    <Section eyebrow="Operations">
      <div role="list" aria-label="Operations">
        {butlers.map((butler, i) => (
          <div key={butler.name} role="listitem">
            <RowLink
              to={`/butlers/${encodeURIComponent(butler.name)}`}
              aria-label={`View ${butler.name}`}
              style={{
                display: "grid",
                gridTemplateColumns: "16px minmax(0, 1fr) auto minmax(86px, auto)",
                alignItems: "center",
                gap: "8px",
                paddingTop: "10px",
                paddingBottom: "10px",
                borderTop: i === 0 ? "1px solid var(--border)" : undefined,
                borderBottom: "1px solid var(--border)",
                color: "inherit",
                textDecoration: "none",
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
                  {butler.costUsd > 0 ? ` · ${formatCostUsd(butler.costUsd)} today` : ""}
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
            </RowLink>
          </div>
        ))}
        {butlers.length === 0 && butlersError && (
          <div role="listitem">
          <p
            role="alert"
            style={{
              fontFamily: "var(--font-serif)",
              fontSize: "14px",
              fontStyle: "italic",
              color: "var(--destructive, var(--muted-foreground))",
              paddingTop: "10px",
              paddingBottom: "10px",
            }}
          >
            Butler health source unavailable.
          </p>
          </div>
        )}
        {butlers.length === 0 && !butlersError && (
          <p
            role="listitem"
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

// eslint-disable-next-line no-restricted-syntax -- relative "X ago" suffix (bu-sd0l7.3), a different contract than lib/format-duration.ts's plain-span shapes.
function formatDuration(seconds: number): string {
  if (seconds < 60) return `${Math.max(0, Math.round(seconds))}s ago`;
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.round(minutes / 60);
  return `${hours}h ago`;
}
