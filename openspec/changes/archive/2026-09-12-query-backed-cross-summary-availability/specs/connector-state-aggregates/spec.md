# Connector State Aggregates

## ADDED Requirements

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
