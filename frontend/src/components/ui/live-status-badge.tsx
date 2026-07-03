/**
 * LiveStatusBadge — a "Live"/"Idle" pill driven by real event freshness.
 *
 * Originally built for IngestionTimelinePage (bu-86c4c.8, move 5: "make the
 * ingestion Live badge decay on a clock so it can never show stale green")
 * and generalized here (bu-86c4c.10) for reuse by the fleet chronicle
 * (/timeline), which needs the identical honesty property: the badge must
 * decay from "Live" to "Idle" on its own once the freshness window elapses,
 * even if the page never re-renders for any other reason.
 */

import { useEffect, useState } from "react";

/** Freshness window: an event received within this many ms is "live". */
const LIVE_FRESHNESS_MS = 60_000;

/** How often the badge re-evaluates its own age against the wall clock. */
const CLOCK_TICK_MS = 5_000;

/**
 * A `now` timestamp that ticks on a wall clock rather than only advancing
 * when its caller re-renders for some other reason. Used so freshness
 * badges decay to "stale" on their own instead of staying frozen at
 * whatever `now` happened to be at the last data-driven render.
 */
function useTickingNow(intervalMs: number = CLOCK_TICK_MS): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), intervalMs);
    return () => clearInterval(id);
  }, [intervalMs]);
  return now;
}

type LiveStatus = "checking" | "live" | "idle";

export interface LiveStatusBadgeProps {
  /**
   * ISO-8601 timestamp of the most-recent event.
   * - undefined → initial loading state (before the first fetch completes)
   * - null → the stream is empty (query returned, no events) → "idle"
   * - string → has events; freshness determines "live" vs "idle"
   */
  latestReceivedAt: string | null | undefined;
}

function deriveStatus(latestReceivedAt: string | null | undefined, now: number): LiveStatus {
  if (latestReceivedAt === undefined) return "checking";
  if (latestReceivedAt === null) return "idle";
  const date = new Date(latestReceivedAt);
  if (Number.isNaN(date.getTime())) return "idle";
  const age = now - date.getTime();
  return age <= LIVE_FRESHNESS_MS ? "live" : "idle";
}

export function LiveStatusBadge({ latestReceivedAt }: LiveStatusBadgeProps) {
  // `now` ticks on a wall clock (not just when latestReceivedAt changes) so
  // the badge decays from "Live" to "Idle" on its own once the freshness
  // window elapses, even if the stream goes quiet and never reports a new
  // timestamp.
  const now = useTickingNow();
  const status = deriveStatus(latestReceivedAt, now);

  if (status === "checking") {
    return (
      <span className="inline-flex items-center gap-1.5 font-mono text-[11px] tracking-[0.01em] text-muted-foreground">
        <span className="size-1.5 rounded-full bg-muted-foreground animate-pulse" />
        checking…
      </span>
    );
  }

  if (status === "live") {
    return (
      <span
        className="inline-flex items-center gap-1.5 font-mono text-[11px] tracking-[0.01em]"
        style={{ color: "var(--green, theme(colors.emerald.600))" }}
        data-testid="live-status-badge-live"
      >
        <span
          className="size-1.5 rounded-full animate-pulse"
          style={{ backgroundColor: "var(--green, theme(colors.emerald.600))" }}
        />
        Live
      </span>
    );
  }

  return (
    <span
      className="inline-flex items-center gap-1.5 font-mono text-[11px] tracking-[0.01em] text-muted-foreground"
      data-testid="live-status-badge-idle"
    >
      <span className="size-1.5 rounded-full bg-muted-foreground/50" />
      Idle
    </span>
  );
}
