# Connector State Aggregates

## MODIFIED Requirements

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
