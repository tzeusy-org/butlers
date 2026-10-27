# connector-google-health

## MODIFIED Requirements

### Requirement: Per-Resource Polling Loops

The connector SHALL run independent polling loops per data type bundle.

ID: REQ-connector-google-health-005
Source: bu-k5l35 (archived change 2026-04-24-google-health-connector); [Observed] `src/butlers/connectors/google_health.py` `RESOURCE_BUNDLES`
Scope: v1-mandatory

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
- **THEN** it SHALL also poll `weight` and `body_fat` bundles (`dataTypes/weight` and `dataTypes/body-fat`, authenticated GET `dataPoints:reconcile` endpoints under Reconciled Stream Consumption, within the already-requested `health_metrics_and_measurements.readonly` scope), each defaulting to 3600s
- **AND** it SHALL emit one envelope per sample, with `event.external_event_id` `google_health:<email>:weight:<physicalTime>` (or `body_fat`) and `control.idempotency_key` equal to it, so a trailing-window re-poll is idempotent per sample time
- **AND** the weight value SHALL be converted from grams to kilograms at the connector, with body fat as a percentage
- **AND** the Health ingest SHALL write `measurement_weight` or `measurement_body_fat` with `valid_at` equal to the sample time and `metadata.source='google_health'`


#### Scenario: Resource-specific provider evidence remains distinct

- **WHEN** the body-measurement operation is selected
- **THEN** `weight` SHALL use `/users/me/dataTypes/weight/dataPoints:reconcile` through the existing Google Health client: retained HTTP 200 evidence contains a `dataPoints` array, `weight.weightGrams` as a number, `weight.sampleTime.physicalTime` as a string, and `nextPageToken` as a string
- **AND** `body_fat` SHALL use `/users/me/dataTypes/body-fat/dataPoints:reconcile` through that same client: its retained HTTP 200 empty object establishes an available empty response, not the shape of a positive body-fat point
- **AND** a weight point SHALL NOT establish the body-fat percentage or sample-time shape; the existing body-fat percentage normalization requirement remains subject to its own positive provider-shape verification
- **AND** the original change's task 2.3 SHALL remain incomplete until actual permitted, content-free point-shape evidence covers both resources; empty body-fat responses and synthetic unit fixtures SHALL NOT be reported as that proof

#### Scenario: An empty response does not invent a measurement

- **WHEN** either body-measurement request succeeds with an empty object or no returned points
- **THEN** the connector SHALL emit no sample for that response and SHALL NOT substitute zero, a poll timestamp, or a fabricated body-fat shape
- **AND** it SHALL retain the last committed sample cursor rather than invent a sample identity from emptiness
- **AND** absence from that response SHALL NOT retract, delete, or overwrite a previously stored measurement

#### Scenario: Provider pages and durable sample cursors remain distinct

- **WHEN** a body-measurement response carries a continuation token
- **THEN** the connector SHALL consume the complete selected resource window using the provider's verified continuation semantics for that operation before claiming the window complete
- **AND** verification and implementation of that continuation remain work of `ingest-google-health-body-measurements`; the retained one-page shape receipt alone SHALL NOT establish multi-page completeness
- **AND** an intermediate `nextPageToken` SHALL NOT replace the existing durable cursor keyed by account and resource
- **AND** the durable sample cursor SHALL advance only after the complete poll's required submissions succeed, retaining the existing Source Filter Gate rule that a properly recorded policy drop is handled

#### Scenario: Partial failure retains the last committed checkpoint

- **WHEN** a required provider page or sample submission fails before poll completion
- **THEN** the connector SHALL retain its last committed account/resource cursor and SHALL NOT mark the uncompleted window successfully consumed
- **AND** a submission exception or refusal SHALL NOT become successful consumption merely because the submission helper returned
- **AND** already accepted samples may be retried with their unchanged sample-time idempotency keys; this SHALL NOT claim that accepted effects were rolled back
- **AND** existing error-buffer, filtered-content privacy, replay, rate-limit and account-isolation requirements SHALL remain in force

#### Scenario: Reconciliation does not create local mutation authority

- **WHEN** the connector re-polls a sample or the provider returns a changed or absent reading
- **THEN** the existing account/resource/physical-time external identity and idempotency key SHALL remain unchanged; a same-key replay retains the existing deduplication behavior
- **AND** selecting the reconciled stream SHALL NOT be represented as a verified update/deletion changefeed or authority to replace or retract local fact history
- **AND** no replacement, retraction, deletion, value-derived identity or changed-payload policy is introduced by this endpoint correction; any such behavior requires an exact adopted contract before implementation
- **AND** Health SHALL retain sample-time `valid_at`, `metadata.source='google_health'` and the existing owner-local-day dominance rule, with device rows preserved and owner-logged readings retaining precedence
