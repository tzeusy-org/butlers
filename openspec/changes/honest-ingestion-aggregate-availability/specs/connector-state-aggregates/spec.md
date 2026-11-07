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
`[0] * 24` for the sparkline) with `aggregates_available` left `true` only when
other observations in the same response do not contradict that absence. An
empty sparkline matrix SHALL produce the degraded envelope with one warning
when raw `rate1h > 0` for any requested window, or when `int(ingested) > 0`
for `window=1h` or `window=24h`. A positive `7d` total with zero `rate1h` MAY
reflect events older than 24 hours and SHALL remain available. Fractional
`increase()` extrapolation below one SHALL NOT by itself contradict an empty
matrix. A nonempty matrix containing sampled zeros SHALL remain readable.
Backlog counters (`failed_total`, `replay_pending_total`, `written_off_total`)
are sourced from PostgreSQL, not Prometheus, and SHALL degrade independently:
their unavailability SHALL be signalled by `backlog_available: false` with each
counter `null` rather than zero, so an unknown backlog is never reported as an
empty one.

ID: REQ-connector-state-aggregates-005
Source: [Observed] src/butlers/api/routers/ingestion_pipeline.py (_degraded_response and _fetch_pipeline_stats); tests/api/test_ingestion_pipeline.py (degraded-mode observations)
Scope: v1-mandatory

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
  query returns an empty matrix without contradictory activity in the same response
- **THEN** the affected field is `0` (or `[0] * 24` for the sparkline)
- **AND** `aggregates_available` remains `true`, because Prometheus was reached
  and answered

#### Scenario: Contradictory empty sparkline degrades the envelope

- **WHEN** the sparkline matrix is empty and raw `rate1h > 0` in any window,
  or `int(ingested) > 0` in a `1h` or `24h` window
- **THEN** the endpoint returns HTTP 200 with the existing degraded envelope
  and `aggregates_available: false`
- **AND** one warning identifies the disagreement without inventing sparkline values

#### Scenario: Older events and sub-unit extrapolation do not contradict absence

- **WHEN** the sparkline matrix is empty, `rate1h` is zero, and either the
  requested window is `7d` or `int(ingested)` is zero
- **THEN** the observed total and 24 zero sparkline buckets remain available

#### Scenario: Sampled zero buckets are distinct from an empty matrix

- **WHEN** the sparkline matrix contains readable zero-valued samples
- **THEN** those measured zeros remain available even beside a positive
  ingested total or rate
