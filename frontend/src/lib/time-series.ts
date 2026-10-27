// ---------------------------------------------------------------------------
// Time-true trend series (bu-q7vx1q.6)
//
// Irregular observations must be drawn at their real time, not evenly spaced.
// buildTimeSeries turns readings into numeric-millisecond rows for a
// type="number" scale="time" axis, breaks the line where silence exceeds the
// caller's maxGap, and reports how old the latest reading is so the chart can
// label its tail instead of letting a stale line pass as current.
// ---------------------------------------------------------------------------

export const DAY_MS = 86_400_000;

export interface TimeSeriesInput<K extends string> {
  /** Observation time: ISO string, epoch ms, or Date. Unparseable rows are dropped. */
  at: string | number | Date;
  values: Record<K, number | null>;
}

export type TimeSeriesRow<K extends string> = { x: number } & Record<K, number | null>;

export interface BuildTimeSeriesOptions {
  /** Left edge of the axis. Defaults to the first reading. */
  windowStart?: number;
  /** Right edge of the axis. Callers pass an injected "now". */
  windowEnd: number;
  /** A silence longer than this breaks the line. Declared by the caller per series. */
  maxGapMs: number;
}

export interface TimeSeries<K extends string> {
  rows: TimeSeriesRow<K>[];
  domain: [number, number];
  /** Rows that carry at least one value, i.e. real observations. */
  observationCount: number;
  lastReadingAt: number | null;
  /** Whole days from the last reading to the axis end; null with no readings. */
  tailDays: number | null;
}

function toMs(at: string | number | Date): number {
  return at instanceof Date ? at.getTime() : typeof at === "number" ? at : new Date(at).getTime();
}

export function buildTimeSeries<K extends string>(
  points: TimeSeriesInput<K>[],
  keys: readonly K[],
  { windowStart, windowEnd, maxGapMs }: BuildTimeSeriesOptions,
): TimeSeries<K> {
  const observed = points
    .map((p) => ({ x: toMs(p.at), values: p.values }))
    .filter(
      (p) =>
        Number.isFinite(p.x) &&
        p.x <= windowEnd &&
        (windowStart === undefined || p.x >= windowStart) &&
        keys.some((k) => p.values[k] != null),
    )
    .sort((a, b) => a.x - b.x);

  const rows: TimeSeriesRow<K>[] = [];
  for (const [i, p] of observed.entries()) {
    const prev = observed[i - 1];
    if (prev && p.x - prev.x > maxGapMs) {
      const gap = { x: Math.floor((prev.x + p.x) / 2) } as TimeSeriesRow<K>;
      for (const k of keys) gap[k] = null as TimeSeriesRow<K>[K];
      rows.push(gap);
    }
    rows.push({ x: p.x, ...p.values } as TimeSeriesRow<K>);
  }

  const lastReadingAt = observed.length ? observed[observed.length - 1].x : null;
  const start = windowStart ?? observed[0]?.x ?? windowEnd;
  return {
    rows,
    domain: [start, windowEnd],
    observationCount: observed.length,
    lastReadingAt,
    tailDays: lastReadingAt === null ? null : Math.floor((windowEnd - lastReadingAt) / DAY_MS),
  };
}

/** Tail label shown once the newest reading is older than a day. */
export function tailLabel(tailDays: number | null): string | null {
  return tailDays !== null && tailDays >= 1 ? `last reading ${tailDays}d ago` : null;
}
