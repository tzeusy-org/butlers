import { useMemo, useState } from "react";
import { format } from "date-fns";
import { useSearchParams } from "react-router";
import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import type {
  Measurement,
  MeasurementParams,
  MeasurementTypeInfo,
  MeasurementTrendWindowDays,
} from "@/api/types";
import { Button } from "@/components/ui/button";
import { SourceDegradedNote } from "@/components/ui/query-boundary";
import { Skeleton } from "@/components/ui/skeleton";
import { Time } from "@/components/ui/time";
import { hasValidMeasurementUrlState } from "@/lib/measurement-door";
import { chartableMeasurementTypes } from "@/lib/measurement-vocabulary";
import { cn } from "@/lib/utils";
import {
  useMeasurements,
  useMeasurementTrend,
  useMeasurementTypes,
} from "@/hooks/use-health";
import { chartColor } from "@/lib/chart-colors";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

// Trend lookback windows (days) — each wired to the real `window_days` query param.
const TREND_WINDOWS: { value: MeasurementTrendWindowDays; label: string }[] = [
  { value: 7, label: "7D" },
  { value: 14, label: "14D" },
  { value: 30, label: "30D" },
  { value: 90, label: "90D" },
];

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/**
 * Resolve the health butler's hue to a literal color string for Recharts.
 * Recharts cannot consume a CSS custom property directly, so we read the live
 * computed value of the chart-series token used by the measurement plot.
 * The diastolic line reuses the same hue at reduced opacity.
 */
function useChartColor(index: number): string {
  const token = chartColor(index);
  const property = token.slice(4, -1);
  const [color] = useState<string>(() => {
    if (typeof document === "undefined") return token;
    const value = getComputedStyle(document.documentElement)
      .getPropertyValue(property)
      .trim();
    return value || token;
  });
  return color;
}

