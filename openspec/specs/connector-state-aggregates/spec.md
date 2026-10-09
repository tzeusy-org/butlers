# connector-state-aggregates Specification

## Purpose
Keeps the ingestion console honest about whether its cross-connector aggregate panels are backed by live Prometheus answers.

## Requirements

### Requirement: Cross-summary aggregate availability is query-backed

`GET /api/ingestion/connectors/cross-summary` SHALL publish
`aggregates_available: true` only when Prometheus has answered the funnel
queries that back the console's aggregate panels. The flag SHALL be resolved
through the same 60-second TTL cache `GET /api/ingestion/pipeline` publishes
its own flag from, so the two endpoints SHALL NOT report different
availability for the same aggregates at the same moment.

The flag SHALL NOT be derived from `PROMETHEUS_URL` being set, from any other
configuration value, or from the mere absence of an error. A cold cache is not
evidence of availability: the handler SHALL either resolve the flag from a
query it issued or report `false`.

Every other field in the response is sourced from `connector_registry` and is
independent of Prometheus. Those fields SHALL still be returned, with their
real values, when `aggregates_available` is `false`, and the availability
resolution SHALL NOT be able to fail the request: an exception escaping it
SHALL be logged and SHALL lower the flag to `false`, never produce HTTP 500 or
zeroed fleet counts.

#### Scenario: Configured but unreachable Prometheus is not available

- **WHEN** `PROMETHEUS_URL` is set and the funnel queries return a transport or
  query error
- **THEN** `aggregates_available` is `false`
- **AND** the response is HTTP 200 with the real DB-sourced fleet counts

#### Scenario: Prometheus answered

- **WHEN** the funnel queries return readable values
- **THEN** `aggregates_available` is `true`

#### Scenario: Unreadable samples do not count as an answer

- **WHEN** Prometheus returns a well-formed response whose scalar will not
  parse as a finite number
- **THEN** `aggregates_available` is `false`

#### Scenario: Warm cache answers without a new query

- **WHEN** the pipeline TTL cache holds an entry younger than 60 seconds
- **THEN** `aggregates_available` echoes that entry's flag
- **AND** no PromQL query is issued for the cross-summary request

#### Scenario: A failing availability probe degrades only the flag

- **WHEN** resolving availability raises
- **THEN** the failure is logged
- **AND** `aggregates_available` is `false`
- **AND** the fleet counts and message totals are returned unchanged

### Requirement: Connector Prometheus counter samples are source-honest

Connector heartbeat and aggregate readers SHALL count only finite,
non-negative Prometheus `*_total` samples whose connector labels match the
requested connector.  Counter-family metadata such as `*_created` SHALL NOT
contribute to an operational count, and malformed, NaN, or infinite samples
SHALL be ignored rather than coerced into a measurement.

#### Scenario: Valid total samples retain existing counts

- **WHEN** a connector Counter family contains finite `*_total` samples for
  the connector's labels
- **THEN** those samples contribute their existing numeric counts to the
  corresponding heartbeat or aggregate summary
- **AND** unrelated connector labels do not contribute

#### Scenario: Created timestamps never become counts

- **WHEN** a Counter family contains both a valid `*_total` sample and a
  `*_created` timestamp sample
- **THEN** only the `*_total` sample contributes to the summary
- **AND** the timestamp-sized value is retained only as Prometheus metadata

#### Scenario: Invalid numeric samples do not affect valid totals

- **WHEN** a response contains malformed, NaN, or infinite values alongside a
  valid `*_total` sample
- **THEN** the invalid samples are ignored
- **AND** the valid total remains unchanged

#### Scenario: No usable counter is explicitly unavailable

- **WHEN** a connector counter field has no finite, non-negative `*_total`
  sample after filtering
- **THEN** the counter read exposes a typed unavailable state for that field
- **AND** the aggregate API exposes `meta.aggregates_available=false` when no
  usable total exists
- **AND** neither surface publishes a timestamp-sized or fabricated zero as an
  observed count

