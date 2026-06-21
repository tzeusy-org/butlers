/**
 * Pure geometry helpers for the calendar time-grid drag interactions
 * (create / move / resize). Kept separate from the page component so they can be
 * unit-tested and so the page file only exports its component (react-refresh).
 */

/** Height of each hour row in the time-axis grid (px). */
export const HOUR_HEIGHT_PX = 60;
/** Snap granularity for grid drag interactions (minutes). */
export const SNAP_MINUTES = 30;
/** Minutes in a full day, used to clamp grid drag math. */
export const MINUTES_PER_DAY = 24 * 60;

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
