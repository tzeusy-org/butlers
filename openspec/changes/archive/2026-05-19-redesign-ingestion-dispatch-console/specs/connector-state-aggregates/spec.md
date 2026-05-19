# Connector State Aggregates

## Purpose
Defines the data source, caching contract, degraded-mode response shape, and aggregation prohibitions for the connector state aggregates surfaced on the `/ingestion` dashboard: `spark24h` (24-hour sparkline of accepted events), `rate1h` (1-hour event rate), `routedPct` (percentage of events routed vs. filtered), and `filtered24h` (count of filtered events in the last 24 hours). These aggregates are read by the connector roster list, connector detail view, and the `GET /api/ingestion/pipeline?window=24h` pipeline endpoint. The sw_025 migration deliberately dropped the SQL rollup tables that previously backed these aggregates; this spec ratifies the Prometheus-PromQL-with-TTL-cache path as the chosen replacement and prohibits the per-request UNION ALL fallback at poll cadence.

## ADDED Requirements

### Requirement: Prometheus PromQL is the aggregate source of truth
The system SHALL source `spark24h`, `rate1h`, `routedPct`, and `filtered24h` from Prometheus via PromQL queries executed through the existing `prometheus.py` integration. The system SHALL NOT re-introduce SQL rollup tables for these aggregates without an explicit superseding spec that justifies the reversal of the sw_025 migration decision.

#### Scenario: Aggregate fetch goes through prometheus.py
- **WHEN** the dashboard requests `spark24h`, `rate1h`, `routedPct`, or `filtered24h` for a connector
- **THEN** the handler issues a PromQL query through `prometheus.py`
- **AND** the handler does NOT query a SQL rollup table for these values

#### Scenario: Rollup tables are not re-introduced
- **WHEN** a migration is proposed that re-introduces a SQL rollup table for these aggregates
- **THEN** the migration MUST be blocked until a superseding spec is ratified that explicitly justifies reversing the sw_025 decision

### Requirement: 60-second TTL cache
The aggregate fetch path SHALL cache PromQL query results for 60 seconds per (connector, metric) cache key. Subsequent reads within the TTL window SHALL be served from cache without re-querying Prometheus. The cache SHALL be refreshed lazily on read; no background refresh job is required.

#### Scenario: Cache hit within TTL
- **WHEN** two consecutive requests for the same connector's `spark24h` arrive within 60 seconds
- **THEN** the second request returns the cached value without issuing a PromQL query

#### Scenario: Cache miss after TTL expiry
- **WHEN** a request arrives more than 60 seconds after the last cached value
- **THEN** the handler issues a fresh PromQL query and updates the cache

#### Scenario: Cache key includes connector and metric
- **WHEN** two different connectors request `spark24h`
- **THEN** each is served from its own cache entry
- **AND** a cache miss on one connector's entry does not invalidate the other's

### Requirement: Degraded-mode response shape
When Prometheus is unreachable, returns an error, or returns malformed data, the aggregate endpoint SHALL return HTTP 200 with zero values for the affected aggregates and the field `aggregates_available: false` in the response body. The handler SHALL NEVER return HTTP 500 for a Prometheus failure — the dashboard remains usable even when the metric backend is degraded.

#### Scenario: Prometheus unreachable
- **WHEN** the PromQL query raises a connection error
- **THEN** the handler returns HTTP 200 with the affected aggregate fields set to 0 and `aggregates_available: false` in the body
- **AND** the failure is logged with sufficient detail to diagnose the Prometheus outage

#### Scenario: Prometheus returns malformed data
- **WHEN** the PromQL response cannot be parsed into the expected numeric form
- **THEN** the handler returns HTTP 200 with zeros and `aggregates_available: false`

#### Scenario: Healthy response sets flag true
- **WHEN** the PromQL query succeeds and parses cleanly
- **THEN** the handler returns HTTP 200 with the aggregate values and `aggregates_available: true`

#### Scenario: Handler never returns 500 for Prometheus failure
- **WHEN** any Prometheus-related failure mode occurs (timeout, connection refused, malformed response, query syntax error)
- **THEN** the handler SHALL NOT return HTTP 500
- **AND** the degraded-mode response shape SHALL be used instead

### Requirement: Pipeline endpoint uses TTL cache or materialized view
The `GET /api/ingestion/pipeline?window=24h` endpoint SHALL be backed by either the same 60-second TTL cache that serves connector aggregates or a materialized view refreshed no more often than every 60 seconds. Per-request UNION ALL aggregation across `public.ingestion_events` and `connectors.filtered_events` at poll cadence SHALL be prohibited.

#### Scenario: Pipeline served from cache
- **WHEN** the pipeline endpoint is polled at 30-second cadence
- **THEN** a single PromQL query (or materialized-view read) happens at most once per 60 seconds
- **AND** intermediate polls return cached values

#### Scenario: Per-request UNION ALL prohibited
- **WHEN** the pipeline endpoint implementation is reviewed
- **THEN** no SQL path SHALL execute a per-request UNION ALL aggregation across `public.ingestion_events` and `connectors.filtered_events`
- **AND** any aggregation across those tables MUST be served from a cache or materialized view

#### Scenario: Materialized view alternative honored
- **WHEN** an implementation chooses a materialized view in place of the PromQL+TTL path
- **THEN** the view SHALL be refreshed no more often than every 60 seconds
- **AND** the view SHALL provide the same response shape as the PromQL path (including the `aggregates_available` flag, which SHALL be `true` when the view is fresh and `false` when the refresh job has failed for longer than the refresh interval)

### Requirement: Aggregate response field shape
The aggregate fields surfaced on the connector roster, connector detail, and pipeline endpoints SHALL conform to this shape:

- `spark24h: number[]` — 24 buckets of accepted event counts (one per hour), oldest first
- `rate1h: number` — events per minute over the trailing 60 minutes
- `routedPct: number` — value in `[0.0, 100.0]` representing routed-vs-total percentage over the trailing 24 hours
- `filtered24h: number` — count of filtered events over the trailing 24 hours
- `aggregates_available: boolean` — true when fields above are sourced from a healthy Prometheus/materialized-view read; false when the degraded-mode fallback is in effect

#### Scenario: Healthy response shape
- **WHEN** the aggregate endpoint returns healthy data
- **THEN** all five fields above are present with values consistent with the type contract

#### Scenario: Degraded response shape
- **WHEN** the aggregate endpoint returns degraded data
- **THEN** `spark24h` is an array of 24 zeros, `rate1h` is 0, `routedPct` is 0, `filtered24h` is 0, and `aggregates_available` is false
