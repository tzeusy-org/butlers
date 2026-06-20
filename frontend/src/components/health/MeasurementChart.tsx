import { useMemo, useState } from "react";
import { format } from "date-fns";
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
  MeasurementTrendWindowDays,
} from "@/api/types";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Time } from "@/components/ui/time";
import { cn } from "@/lib/utils";
import { useMeasurements, useMeasurementTrend } from "@/hooks/use-health";
import { butlerHueVar } from "@/components/ui/ButlerMark";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

// Only types the system can actually produce. The legacy `glucose`, `sleep`,
// and `oxygen` tabs are dropped — the create form can never produce them, so
// they yielded a perpetual "No data" state. `blood_sugar`, `spo2`, and `steps`
// are the real predicates the fact store carries.
const CHART_TYPES = [
  "weight",
  "blood_pressure",
  "heart_rate",
  "blood_sugar",
  "temperature",
  "spo2",
  "steps",
] as const;

// Trend lookback windows (days) — each wired to the real `window_days` query param.
const TREND_WINDOWS: { value: MeasurementTrendWindowDays; label: string }[] = [
  { value: 7, label: "7D" },
  { value: 14, label: "14D" },
  { value: 30, label: "30D" },
  { value: 90, label: "90D" },
];

// Fallback hue used only when the computed CSS variable is unavailable (e.g.
// jsdom in unit tests). Mirrors the light-mode value of the health hue token
// (currently --category-5, rose). Recharts needs a literal color string.
const HEALTH_HUE_FALLBACK = "oklch(0.641 0.140 11.2)";

// Derive the CSS property name from the canonical butler-hue helper so this
// always tracks the mark's slot — no separate constant to keep in sync.
// butlerHueVar("health") returns e.g. "var(--category-5)"; slice off the wrapper.
const HEALTH_HUE_PROP = butlerHueVar("health").slice(4, -1);

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

export interface MeasurementChartProps {
  /** Override the default type filter. */
  initialType?: string;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/**
 * Resolve the health butler's hue to a literal color string for Recharts.
 * Recharts cannot consume a CSS custom property directly, so we read the live
 * computed value of the token that `butlerHueVar("health")` resolves to.
 * The diastolic line reuses the same hue at reduced opacity.
 */
function useCategoryHue(): string {
  const [hue] = useState<string>(() => {
    if (typeof document === "undefined") return HEALTH_HUE_FALLBACK;
    const value = getComputedStyle(document.documentElement)
      .getPropertyValue(HEALTH_HUE_PROP)
      .trim();
    return value || HEALTH_HUE_FALLBACK;
  });
  return hue;
}

/** Extract a numeric value from a measurement for charting. */
function extractValue(m: Measurement, key: string): number | null {
  const v = m.value[key];
  if (typeof v === "number") return v;
  if (typeof v === "string") {
    const n = Number(v);
    return Number.isNaN(n) ? null : n;
  }
  return null;
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

export default function MeasurementChart({ initialType }: MeasurementChartProps) {
  const [activeType, setActiveType] = useState<string>(initialType ?? "weight");
  const [windowDays, setWindowDays] = useState<MeasurementTrendWindowDays>(14);
  const [since, setSince] = useState("");
  const [until, setUntil] = useState("");
  const [showTable, setShowTable] = useState(false);

  const hue = useCategoryHue();

  // --- Trend (the leading surface) ------------------------------------------
  const trendQuery = useMeasurementTrend({
    type: activeType,
    window_days: windowDays,
    bucket: "daily",
  });
  const buckets = useMemo(() => trendQuery.data?.buckets ?? [], [trendQuery.data]);
  // Show newest bucket first so the most relevant data is at the top.
  const reversedBuckets = useMemo(() => [...buckets].reverse(), [buckets]);

  // --- Raw measurements (chart + table) -------------------------------------
  const params: MeasurementParams = {
    type: activeType,
    since: since || undefined,
    until: until || undefined,
    limit: 500,
  };
  const { data, isLoading } = useMeasurements(params);
  const measurements = useMemo(() => data?.data ?? [], [data]);

  const isBP = activeType === "blood_pressure";

  // Build chart data
  const chartData = useMemo(() => {
    if (!measurements.length) return [];

    const sorted = [...measurements].sort(
      (a, b) => new Date(a.measured_at).getTime() - new Date(b.measured_at).getTime(),
    );

    if (isBP) {
      return sorted.map((m) => ({
        date: format(new Date(m.measured_at), "MMM d"),
        systolic: extractValue(m, "systolic"),
        diastolic: extractValue(m, "diastolic"),
      }));
    }

    return sorted.map((m) => {
      const keys = Object.keys(m.value);
      const key = keys.includes("value") ? "value" : (keys[0] ?? "value");
      return {
        date: format(new Date(m.measured_at), "MMM d"),
        value: extractValue(m, key),
      };
    });
  }, [measurements, isBP]);

  const typeLabel = activeType.replace(/_/g, " ");

  return (
    <div className="space-y-5">
      {/* Type selector — Dispatch mono tabs */}
      <div className="flex flex-wrap items-center gap-1.5" role="tablist" aria-label="Measurement type">
        {CHART_TYPES.map((t) => {
          const active = activeType === t;
          return (
            <button
              key={t}
              type="button"
              role="tab"
              aria-selected={active}
              onClick={() => setActiveType(t)}
              className={cn(
                "rounded-sm border px-2.5 py-1 font-mono text-[11px] uppercase tracking-[0.08em] transition-colors",
                active
                  ? "border-foreground bg-foreground text-background"
                  : "border-border bg-transparent text-muted-foreground hover:text-foreground",
              )}
            >
              {t.replace(/_/g, " ")}
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

        {trendQuery.isLoading ? (
          <div className="space-y-2">
            {Array.from({ length: 4 }).map((_, i) => (
              <Skeleton key={i} className="h-6 w-full" />
            ))}
          </div>
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
          <label className="font-mono text-[10px] uppercase tracking-[0.14em] text-muted-foreground">
            From
          </label>
          <input
            type="date"
            value={since}
            onChange={(e) => setSince(e.target.value)}
            className="border-input bg-background ring-offset-background focus-visible:ring-ring flex h-9 w-40 rounded-md border px-3 py-2 text-sm focus-visible:ring-2 focus-visible:ring-offset-2 focus-visible:outline-none"
          />
        </div>
        <div className="flex items-center gap-2">
          <label className="font-mono text-[10px] uppercase tracking-[0.14em] text-muted-foreground">
            To
          </label>
          <input
            type="date"
            value={until}
            onChange={(e) => setUntil(e.target.value)}
            className="border-input bg-background ring-offset-background focus-visible:ring-ring flex h-9 w-40 rounded-md border px-3 py-2 text-sm focus-visible:ring-2 focus-visible:ring-offset-2 focus-visible:outline-none"
          />
        </div>
        {(since || until) && (
          <Button
            variant="ghost"
            size="sm"
            onClick={() => {
              setSince("");
              setUntil("");
            }}
          >
            Clear
          </Button>
        )}
      </div>

      {/* Chart */}
      {isLoading ? (
        <Skeleton className="h-64 w-full" />
      ) : chartData.length === 0 ? (
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
                    stroke={hue}
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
