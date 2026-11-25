/**
 * Multi-day placement for the calendar grids (bu-5ii4gj.4 D2): an event that spans several
 * days appears on every day it covers, not only the day it starts, and events that overlap in
 * time share a day column side by side instead of stacking on top of each other.
 *
 * Pure functions over ISO strings and an IANA zone; the grids only render what these return.
 */

import { formatInTimeZone } from "date-fns-tz";

import {
  MINUTES_PER_DAY,
  minuteOfDayInTz,
  tzDayKey,
} from "@/lib/calendar-grid";

/** Longest span rendered per event; a runaway end date must not fan out into hundreds of days. */
export const MAX_SPAN_DAYS = 62;

export interface SpanEntry {
  entry_id: string;
  start_at: string;
  end_at: string;
  /** All-day events are stored as midnight in this zone, so their dates are read in it. */
  all_day?: boolean;
  timezone?: string | null;
}

/** An inclusive run of day keys (`yyyy-MM-dd`) a grid shows. */
export interface DayWindow {
  startKey: string;
  endKey: string;
}

/** An event's slice on one day. */
export interface DaySegment<T extends SpanEntry> {
  entry: T;
  dayKey: string;
  /** Minute of the day the slice starts (0 when the event began on an earlier day). */
  startMin: number;
  /** Minute of the day the slice ends (1440 when the event runs past this day). */
  endMin: number;
  continuesFromPrev: boolean;
  continuesToNext: boolean;
}

function addDaysToKey(key: string, days: number): string {
  const [y, m, d] = key.split("-").map(Number);
  return new Date(Date.UTC(y, m - 1, d + days)).toISOString().slice(0, 10);
}

function validZone(zone: string | null | undefined): string | null {
  if (!zone) return null;
  try {
    formatInTimeZone(new Date(0), zone, "yyyy");
    return zone;
  } catch {
    return null;
  }
}

/** The zone an entry's dates are read in: its own for all-day events, else the workspace's. */
function spanZone(entry: SpanEntry, tz: string): string {
  return (entry.all_day && validZone(entry.timezone)) || tz;
}

interface Span {
  firstKey: string;
  lastKey: string;
  zone: string;
  /** The end is usable: present, parseable and after the start. */
  hasEnd: boolean;
  endMs: number;
}

/** First and last day an event touches (end instant exclusive), uncapped and unclipped. */
function entrySpan(entry: SpanEntry, tz: string): Span | null {
  const zone = spanZone(entry, tz);
  const firstKey = tzDayKey(entry.start_at, zone);
  if (!firstKey) return null;
  const startMs = new Date(entry.start_at).getTime();
  const endMs = new Date(entry.end_at).getTime();
  const hasEnd = !Number.isNaN(endMs) && endMs > startMs;
  const lastKey = hasEnd ? tzDayKey(endMs - 1, zone) || firstKey : firstKey;
  return { firstKey, lastKey, zone, hasEnd, endMs };
}

/**
 * Day keys an event covers, first to last. The end instant is exclusive, so an event that ends
 * exactly at midnight does not claim the next day; a missing or reversed end covers the start day
 * only. All-day events read their dates in their own zone. `window` clips the keys to what a grid
 * shows (so a long event stays on later months); without it a span is capped at MAX_SPAN_DAYS.
 */
export function entryDayKeys(
  entry: SpanEntry,
  tz: string,
  window?: DayWindow,
): string[] {
  const span = entrySpan(entry, tz);
  if (!span) return [];
  let key =
    window && window.startKey > span.firstKey ? window.startKey : span.firstKey;
  const stop =
    window && window.endKey < span.lastKey ? window.endKey : span.lastKey;
  const keys: string[] = [];
  while (key <= stop && keys.length < MAX_SPAN_DAYS) {
    keys.push(key);
    key = addDaysToKey(key, 1);
  }
  return keys;
}

