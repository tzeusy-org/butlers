/**
 * ActivitySparkline (entity v3 — "90-day activity sparkline", bu-xzh76)
 *
 * Renders a 90-day activity sparkline in the entity detail hero, sourced from
 * GET /api/butlers/relationship/entities/{id}/activity?bins=daily&window=90d.
 *
 * Design language (spec "90-day activity sparkline"):
 * - one vertical stick per day in the window; days with no activity render at
 *   4% opacity (never collapsed out — "no day MUST be omitted or interpolated");
 * - no axes, no tooltips, no charting-library chrome;
 * - a tabular-num count caption;
 * - absence of any activity in the window renders the canned serif line, not an
 *   empty chart.
 */

import { useEntityActivityBins } from "@/hooks/use-entities";

/** Stick height floor (px) so even the busiest day stays a quiet hairline bar. */
const STICK_MAX_HEIGHT = 28;
/** Quiet-day opacity per spec (4%). */
const QUIET_OPACITY = 0.04;

export function ActivitySparkline({ entityId }: { entityId: string }) {
  const { data, isLoading, isError } = useEntityActivityBins(entityId, {
    window: "90d",
  });

  if (isLoading) {
    return (
      <div
        data-testid="sparkline-loading"
        className="h-8 w-full animate-pulse rounded bg-muted/40"
      />
    );
  }

  // Degrade quietly on error — the sparkline is a quick-refresh affordance, not
  // load-bearing. Render nothing rather than an error chrome.
  if (isError || !data) return null;

  const bins = data.bins;
  const total = bins.reduce((sum, b) => sum + b.count, 0);

  // No activity in the window → canned serif line, not an empty chart.
  if (total === 0) {
    return (
      <p
        data-testid="sparkline-empty"
        className="font-serif text-sm italic text-muted-foreground"
      >
        No activity in the last 90 days.
      </p>
    );
  }

  const maxCount = Math.max(...bins.map((b) => b.count), 1);
  const activeDays = bins.filter((b) => b.count > 0).length;

  return (
    <div data-testid="activity-sparkline" className="space-y-1.5">
      <div
        className="flex h-7 items-end gap-px"
        role="img"
        aria-label={`Activity over the last ${bins.length} days: ${total} events across ${activeDays} days`}
      >
        {bins.map((bin) => {
          const quiet = bin.count === 0;
          const height = quiet
            ? 2
            : Math.max(2, Math.round((bin.count / maxCount) * STICK_MAX_HEIGHT));
          return (
            <span
              key={bin.date}
              data-testid="sparkline-stick"
              data-quiet={quiet ? "true" : "false"}
              data-count={bin.count}
              className="flex-1 rounded-[1px] bg-foreground"
              style={{
                height: `${height}px`,
                opacity: quiet ? QUIET_OPACITY : 1,
              }}
            />
          );
        })}
      </div>
      <p className="text-xs tabular-nums text-muted-foreground">
        {total} {total === 1 ? "event" : "events"} · {activeDays} active{" "}
        {activeDays === 1 ? "day" : "days"} · last {bins.length} days
      </p>
    </div>
  );
}
