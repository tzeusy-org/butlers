/** Deep links into the calendar page, and how the page reads them back. */

import { format } from "date-fns";

/** The hour the time grid scrolls to when no deep link names one. */
export const DEFAULT_SCROLL_HOUR = 7.5;

/** The calendar's day view for `date` (yyyy-MM-dd), optionally scrolled to a time of day. */
export function dayViewDoor(date: string, startIso?: string): string {
  const params = new URLSearchParams({ view: "user", range: "day", anchor: date });
  // The grid reads its hours in the browser's zone, so the scroll target is formatted there.
  if (startIso) params.set("time", format(new Date(startIso), "HH:mm"));
  return `/calendar?${params.toString()}`;
}

/** The hour to scroll the grid to: a valid `HH:mm` deep link (with half an hour of context), else the default. */
export function scrollHourFor(time: string | null): number {
  const match = time ? /^([01]\d|2[0-3]):([0-5]\d)$/.exec(time) : null;
  if (!match) return DEFAULT_SCROLL_HOUR;
  return Math.max(0, Number(match[1]) + Number(match[2]) / 60 - 0.5);
}