/** Slice an event onto each day it covers (clipped to `window` when given). */
export function entryDaySegments<T extends SpanEntry>(
  entry: T,
  tz: string,
  window?: DayWindow,
): DaySegment<T>[] {
  const span = entrySpan(entry, tz);
  if (!span) return [];
  const startMin = minuteOfDayInTz(entry.start_at, span.zone);
  // The end is exclusive: a 24:00 end reads as minute 1440 of its last covered day. A missing or
  // reversed end is a point event.
  const lastEnd = span.hasEnd
    ? minuteOfDayInTz(span.endMs - 1, span.zone) + 1
    : startMin;
  return entryDayKeys(entry, tz, window).map((dayKey) => {
    const isFirst = dayKey === span.firstKey;
    const isLast = dayKey === span.lastKey;
    return {
      entry,
      dayKey,
      startMin: isFirst ? startMin : 0,
      endMin: isLast ? lastEnd : MINUTES_PER_DAY,
      continuesFromPrev: !isFirst,
      continuesToNext: !isLast,
    };
  });
}

/** Whether an event began before, or runs past, the day `key` (for the continuation markers). */
export function entryContinuation(
  entry: SpanEntry,
  key: string,
  tz: string,
): { fromPrev: boolean; toNext: boolean } {
  const span = entrySpan(entry, tz);
  if (!span) return { fromPrev: false, toNext: false };
  return { fromPrev: key > span.firstKey, toNext: key < span.lastKey };
}

/** Bucket entries onto every day they cover; each day's list is ordered by start. */
export function bucketEntriesByDay<T extends SpanEntry>(
  entries: readonly T[],
  tz: string,
  window?: DayWindow,
): Map<string, T[]> {
  const buckets = new Map<string, T[]>();
  for (const entry of entries) {
    for (const key of entryDayKeys(entry, tz, window)) {
      const bucket = buckets.get(key);
      if (bucket) bucket.push(entry);
      else buckets.set(key, [entry]);
    }
  }
  for (const bucket of buckets.values()) {
    bucket.sort((a, b) => Date.parse(a.start_at) - Date.parse(b.start_at));
  }
  return buckets;
}

/** Where a segment sits among the overlapping segments of its day. */
export interface PackedSegment<T extends SpanEntry> extends DaySegment<T> {
  /** Zero-based column within its overlap cluster. */
  column: number;
  /** Columns in the cluster; the segment is 1/columns of the day width. */
  columns: number;
}

/**
 * Column packing: segments that overlap in time are placed in side-by-side columns. A cluster is
 * a run of transitively overlapping segments; each segment takes the first column free at its
 * start, and every segment in the cluster shares the cluster's column count.
 */
export function packSegments<T extends SpanEntry>(
  segments: readonly DaySegment<T>[],
  minDurationMin = 15,
): PackedSegment<T>[] {
  const ordered = [...segments].sort(
    (a, b) =>
      a.startMin - b.startMin ||
      b.endMin - a.endMin ||
      a.entry.entry_id.localeCompare(b.entry.entry_id),
  );
  const end = (s: DaySegment<T>) =>
    Math.max(s.endMin, s.startMin + minDurationMin);
  const packed: PackedSegment<T>[] = [];
  let cluster: PackedSegment<T>[] = [];
  let clusterEnd = -1;
  let columnEnds: number[] = [];

  const closeCluster = () => {
    for (const seg of cluster) seg.columns = columnEnds.length;
    packed.push(...cluster);
    cluster = [];
    columnEnds = [];
  };

  for (const segment of ordered) {
    if (cluster.length > 0 && segment.startMin >= clusterEnd) closeCluster();
    let column = columnEnds.findIndex(
      (columnEnd) => columnEnd <= segment.startMin,
    );
    if (column === -1) {
      column = columnEnds.length;
      columnEnds.push(0);
    }
    columnEnds[column] = end(segment);
    clusterEnd = Math.max(cluster.length > 0 ? clusterEnd : -1, end(segment));
    cluster.push({ ...segment, column, columns: 1 });
  }
  if (cluster.length > 0) closeCluster();
  return packed;
}
