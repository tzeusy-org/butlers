## Why

The connector detail 24h histogram drew buckets by array position. The stats route returned only
the hours that had events, and the page right-justified them into 24 columns, so one event 21 hours
ago was drawn in the newest column. The columns were also labelled with clock hours (`00`..`23`) that
the rolling window does not represent. The north star says failure never impersonates health; a
count strip that misplaces its buckets misleads the owner about when their senses were active
(bu-s11n0s.5, JARVIS pursuit run 16).

## What changes

- `GET /api/ingestion/connectors/{type}/{endpoint}/stats` returns dense, time-keyed buckets: exactly
  24 hourly (or 7 / 30 daily) rows, oldest first, ending at the UTC bucket that contains `NOW()`.
  This is the same window the roster summary's `hourly_events` uses. A bucket with no events is a
  measured zero.
- The detail histogram places each row by its bucket start (`frontend/src/lib/bucket-series.ts`),
  never by array position. A slot no row covers renders as "not reported" (no bar), distinct from a
  measured zero. The positional `deriveSparkline` and `deriveFilteredSparkline` are deleted.
- The histogram labels its columns relatively (`-24h` … `now`).

## Deferred (still bu-s11n0s.5)

- S2: per-bucket listening state from the switchboard heartbeat log (`live | deaf | unknown`), and
  removal of the default-0 liveness fields (`heartbeat_count`, `healthy_count`, `degraded_count`,
  `error_count`) from the stats contract.
- S3: the ESLint ban on positional zero-fill in chart derivations, and migration of the
  session/activity strips.
