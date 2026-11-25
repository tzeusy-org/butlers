/**
 * Multi-day placement for the calendar grids (bu-5ii4gj.4 D2): an event that spans several
 * days appears on every day it covers, not only the day it starts, and events that overlap in
 * time share a day column side by side instead of stacking on top of each other.
 *
 * Pure functions over ISO strings and an IANA zone; the grids only render what these return.
 */

import {
  MINUTES_PER_DAY,
  minuteOfDayInTz,
  tzDayKey,
} from "@/lib/calendar-grid";

/** Longest span rendered per event; a runaway end date must not fan out into hundreds of days. */
export const MAX_SPAN_DAYS = 62;

interface SpanEntry {
  entry_id: string;
  start_at: string;
  end_at: string;
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

/**
 * Day keys an event covers in `tz`, first to last. The end instant is exclusive, so an event that
 * ends exactly at midnight does not claim the next day. An event with a missing or reversed end
 * covers its start day only.
 */
export function entryDayKeys(entry: SpanEntry, tz: string): string[] {
  const first = tzDayKey(entry.start_at, tz);
  if (!first) return [];
  const startMs = new Date(entry.start_at).getTime();
  const endMs = new Date(entry.end_at).getTime();
  const last =
    Number.isNaN(endMs) || endMs <= startMs
      ? first
      : tzDayKey(endMs - 1, tz) || first;
  const keys: string[] = [];
  let key = first;
  while (keys.length < MAX_SPAN_DAYS) {
    keys.push(key);
    if (key >= last) break;
    key = addDaysToKey(key, 1);
  }
  return keys;
}

/** Slice an event onto each day it covers. */
export function entryDaySegments<T extends SpanEntry>(
  entry: T,
  tz: string,
): DaySegment<T>[] {
  const keys = entryDayKeys(entry, tz);
  const endMs = new Date(entry.end_at).getTime();
  const startMin = minuteOfDayInTz(entry.start_at, tz);
  // The end is exclusive: a 24:00 end reads as minute 1440 of its last covered day.
  const lastEnd = Number.isNaN(endMs)
    ? startMin
    : minuteOfDayInTz(endMs - 1, tz) + 1;
  return keys.map((dayKey, i) => {
    const isFirst = i === 0;
    const isLast = i === keys.length - 1;
    return {
      entry,
      dayKey,
      startMin: isFirst ? startMin : 0,
      endMin: isLast
        ? keys.length === 1
          ? Math.max(lastEnd, startMin)
          : lastEnd
        : MINUTES_PER_DAY,
      continuesFromPrev: !isFirst,
      continuesToNext: !isLast,
    };
  });
}

/** Bucket entries onto every day they cover, each day's list ordered by start. */
export function bucketEntriesByDay<T extends SpanEntry>(
  entries: readonly T[],
  tz: string,
): Map<string, T[]> {
  const buckets = new Map<string, T[]>();
  for (const entry of entries) {
    for (const key of entryDayKeys(entry, tz)) {
      const bucket = buckets.get(key);
      if (bucket) bucket.push(entry);
      else buckets.set(key, [entry]);
    }
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

/** Whether an event began before, or runs past, the day `key` (for the arrow markers). */
export function entryContinuation(
  entry: SpanEntry,
  key: string,
  tz: string,
): { fromPrev: boolean; toNext: boolean } {
  const keys = entryDayKeys(entry, tz);
  return {
    fromPrev: keys.length > 0 && keys[0] !== key,
    toNext: keys.length > 0 && keys[keys.length - 1] !== key,
  };
}
