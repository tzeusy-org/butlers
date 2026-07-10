// ---------------------------------------------------------------------------
// TrendsLens — week/month zoom-out over balance, streaks, and anomalies (IEA §10)
//
// A per-lane day-by-day mini series across the trailing week or month, with
// streak counts and notable spike/drop anomalies. The window toggle is
// controlled by the parent (URL/local state) so the lens stays a pure view.
//
// Degraded-mode: trends_source_error → SourceDegradedNote; never a truthful-
// empty lens. Per-day `unavailable` (feeder_dark) bars render hatched, never
// as a truthful zero.
//
// Presentational: takes the query result pieces + window controls as props
// (renderToStaticMarkup testable).
// ---------------------------------------------------------------------------

import type { ReactNode } from "react";

import type {
  ChroniclerTrendLaneSeries,
  ChroniclerTrendsResponse,
} from "@/api/types";
import { SourceDegradedNote } from "@/components/ui/query-boundary";
import { Skeleton } from "@/components/ui/skeleton";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { LANE_TAXONOMY, type Category } from "./lane-taxonomy";
import { formatSeconds } from "./iea-format";

export type TrendsWindow = "week" | "month";

export interface TrendsLensProps {
  data: ChroniclerTrendsResponse | undefined;
  isLoading?: boolean;
  isError?: boolean;
  onRetry?: () => void;
  window: TrendsWindow;
  onWindowChange: (window: TrendsWindow) => void;
}

function laneConfig(lane: string) {
  return (LANE_TAXONOMY as Record<string, (typeof LANE_TAXONOMY)[Category]>)[lane] ?? LANE_TAXONOMY.other;
}

function LaneSparkline({ series }: { series: ChroniclerTrendLaneSeries }) {
  const config = laneConfig(series.lane);
  const maxSeconds = Math.max(1, ...series.days.map((d) => d.seconds));
  return (
    <div className="flex items-center gap-3" data-testid={`trends-lane-${series.lane}`}>
      <span className="w-16 shrink-0 text-xs font-medium">{config.label}</span>
      <div className="flex h-10 flex-1 items-end gap-0.5">
        {series.days.map((d) => {
          const heightPct = Math.max(3, (d.seconds / maxSeconds) * 100);
          const title = d.unavailable
            ? `${d.local_date}: unavailable`
            : `${d.local_date}: ${formatSeconds(d.seconds)}`;
          return (
            <div
              key={d.local_date}
              className="flex-1 rounded-sm"
              style={{
                height: `${heightPct}%`,
                backgroundColor: d.unavailable ? "var(--muted)" : config.hex,
                ...(d.unavailable
                  ? {
                      backgroundImage:
                        "repeating-linear-gradient(45deg, var(--muted-foreground) 0, var(--muted-foreground) 1px, transparent 1px, transparent 4px)",
                      opacity: 0.5,
                    }
                  : {}),
              }}
              title={title}
              data-testid={
                d.unavailable ? `trends-bar-unavailable-${series.lane}` : undefined
              }
            />
          );
        })}
      </div>
      {series.streak_days > 0 && (
        <Badge variant="outline" className="shrink-0 text-[10px]" data-testid={`trends-streak-${series.lane}`}>
          {series.streak_days}-day streak
        </Badge>
      )}
    </div>
  );
}

export function TrendsLens({
  data,
  isLoading,
  isError,
  onRetry,
  window,
  onWindowChange,
}: TrendsLensProps) {
  const toggle = (
    <div className="flex gap-1" role="group" aria-label="Trends window">
      <Button
        variant={window === "week" ? "default" : "outline"}
        size="sm"
        onClick={() => onWindowChange("week")}
        aria-pressed={window === "week"}
        data-testid="trends-window-week"
      >
        Week
      </Button>
      <Button
        variant={window === "month" ? "default" : "outline"}
        size="sm"
        onClick={() => onWindowChange("month")}
        aria-pressed={window === "month"}
        data-testid="trends-window-month"
      >
        Month
      </Button>
    </div>
  );

  let body: ReactNode;

  if (isLoading) {
    body = (
      <div className="space-y-2" role="status" aria-label="Loading trends" data-testid="trends-skeleton">
        {Array.from({ length: 4 }, (_, i) => (
          <Skeleton key={i} className="h-10 w-full rounded-md" />
        ))}
      </div>
    );
  } else if (isError || data?.trends_source_error) {
    body = (
      <SourceDegradedNote label="Trends" detail="data source unreachable" onRetry={onRetry} />
    );
  } else if (!data) {
    body = null;
  } else {
    const activeLanes = data.lanes
      .filter((l) => l.days.some((d) => d.seconds > 0 || d.unavailable))
      .sort((a, b) => laneConfig(a.lane).sortOrder - laneConfig(b.lane).sortOrder);

    if (activeLanes.length === 0) {
      body = (
        <p className="text-sm text-muted-foreground" data-testid="trends-empty">
          No lane activity across this window yet.
        </p>
      );
    } else {
      body = (
        <div className="space-y-4" data-testid="trends-lens">
          <div className="space-y-2">
            {activeLanes.map((series) => (
              <LaneSparkline key={series.lane} series={series} />
            ))}
          </div>
          {data.anomalies.length > 0 && (
            <div className="space-y-1" data-testid="trends-anomalies">
              <span className="text-[10px] uppercase tracking-wider text-muted-foreground">
                Notable
              </span>
              <ul className="space-y-1">
                {data.anomalies.map((a) => (
                  <li
                    key={`${a.lane}-${a.local_date}-${a.direction}`}
                    className="text-xs text-muted-foreground"
                  >
                    <span className="font-medium" style={{ color: laneConfig(a.lane).hex }}>
                      {laneConfig(a.lane).label}
                    </span>{" "}
                    {a.direction === "spike" ? "spiked" : "dropped"} on {a.local_date} (
                    {formatSeconds(a.seconds)} vs {formatSeconds(a.baseline_seconds)} usual)
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      );
    }
  }

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <span className="text-xs text-muted-foreground">
          {window === "week" ? "Trailing 7 days" : "Trailing 30 days"}
        </span>
        {toggle}
      </div>
      {body}
    </div>
  );
}
