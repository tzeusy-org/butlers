# connector-google-health

## MODIFIED Requirements

### Requirement: Per-Resource Polling Loops

The connector SHALL run independent polling loops per data type bundle.

#### Scenario: Default poll intervals

- **WHEN** the connector has no per-resource overrides configured
- **THEN** it SHALL use these defaults: sleep sessions (1800s), daily activity summary (1800s), daily resting HR (1800s), daily HRV (3600s), daily SpO2 (3600s), daily breathing rate (3600s), VO2 max (86400s)

#### Scenario: First-run backfill

- **WHEN** a per-resource polling loop runs for the first time (no cursor exists)
- **THEN** the connector SHALL request the trailing `GOOGLE_HEALTH_BACKFILL_DAYS` days (default 30)
- **AND** SHALL emit an `ingest.v1` envelope per distinct record found

#### Scenario: Steady-state polling

- **WHEN** a per-resource polling loop runs on a subsequent tick
- **THEN** the connector SHALL request data since the last cursor
- **AND** SHALL emit envelopes only for records not already observed
- **AND** SHALL advance the cursor after successful submission

#### Scenario: Body measurement resources

- **WHEN** the connector runs its resource bundles
- **THEN** it SHALL also poll `weight` and `body_fat` bundles (`dataTypes/weight` and `dataTypes/body-fat`, plain `dataPoints` list endpoints, within the already-requested `health_metrics_and_measurements.readonly` scope), each defaulting to 3600s
- **AND** it SHALL emit one envelope per sample, with `event.external_event_id` `google_health:<email>:weight:<physicalTime>` (or `body_fat`) and `control.idempotency_key` equal to it, so a trailing-window re-poll is idempotent per sample time
- **AND** the weight value SHALL be converted from grams to kilograms at the connector, with body fat as a percentage
- **AND** the Health ingest SHALL write `measurement_weight` or `measurement_body_fat` with `valid_at` equal to the sample time and `metadata.source='google_health'`
