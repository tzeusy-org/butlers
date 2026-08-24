# Connector State Aggregates

## Purpose

Defines the data source, caching contract, degraded-mode response shape, and
aggregation prohibitions for the ingestion funnel aggregates surfaced on the
`/ingestion` dashboard: `spark24h` (24-bucket sparkline of ingested events),
`rate1h` (events per minute over the trailing hour), `routed_pct` (routed share
of the funnel), and `filtered24h` (filtered events over the trailing 24 hours).
These aggregates are read by the Filters pipeline view, the connector
cross-summary, and `GET /api/ingestion/pipeline`. The sw_025 migration dropped
the SQL rollup tables that previously backed them; this capability ratifies the
Prometheus-PromQL-with-TTL-cache path that shipped in their place, and records
where the shipped path is quieter about failure than the envelope suggests.

## ADDED Requirements

### Requirement: Prometheus PromQL is the aggregate source of truth

The funnel aggregates SHALL be sourced from Prometheus over its HTTP query API
through `butlers.modules.metrics.prometheus` (`async_query` for instant
queries, `async_query_range` for the sparkline). `routed_pct` SHALL be derived
arithmetically from the funnel counters rather than queried, as
`routed_total / (ingested + filtered + errored) * 100.0`, and SHALL be `0.0`
when that denominator is zero. No SQL rollup table or materialized view SHALL
back these aggregates; re-introducing one requires a superseding spec that
justifies reversing the sw_025 decision.

#### Scenario: Aggregate fetch goes through the Prometheus HTTP API

- **WHEN** the pipeline endpoint computes `ingested`, `filtered`, `errored`,
  `rate1h`, or `filtered24h`
- **THEN** the handler issues an instant PromQL query through
  `butlers.modules.metrics.prometheus.async_query`
- **AND** no SQL rollup table is read for those values

#### Scenario: Sparkline uses a range query pinned to 24 hours

- **WHEN** `spark24h` is computed
- **THEN** the handler issues `async_query_range` for
  `sum(increase(ingestion_events_ingested_total[1h]))` over `now-24h .. now`
  with `step = 3600`
- **AND** the window is 24 hours regardless of the `window` request parameter

#### Scenario: routed_pct is derived, not queried

- **WHEN** `routed_pct` is computed
- **THEN** it is calculated from the already-fetched funnel counters
- **AND** no dedicated PromQL query is issued for it

#### Scenario: No rollup table backs the aggregates

- **WHEN** the aggregate implementation is reviewed
- **THEN** no SQL rollup table or materialized view exists for the funnel
  aggregates
- **AND** a migration proposing one is blocked until a superseding spec is
  ratified

### Requirement: 60-second TTL cache

The pipeline aggregate fetch path SHALL cache its Prometheus results for
`_CACHE_TTL_SECONDS = 60.0`, keyed by the requested `window` value alone. The
cache SHALL be refreshed lazily on read under an `asyncio.Lock`, using a
monotonic clock; no background refresh job SHALL be required. The Prometheus
fetch SHALL happen outside the lock so concurrent readers are not serialized
behind a slow backend.

#### Scenario: Cache hit within TTL

- **WHEN** two requests for the same `window` arrive less than 60 seconds apart
- **THEN** the second request is served from the cached payload
- **AND** no PromQL query is issued for it

#### Scenario: Cache miss after TTL expiry

- **WHEN** a request arrives 60 seconds or more after the cached entry was
  stored
- **THEN** the handler issues fresh PromQL queries and replaces the entry

#### Scenario: Cache key is the window alone

- **WHEN** requests for `window=1h` and `window=24h` are served
- **THEN** each is stored under its own cache entry keyed by the window string
- **AND** the cache key carries no connector or metric dimension

#### Scenario: Refresh is lazy, not scheduled

- **WHEN** no request arrives for a given window
- **THEN** no background task refreshes that window's cache entry

### Requirement: Degraded-mode response shape

When the aggregate source is unavailable, the pipeline endpoint SHALL return
HTTP 200 with every funnel field zeroed, `routed_by_butler` empty, `spark24h`
an array of 24 zeros, and `aggregates_available: false`. The handler SHALL
NEVER return HTTP 500 for a Prometheus failure. The degraded envelope SHALL be
used when `PROMETHEUS_URL` is unset or empty, when the `ingested`, `filtered`,
or `errored` query raises, and when any other exception escapes the fetch path.

Backlog counters (`failed_total`, `replay_pending_total`, `written_off_total`)
are sourced from PostgreSQL, not Prometheus, and SHALL degrade independently:
their unavailability SHALL be signalled by `backlog_available: false` with each
counter `null` rather than zero, so an unknown backlog is never reported as an
empty one.

#### Scenario: Prometheus not configured

- **WHEN** `PROMETHEUS_URL` is unset or empty
- **THEN** the handler returns HTTP 200 with the degraded envelope and
  `aggregates_available: false`

#### Scenario: Prometheus query raises

