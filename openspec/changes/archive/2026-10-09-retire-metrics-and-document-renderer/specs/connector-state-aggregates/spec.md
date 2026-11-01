## MODIFIED Requirements

### Requirement: Prometheus PromQL is the aggregate source of truth

The funnel aggregates SHALL be sourced from Prometheus over its HTTP query API
through `butlers.core.prometheus` (`async_query` for instant
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
  `butlers.core.prometheus.async_query`
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