/** Convert only finite numeric values into chart points. */
function finiteNumber(value: unknown): number | null {
  if (typeof value === "number") return Number.isFinite(value) ? value : null;
  if (typeof value !== "string" || !value.trim()) return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

/** Extract a named numeric value from a measurement for charting. */
function extractValue(m: Measurement, key: string): number | null {
  const v = m.value[key];
  return finiteNumber(v);
}

function chartDate(measuredAt: string): string {
  const date = new Date(measuredAt);
  return Number.isNaN(date.getTime()) ? "Unknown date" : format(date, "MMM d");
}

interface ChartPoint {
  date: string;
  value?: number | null;
  systolic?: number | null;
  diastolic?: number | null;
}

/** Format a measurement's value object as a readable string for display. */
function formatValue(m: Measurement): string {
  const v = m.value ?? {};
  if (m.type === "blood_pressure" && v.systolic != null && v.diastolic != null) {
    return `${v.systolic}/${v.diastolic}`;
  }
  if ("value" in v && v.value != null) {
    return String(v.value);
  }
  const entries = Object.entries(v).filter(([, val]) => val != null);
  if (entries.length === 0) return "—";
  return entries.map(([k, val]) => `${k}: ${val}`).join(", ");
}

/** Round a trend value to at most one decimal place. */
function formatTrendValue(value: number): string {
  if (!Number.isFinite(value)) return "—";
  return Number.isInteger(value) ? String(value) : value.toFixed(1);
}

/** Direction glyph comparing a bucket mean to the previous bucket. */
function trendArrow(delta: number | null): string {
  if (delta == null || delta === 0) return "→"; // →
  return delta > 0 ? "↑" : "↓"; // ↑ / ↓
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

/** Single serif-italic empty line — Dispatch empty state (no decorated chrome). */
function EmptyLine({ children }: { children: React.ReactNode }) {
  return (
    <p className="py-8 font-serif text-sm italic text-muted-foreground">{children}</p>
  );
}

// ---------------------------------------------------------------------------
// MeasurementChart
// ---------------------------------------------------------------------------

export default function MeasurementChart() {
  const [searchParams, setSearchParams] = useSearchParams();
  const requestedType = searchParams.get("type") ?? "";
  const since = searchParams.get("since") ?? "";
  const until = searchParams.get("until") ?? "";
  const [windowDays, setWindowDays] = useState<MeasurementTrendWindowDays>(14);
  const [showTable, setShowTable] = useState(false);
  const {
    data: measurementTypesData,
    isLoading: measurementTypesLoading,
    isError: measurementTypesError,
    refetch: refetchMeasurementTypes,
  } = useMeasurementTypes();
  const chartTypes = useMemo(
    () => chartableMeasurementTypes(measurementTypesData?.types ?? []),
    [measurementTypesData],
  );
  const chartEligibleTypes = useMemo(
    () => new Set(chartTypes.map((measurementType) => measurementType.type)),
    [chartTypes],
  );
  const requestedTypeInfo = useMemo<MeasurementTypeInfo | undefined>(
    () => chartTypes.find((measurementType) => measurementType.type === requestedType),
    [chartTypes, requestedType],
  );
  const activeTypeInfo = useMemo<MeasurementTypeInfo | undefined>(
    () => requestedTypeInfo ?? chartTypes[0],
    [chartTypes, requestedTypeInfo],
  );
  const activeType = activeTypeInfo?.type ?? "";
  const typeLabel = (activeTypeInfo?.label ?? activeType) || "measurement";
  const vocabularyReady = !measurementTypesLoading && !measurementTypesError;
  const invalidChartUrl = !hasValidMeasurementUrlState(
    requestedType,
    since,
    until,
    chartEligibleTypes,
  );
  const chartQueryEnabled = vocabularyReady && !!activeType && !invalidChartUrl;
  const isBP = activeType === "blood_pressure";
  // The trend endpoint aggregates metadata.value as a scalar float. Compound
  // readings have a tab and raw-data view, but no implicit series key is safe.
  const supportsTrend = activeTypeInfo?.value_shape === "scalar";

  const hue = useChartColor(0);
  const secondaryHue = useChartColor(1);

  // --- Trend (the leading surface) ------------------------------------------
  const trendQuery = useMeasurementTrend(
    {
      type: supportsTrend ? activeType : "",
      window_days: windowDays,
      bucket: "daily",
    },
    { enabled: chartQueryEnabled && supportsTrend },
  );
  const buckets = useMemo(() => trendQuery.data?.buckets ?? [], [trendQuery.data]);
  // Show newest bucket first so the most relevant data is at the top.
  const reversedBuckets = useMemo(() => [...buckets].reverse(), [buckets]);

  // --- Raw measurements (chart + table) -------------------------------------
  const params: MeasurementParams = {
    type: activeType || undefined,
    since: invalidChartUrl ? undefined : since || undefined,
    until: invalidChartUrl ? undefined : until || undefined,
    limit: 500,
  };
  const { data, isLoading, isError, refetch } = useMeasurements(params, {
    enabled: chartQueryEnabled,
  });
  const measurements = useMemo(
    () => (invalidChartUrl ? [] : data?.data ?? []),
    [data, invalidChartUrl],
  );

  function setChartParam(key: "type" | "since" | "until", value: string) {
    setSearchParams(
      (previous) => {
        const next = new URLSearchParams(previous);
        if (value) next.set(key, value);
        else next.delete(key);
        return next;
      },
      { replace: true },
    );
  }

  function clearDateBounds() {
    setSearchParams(
      (previous) => {
        const next = new URLSearchParams(previous);
        next.delete("since");
        next.delete("until");
        return next;
      },
      { replace: true },
    );
  }

  // Build only semantically named series: scalars use the API's normalized
  // value key, while blood pressure has its explicit two-key contract.
  const chartData = useMemo<ChartPoint[]>(() => {
    if (!measurements.length || (!supportsTrend && !isBP)) return [];

    const sorted = [...measurements].sort(
      (a, b) => new Date(a.measured_at).getTime() - new Date(b.measured_at).getTime(),
    );

    if (isBP) {
      return sorted.map((m) => ({
        date: chartDate(m.measured_at),
        systolic: extractValue(m, "systolic"),
        diastolic: extractValue(m, "diastolic"),
      }));
    }

    return sorted.map((m) => ({
      date: chartDate(m.measured_at),
      value: extractValue(m, "value"),
    }));
  }, [measurements, isBP, supportsTrend]);
  const hasChartableValues = chartData.some((point) =>
    isBP
      ? point.systolic != null || point.diastolic != null
      : point.value != null,
  );

  if (measurementTypesLoading) {
    return (
      <div className="space-y-3" role="status" aria-label="Loading measurement chart types">
        <Skeleton className="h-7 w-72" />
        <Skeleton className="h-64 w-full" />
      </div>
    );
  }

  if (measurementTypesError) {
    return (
      <SourceDegradedNote
        label="Measurement chart types"
        detail="unavailable"
        onRetry={() => void refetchMeasurementTypes()}
        testId="measurement-types-degraded"
      />
    );
  }

  if (!activeTypeInfo) {
    return <EmptyLine>No chartable measurement types are available.</EmptyLine>;
  }

  return (
    <div className="space-y-5">
      {/* Type selector — Dispatch mono tabs */}
      <div className="flex flex-wrap items-center gap-1.5" role="tablist" aria-label="Measurement type">
        {chartTypes.map((measurementType) => {
          const active = activeType === measurementType.type;
          return (
            <button
              key={measurementType.type}
              type="button"
              role="tab"
              aria-selected={active}
              onClick={() => setChartParam("type", measurementType.type)}
              className={cn(
                "rounded-sm border px-2.5 py-1 font-mono text-[11px] uppercase tracking-[0.08em] transition-colors",
                active
                  ? "border-foreground bg-foreground text-background"
                  : "border-border bg-transparent text-muted-foreground hover:text-foreground",
              )}
            >
              {measurementType.label}
            </button>
          );
        })}
      </div>

      {/* Trend rule-list — the leading surface (mono-time / status-dot / value / →) */}
      <section aria-label="Measurement trend" className="space-y-2">
        <div className="flex items-center justify-between gap-3">
          <span className="font-mono text-[10px] uppercase tracking-[0.14em] text-muted-foreground">
            Trend · {typeLabel} · last {windowDays}d
          </span>
          <div className="flex items-center gap-1">
            {TREND_WINDOWS.map((w) => (
              <button
                key={w.value}
                type="button"
                aria-pressed={windowDays === w.value}
                onClick={() => setWindowDays(w.value)}
                className={cn(
                  "rounded-sm px-1.5 py-0.5 font-mono text-[10px] uppercase tracking-[0.08em] transition-colors",
                  windowDays === w.value
                    ? "text-foreground"
                    : "text-muted-foreground hover:text-foreground",
                )}
              >
                {w.label}
              </button>
            ))}
          </div>
        </div>

        {invalidChartUrl ? (
          <EmptyLine>That chart link has invalid type or date filters.</EmptyLine>
        ) : !supportsTrend ? (
          <EmptyLine>
            Trend aggregation is unavailable for compound {typeLabel} readings.
          </EmptyLine>
        ) : trendQuery.isLoading ? (
          <div className="space-y-2">
            {Array.from({ length: 4 }).map((_, i) => (
              <Skeleton key={i} className="h-6 w-full" />
            ))}
          </div>
        ) : trendQuery.isError ? (
          <SourceDegradedNote
            label={`${typeLabel} trend`}
            detail="trend source unavailable"
            onRetry={() => void trendQuery.refetch()}
            testId="measurement-trend-degraded"
          />
        ) : buckets.length === 0 ? (
          <EmptyLine>No trend for {typeLabel} in the last {windowDays} days.</EmptyLine>
        ) : (
          <div className="divide-y divide-border/60 border-y border-border/60">
            {reversedBuckets.map((b, i, arr) => {
              // arr[i + 1] is the chronologically prior bucket (reversed order).
              const prev = i < arr.length - 1 ? arr[i + 1] : null;
              const delta = prev ? b.value_mean - prev.value_mean : null;
              return (
                <div
                  key={b.bucket_start}
                  className="grid grid-cols-[12px_1fr_auto_14px] items-center gap-3 py-2"
                >
                  <span
                    className="h-2 w-2 rounded-full bg-muted-foreground/40"
                    aria-hidden="true"
                  />
                  <span className="font-mono text-[11px] text-muted-foreground tnum">
                    <Time value={b.bucket_start} mode="absolute" precision="day" compact />
                    <span className="ml-2 text-muted-foreground/60">n={b.sample_count}</span>
                  </span>
                  <span className="text-right font-mono text-[12.5px] text-foreground tnum">
                    {formatTrendValue(b.value_mean)}
                  </span>
                  <span
                    className="text-right font-mono text-[12px] text-muted-foreground"
                    aria-label={
                      delta == null || delta === 0
                        ? "no change"
                        : delta > 0
                          ? "trending up"
                          : "trending down"
                    }
                  >
                    {trendArrow(delta)}
                  </span>
                </div>
              );
            })}
          </div>
        )}
      </section>

      {/* Date range filters */}
      <div className="flex flex-wrap items-center gap-3">
        <div className="flex items-center gap-2">
          <label htmlFor="measurement-chart-since" className="font-mono text-[10px] uppercase tracking-[0.14em] text-muted-foreground">
            From
          </label>
          <input
            id="measurement-chart-since"
            type="date"
            value={since}
            onChange={(e) => setChartParam("since", e.target.value)}
            className="border-input bg-background ring-offset-background focus-visible:ring-ring flex h-9 w-40 rounded-md border px-3 py-2 text-sm focus-visible:ring-2 focus-visible:ring-offset-2 focus-visible:outline-none"
          />
        </div>
        <div className="flex items-center gap-2">
          <label htmlFor="measurement-chart-until" className="font-mono text-[10px] uppercase tracking-[0.14em] text-muted-foreground">
            To
          </label>
          <input
            id="measurement-chart-until"
            type="date"
            value={until}
            onChange={(e) => setChartParam("until", e.target.value)}
            className="border-input bg-background ring-offset-background focus-visible:ring-ring flex h-9 w-40 rounded-md border px-3 py-2 text-sm focus-visible:ring-2 focus-visible:ring-offset-2 focus-visible:outline-none"
          />
        </div>
        {(since || until) && (
          <Button
            variant="ghost"
            size="sm"
            onClick={clearDateBounds}
          >
            Clear
          </Button>
        )}
      </div>

      {/* Chart */}
      {invalidChartUrl ? null : isLoading ? (
        <Skeleton className="h-64 w-full" />
      ) : isError ? (
        <SourceDegradedNote
          label={`${typeLabel} readings`}
          detail="measurement source unavailable"
          onRetry={() => void refetch()}
          testId="measurement-readings-degraded"
        />
      ) : !supportsTrend && !isBP ? (
        <EmptyLine>
          No unambiguous {typeLabel} series is available for this range.
        </EmptyLine>
      ) : !hasChartableValues ? (
        <EmptyLine>No {typeLabel} readings for this range.</EmptyLine>
      ) : (
        <div className="h-72 w-full">
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={chartData} margin={{ top: 5, right: 20, bottom: 5, left: 0 }}>
              <CartesianGrid strokeDasharray="3 3" className="stroke-border/60" />
              <XAxis dataKey="date" className="text-xs" />
              <YAxis className="text-xs" />
              <Tooltip />
              {isBP ? (
                <>
                  <Line
                    type="monotone"
                    dataKey="systolic"
                    stroke={hue}
                    strokeWidth={2}
                    dot={false}
                    name="Systolic"
                  />
                  <Line
                    type="monotone"
                    dataKey="diastolic"
                    stroke={secondaryHue}
                    strokeOpacity={0.5}
                    strokeWidth={2}
                    strokeDasharray="4 3"
                    dot={false}
                    name="Diastolic"
                  />
                </>
              ) : (
                <Line
                  type="monotone"
                  dataKey="value"
                  stroke={hue}
                  strokeWidth={2}
                  dot={false}
                  name={typeLabel}
                />
              )}
            </LineChart>
          </ResponsiveContainer>
        </div>
      )}

      {/* Raw data table toggle */}
      {measurements.length > 0 && (
        <div className="space-y-2">
          <Button variant="outline" size="sm" onClick={() => setShowTable((v) => !v)}>
            {showTable ? "Hide" : "Show"} raw data
          </Button>
          {showTable && (
            <div className="divide-y divide-border/60 border-y border-border/60">
              <div className="grid grid-cols-[1fr_1fr_2fr] gap-3 py-2 font-mono text-[10px] uppercase tracking-[0.14em] text-muted-foreground">
                <span>Date</span>
                <span>Value</span>
                <span>Notes</span>
              </div>
              {measurements.map((m) => (
                <div key={m.id} className="grid grid-cols-[1fr_1fr_2fr] gap-3 py-2 text-sm">
                  <span className="font-mono text-[11px] text-muted-foreground tnum">
                    <Time value={m.measured_at} mode="absolute" precision="minute" compact />
                  </span>
                  <span className="font-mono text-[12px] text-foreground tnum">
                    {formatValue(m)}
                  </span>
                  <span className="max-w-xs truncate text-muted-foreground">
                    {m.notes ?? "—"}
                  </span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
