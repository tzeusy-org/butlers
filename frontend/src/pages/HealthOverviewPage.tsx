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
 *   - KpiStrip: 4 cells — weight, blood_pressure, heart_rate, blood_sugar
 *     sourced from GET /api/health/measurements/latest
 *   - Data-freshness chip from GET /api/health/measurements/sources
 *
 * Right column (index):
 *   - AttentionList sourced from GET /api/switchboard/insights?butler=health&status=pending
 *     Each item links to its signal; zero items → single serif-italic line
 *
 * Cost guards:
 *   - useHealthBriefing sets NO refetchInterval (5-min TTL + manual pill refresh)
 *   - useInsights sets NO refetchInterval (manual pill refresh)
 *   - useMeasurementsLatest and useMeasurementSources use their deterministic
 *     30s/60s intervals from use-health.ts
 *
 * Design contracts:
 *   - Health hue (--category-5 / "health" slot) ONLY on ButlerMark
 *   - No Card shells, no shadcn Card chrome
 *   - Display-500 headline (font-medium, not font-bold)
 *   - Absent readings render "—", never a fake number
 *   - Empty attention index: one serif-italic line, no decoration
 *
 * Spec: openspec/changes/health-dashboard-overview-redesign/specs/
 *       dashboard-domain-pages/spec.md → "Health Overview landing page"
 *
 * bu-w7b18.1
 */

import { useHealthBriefing } from "@/hooks/use-health-briefing.ts";
import { useInsights } from "@/hooks/use-insights.ts";
import { useMeasurementsLatest, useMeasurementSources } from "@/hooks/use-health.ts";
import type { LatestMeasurementEntry, MeasurementSource } from "@/api/types.ts";
import type { InsightCandidate } from "@/api/types.ts";

import { AttentionList } from "@/components/overview/AttentionList.tsx";
import type { AttentionListItem } from "@/components/overview/AttentionList.tsx";
import { BriefingStatus } from "@/components/overview/BriefingStatus.tsx";
import { DateEyebrow } from "@/components/overview/DateEyebrow.tsx";
import { Elaboration } from "@/components/overview/Elaboration.tsx";
import { KpiStrip } from "@/components/overview/KpiStrip.tsx";
import { Section } from "@/components/overview/Section.tsx";
import { ButlerMark } from "@/components/ui/ButlerMark.tsx";
import { Display } from "@/components/ui/Display.tsx";

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
  const n = typeof raw === "string" ? parseFloat(raw) : raw;
  if (isNaN(n)) return "—";
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

/**
 * Map an InsightCandidate to a signal href.
 * Falls back to null (no link) when the category is not mapped.
 */
function insightHref(candidate: InsightCandidate): string | null {
  const { category, metadata } = candidate;
  // If the insight carries an explicit href in metadata, use it.
  if (
    metadata &&
    typeof (metadata as Record<string, unknown>)["href"] === "string"
  ) {
    return (metadata as Record<string, unknown>)["href"] as string;
  }
  // Map known health signal categories to their sub-pages.
  switch (category) {
    case "medication":
    case "adherence":
      return "/health/medications";
    case "measurement":
    case "blood_pressure":
    case "heart_rate":
    case "weight":
    case "blood_sugar":
      return "/health/measurements";
    case "symptom":
      return "/health/symptoms";
    case "condition":
      return "/health/conditions";
    case "meal":
    case "nutrition":
      return "/health/meals";
    default:
      return "/health/measurements";
  }
}

/** Severity label used by AttentionList to pick the glyph color. */
function insightSeverity(priority: number): string {
  if (priority <= 1) return "high";
  if (priority <= 2) return "medium";
  return "low";
}

function toAttentionItems(candidates: InsightCandidate[]): AttentionListItem[] {
  return candidates.map((c) => ({
    id: c.id,
    severity: insightSeverity(c.priority),
    title: c.message,
    detail: null,
    href: insightHref(c),
  }));
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

const KPI_TYPES = ["weight", "blood_pressure", "heart_rate", "blood_sugar"];
const INSIGHT_PARAMS = { butler: "health", status: "pending" };

export default function HealthOverviewPage() {
  // --- Voice briefing (no refetchInterval — LLM cost guard) ---
  const {
    data: briefing,
    isFetching: briefingFetching,
    refetch: refetchBriefing,
  } = useHealthBriefing();

  // --- KPI measurements latest ---
  const { data: latestData } = useMeasurementsLatest(KPI_TYPES);
  const measurements = latestData?.measurements ?? {};

  // --- Source freshness ---
  const { data: sourcesData } = useMeasurementSources();
  const sources = sourcesData ?? [];

  // --- Insight candidates (no refetchInterval — manual refresh via pill) ---
  const { data: insights } = useInsights(INSIGHT_PARAMS);
  const attentionItems = toAttentionItems(insights ?? []);

  // --- Derived briefing values with safe fallbacks ---
  const greet = briefing?.greet ?? "Good day.";
  const headline = briefing?.headline ?? "Health overview loading…";
  const elaboration =
    briefing?.elaboration ??
    "Your health butler is composing a fresh briefing. Check back in a moment.";

  // --- KPI strip cells ---
  const weightEntry = measurements["weight"] ?? null;
  const bpEntry = measurements["blood_pressure"] ?? null;
  const hrEntry = measurements["heart_rate"] ?? null;
  const bsEntry = measurements["blood_sugar"] ?? null;

  const kpiCells: [
    { eyebrow: string; value: string },
    { eyebrow: string; value: string },
    { eyebrow: string; value: string },
    { eyebrow: string; value: string },
  ] = [
    { eyebrow: "Weight", value: fmtScalar(weightEntry) },
    { eyebrow: "Blood pressure", value: fmtBloodPressure(bpEntry) },
    { eyebrow: "Heart rate", value: fmtScalar(hrEntry) },
    { eyebrow: "Blood sugar", value: fmtScalar(bsEntry) },
  ];

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

          {/* KPI strip: weight / blood_pressure / heart_rate / blood_sugar */}
          <Section eyebrow="Vitals">
            <KpiStrip cells={kpiCells} />
          </Section>

          {/* Data-freshness chip(s) — only rendered when source data exists */}
          <FreshnessChips sources={sources} />
        </div>

        {/* ===================== RIGHT COLUMN — index ===================== */}
        <div
          style={{ display: "flex", flexDirection: "column", gap: "32px" }}
          aria-label="Health attention index"
          data-testid="health-attention-index"
        >
          <Section eyebrow="Needs attention">
            <AttentionList items={attentionItems} />
          </Section>
        </div>
      </div>
    </div>
  );
}
