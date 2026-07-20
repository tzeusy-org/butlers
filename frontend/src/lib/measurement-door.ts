/** Typed, same-origin measurement doors carried by health insight metadata. */

export interface MeasurementDoor {
  type: string;
  since: string;
  until: string;
}

const MEASUREMENT_DOOR_CATEGORIES = new Set(["measurement-gap", "correlation-drift"]);
const DATE_ONLY = /^\d{4}-\d{2}-\d{2}$/;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** True only for a real ISO date with no time or locale-dependent interpretation. */
export function isDateOnly(value: string): boolean {
  if (!DATE_ONLY.test(value)) return false;
  const date = new Date(`${value}T00:00:00.000Z`);
  return Number.isFinite(date.getTime()) && date.toISOString().slice(0, 10) === value;
}

/** Date filters may be absent independently, but any supplied range must be ordered. */
export function hasValidMeasurementDateBounds(since: string, until: string): boolean {
  if ((since && !isDateOnly(since)) || (until && !isDateOnly(until))) return false;
  return !since || !until || since <= until;
}

/**
 * Raw URL state is query-safe only when its optional type is chart-eligible
 * and its optional date bounds are real and ordered.
 */
export function hasValidMeasurementUrlState(
  type: string,
  since: string,
  until: string,
  chartEligibleTypes: ReadonlySet<string>,
): boolean {
  return (!type || chartEligibleTypes.has(type)) && hasValidMeasurementDateBounds(since, until);
}

/**
 * Narrow untrusted insight metadata to the only typed measurement-door contract
 * the dashboard may use for navigation.
 */
export function measurementDoorFromInsight(
  category: string,
  metadata: unknown,
): MeasurementDoor | null {
  if (!MEASUREMENT_DOOR_CATEGORIES.has(category) || !isRecord(metadata)) return null;
  const door = metadata.measurement_door;
  if (!isRecord(door)) return null;

  const { type, since, until } = door;
  if (
    typeof type !== "string" ||
    !type ||
    type.trim() !== type ||
    typeof since !== "string" ||
    typeof until !== "string" ||
    !hasValidMeasurementDateBounds(since, until) ||
    !since ||
    !until
  ) {
    return null;
  }

  return { type, since, until };
}

/** Build a fixed same-origin destination after metadata has passed the guard. */
export function measurementDoorHref({ type, since, until }: MeasurementDoor): string {
  const params = new URLSearchParams({ type, since, until });
  return `/health/measurements?${params.toString()}`;
}
