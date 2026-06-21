// ---------------------------------------------------------------------------
// Medication schedule helpers
//
// Pure functions extracted from MedicationTracker.tsx so their edge-case-prone
// logic can be unit-tested directly. Kept out of the `.tsx` component file to
// avoid tripping the `react-refresh/only-export-components` eslint rule, which
// forbids non-component exports from component modules.
// ---------------------------------------------------------------------------

/**
 * Parse a schedule entry like "08:00" into minutes-since-midnight.
 *
 * Returns null for anything that is not a valid "HH:MM" 24-hour clock string
 * (non-strings, malformed text, out-of-range hours/minutes).
 */
export function parseScheduleTime(raw: unknown): number | null {
  if (typeof raw !== "string") return null;
  const m = /^(\d{1,2}):(\d{2})$/.exec(raw.trim());
  if (!m) return null;
  const hours = Number(m[1]);
  const mins = Number(m[2]);
  if (hours > 23 || mins > 59) return null;
  return hours * 60 + mins;
}
