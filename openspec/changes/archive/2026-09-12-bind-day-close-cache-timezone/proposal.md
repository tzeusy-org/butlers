## Why

The day-close cache currently identifies a local calendar day by date alone.
The same date has different UTC windows in different owner timezones, so a
date-only cache entry can be read, rate-limited, or locked as though it proved
a different local day.

## What Changes

- Require a validated IANA `tz` on the day-close cache read and refresh
  endpoints; neither endpoint may silently select a default timezone.
- Define one exact `(date, tz)` cache identity and use it consistently for
  cache reads and writes, the writer's collision-safe tuple lock, staleness provenance
  lookup, refresh rate limiting, and refresh responses.
- Add a Chronicler-local composite-key lock registry so different tuples never
  contend through a lossy advisory-lock hash.
- Propagate the required timezone through the typed dashboard read and refresh
  clients, query keys, and stale-summary regeneration affordance.
- Keep existing date-only cache rows untouched. They are intentionally not
  searched by the new tuple key and therefore remain audit data that reads as
  a cache miss.
- Document the breaking request requirement and the deliberate preservation of
  legacy cache rows.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `chronicler-api`: bind the day-close cache surface, its read requests, and
  refresh admission/rate-limit behavior to an exact local-day timezone.
- `butler-chronicler`: make a persisted day-close prose entry identify the
  exact owner-local day window that produced it.
- `dashboard-chronicles`: require the dashboard cache client to carry the
  owner timezone when addressing a selected local day.

## Impact

- `src/butlers/chronicler/` cache identity and day-close writer paths
- `roster/chronicler/migrations/025_day_close_cache_tuple_locks.py`
- `roster/chronicler/api/models.py` and `roster/chronicler/api/router.py`
- Typed frontend API client/query-key contracts and focused UI tests
- `docs/frontend/backend-api-contract.md`
- Focused Chronicler writer, reader, refresh, and editorial-cache regressions
- No cache/data rewrite or deletion, new LLM path, schedule,
  cross-schema read, credential action, or deployment change
