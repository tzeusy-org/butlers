# Ingestion read optimization

The Timeline shared database pool also serves Secrets and Models. The incident
reproduced a partial Secrets inventory with `shared-public` unavailable and
multi-second API latency while simple direct database reads were fast. The
investigation identified costly ID lookups and repeated dashboard reads; pool
pressure was a symptom, not evidence that PostgreSQL was unavailable.

## Changes

- `core_231` adds an ID-leading index to filtered-event partitions. It builds
  leaf indexes concurrently, attaches them to a partitioned parent, repairs
  incomplete concurrent builds on retry, and covers future partitions. It
  leaves event data and replay authority unchanged.
- Ingestion GET routes admit at most two reads per API application. Admission
  waits at most 250 ms and admitted reads have a 10-second execution deadline.
  Busy or timed-out reads return HTTP 503 with `Retry-After: 1`. Disconnection
  cancels work. Cancellation cleanup completes before the slot is reused;
  cleanup time is additional to the execution deadline.
- The ledger refreshes its head independently of historical pagination.
  Ingestion bursts coalesce over a fixed 250 ms interval; they do not refetch
  unrelated event details or audited payloads. Session notifications retain
  their dynamic evidence invalidations.
- Query cancellation reaches the HTTP client. The drawer stops requesting an
  unused per-event rollup, and row hover/focus no longer prefetches an audited
  detail read.
- Window rollups keep matching event IDs in PostgreSQL and aggregate through
  each owning session pool. They no longer transfer an arbitrary 10,000-ID
  sample to the application. An unavailable source becomes HTTP 503, rather
  than successful zero or partial totals.

## Verification evidence

The migration regression uses the same synthetic 120,000-row table before and
after upgrade. One recorded run measured 7.621 ms before and 0.209 ms after,
with shared-buffer work decreasing from 601 to 25 blocks. The regression
asserts plan/work improvement rather than a machine-dependent time threshold.
These measurements are synthetic, not a live deployment speedup claim.

The same migration test covers full-chain upgrade, repeated per-schema
execution, concurrent installation, future partition inheritance, downgrade
without event loss, and repair of an invalid concurrent index.

A real PostgreSQL rollup regression verifies complete counts and stored-cost
totals for 10,002 matching events/sessions, filtered-event selection, and
unavailable session-source handling. It proves correctness beyond the former
cap, not lower aggregate database CPU. The relational filter is evaluated
through each owning pool, and should be profiled under representative windows.

Admission tests verify busy responses, unaffected unrelated routes/mutations,
execution timeout, disconnect cancellation and permit recovery. The existing
full-app API tests exercise the middleware stack: an AnyIO cancellation scope
is necessary for the disconnect watcher because one-shot task cancellation
can deadlock inside nested `BaseHTTPMiddleware` receive wrappers.

Frontend regressions cover head-only refresh, retained historical cursors,
late-response cancellation, reduced drawer reads and coalesced invalidation.

## Rollout and observation

Apply the migration through the normal Alembic/Compose workflow after review.
Serialize upgrades from older core revisions: older migrations do not share
this migration's admission lock and may conflict with concurrent index builds.
Within `core_231`, lock acquisition uses bounded try-lock polling outside
database transactions. A blocking advisory-lock waiter can retain a snapshot
that a concurrent index build waits for, causing a deadlock. DDL lock waits
and migration admission are bounded to five seconds; contention fails for retry.

Monitor these OpenTelemetry instruments through the dashboard's existing OTLP
exporter, without event IDs, payloads or filter-value labels:

- `butlers.ingestion.read.admission` (seconds): admission wait, not pool acquisition time.
- `butlers.ingestion.read.duration` (seconds): admitted work, including cleanup.
- `butlers.ingestion.read.outcomes`: `ok`, `error`, `busy`, `timeout`,
  `disconnected`, or `cancelled`, by registered operation name.

After deployment, repeat Inventory and Models latency probes while paging the
Timeline and opening a drawer. Check degraded-source metadata as well as HTTP
status. A healthy transport response alone does not establish complete data.

The limit is per API application, not a PostgreSQL connection reservation.
Two admitted reads may fan out across several owning pools. No pool size was
increased. Aggregate caching, precomputed rollups and additional pool isolation
remain measurement-driven options if contention persists after these changes.
