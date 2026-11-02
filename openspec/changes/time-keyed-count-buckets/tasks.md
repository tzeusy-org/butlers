## 1. Time-keyed detail buckets (S1)

- [x] 1.1 Stats route returns dense `generate_series` buckets over the roster's window, keyed by bucket start.
- [x] 1.2 `lib/bucket-series.ts` places rows by bucket start; unmatched slots are `null`, never zero.
- [x] 1.3 Connector detail histogram uses keyed series, draws no bar for unreported slots, and labels relatively.

## 2. Listening state (S2)

- [x] 2.1 Stats buckets and roster slots carry heartbeat-derived `live | deaf | unknown`, with a separate availability flag.
- [x] 2.2 Drop the default-0 `heartbeat_count` / `healthy_count` / `degraded_count` / `error_count` fields from the stats DTOs and frontend.
- [x] 2.3 Detail histogram and roster sparkline hatch deaf slots and describe them as `not listening <N>h`.

## 3. Count-strip truth (S3)

- [x] 3.1 `count-bucket/no-zero-fill` lint rule bans zero-filled arrays in non-test frontend source, with a reasoned line-level disable as the escape hatch.
- [x] 3.2 Activity tab hourly stripe and daily bars key buckets by `hour_start` / `date`; daily-activity returns a dense window.
- [x] 3.3 Fleet board returns `hourly_buckets` keyed by hour start (empty with a null total on failure); the board hook and overview stripe place them by time and drill into the slot's own hour.
- [x] 3.4 Management-tab owner-hour stripe and connector sparkline fallbacks render unreported slots, not zeros; the unused positional `bucketSessionsByHour` is deleted.
