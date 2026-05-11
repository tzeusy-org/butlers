/**
 * ButlerHealthMeasurementsTab
 *
 * Measurements bespoke tab for the health butler detail page.
 * Replaces the old inline ButlerHealthTab 6-link directory with a real data grid.
 *
 * Five rows (4-col grid):
 *  1. KPI quartet — glucose / HRV / steps / sleep duration (GET /measurements/latest)
 *  2. Trend panels — glucose trend (span 2) + heart rate trend (span 2)   [14d]
 *  3. Trend panels — HRV trend (span 2) + weight trend (span 2)           [14d]
 *  4. Sleep stages bar (span 2) + sources (span 2)
 *  5. Active medications (span 2) + recent conditions (span 2)
 *
 * Trend panels: 14-day window from the existing paginated /measurements endpoint.
 * The existing useMeasurements hook is reused for trends; no separate trend hook exists.
 *
 * Drilldown link to /health/measurements is preserved so the deeper measurements
 * page continues to work as a route.
 *
 * bead: bu-iuol4.23
 */

import { useMemo } from "react";
import { Link } from "react-router";

import type {
  LatestMeasurementEntry,
  Measurement,
  MeasurementSource,
  Medication,
  HealthCondition,
  SleepLatestResponse,
} from "@/api/index.ts";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Time } from "@/components/ui/time";
import {
  useMeasurementsLatest,
  useSleepLatest,
  useMeasurementSources,
  useMeasurements,
  useMedications,
  useConditions,
} from "@/hooks/use-health";

// ---------------------------------------------------------------------------
// Shared UI helpers — loading / empty
// ---------------------------------------------------------------------------

function LoadingLine({ testId }: { testId?: string }) {
  return (
    <p className="text-sm text-muted-foreground" data-testid={testId ?? "loading-line"}>
      Loading…
    </p>
  );
}

function EmptyLine({ children }: { children: React.ReactNode }) {
  return (
    <p className="text-sm text-muted-foreground italic" data-testid="empty-state-line">
      {children}
    </p>
  );
}

// ---------------------------------------------------------------------------
// Format helpers
// ---------------------------------------------------------------------------

/** Round a numeric string or number to at most 1 decimal place. */
function fmtNum(raw: string | number | undefined | null): string {
  if (raw === undefined || raw === null) return "—";
  const n = typeof raw === "string" ? parseFloat(raw) : raw;
  if (isNaN(n)) return "—";
  return n % 1 === 0 ? String(n) : n.toFixed(1);
}

/** Extract a scalar value from a JSONB measurement value field. */
function extractScalar(
  entry: LatestMeasurementEntry | null | undefined,
  key?: string,
): string {
  if (!entry) return "—";
  const v = entry.value;
  if (key && typeof v === "object" && v !== null) {
    const keyed = (v as Record<string, unknown>)[key];
    return fmtNum(keyed as string | number);
  }
  // Try common keys: value, v, amount, reading
  for (const k of ["value", "v", "amount", "reading"]) {
    const keyed = (v as Record<string, unknown>)[k];
    if (keyed !== undefined && keyed !== null) return fmtNum(keyed as string | number);
  }
  // Scalar JSONB
  if (typeof v === "number") return fmtNum(v);
  return "—";
}

/** Format total minutes as "Xh Ym" or "Xm". */
function fmtMinutes(min: number | null | undefined): string {
  if (min === null || min === undefined) return "—";
  const h = Math.floor(min / 60);
  const m = min % 60;
  if (h === 0) return `${m}m`;
  if (m === 0) return `${h}h`;
  return `${h}h ${m}m`;
}

// ---------------------------------------------------------------------------
// Row 1: KPI quartet
// ---------------------------------------------------------------------------

interface KpiCellProps {
  label: string;
  value: string;
  unit?: string | null;
  isLoading: boolean;
}

function KpiCell({ label, value, unit, isLoading }: KpiCellProps) {
  return (
    <Card data-testid="kpi-cell">
      <CardContent className="pt-4">
        <p className="text-xs text-muted-foreground">{label}</p>
        <p className="text-2xl font-bold font-mono tnum truncate" data-testid="kpi-value">
          {isLoading ? "…" : value}
        </p>
        {unit && !isLoading && value !== "—" && (
          <p className="text-xs text-muted-foreground">{unit}</p>
        )}
      </CardContent>
    </Card>
  );
}

