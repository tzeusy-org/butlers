/**
 * Pure helpers for the printable calendar agenda (bu-8yi687).
 *
 * Kept separate from the React component so they can be unit-tested directly and
 * so the component file only exports components (react-refresh constraint).
 */

import { format } from "date-fns";

import type { UnifiedCalendarEntry } from "@/api/types.ts";

/** Stable day key (local time) used for grouping + sort. */
export function dayKey(iso: string): string {
  return format(new Date(iso), "yyyy-MM-dd");
}

/** Human day heading, e.g. "Monday, 22 February 2026". */
export function dayHeading(iso: string): string {
  return format(new Date(iso), "EEEE, d MMMM yyyy");
}

/** Time range label for one entry; all-day entries read "All day". */
export function timeLabel(entry: UnifiedCalendarEntry): string {
  if (entry.all_day) return "All day";
  const start = format(new Date(entry.start_at), "h:mm a");
  const end = format(new Date(entry.end_at), "h:mm a");
  return `${start} – ${end}`;
}

export interface AgendaDay {
  key: string;
  heading: string;
  entries: UnifiedCalendarEntry[];
}

/** Group entries by local day and sort days + intra-day entries chronologically. */
export function groupEntriesByDay(entries: UnifiedCalendarEntry[]): AgendaDay[] {
  const buckets = new Map<string, UnifiedCalendarEntry[]>();
  for (const entry of entries) {
    const key = dayKey(entry.start_at);
    const bucket = buckets.get(key) ?? [];
    bucket.push(entry);
    buckets.set(key, bucket);
  }
  return [...buckets.entries()]
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([key, dayEntries]) => ({
      key,
      heading: dayHeading(dayEntries[0].start_at),
      entries: [...dayEntries].sort((a, b) => a.start_at.localeCompare(b.start_at)),
    }));
}
