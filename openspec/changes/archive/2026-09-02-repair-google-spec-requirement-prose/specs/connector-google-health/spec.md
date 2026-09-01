## MODIFIED Requirements

### Requirement: Reconciled Stream Consumption

The connector SHALL consume each supported data-type bundle through its
Reconciled Stream endpoint rather than through raw per-source data points.

#### Scenario: Reconciled stream preference

- **WHEN** the connector fetches a data type bundle that supports the Reconciled Stream
- **THEN** the connector SHALL use the Reconciled Stream by calling the resource's `dataPoints:reconcile` method endpoint (daily-summary and sleep bundles poll `:reconcile` paths; the activity bundle uses `:dailyRollUp`)

### Requirement: Rate-Limit Discipline

The connector SHALL respect Google Health API rate limits, backing off without
advancing its cursor and capturing the rate-limit headers it observes as
metrics.

#### Scenario: 429 response handling

- **WHEN** the Google Health API returns HTTP 429
- **THEN** the connector SHALL honour any `Retry-After` header
- **AND** SHALL fall back to exponential backoff with jitter if no such header is returned
- **AND** SHALL NOT advance the cursor for the failed request

#### Scenario: Rate-limit header capture

- **WHEN** any Google Health API response carries rate-limit headers
- **THEN** the connector SHALL capture the values as Prometheus metrics labelled by resource

### Requirement: Source Filter Gate

The connector SHALL evaluate every `ingest.v1` envelope against the source
filter gate before submission, and SHALL treat a dropped envelope as handled
for cursor purposes.

#### Scenario: Source filter gate evaluation

- **WHEN** the connector is about to submit an `ingest.v1` envelope
- **THEN** it SHALL invoke `IngestionPolicyEvaluator` with scope `connector:google_health:<endpoint_identity>`
- **AND** if the evaluator returns `drop`, the envelope SHALL be recorded in the filtered-events buffer and the cursor SHALL still advance

### Requirement: Filtered Event Flush

The connector SHALL record every envelope the source filter gate drops and
flush those records at the end of the poll cycle.

#### Scenario: Filtered envelope recording

- **WHEN** the source filter gate drops an envelope
- **THEN** the connector SHALL buffer a record with `connector_type="google_health"`, `source_channel="wellness"`, `status="filtered"`, and flush to `connectors.filtered_events` at end of poll cycle

### Requirement: Replay Queue Drain

The connector SHALL drain pending replay requests targeting it before doing
new work in a poll cycle.

#### Scenario: Replay drain on each poll cycle

- **WHEN** a poll cycle begins
- **THEN** the connector SHALL first drain any pending replay requests targeting `connector_type="google_health"`

### Requirement: Structural Cost Gates Not Applicable

Wellness is a single-owner passive signal. The connector SHALL NOT invoke participant-count or chat-metadata structural cost gates.

#### Scenario: Structural cost gates are not invoked

- **WHEN** the connector prepares a wellness envelope for submission
- **THEN** it SHALL NOT invoke participant-count or chat-metadata structural cost gates
