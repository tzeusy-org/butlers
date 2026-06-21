/**
 * Pure geometry helpers for the calendar time-grid drag interactions
 * (create / move / resize). Kept separate from the page component so they can be
 * unit-tested and so the page file only exports its component (react-refresh).
 *
 * Also home to the timezone-aware time helpers (bu-jtyzs): the calendar
 * workspace renders every event time in the configured *workspace* timezone
 * (`default_timezone` from meta), not the browser's local zone. These helpers
 * lean on `date-fns-tz` so day-bucketing, vertical placement, and the time
 * labels all agree on a single zone.
 */

import {
  addDays,
  addMonths,
  addWeeks,
  format as formatLocal,
  startOfDay,
  startOfMonth,
  startOfWeek,
} from "date-fns";
import { formatInTimeZone, fromZonedTime, toZonedTime } from "date-fns-tz";

/** Height of each hour row in the time-axis grid (px). */
export const HOUR_HEIGHT_PX = 60;
/** Snap granularity for grid drag interactions (minutes). */
export const SNAP_MINUTES = 30;
/** Minutes in a full day, used to clamp grid drag math. */
export const MINUTES_PER_DAY = 24 * 60;

/** Coerce an ISO string / epoch ms / Date into a Date (may be Invalid Date). */
function toDate(value: string | number | Date): Date {
  return value instanceof Date ? value : new Date(value);
}

/**
 * Format an event instant in the given IANA timezone. Returns `fallback` when
 * the value is missing or unparseable so callers never render "Invalid Date".
 */
export function formatEventTime(
  value: string | number | Date | null | undefined,
  tz: string,
  fmt: string,
  fallback = "",
): string {
  if (value === null || value === undefined || value === "") return fallback;
  const date = toDate(value);
  if (Number.isNaN(date.getTime())) return fallback;
  try {
    return formatInTimeZone(date, tz, fmt);
  } catch {
    return fallback;
  }
}

/**
 * Format an instant as a `yyyy-MM-dd'T'HH:mm` value for a `datetime-local`
 * input, rendered in `tz` (the workspace wall clock). Returns `fallback` when
 * the value is missing/unparseable so the form never shows "Invalid Date".
 *
 * Inverse of {@link dateTimeLocalToIso}; keeps create/edit form prefill in the
 * workspace zone rather than the browser's local zone.
 */
export function tzDateTimeLocalInput(
  value: string | number | Date | null | undefined,
  tz: string,
  fallback = "",
): string {
  if (value === null || value === undefined || value === "") return fallback;
  const date = toDate(value);
  if (Number.isNaN(date.getTime())) return fallback;
  try {
    return formatInTimeZone(date, tz, "yyyy-MM-dd'T'HH:mm");
  } catch {
    return fallback;
  }
}

/**
 * Parse a `datetime-local` wall-clock string (`yyyy-MM-dd'T'HH:mm`) as an
 * instant interpreted in `tz`, returning a UTC ISO string. Returns null when
 * blank or unparseable. Inverse of {@link tzDateTimeLocalInput}.
 */
export function dateTimeLocalToIso(value: string, tz: string): string | null {
  if (!value.trim()) return null;
  try {
    const instant = fromZonedTime(value, tz);
    return Number.isNaN(instant.getTime()) ? null : instant.toISOString();
  } catch {
    return null;
  }
}

/**
 * Calendar-day key (`yyyy-MM-dd`) for an instant, evaluated in `tz`. Used to
 * bucket events into the correct day column/section under the workspace zone.
 */
export function tzDayKey(value: string | number | Date, tz: string): string {
  const date = toDate(value);
  if (Number.isNaN(date.getTime())) return "";
  return formatInTimeZone(date, tz, "yyyy-MM-dd");
}

/** Calendar ranges the workspace can display. */
export type CalendarRange = "month" | "week" | "day" | "list";

/**
 * Visible calendar window for `range`, anchored in the workspace timezone `tz`.
 *
 * The column-facing fields (`start`, `end`) are *browser-local midnights* on the
 * workspace-tz calendar dates: `format(day, "yyyy-MM-dd")` on any day derived
 * from them yields the same key that {@link tzDayKey} buckets events under, and
 * the grid-drag helpers ({@link isoAtMinuteInTz}) — which read a day's local
 * `yyyy-MM-dd` — stay correct.
 *
 * `queryStart` / `queryEnd` are the UTC instants at the workspace-tz midnight
 * boundaries, so a backend fetch covers events that land on a visible workspace
 * day even when the browser zone differs from `tz` (e.g. an event at workspace
 * 00:30 Monday that is still Sunday in the browser's zone).
 *
 * When the browser zone equals `tz` (the common case) this is byte-for-byte the
 * old browser-local behaviour.
 */
export interface CalendarWindow {
  start: Date;
  end: Date;
  queryStart: string;
  queryEnd: string;
}

/**
 * The workspace-tz calendar date of `value`, returned as a browser-local
 * midnight Date so plain date-fns arithmetic/formatting stays in calendar-date
 * space (and matches {@link tzDayKey}). Falls back to the local start-of-day for
 * unparseable input.
 */
function tzCalendarDate(value: Date, tz: string): Date {
  const key = tzDayKey(value, tz);
  if (!key) return startOfDay(value);
  const [y, m, d] = key.split("-").map(Number);
  return new Date(y, m - 1, d);
}

