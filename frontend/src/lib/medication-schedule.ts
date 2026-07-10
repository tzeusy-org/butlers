// ---------------------------------------------------------------------------
// Medication schedule helpers
//
// Pure functions extracted from MedicationTracker.tsx so their edge-case-prone
// logic can be unit-tested directly. Kept out of the `.tsx` component file to
// avoid tripping the `react-refresh/only-export-components` eslint rule, which
// forbids non-component exports from component modules.
// ---------------------------------------------------------------------------

import type { Medication } from "@/api/types";

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

/**
 * Minutes-since-midnight of the wall clock in `timeZone` at instant `at`.
 *
 * Owner-authored schedule times ("08:00") are wall-clock times in the OWNER's
 * timezone, so "now" must be read in that same zone to decide which dose is
 * next — NOT in the viewer's host/browser timezone. Reading host-local "now"
 * (via `Date#getHours`) silently picks the wrong next dose whenever the viewer
 * is not in the owner's zone: a HEALTH-CRITICAL error for a medication tracker.
 *
 * Uses `Intl.DateTimeFormat` with an explicit `timeZone` (the app convention —
 * see `dayKeyInTimeZone` in `@/lib/day-window`) and `hourCycle: "h23"` so
 * midnight reads as 00, not 24. Falls back to UTC for an unusable timezone
 * rather than to host-local, which would reintroduce the very bug this closes.
 */
export function minutesOfDayInTimeZone(timeZone: string, at: Date = new Date()): number {
  const read = (tz: string): number => {
    const parts = new Intl.DateTimeFormat("en-GB", {
      timeZone: tz,
      hour: "2-digit",
      minute: "2-digit",
      hourCycle: "h23",
    }).formatToParts(at);
    const lookup = Object.fromEntries(parts.map((p) => [p.type, p.value]));
    return Number(lookup.hour) * 60 + Number(lookup.minute);
  };
  try {
    return read(timeZone);
  } catch {
    return read("UTC");
  }
}

export interface NextDose {
  medicationId: string;
  name: string;
  dosage: string;
  /** Minutes-from-now until the next scheduled time (for sorting). */
  minutesAway: number;
  /** "HH:MM" scheduled time. */
  time: string;
  /** True when the soonest occurrence is tomorrow. */
  tomorrow: boolean;
}

/**
 * Upcoming scheduled doses across active medications, soonest first.
 *
 * `timeZone` is the owner's IANA zone (from `useTimezone()`); both "now" and
 * the schedule-time comparison happen in that zone so the result is stable no
 * matter where the dashboard is being viewed from. `now` is injectable for
 * deterministic tests.
 */
export function computeNextDoses(
  medications: Medication[],
  timeZone: string,
  now: Date = new Date(),
): NextDose[] {
  const nowMinutes = minutesOfDayInTimeZone(timeZone, now);
  const next: NextDose[] = [];

  for (const med of medications) {
    if (!med.active) continue;
    let soonest: { minutesAway: number; minutesOfDay: number; tomorrow: boolean } | null = null;
    for (const entry of med.schedule ?? []) {
      const minutesOfDay = parseScheduleTime(entry);
      if (minutesOfDay == null) continue;
      const tomorrow = minutesOfDay < nowMinutes;
      const minutesAway = tomorrow ? minutesOfDay + 1440 - nowMinutes : minutesOfDay - nowMinutes;
      if (soonest == null || minutesAway < soonest.minutesAway) {
        soonest = { minutesAway, minutesOfDay, tomorrow };
      }
    }
    if (soonest) {
      const hh = String(Math.floor(soonest.minutesOfDay / 60)).padStart(2, "0");
      const mm = String(soonest.minutesOfDay % 60).padStart(2, "0");
      next.push({
        medicationId: med.id,
        name: med.name,
        dosage: med.dosage,
        minutesAway: soonest.minutesAway,
        time: `${hh}:${mm}`,
        tomorrow: soonest.tomorrow,
      });
    }
  }

  return next.sort((a, b) => a.minutesAway - b.minutesAway).slice(0, 8);
}
