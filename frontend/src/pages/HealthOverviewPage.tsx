/**
 * HealthOverviewPage -- editorial landing page for the Health butler.
 *
 * Route: /health (registered in router-config.tsx)
 *
 * Two-column editorial layout (1.4fr / 1fr), collapses to single column on
 * narrow viewports. Follows the Dispatch design language used by DashboardPage.
 *
 * Left column (narrative):
 *   - DateEyebrow + BriefingStatus pill (manual refresh via pill; NO auto-refresh)
 *   - Display headline: single most important current health fact
 *   - Voice elaboration paragraph
 *   - KpiStrip: 4 structural cells — observed core vitals retain their slots;
 *     eligible dynamic types may fill absent core positions, sourced from
 *     GET /api/health/measurements/types + /latest
 *   - Data-freshness chip from GET /api/health/measurements/sources
 *
 * Right column (indexes):
 *   - HealthLedgerIndex offers stable links to all six Health record surfaces
 *   - AttentionList sourced from GET /api/switchboard/insights?butler=health&status=pending
 *     Each item links to its signal; zero items → single serif-italic line
 *
 * Cost guards:
 *   - useHealthBriefing sets NO refetchInterval (5-min TTL + manual pill refresh)
 *   - useInsights sets NO refetchInterval (manual pill refresh)
 *   - useMeasurementTypes, useMeasurementsLatest, and useMeasurementSources
 *     use their deterministic 30s/60s intervals from use-health.ts
 *
 * Design contracts:
 *   - Health hue (--category-5 / "health" slot) ONLY on ButlerMark
 *   - No Card shells, no shadcn Card chrome
 *   - Display-500 headline (font-medium, not font-bold)
 *   - Absent readings render "—", never a fake number
 *   - Empty attention index: one serif-italic line, no decoration
 *
 * Spec: openspec/specs/dashboard-domain-pages/spec.md →
 *       "Health Overview landing page"
 *
 * bu-w7b18.1
 */

import { useMemo } from "react";

import { useHealthBriefing } from "@/hooks/use-health-briefing.ts";
import { useInsights } from "@/hooks/use-insights.ts";
import { useRegisterCommands, type PaletteCommand } from "@/lib/command-registry";
import { healthInsightSeverity } from "@/lib/health-insight-priority";
import {
  useMeasurementsLatest,
  useMeasurementSources,
  useMeasurementTypes,
} from "@/hooks/use-health.ts";
import { insightHref } from "@/lib/health-insight-links";
import {
  chartableMeasurementTypes,
  selectKpiMeasurementSlots,
} from "@/lib/measurement-vocabulary";
import type { LatestMeasurementEntry, MeasurementSource } from "@/api/types.ts";
import type { InsightCandidate } from "@/api/types.ts";

import { AttentionList } from "@/components/overview/AttentionList.tsx";
import type { AttentionListItem } from "@/components/overview/AttentionList.tsx";
import { HealthLedgerIndex } from "@/components/health/HealthLedgerIndex.tsx";
import { BriefingStatus } from "@/components/overview/BriefingStatus.tsx";
import { DateEyebrow } from "@/components/overview/DateEyebrow.tsx";
import { Elaboration } from "@/components/overview/Elaboration.tsx";
import { KpiStrip } from "@/components/overview/KpiStrip.tsx";
import { Section } from "@/components/overview/Section.tsx";
import { ButlerMark } from "@/components/ui/ButlerMark.tsx";
import { Display } from "@/components/ui/Display.tsx";
import { SourceDegradedNote } from "@/components/ui/query-boundary.tsx";

// ---------------------------------------------------------------------------
// KPI value helpers
// ---------------------------------------------------------------------------

/**
 * Format a scalar measurement value to a display string.
 * Returns "—" when absent, never a fake or placeholder value.
 */
function fmtScalar(
  entry: LatestMeasurementEntry | null | undefined,
  key?: string,
): string {
  if (!entry) return "—";
  const v = entry.value;
  if (typeof v === "number" || typeof v === "string") {
    return fmtNum(v);
  }
  if (v && typeof v === "object") {
    if (key) {
      const keyed = (v as Record<string, unknown>)[key];
      return fmtNum(keyed as string | number | null | undefined);
    }
    for (const k of ["value", "v", "amount", "reading", "bpm", "mg_dl", "kg", "lbs"]) {
      const keyed = (v as Record<string, unknown>)[k];
      if (keyed !== undefined && keyed !== null) {
        return fmtNum(keyed as string | number | null | undefined);
      }
    }
  }
  return "—";
}

