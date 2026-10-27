# Dashboard Ingestion Dispatch Console

## ADDED Requirements

### Requirement: Connector aggregate availability is rendered as a data state

The ingestion dashboard SHALL preserve the connector aggregate API's
availability state.  A response with `meta.aggregates_available=false` SHALL
render an explicit unavailable/degraded state for the affected aggregate
surface and SHALL NOT be presented as an empty or zero-valued connector
summary.  A measured zero SHALL remain renderable only when the API explicitly
reports that the aggregate source is available.

#### Scenario: Unavailable connector counters are not rendered as zero

- **WHEN** the connector aggregate response reports
  `meta.aggregates_available=false` because no usable `*_total` sample exists
- **THEN** the dashboard names the aggregate source as unavailable or degraded
- **AND** it does not render the affected count as `0` or use it in an
  all-clear KPI

#### Scenario: A measured empty aggregate remains distinct from unavailable

- **WHEN** the aggregate source is available and reports no matching events
- **THEN** the dashboard may render the measured empty state or zero count
- **AND** it does not replace that state with an unavailable error

#### Scenario: An absent fanout metric is unavailable

- **WHEN** the exact-family availability probe finds no live
  `butlers_switchboard_subroute_dispatched_total` series with bounded
  `source="connector"` provenance and a non-empty `destination_butler`
- **THEN** the API reports `meta.aggregates_available=false`
- **AND** the dashboard renders the routing distribution as unavailable rather
  than a measured empty state

#### Scenario: Exact endpoint fanout stays out of metric labels

- **WHEN** the fanout endpoint returns per-connector and per-endpoint rows
- **THEN** those exact dimensions come from the sessions-to-ingestion-events DB
  projection
- **AND** raw connector or endpoint identities do not become OTel metric labels
- **AND** a failed DB fan-out leg reports `meta.aggregates_available=false`

#### Scenario: A routing distribution requires queried DB targets

- **WHEN** the producer is live but no DB target was queried
- **THEN** the API reports `meta.aggregates_available=false`, not a measured empty distribution
- **AND** one or more successful target queries returning zero rows, with no failed targets, reports a measured empty distribution
- **AND** a failed target query reports `meta.aggregates_available=false` while preserving rows from successful targets