### Requirement: Prometheus PromQL is the aggregate source of truth

The funnel aggregates SHALL be sourced from Prometheus over its HTTP query API
through `butlers.modules.metrics.prometheus` (`async_query` for instant
queries, `async_query_range` for the sparkline). `routed_pct` SHALL be derived
arithmetically from the funnel counters rather than queried, as
`routed_total / (ingested + filtered + errored) * 100.0`, and SHALL be `0.0`
when that denominator is zero. No SQL rollup table or materialized view SHALL
back these aggregates; re-introducing one requires a superseding spec that
justifies changing the Prometheus-only aggregate contract.

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

"Unavailable" SHALL cover any value the handler could not observe, not only a
transport failure: a Prometheus-reported query error on any of the six instant
queries or the sparkline range query, a scalar that will not parse as a finite
number, a per-butler routed series whose value cannot be read, and a sparkline
matrix of unexpected shape SHALL each produce the degraded envelope. The
handler SHALL NOT substitute a value it did not observe — no zero for an
unparseable scalar, and no uniform fill of the ingested total across the 24
sparkline buckets. When `aggregates_available` is `true`, every value in the
response SHALL be one Prometheus actually reported.

An empty PromQL result set is an observation, not a failure: Prometheus
answering with no series for a `sum(increase(...))` SHALL read as `0` (and as
`[0] * 24` for the sparkline) with `aggregates_available` left `true`.

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

#### Scenario: Unparseable scalar lowers the flag instead of reading as zero

- **WHEN** a PromQL response is well-formed HTTP but its scalar value cannot be
  parsed, or parses as `NaN` or `Inf`
- **THEN** the handler returns the degraded envelope with
  `aggregates_available: false`
- **AND** the affected field SHALL NOT be published as `0` under
  `aggregates_available: true`

#### Scenario: Unusable sparkline matrix lowers the flag instead of filling uniformly

- **WHEN** the sparkline range query returns an error, a result element without
  a `values` series, a series carrying no points, or a bucket value that will
  not parse as a finite number
- **THEN** the handler returns the degraded envelope with
  `aggregates_available: false`
- **AND** the ingested total SHALL NOT be spread evenly across the 24 buckets

#### Scenario: A failed routed, rate1h, or filtered24h query degrades the envelope

- **WHEN** the per-butler routed breakdown, `rate1h`, or `filtered24h` query
  returns a Prometheus error, or one routed series' value cannot be read
- **THEN** the handler returns the degraded envelope with
  `aggregates_available: false`
- **AND** `routed_pct` SHALL NOT be published as `0.0`, nor the breakdown
  published with the unreadable series silently omitted

#### Scenario: Empty result set is a truthful zero

- **WHEN** an instant query returns an empty vector, or the sparkline range
  query returns an empty matrix
- **THEN** the affected field is `0` (or `[0] * 24` for the sparkline)
- **AND** `aggregates_available` remains `true`, because Prometheus was reached
  and answered

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
- **THEN** a poll after a completed refresh and within that entry's 60-second
  TTL returns the cached values without another Prometheus fetch
- **AND** concurrent cold misses may each fetch outside the lock; this cache
  does not promise single-flight refresh or a global rate limit

#### Scenario: Invalid window is rejected

- **WHEN** the endpoint is called with a `window` outside `1h`, `24h`, `7d`
- **THEN** the response is HTTP 422

#### Scenario: No per-request UNION ALL on the pipeline endpoint

- **WHEN** the pipeline endpoint implementation is reviewed
- **THEN** no SQL path executes a per-request `UNION ALL` across
  `public.ingestion_events` and `connectors.filtered_events`
- **AND** the backlog query is a single grouped `COUNT(*)` over
  `public.ingestion_events` restricted to the backlog statuses

#### Scenario: Materialized view requires a superseding contract

- **WHEN** a change proposes a materialized view or SQL rollup for these aggregates
- **THEN** the current Prometheus-only requirement remains binding until a
  superseding spec is adopted
- **AND** this historical requirement title grants no materialized-view exception

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
