// ---------------------------------------------------------------------------
// Time-keyed count buckets [bu-s11n0s.5]
//
// A count strip must place each bucket at its real time. The connector detail
// histogram used to take the last N rows of a sparse response and right-justify
// them (`padded[24 - len + i]`), so a single event 21 hours ago was drawn in the
// newest column, under clock labels ('00'..'23') that named hours the bars did
// not represent.
//
// These helpers key every row by its bucket-start instant instead. Slots are
// aligned to UTC hour boundaries (the backend truncates in UTC, see
// ingestion_connectors.py::_connector_stats_from_db), and the window ends at
// the hour containing `nowMs`, matching the backend's dense 24-bucket window
// and the roster summary's `hourly_events`. A slot no row covers is `null`
// ("not reported"), never a fabricated zero.
// ---------------------------------------------------------------------------

export const HOUR_MS = 3_600_000;

/**
 * Start instants (epoch ms) of `count` consecutive UTC-hour slots, oldest
 * first, the last one being the hour that contains `nowMs`.
 */
export function hourlySlotStarts(nowMs: number, count = 24): number[] {
  const last = Math.floor(nowMs / HOUR_MS) * HOUR_MS;
  return Array.from({ length: count }, (_, i) => last - (count - 1 - i) * HOUR_MS);
}

/**
 * Place `rows` into `slotStarts` by their bucket-start timestamp.
 *
 * Row order is irrelevant. Rows outside the window, or with an unparseable
 * timestamp, are dropped. A slot no row matches is `null`, so a caller can draw
 * "not reported" distinctly from a measured zero.
 */
export function buildBucketSeries<T, V = number>(
  rows: readonly T[],
  bucketStart: (row: T) => string,
  value: (row: T) => V,
  slotStarts: readonly number[],
): (V | null)[] {
  const byStart = new Map<number, V>();
  for (const row of rows) {
    const ms = Date.parse(bucketStart(row));
    if (!Number.isNaN(ms)) byStart.set(ms, value(row));
  }
  return slotStarts.map((start) => byStart.get(start) ?? null);
}

/**
 * Accessible summary of a strip's listening states (bu-s11n0s.5): the number
 * of deaf hourly slots as "not listening Nh", or "" when none are deaf. Only
 * `deaf` counts: `unknown` and unreported slots make no liveness claim.
 */
export function describeNotListening(listening: readonly (string | null)[] | undefined): string {
  const deaf = listening?.filter((state) => state === "deaf").length ?? 0;
  return deaf > 0 ? `not listening ${deaf}h` : "";
}