function fmtNum(raw: string | number | null | undefined): string {
  if (raw === undefined || raw === null) return "—";
  if (typeof raw === "string" && !raw.trim()) return "—";
  const n = typeof raw === "string" ? Number(raw) : raw;
  if (!Number.isFinite(n)) return "—";
  return n % 1 === 0 ? String(n) : n.toFixed(1);
}

/**
 * Format a blood pressure entry as "systolic/diastolic" (e.g. "120/80").
 * Returns "—" when absent or missing both keys.
 */
function fmtBloodPressure(entry: LatestMeasurementEntry | null | undefined): string {
  if (!entry || typeof entry.value !== "object" || entry.value === null) return "—";
  const v = entry.value as Record<string, unknown>;
  const sys = fmtNum(v["systolic"] as string | number | null | undefined);
  const dia = fmtNum(v["diastolic"] as string | number | null | undefined);
  if (sys === "—" && dia === "—") return "—";
  return `${sys}/${dia}`;
}

// ---------------------------------------------------------------------------
// KPI freshness — per-vital age + SLA tint + source tooltip
// ---------------------------------------------------------------------------

/**
 * Per-vital freshness SLA in DAYS. Past this age, the KPI's age label reads
 * amber to flag a stale reading rather than presenting week-old data as
 * current (the "fabricated calm" this move removes). SLAs are vital-specific:
 * weight drifts slowly (a few days is fine), while heart rate and blood sugar
 * are expected roughly daily, so they go amber sooner. Documented constant,
 * tuned per vital — not a single global threshold.
 */
const VITAL_SLA_DAYS: Record<string, number> = {
  weight: 3,
  blood_pressure: 3,
  heart_rate: 2,
  blood_sugar: 2,
};

/**
 * Compact age of a reading for the KPI delta line (e.g. "7d", "3h", "now").
 * Returns null when the timestamp is absent or unparseable, so a cell with no
 * reading shows no age (never a fabricated "0d").
 */
function measurementAge(
  measuredAt: string | null | undefined,
): { label: string; days: number } | null {
  if (!measuredAt) return null;
  const ts = new Date(measuredAt).getTime();
  if (isNaN(ts)) return null;
  const ageMs = Date.now() - ts;
  const minutes = Math.floor(ageMs / 60_000);
  const days = ageMs / 86_400_000;
  let label: string;
  if (minutes < 1) label = "now";
  else if (minutes < 60) label = `${minutes}m`;
  else if (minutes < 1440) label = `${Math.floor(minutes / 60)}h`;
  else label = `${Math.floor(days)}d`;
  return { label, days };
}

/** Resolve a reading's data source from its metadata (canonical `source`,
 * falling back to the legacy `provider` key). */
function entrySource(entry: LatestMeasurementEntry | null | undefined): string | null {
  const meta = entry?.metadata;
  if (!meta) return null;
  const source = meta["source"] ?? meta["provider"];
  return typeof source === "string" && source ? source : null;
}

/**
 * Build a KPI cell that threads the reading's age (amber past the vital's SLA)
 * into the delta line and its data source into the cell tooltip.
 */
function buildVitalCell(
  eyebrow: string,
  type: string,
  value: string,
  entry: LatestMeasurementEntry | null | undefined,
): { eyebrow: string; value: string; delta?: string; deltaTone?: "muted" | "amber"; title?: string } {
  const age = entry ? measurementAge(entry.measured_at) : null;
  const source = entrySource(entry);
  const sla = VITAL_SLA_DAYS[type];
  const stale = age != null && sla != null && age.days > sla;
  return {
    eyebrow,
    value,
    delta: age?.label,
    deltaTone: stale ? "amber" : "muted",
    title: source ? `Source: ${source}` : undefined,
  };
}

// ---------------------------------------------------------------------------
// Source freshness chip
// ---------------------------------------------------------------------------

function freshnessLabel(lastSampleAt: string | null): string {
  if (!lastSampleAt) return "";
  const ts = new Date(lastSampleAt).getTime();
  if (isNaN(ts)) return "";
  const ageMs = Date.now() - ts;
  const minutes = Math.floor(ageMs / 60_000);
  if (minutes < 1) return "<1m ago";
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  return `${days}d ago`;
}

interface FreshnessChipsProps {
  sources: MeasurementSource[];
}

