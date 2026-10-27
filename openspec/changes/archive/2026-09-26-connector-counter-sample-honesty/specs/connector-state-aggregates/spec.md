# Connector State Aggregates

## ADDED Requirements

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
