# connector-state-aggregates Specification

## Purpose
TBD - created by archiving change query-backed-cross-summary-availability. Update Purpose after archive.

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
