import type { MeasurementTypeInfo } from "@/api/types";

const CORE_KPI_SLOTS = [
  { type: "weight", label: "Weight" },
  { type: "blood_pressure", label: "Blood pressure" },
  { type: "heart_rate", label: "Heart rate" },
  { type: "blood_sugar", label: "Blood sugar" },
] as const;

const CORE_KPI_TYPE_SET = new Set<string>(CORE_KPI_SLOTS.map((slot) => slot.type));

export interface KpiMeasurementSlot {
  type: string | null;
  label: string;
}

function fallbackLabel(type: string): string {
  const words = type
    .replace(/[_-]+/g, " ")
    .trim()
    .split(/\s+/)
    .filter(Boolean);
  return words.map((word) => `${word.slice(0, 1).toUpperCase()}${word.slice(1)}`).join(" ");
}

function normaliseMeasurementTypes(types: readonly MeasurementTypeInfo[]): MeasurementTypeInfo[] {
  const seen = new Set<string>();
  const normalised: MeasurementTypeInfo[] = [];

  for (const candidate of types) {
    const type = typeof candidate.type === "string" ? candidate.type.trim() : "";
    if (!type || seen.has(type)) continue;

    seen.add(type);
    const label = typeof candidate.label === "string" ? candidate.label.trim() : "";
    normalised.push({ ...candidate, type, label: label || fallbackLabel(type) });
  }

  return normalised;
}

/** Return only observed types whose values the API has declared chartable. */
export function chartableMeasurementTypes(
  types: readonly MeasurementTypeInfo[],
): MeasurementTypeInfo[] {
  return normaliseMeasurementTypes(types).filter((type) => type.chart_eligible);
}

function latestTimestamp(type: MeasurementTypeInfo): number {
  const timestamp = Date.parse(type.latest_at);
  return Number.isFinite(timestamp) ? timestamp : Number.NEGATIVE_INFINITY;
}

/**
 * Keep the four KPI cells structural. Core vital types retain their existing
 * positions whenever observed; a missing core position can be filled only by
 * a server-authorized dynamic candidate, newest first with a type-name tie
 * break. Remaining cells retain their core label with no requested value.
 */
export function selectKpiMeasurementSlots(
  types: readonly MeasurementTypeInfo[],
): [KpiMeasurementSlot, KpiMeasurementSlot, KpiMeasurementSlot, KpiMeasurementSlot] {
  const observed = normaliseMeasurementTypes(types);
  const byType = new Map(observed.map((type) => [type.type, type]));
  const dynamic = observed
    .filter((type) => !CORE_KPI_TYPE_SET.has(type.type) && type.kpi_eligible)
    .sort((left, right) => {
      const timestampDelta = latestTimestamp(right) - latestTimestamp(left);
      return timestampDelta !== 0 ? timestampDelta : left.type.localeCompare(right.type);
    });
  let dynamicIndex = 0;

  return CORE_KPI_SLOTS.map((core) => {
    const coreType = byType.get(core.type);
    if (coreType) return { type: coreType.type, label: coreType.label };

    const dynamicType = dynamic.at(dynamicIndex++);
    if (dynamicType) return { type: dynamicType.type, label: dynamicType.label };

    return { type: null, label: core.label };
  }) as [KpiMeasurementSlot, KpiMeasurementSlot, KpiMeasurementSlot, KpiMeasurementSlot];
}
