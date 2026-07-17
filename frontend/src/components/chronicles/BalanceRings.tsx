// ---------------------------------------------------------------------------
// BalanceRings — per-lane "vs usual" balance for the day (IEA §10)
//
// Each lane renders a small ring whose filled arc is the day's total scaled
// against the owner's rolling baseline ("usual"), annotated with a signed
// delta. Only the Activity layer is counted (the backend guarantees this).
//
// Degraded-mode contract (butlers/CLAUDE.md API Conventions):
//   - balance_source_error → the whole panel renders a SourceDegradedNote;
//     never a truthful-empty ring set.
//   - status === "not_yet_materialized" → a gentle "not settled yet" note (a
//     legitimate absence, NOT a degraded source and NOT a zero).
//   - a lane's `unavailable` (feeder_dark) → that ring shows "unavailable",
//     never a truthful zero/delta.
//   - baseline_seconds === null → "no usual yet", never a fabricated 0 delta.
//
// Presentational: takes the query result pieces as props so it is testable via
// renderToStaticMarkup without react-query.
// ---------------------------------------------------------------------------

import type { ChroniclerBalanceResponse } from "@/api/types";
import { SourceDegradedNote } from "@/components/ui/query-boundary";
import { Skeleton } from "@/components/ui/skeleton";
import { LANE_TAXONOMY, type Category } from "./lane-taxonomy";
import { deltaDirection, formatSeconds, formatSignedDelta } from "./iea-format";

export interface BalanceRingsProps {
  data: ChroniclerBalanceResponse | undefined;
  isLoading?: boolean;
  isError?: boolean;
  onRetry?: () => void;
}

const RING_RADIUS = 18;
const RING_CIRCUMFERENCE = 2 * Math.PI * RING_RADIUS;

const DELTA_COLOR: Record<"up" | "down" | "flat", string> = {
  up: "var(--green-text, var(--green))",
  down: "var(--amber-text, var(--amber))",
  flat: "var(--muted-foreground)",
};

function laneConfig(lane: string) {
  return (LANE_TAXONOMY as Record<string, (typeof LANE_TAXONOMY)[Category]>)[lane] ?? LANE_TAXONOMY.other;
}

function Ring({
  lane,
  seconds,
  baselineSeconds,
  deltaSeconds,
  unavailable,
}: {
  lane: string;
  seconds: number;
  baselineSeconds: number | null;
  deltaSeconds: number | null;
  unavailable: boolean;
}) {
  const config = laneConfig(lane);
  // Arc fraction: day's total relative to the baseline (capped at 1 full ring).
  // With no baseline yet ("no usual"), there is nothing to fill against — the
  // ring stays empty rather than filling to 100%, which would falsely read as
  // "met usual" (the seconds text + "no usual yet" label carry the real total).
  const fraction =
    unavailable || !baselineSeconds || baselineSeconds <= 0
      ? 0
      : Math.max(0, Math.min(1, seconds / baselineSeconds));
  const dash = fraction * RING_CIRCUMFERENCE;

  return (
    <div
      className="flex flex-col items-center gap-1 text-center"
      data-testid={`balance-ring-${lane}`}
    >
      <div className="relative">
        <svg width="44" height="44" viewBox="0 0 44 44" role="img" aria-label={`${config.label} balance`}>
          <circle
            cx="22"
            cy="22"
            r={RING_RADIUS}
            fill="none"
            stroke="var(--muted)"
            strokeWidth="4"
          />
          {!unavailable && (
            <circle
              cx="22"
              cy="22"
              r={RING_RADIUS}
              fill="none"
              stroke={config.color}
              strokeWidth="4"
              strokeLinecap="round"
              strokeDasharray={`${dash} ${RING_CIRCUMFERENCE}`}
              transform="rotate(-90 22 22)"
            />
          )}
        </svg>
      </div>
      <span className="text-xs font-medium">{config.label}</span>
      {unavailable ? (
        <span
          className="text-[10px] text-[var(--amber-text)]"
          data-testid={`balance-ring-${lane}-unavailable`}
        >
          unavailable
        </span>
      ) : (
        <>
          <span className="text-[11px] tnum text-muted-foreground">{formatSeconds(seconds)}</span>
          {deltaSeconds == null ? (
            <span className="text-[10px] text-muted-foreground">no usual yet</span>
          ) : (
            <span
              className="text-[10px] tnum"
              style={{ color: DELTA_COLOR[deltaDirection(deltaSeconds)] }}
            >
              {formatSignedDelta(deltaSeconds)}
            </span>
          )}
        </>
      )}
    </div>
  );
}

export function BalanceRings({ data, isLoading, isError, onRetry }: BalanceRingsProps) {
  if (isLoading) {
    return (
      <div
        className="flex flex-wrap gap-4"
        role="status"
        aria-label="Loading balance"
        data-testid="balance-skeleton"
      >
        {Array.from({ length: 5 }, (_, i) => (
          <Skeleton key={i} className="h-16 w-14 rounded-md" />
        ))}
      </div>
    );
  }

  if (isError || data?.balance_source_error) {
    return (
      <SourceDegradedNote
        label="Balance vs usual"
        detail="data source unreachable"
        onRetry={onRetry}
      />
    );
  }

  if (!data) return null;

  if (data.status === "not_yet_materialized") {
    return (
      <p className="text-sm text-muted-foreground" data-testid="balance-not-settled">
        Balance is not settled yet for this day.
      </p>
    );
  }

  // Sort by taxonomy order; show lanes with either activity or a baseline for
  // context (a genuine zero-with-baseline lane is meaningful; a zero-with-no-
  // baseline lane is just noise).
  const rings = [...data.lanes]
    .filter((l) => l.seconds > 0 || (l.baseline_seconds ?? 0) > 0 || l.unavailable)
    .sort((a, b) => laneConfig(a.lane).sortOrder - laneConfig(b.lane).sortOrder);

  if (rings.length === 0) {
    return (
      <p className="text-sm text-muted-foreground" data-testid="balance-empty">
        No lane activity recorded for this day.
      </p>
    );
  }

  return (
    <div className="flex flex-wrap gap-4" data-testid="balance-rings">
      {rings.map((l) => (
        <Ring
          key={l.lane}
          lane={l.lane}
          seconds={l.seconds}
          baselineSeconds={l.baseline_seconds}
          deltaSeconds={l.delta_seconds}
          unavailable={l.unavailable}
        />
      ))}
    </div>
  );
}
