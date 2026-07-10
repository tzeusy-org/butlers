// ---------------------------------------------------------------------------
// Pure helpers for the owner work-schedule surface (bu-whhll.11).
//
// A routine's days are stored as a `dow_mask` bitmask over ISO weekday,
// bit 0 = Monday ... bit 6 = Sunday (`1 << date.weekday()`), matching the
// backend (chronicler.routines / routines.py). These helpers convert between
// that mask and a UI day set, and format days/windows for display. Kept pure
// (no React, no I/O) so they unit-test directly.
// ---------------------------------------------------------------------------

/** Short weekday labels, index 0 = Monday ... 6 = Sunday (ISO order). */
export const DOW_LABELS: readonly string[] = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

/** Ordered list of day indices (0..6) set in the mask. */
export function daysFromDowMask(mask: number): number[] {
  const days: number[] = [];
  for (let i = 0; i < 7; i++) {
    if (mask & (1 << i)) days.push(i);
  }
  return days;
}

/** Build a dow_mask from a collection of day indices (0..6). */
export function dowMaskFromDays(days: Iterable<number>): number {
  let mask = 0;
  for (const d of days) {
    if (d >= 0 && d <= 6) mask |= 1 << d;
  }
  return mask;
}

/**
 * Render a dow_mask as contiguous-range labels, e.g. "Mon–Fri" or "Mon, Wed".
 * Mirrors routines.py `_format_dow_ranges` (uses an en-dash for ranges).
 */
export function formatDowMask(mask: number): string {
  const days = daysFromDowMask(mask);
  if (days.length === 0) return "";
  const ranges: string[] = [];
  let i = 0;
  while (i < days.length) {
    let j = i;
    while (j + 1 < days.length && days[j + 1] === days[j] + 1) j++;
    ranges.push(i === j ? DOW_LABELS[days[i]] : `${DOW_LABELS[days[i]]}–${DOW_LABELS[days[j]]}`);
    i = j + 1;
  }
  return ranges.join(", ");
}

/**
 * Normalize a "HH:MM" or "HH:MM:SS" wall-clock string to "HH:MM" for display.
 * Returns the input unchanged when it does not match that shape.
 */
export function formatWindowTime(value: string): string {
  const m = /^(\d{2}):(\d{2})(?::\d{2})?$/.exec(value);
  return m ? `${m[1]}:${m[2]}` : value;
}

/** Format a routine window as "09:30–19:30". */
export function formatWindow(start: string, end: string): string {
  return `${formatWindowTime(start)}–${formatWindowTime(end)}`;
}

/**
 * Coerce an HTML time-input value ("HH:MM") to the "HH:MM:SS" the API expects.
 * A value already carrying seconds is returned unchanged.
 */
export function toApiTime(value: string): string {
  if (/^\d{2}:\d{2}$/.test(value)) return `${value}:00`;
  return value;
}

export interface ScheduleDraft {
  dowMask: number;
  windowStart: string; // "HH:MM"
  windowEnd: string; // "HH:MM"
  label: string;
}

/**
 * Validate a draft declaration. Returns null when valid, else a human-readable
 * reason. Mirrors the server's guards (>=1 day, same-day window, non-empty
 * label) so the owner sees the problem before a round trip.
 */
export function validateScheduleDraft(draft: ScheduleDraft): string | null {
  if (draft.dowMask <= 0) return "Pick at least one day.";
  if (!draft.label.trim()) return "Add a label (where you work).";
  if (!/^\d{2}:\d{2}$/.test(draft.windowStart) || !/^\d{2}:\d{2}$/.test(draft.windowEnd)) {
    return "Enter a start and end time.";
  }
  if (draft.windowEnd <= draft.windowStart) {
    return "End time must be after start time.";
  }
  return null;
}