function FreshnessChips({ sources }: FreshnessChipsProps) {
  const chips = sources
    .map((s) => {
      const age = freshnessLabel(s.last_sample_at);
      return age ? { name: s.name, age } : null;
    })
    .filter((c): c is { name: string; age: string } => c !== null);

  if (chips.length === 0) return null;

  return (
    <div
      style={{ display: "flex", flexWrap: "wrap", gap: "6px" }}
      aria-label="Data freshness"
      data-testid="freshness-chips"
    >
      {chips.map((c) => (
        <span
          key={c.name}
          style={{
            fontFamily: "var(--font-mono)",
            fontSize: "9px",
            lineHeight: 1,
            color: "var(--muted-foreground)",
            border: "1px solid var(--border)",
            borderRadius: "var(--radius-sm)",
            padding: "2px 6px",
            whiteSpace: "nowrap",
          }}
        >
          {c.name} · synced {c.age}
        </span>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Insight → AttentionListItem adapter
// ---------------------------------------------------------------------------

function toAttentionItems(
  candidates: InsightCandidate[],
  chartEligibleTypes: ReadonlySet<string>,
): AttentionListItem[] {
  return candidates.map((c) => ({
    id: c.id,
    severity: healthInsightSeverity(c.priority),
    title: c.message,
    detail: null,
    href: insightHref(c, chartEligibleTypes),
  }));
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

const INSIGHT_PARAMS = { butler: "health", status: "pending" };

export default function HealthOverviewPage() {
  // --- Voice briefing (no refetchInterval — LLM cost guard) ---
  const {
    data: briefing,
    isFetching: briefingFetching,
    isError: briefingError,
    refetch: refetchBriefing,
  } = useHealthBriefing();

  // --- KPI measurement vocabulary + latest readings ---
  const {
    data: measurementTypesData,
    isLoading: measurementTypesLoading,
    isError: measurementTypesError,
    refetch: refetchMeasurementTypes,
  } = useMeasurementTypes();
  const measurementTypes = measurementTypesData?.types ?? [];
  const chartEligibleTypes = useMemo(
    () =>
      new Set(
        chartableMeasurementTypes(measurementTypes).map((measurementType) => measurementType.type),
      ),
    [measurementTypes],
  );
  const kpiSlots = selectKpiMeasurementSlots(measurementTypes);
  const kpiTypes = kpiSlots.flatMap((slot) => (slot.type ? [slot.type] : []));
  const {
    data: latestData,
    isLoading: latestMeasurementsLoading,
    isError: latestMeasurementsError,
    refetch: refetchLatestMeasurements,
  } = useMeasurementsLatest(
    measurementTypesLoading || measurementTypesError ? [] : kpiTypes,
  );
  const measurements = latestData?.measurements ?? {};

  // --- Source freshness ---
  const { data: sourcesData, isError: sourcesError } = useMeasurementSources();
  const sources = sourcesData ?? [];

  // --- Insight candidates (no refetchInterval — manual refresh via pill) ---
  const { data: insights, isError: insightsError, refetch: refetchInsights } =
    useInsights(INSIGHT_PARAMS);
  const attentionItems: AttentionListItem[] = insightsError
    ? [
        {
          id: "health:insights:source-error",
          severity: "high",
          title: "Health signals unavailable",
          detail: "Could not load the attention index.",
          href: null,
          isSourceError: true,
          onRetry: () => void refetchInsights(),
        },
      ]
    : toAttentionItems(insights ?? [], chartEligibleTypes);

  // --- Derived briefing values with safe fallbacks. A failed briefing fetch
  // must never render the indefinite "Health overview loading…" copy forever
  // -- that reads as still-loading when it is actually down (bu-86c4c.2,
  // JARVIS audit move 1b: "the suite speaks two control languages" finding
  // named this exact page/line range). ---
  const greet = briefing?.greet ?? "Good day.";
  const headline = briefingError
    ? "Briefing unavailable."
    : (briefing?.headline ?? "Health overview loading…");
  const elaboration = briefingError
    ? "Could not reach the health briefing service. Retry from the status pill above."
    : (briefing?.elaboration ??
      "Your health butler is composing a fresh briefing. Check back in a moment.");

  // --- KPI strip cells ---
  // Core slots stay in place when observed. If a future server contract marks
  // an alternate type KPI-eligible, the pure slot selector fills only an
  // absent core position, preserving the structural four-cell strip.
  const kpiLoading = measurementTypesLoading || latestMeasurementsLoading;
  const kpiCells = kpiSlots.map((slot) => {
    const entry = slot.type ? measurements[slot.type] ?? null : null;
    const value = slot.type === "blood_pressure" ? fmtBloodPressure(entry) : fmtScalar(entry);
    const cell = buildVitalCell(slot.label, slot.type ?? "", value, entry);
    return kpiLoading ? { ...cell, delta: "Loading…" } : cell;
  }) as [
    ReturnType<typeof buildVitalCell>,
    ReturnType<typeof buildVitalCell>,
    ReturnType<typeof buildVitalCell>,
    ReturnType<typeof buildVitalCell>,
  ];

  // Palette verbs (bu-t64p2 -- reachability sweep, bu-qvnce.11 slice 5).
  // Both refetches already exist (briefing pill, insights error-state retry)
  // -- surfaced unconditionally here rather than only on error.
  const healthCommands = useMemo<PaletteCommand[]>(
    () => [
      {
        id: "health-reload-briefing",
        label: "Reload health briefing",
        keywords: ["refresh", "reload", "briefing"],
        perform: () => void refetchBriefing(),
      },
      {
        id: "health-reload-attention",
        label: "Reload attention index",
        keywords: ["refresh", "reload", "insights", "signals"],
        perform: () => void refetchInsights(),
      },
    ],
    [refetchBriefing, refetchInsights],
  );
  useRegisterCommands(healthCommands);

  return (
    <div
      className="max-w-5xl"
      data-testid="health-overview-page"
    >
      {/*
       * Responsive two-column editorial grid.
       * Narrow (< lg / < 1024px): single column, left column on top.
       * Wide (≥ lg / ≥ 1024px): 1.4fr / 1fr, gap 56px (gap-14).
       */}
      <div className="grid gap-8 items-start lg:gap-14 lg:grid-cols-[1.4fr_1fr]">
        {/* ===================== LEFT COLUMN — narrative ===================== */}
        <div
          style={{ display: "flex", flexDirection: "column", gap: "28px" }}
          aria-label="Health briefing"
        >
          {/* Butler identity mark + date eyebrow + briefing status pill */}
          <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
            {/*
             * Health hue (--category-5) appears ONLY on ButlerMark.
             * No other chrome element uses the health category hue.
             */}
            <ButlerMark name="health" tone="fill" size={16} />
            <DateEyebrow
              statusSlot={
                <BriefingStatus
                  source={briefing?.source}
                  generatedAt={briefing?.generated_at}
                  isFetching={briefingFetching}
                  isError={briefingError}
                  onRefetch={() => { void refetchBriefing(); }}
                />
              }
            />
          </div>

          {/* Display headline — the single most important current health fact */}
          <div>
            <p
              style={{
                fontFamily: "var(--font-sans)",
                fontSize: "44px",
                fontWeight: 500,
                letterSpacing: "-0.025em",
                lineHeight: 1.08,
                color: "var(--muted-foreground)",
                maxWidth: "14ch",
              }}
              data-testid="health-greet"
            >
              {greet}
            </p>
            <Display
              style={{ maxWidth: "14ch" }}
              data-testid="health-headline"
            >
              {headline}
            </Display>
          </div>

          {/* Voice elaboration paragraph */}
          <Elaboration text={elaboration} isFetching={briefingFetching} />

          {/* KPI strip: exactly four structural cells. Observed core vital
              positions are retained and server-eligible dynamic types may fill
              an absent one. Cells fall back to "—" (never a fake number) when
              a reading is absent; a source error additionally gets a named
              degraded note below so "—" everywhere doesn't get mistaken for
              "no data logged". */}
          <Section eyebrow="Vitals">
            <KpiStrip cells={kpiCells} />
            {measurementTypesError && (
              <SourceDegradedNote
                label="Vitals"
                detail="measurement vocabulary unavailable, cells cannot be selected safely"
                onRetry={() => void refetchMeasurementTypes()}
                className="mt-2"
                testId="measurement-types-degraded"
              />
            )}
            {latestMeasurementsError && (
              <SourceDegradedNote
                label="Vitals"
                detail="measurements source unavailable, readings above may be stale or missing"
                onRetry={() => void refetchLatestMeasurements()}
                className="mt-2"
              />
            )}
          </Section>

          {/* Data-freshness chip(s) — only rendered when source data exists */}
          <FreshnessChips sources={sources} />
          {sourcesError && (
            <SourceDegradedNote label="Data freshness" detail="source unavailable" />
          )}
        </div>

        {/* ===================== RIGHT COLUMN — indexes ===================== */}
        <div
          style={{ display: "flex", flexDirection: "column", gap: "32px" }}
          aria-label="Health indexes"
          data-testid="health-attention-index"
        >
          <HealthLedgerIndex />
          <Section eyebrow="Needs attention">
            <AttentionList items={attentionItems} />
          </Section>
        </div>
      </div>
    </div>
  );
}