- **WHEN** the `ingested`, `filtered`, or `errored` query raises
- **THEN** the handler returns HTTP 200 with the degraded envelope
- **AND** the failure is logged with enough detail to diagnose the outage

#### Scenario: Handler never returns 500 for a Prometheus failure

- **WHEN** any Prometheus-related failure occurs (timeout, connection refused,
  query error, unexpected exception in the fetch path)
- **THEN** the handler SHALL NOT return HTTP 500
- **AND** the degraded envelope is returned instead

#### Scenario: Backlog degrades to null, not zero

- **WHEN** the backlog count query fails or the database pool is unavailable
- **THEN** `backlog_available` is `false`
- **AND** `failed_total`, `replay_pending_total`, and `written_off_total` are
  `null`
- **AND** the Prometheus-sourced fields are unaffected by the backlog failure

#### Scenario: Unparseable scalar reads as zero without lowering the flag

- **WHEN** a PromQL response is well-formed HTTP but its scalar value cannot be
  parsed, or the sparkline range query returns an unusable matrix
- **THEN** the affected value currently resolves to `0` (or a uniformly filled
  sparkline) while `aggregates_available` remains `true`
- **AND** this quiet coercion is a known honesty gap in the shipped path,
  recorded here so a reader is not misled into treating
  `aggregates_available: true` as proof every field was observed

### Requirement: Pipeline endpoint uses TTL cache or materialized view

`GET /api/ingestion/pipeline` SHALL accept a single query parameter `window`
constrained to `1h`, `24h`, or `7d` and defaulting to `24h`; any other value
SHALL be rejected by request validation with HTTP 422. Its Prometheus-sourced
fields SHALL be served from the 60-second TTL cache above. Per-request
`UNION ALL` aggregation across `public.ingestion_events` and
`connectors.filtered_events` SHALL NOT be performed on this endpoint; the only
SQL it runs is a single bounded status roll-up over `public.ingestion_events`
for the backlog counters.

#### Scenario: Pipeline served from cache under polling

- **WHEN** the endpoint is polled faster than once per 60 seconds
- **THEN** Prometheus is queried at most once per 60 seconds per window
- **AND** intermediate polls return the cached values

#### Scenario: Invalid window is rejected

- **WHEN** the endpoint is called with a `window` outside `1h`, `24h`, `7d`
- **THEN** the response is HTTP 422

#### Scenario: No per-request UNION ALL on the pipeline endpoint

- **WHEN** the pipeline endpoint implementation is reviewed
- **THEN** no SQL path executes a per-request `UNION ALL` across
  `public.ingestion_events` and `connectors.filtered_events`
- **AND** the backlog query is a single grouped `COUNT(*)` over
  `public.ingestion_events` restricted to the backlog statuses

#### Scenario: Materialized view alternative honored

- **WHEN** an implementation replaces the PromQL+TTL path with a materialized
  view
- **THEN** the view SHALL be refreshed no more often than every 60 seconds
- **AND** it SHALL provide the same response shape, including
  `aggregates_available`, which SHALL be `false` when the refresh has failed
  for longer than the refresh interval

### Requirement: Aggregate response field shape

The pipeline endpoint SHALL return a flat, unwrapped JSON object (no
`ApiResponse` envelope) whose keys are:

- `window: string` — echo of the requested window
- `aggregates_available: boolean` — false when the degraded envelope is in effect
- `ingested: integer`, `filtered: integer`, `errored: integer`
- `routed_by_butler: object` — butler name to integer count; `{}` when degraded
- `spark24h: integer[]` — exactly 24 buckets, oldest first
- `rate1h: number` — events per minute over the trailing hour, rounded to 4 decimals
- `routed_pct: number` — 0.0–100.0, rounded to 2 decimals
- `filtered24h: integer`
- `failed_total`, `replay_pending_total`, `written_off_total: integer | null`
- `backlog_available: boolean`

Field names SHALL be snake_case apart from the three counter-named aggregates
`spark24h`, `rate1h`, and `filtered24h`, which are literal names rather than a
casing convention. There SHALL be no `routedPct` alias.

#### Scenario: Healthy response shape

- **WHEN** the endpoint returns healthy data
- **THEN** every key above is present with a value matching its declared type
- **AND** `spark24h` has exactly 24 elements

#### Scenario: Sparkline bucket count is normalized

- **WHEN** the range query returns more than 24 buckets
- **THEN** the last 24 are kept
- **WHEN** it returns fewer than 24
- **THEN** the series is front-padded with zeros to 24

#### Scenario: Degraded response shape

- **WHEN** the degraded envelope is returned
- **THEN** `spark24h` is 24 zeros, `rate1h` is `0.0`, `routed_pct` is `0.0`,
  `filtered24h` is `0`, `routed_by_butler` is `{}`, and
  `aggregates_available` is `false`

## Source References

- Non-Negotiable Rule 4 (the daemon is deterministic infrastructure — the
  aggregate path is a deterministic read, not an LLM judgement)
- RFC 0005 (Observability and telemetry — Prometheus is the metric backend)
- RFC 0007 (Dashboard and API surface)