/** UTC ISO instant at workspace-tz midnight of the calendar date `day` names. */
function zonedMidnightIso(day: Date, tz: string): string {
  return fromZonedTime(`${formatLocal(day, "yyyy-MM-dd")}T00:00:00`, tz).toISOString();
}

/**
 * Compute the visible {@link CalendarWindow} for `range`/`anchor` in workspace
 * timezone `tz`. See {@link CalendarWindow} for the column-vs-query split.
 */
export function tzCalendarWindow(range: CalendarRange, anchor: Date, tz: string): CalendarWindow {
  // Anchor on the workspace-tz calendar date of `anchor`, not the browser's, so
  // the window lines up with how events are bucketed (tzDayKey).
  const anchorCal = tzCalendarDate(anchor, tz);
  let start: Date;
  let end: Date;
  switch (range) {
    case "month": {
      start = startOfMonth(anchorCal);
      end = addMonths(start, 1);
      break;
    }
    case "day": {
      start = startOfDay(anchorCal);
      end = addDays(start, 1);
      break;
    }
    case "list": {
      start = startOfDay(anchorCal);
      end = addDays(start, 30);
      break;
    }
    case "week":
    default: {
      start = startOfWeek(anchorCal, { weekStartsOn: 1 });
      end = addWeeks(start, 1);
      break;
    }
  }
  return {
    start,
    end,
    queryStart: zonedMidnightIso(start, tz),
    queryEnd: zonedMidnightIso(end, tz),
  };
}

/**
 * Minute-of-day (0..1439) of an instant, evaluated in `tz`. Drives the vertical
 * placement of events on the time grid so an event at 09:00 workspace-local sits
 * at the 09:00 row regardless of the browser's zone.
 */
export function minuteOfDayInTz(value: string | number | Date, tz: string): number {
  const zoned = toZonedTime(toDate(value), tz);
  return zoned.getHours() * 60 + zoned.getMinutes();
}

/**
 * Build a UTC ISO string for `minutes` past midnight on `day`'s calendar date,
 * interpreting that wall-clock time in `tz`. Inverse of {@link minuteOfDayInTz};
 * keeps grid drag (move/resize) commits consistent with placement.
 *
 * `minutes` may exceed a day (e.g. an end at 24:00); the overflow rolls the
 * date forward so the resulting instant is still correct in `tz`.
 */
export function isoAtMinuteInTz(day: Date, minutes: number, tz: string): string {
  const dayCarry = Math.floor(minutes / MINUTES_PER_DAY);
  const within = minutes - dayCarry * MINUTES_PER_DAY;
  const dateStr = formatLocal(addDays(day, dayCarry), "yyyy-MM-dd");
  const hh = String(Math.floor(within / 60)).padStart(2, "0");
  const mm = String(within % 60).padStart(2, "0");
  return fromZonedTime(`${dateStr}T${hh}:${mm}:00`, tz).toISOString();
}

/** Snap a minute-of-day value to the nearest `step` boundary, clamped to [0, 1440]. */
export function snapMinutes(minutes: number, step: number = SNAP_MINUTES): number {
  const clamped = Math.min(MINUTES_PER_DAY, Math.max(0, minutes));
  return Math.round(clamped / step) * step;
}

/** Convert a vertical pixel offset within the time grid to minutes-of-day. */
export function offsetToMinutes(offsetY: number): number {
  return (offsetY / HOUR_HEIGHT_PX) * 60;
}

/**
 * Normalize a free-form drag (two minute marks in any order) into a snapped
 * `[startMin, endMin)` window with a minimum duration of `minDuration` minutes.
 */
export function normalizeDragWindow(
  aMin: number,
  bMin: number,
  minDuration: number = SNAP_MINUTES,
): { startMin: number; endMin: number } {
  let startMin = snapMinutes(Math.min(aMin, bMin));
  let endMin = snapMinutes(Math.max(aMin, bMin));
  if (endMin - startMin < minDuration) {
    endMin = startMin + minDuration;
  }
  if (endMin > MINUTES_PER_DAY) {
    endMin = MINUTES_PER_DAY;
    startMin = Math.max(0, endMin - minDuration);
  }
  return { startMin, endMin };
}

/**
 * Shift a fixed-duration window by `deltaMin` minutes, snapping the start and
 * keeping the whole window inside the day.
 */
export function shiftWindow(
  startMin: number,
  durationMin: number,
  deltaMin: number,
): { startMin: number; endMin: number } {
  let nextStart = snapMinutes(startMin + deltaMin);
  if (nextStart + durationMin > MINUTES_PER_DAY) {
    nextStart = MINUTES_PER_DAY - durationMin;
  }
  if (nextStart < 0) {
    nextStart = 0;
  }
  return { startMin: nextStart, endMin: nextStart + durationMin };
}

/**
 * Compute a snapped resize end given the fixed start and the pointer's minute,
 * enforcing a minimum duration and the day boundary.
 */
export function resizeWindowEnd(
  startMin: number,
  pointerMin: number,
  minDuration: number = SNAP_MINUTES,
): number {
  let endMin = snapMinutes(pointerMin);
  if (endMin < startMin + minDuration) {
    endMin = startMin + minDuration;
  }
  if (endMin > MINUTES_PER_DAY) {
    endMin = MINUTES_PER_DAY;
  }
  return endMin;
}