function KpiQuartet({
  measurements,
  sleep,
  isLoading,
}: {
  measurements: Record<string, LatestMeasurementEntry | null>;
  sleep: SleepLatestResponse | undefined;
  isLoading: boolean;
}) {
  const glucoseEntry = measurements["glucose"] ?? null;
  const hrvEntry = measurements["hrv"] ?? null;
  const stepsEntry = measurements["steps"] ?? null;

  const sleepDuration = sleep?.total_minutes;

  const kpis: KpiCellProps[] = [
    {
      label: "Glucose",
      value: extractScalar(glucoseEntry),
      unit: glucoseEntry?.unit ?? "mg/dL",
      isLoading,
    },
    {
      label: "HRV",
      value: extractScalar(hrvEntry),
      unit: hrvEntry?.unit ?? "ms",
      isLoading,
    },
    {
      label: "Steps",
      value: extractScalar(stepsEntry),
      unit: stepsEntry?.unit ?? null,
      isLoading,
    },
    {
      label: "Sleep duration",
      value: fmtMinutes(sleepDuration),
      unit: null,
      isLoading,
    },
  ];

  return (
    <div className="grid grid-cols-2 gap-3 lg:grid-cols-4" data-testid="health-kpi-quartet">
      {kpis.map((kpi) => (
        <KpiCell key={kpi.label} {...kpi} />
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Trend helpers
// ---------------------------------------------------------------------------

/**
 * Build a 14-day ISO window (since/until) relative to now.
 * Returns ISO strings suitable for passing as URL query params.
 */
function fourteenDayWindow(): { since: string; until: string } {
  const until = new Date();
  const since = new Date(until);
  since.setDate(since.getDate() - 14);
  return {
    since: since.toISOString(),
    until: until.toISOString(),
  };
}

/** Summarise a list of Measurement records as sparkline-like text. */
function TrendSummary({
  measurements,
  isLoading,
  label,
  valueKey,
  unit,
}: {
  measurements: Measurement[];
  isLoading: boolean;
  label: string;
  valueKey?: string;
  unit?: string;
}) {
  const points = useMemo(() => {
    return measurements
      .slice()
      .sort((a, b) => new Date(a.measured_at).getTime() - new Date(b.measured_at).getTime())
      .slice(-5); // last 5 readings
  }, [measurements]);

  if (isLoading) {
    return <LoadingLine />;
  }

  if (points.length === 0) {
    return <EmptyLine>No {label.toLowerCase()} readings in the last 14 days.</EmptyLine>;
  }

  return (
    <ol
      className="divide-y text-sm"
      aria-label={`${label} trend · last ${points.length} readings`}
      data-testid="trend-list"
    >
      {points.map((m) => {
        const val = extractScalar(
          { measured_at: m.measured_at, value: m.value, unit: null, metadata: null },
          valueKey,
        );
        return (
          <li
            key={m.id}
            className="flex items-center justify-between py-1.5 gap-2"
            data-testid="trend-row"
          >
            <span className="text-muted-foreground text-xs">
              <Time value={m.measured_at} mode="absolute" precision="day" compact />
            </span>
            <span className="font-mono tnum font-medium">
              {val}
              {unit && val !== "—" ? <span className="ml-1 text-xs text-muted-foreground">{unit}</span> : null}
            </span>
          </li>
        );
      })}
    </ol>
  );
}

// ---------------------------------------------------------------------------
// Row 2+3: Trend panels
// ---------------------------------------------------------------------------

function TrendPanel({
  title,
  type,
  valueKey,
  unit,
  drilldownLink,
}: {
  title: string;
  type: string;
  valueKey?: string;
  unit?: string;
  drilldownLink?: string;
}) {
  const { since, until } = useMemo(() => fourteenDayWindow(), []);
  const { data, isLoading } = useMeasurements({ type, since, until, limit: 50 });
  const measurements = data?.data ?? [];

  return (
    <Card data-testid={`trend-panel-${type}`}>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm font-medium flex items-center justify-between">
          {title} · 14d
          {drilldownLink && (
            <Button variant="ghost" size="sm" asChild className="text-xs text-muted-foreground">
              <Link to={drilldownLink}>View all</Link>
            </Button>
          )}
        </CardTitle>
      </CardHeader>
      <CardContent>
        <TrendSummary
          measurements={measurements}
          isLoading={isLoading}
          label={title}
          valueKey={valueKey}
          unit={unit}
        />
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Row 4a: Sleep stages bar
// ---------------------------------------------------------------------------

const STAGE_COLOR: Record<string, string> = {
  awake: "bg-amber-400",
  light: "bg-sky-300",
  deep: "bg-sky-600",
  rem: "bg-violet-500",
};

function SleepStagesPanel({ sleep, isLoading }: { sleep: SleepLatestResponse | undefined; isLoading: boolean }) {
  const stages = sleep?.stages ?? [];
  const totalMinutes = stages.reduce((sum, s) => sum + s.duration_minutes, 0);

  return (
    <Card data-testid="sleep-stages-panel">
      <CardHeader className="pb-2">
        <CardTitle className="text-sm font-medium">
          Sleep stages
          {sleep?.session_date ? (
            <span className="ml-2 text-xs font-normal text-muted-foreground">
              <Time value={sleep.session_date} mode="absolute" precision="day" compact />
            </span>
          ) : null}
        </CardTitle>
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <LoadingLine />
        ) : stages.length === 0 ? (
          <EmptyLine>No sleep data recorded.</EmptyLine>
        ) : (
          <div data-testid="sleep-stages-bar">
            {/* Stacked proportion bar */}
            <div
              className="flex h-6 w-full rounded overflow-hidden mb-3"
              aria-label="Sleep stage distribution"
              role="img"
            >
              {stages.map((s, idx) => {
                const pct = totalMinutes > 0 ? (s.duration_minutes / totalMinutes) * 100 : 0;
                const cls = STAGE_COLOR[s.stage] ?? "bg-muted";
                return (
                  <div
                    key={`${s.stage}-${idx}`}
                    className={cls}
                    style={{ width: `${pct.toFixed(1)}%` }}
                    title={`${s.stage}: ${fmtMinutes(s.duration_minutes)}`}
                    data-testid={`sleep-stage-${s.stage}`}
                  />
                );
              })}
            </div>
            {/* Legend */}
            <ul className="space-y-1" aria-label="Sleep stage legend">
              {stages.map((s, idx) => {
                const cls = STAGE_COLOR[s.stage] ?? "bg-muted";
                return (
                  <li
                    key={`${s.stage}-${idx}`}
                    className="flex items-center justify-between text-sm"
                    data-testid="sleep-stage-row"
                  >
                    <span className="flex items-center gap-1.5">
                      <span className={`inline-block h-2 w-2 rounded-full ${cls}`} aria-hidden />
                      <span className="capitalize">{s.stage}</span>
                    </span>
                    <span className="font-mono tnum text-muted-foreground text-xs">
                      {fmtMinutes(s.duration_minutes)}
                    </span>
                  </li>
                );
              })}
            </ul>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Row 4b: Measurement sources
// ---------------------------------------------------------------------------

function SourcesPanel({
  sources,
  isLoading,
}: {
  sources: MeasurementSource[];
  isLoading: boolean;
}) {
  return (
    <Card data-testid="sources-panel">
      <CardHeader className="pb-2">
        <CardTitle className="text-sm font-medium">Measurement sources</CardTitle>
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <LoadingLine />
        ) : sources.length === 0 ? (
          <EmptyLine>No sources connected.</EmptyLine>
        ) : (
          <ul className="divide-y" data-testid="sources-list">
            {sources.map((src) => (
              <li
                key={src.name}
                className="flex items-center justify-between py-2 gap-2"
                data-testid="source-row"
              >
                <div className="min-w-0">
                  <p className="text-sm font-medium truncate capitalize">{src.name}</p>
                  {src.last_sample_at ? (
                    <p className="text-xs text-muted-foreground">
                      Last sample: <Time value={src.last_sample_at} mode="relative-compact" />
                    </p>
                  ) : (
                    <p className="text-xs text-muted-foreground">No samples yet</p>
                  )}
                </div>
                <Badge variant="outline" className="shrink-0 font-mono tnum text-xs">
                  {src.sample_count.toLocaleString()}
                </Badge>
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Row 5a: Active medications
// ---------------------------------------------------------------------------

function ActiveMedicationsPanel({
  medications,
  isLoading,
}: {
  medications: Medication[];
  isLoading: boolean;
}) {
  const active = medications.filter((m) => m.active);

  return (
    <Card data-testid="active-medications-panel">
      <CardHeader className="pb-2">
        <CardTitle className="text-sm font-medium flex items-center justify-between">
          Active medications
          <Button variant="ghost" size="sm" asChild className="text-xs text-muted-foreground">
            <Link to="/health/medications">View all</Link>
          </Button>
        </CardTitle>
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <LoadingLine />
        ) : active.length === 0 ? (
          <EmptyLine>No active medications.</EmptyLine>
        ) : (
          <ul className="divide-y" data-testid="medications-list">
            {active.map((med) => (
              <li key={med.id} className="py-2" data-testid="medication-row">
                <p className="text-sm font-medium">{med.name}</p>
                <p className="text-xs text-muted-foreground">
                  {med.dosage} · {med.frequency}
                </p>
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Row 5b: Recent conditions
// ---------------------------------------------------------------------------

const CONDITION_STATUS_VARIANT: Record<string, "default" | "secondary" | "outline" | "destructive"> = {
  active: "destructive",
  managed: "default",
  resolved: "secondary",
};

function RecentConditionsPanel({
  conditions,
  isLoading,
}: {
  conditions: HealthCondition[];
  isLoading: boolean;
}) {
  return (
    <Card data-testid="recent-conditions-panel">
      <CardHeader className="pb-2">
        <CardTitle className="text-sm font-medium flex items-center justify-between">
          Recent conditions
          <Button variant="ghost" size="sm" asChild className="text-xs text-muted-foreground">
            <Link to="/health/conditions">View all</Link>
          </Button>
        </CardTitle>
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <LoadingLine />
        ) : conditions.length === 0 ? (
          <EmptyLine>No conditions recorded.</EmptyLine>
        ) : (
          <ul className="divide-y" data-testid="conditions-list">
            {conditions.map((c) => (
              <li
                key={c.id}
                className="flex items-center justify-between py-2 gap-2"
                data-testid="condition-row"
              >
                <div className="min-w-0">
                  <p className="text-sm font-medium truncate">{c.name}</p>
                  {c.diagnosed_at ? (
                    <p className="text-xs text-muted-foreground">
                      Diagnosed <Time value={c.diagnosed_at} mode="absolute" precision="day" compact />
                    </p>
                  ) : null}
                </div>
                <Badge
                  variant={CONDITION_STATUS_VARIANT[c.status] ?? "outline"}
                  className="shrink-0 capitalize text-xs"
                >
                  {c.status}
                </Badge>
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// ButlerHealthMeasurementsTab — composed entry point
// ---------------------------------------------------------------------------

export default function ButlerHealthMeasurementsTab() {
  // Row 1: KPI quartet
  const { data: latestData, isLoading: latestLoading } = useMeasurementsLatest([
    "glucose",
    "hrv",
    "steps",
  ]);
  const measurements = latestData?.measurements ?? {};

  // Row 1: Sleep duration (from sleep/latest)
  const { data: sleepData, isLoading: sleepLoading } = useSleepLatest();

  // Row 4b: Sources
  const { data: sourcesData, isLoading: sourcesLoading } = useMeasurementSources();
  const sources = sourcesData ?? [];

  // Row 5: Medications + conditions
  const { data: medsData, isLoading: medsLoading } = useMedications({ active: true, limit: 20 });
  const medications = medsData?.data ?? [];

  const { data: condData, isLoading: condLoading } = useConditions({ limit: 10 });
  const conditions = condData?.data ?? [];

  const kpiLoading = latestLoading || sleepLoading;

  return (
    <div className="space-y-4 pt-4" data-testid="health-measurements-tab">
      {/* Row 1: KPI quartet */}
      <KpiQuartet
        measurements={measurements}
        sleep={sleepData}
        isLoading={kpiLoading}
      />

      {/* Row 2: Glucose trend + Heart rate trend */}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <TrendPanel
          title="Glucose"
          type="glucose"
          unit="mg/dL"
          drilldownLink="/health/measurements"
        />
        <TrendPanel
          title="Heart rate"
          type="heart_rate"
          unit="bpm"
          drilldownLink="/health/measurements"
        />
      </div>

      {/* Row 3: HRV trend + Weight trend */}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <TrendPanel
          title="HRV"
          type="hrv"
          unit="ms"
        />
        <TrendPanel
          title="Weight"
          type="weight"
          unit="kg"
        />
      </div>

      {/* Row 4: Sleep stages + Sources */}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <SleepStagesPanel sleep={sleepData} isLoading={sleepLoading} />
        <SourcesPanel sources={sources} isLoading={sourcesLoading} />
      </div>

      {/* Row 5: Active medications + Recent conditions */}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <ActiveMedicationsPanel medications={medications} isLoading={medsLoading} />
        <RecentConditionsPanel conditions={conditions} isLoading={condLoading} />
      </div>
    </div>
  );
}
