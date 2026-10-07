## MODIFIED Requirements

### Requirement: Heartbeat Protocol
All connectors SHALL send periodic heartbeats to the Switchboard for liveness tracking, operational statistics collection, and capability advertisement. Heartbeats SHALL be the sole mechanism for connector self-registration — no manual pre-configuration is needed. Gmail classification advertisement SHALL be a fixed content-free optional extension with bounded state/reason, snapshot generation, last successful refresh, the actual process instance and a server-accepted ordering epoch. When heartbeat is enabled, Gmail startup SHALL attempt a bounded unloaded publication before provider watch/poll/backfill/drop work; publish failure SHALL keep ordinary ingestion operating and SHALL NOT certify classification. An observed startup SHALL invalidate previous classification admission. Positive subsequent evidence SHALL match the accepted instance/epoch, progress monotonically by snapshot generation and remain fresh; a delayed previous-instance/epoch or lower-generation update SHALL NOT overwrite current accepted classification. Duplicate same-generation evidence SHALL not move its successful-refresh timestamp or change its contents. Invalid/legacy evidence SHALL not establish availability. The ordering epoch SHALL be non-secret server-issued metadata at the existing heartbeat boundary, not caller identity or an authorization credential. Registry capabilities SHALL persist as a JSON object through the production codec while preserving unrelated feature flags and all ordinary connector heartbeat semantics. The system SHALL distinguish accepted observed-instance proof from the absence of any server observation; it SHALL NOT describe an older persisted heartbeat as proof of a new process. The opted-in producer SHALL serialize request assembly/send/response adoption, bind every acknowledgement to the exact sent instance, generation and request epoch, and retire timed-out or cancelled attempts so late success/refusal cannot regress newer admission. The acknowledgement generation SHALL echo the request generation, including refusals; startup SHALL echo a null request epoch while returning a new server epoch, and an admitted normal publication SHALL return its sent epoch. An acknowledgement SHALL NOT overwrite a newer local query snapshot or certify a generation it did not carry. Publication failure SHALL NOT demote a successful query snapshot, contacts, last-success time, policy/provider health or genuine loaded drop-time history; independent current registry/API evidence SHALL retain unknown when an unknown transition was accepted, valid admission is missing, freshness expires or historical uncertainty remains.

ID: REQ-connector-base-spec-002
Source: bu-q7vx1q.43 criteria 3/5; docs/connectors/heartbeat.md; existing runtime-role authority and heartbeat admission
Scope: v1-mandatory

#### Scenario: Heartbeat envelope structure (connector.heartbeat.v1)
- **WHEN** a connector sends a heartbeat
- **THEN** the envelope contains: `schema_version` (`"connector.heartbeat.v1"`), `connector` (identity block), `status` (health block), `counters` (operational metrics), `checkpoint` (optional resume cursor), `capabilities` (optional feature flags), `sent_at` (RFC3339 timestamp)

#### Scenario: Connector identity block
- **WHEN** the `connector` section is populated
- **THEN** it contains: `connector_type` (e.g., `"gmail"`, `"telegram_bot"`, `"telegram_user_client"`), `endpoint_identity` (auto-resolved at startup), `instance_id` (UUID4, stable per process lifetime — a new ID indicates restart), `version` (optional software version)

#### Scenario: Health status block
- **WHEN** the `status` section is populated
- **THEN** `state` is one of `healthy` (normal operation), `degraded` (issues but still ingesting), or `error` (unable to ingest)
- **AND** `error_message` is present when state is `degraded` or `error`
- **AND** `uptime_s` is seconds since process start

#### Scenario: Operational counters block
- **WHEN** the `counters` section is populated
- **THEN** it contains monotonically increasing counters since process start: `messages_ingested`, `messages_failed`, `source_api_calls`, `checkpoint_saves`, `dedupe_accepted`
- **AND** counters are read from the Prometheus registry at heartbeat assembly time

#### Scenario: Checkpoint and capabilities advertisement
- **WHEN** the connector has a resume cursor
- **THEN** `checkpoint` contains: `cursor` (opaque string), `updated_at` (last checkpoint save time)
- **AND** optional `capabilities` dict advertises features (e.g., `{"backfill": true}`)

#### Scenario: Heartbeat interval and bounds
- **WHEN** the heartbeat task runs
- **THEN** it fires every `CONNECTOR_HEARTBEAT_INTERVAL_S` (default 120 seconds)
- **AND** the interval is bounded between 30 seconds (minimum) and 300 seconds (maximum)
- **AND** `CONNECTOR_HEARTBEAT_ENABLED=false` disables the task entirely (development only)

#### Scenario: Non-blocking heartbeat failures
- **WHEN** a heartbeat submission fails
- **THEN** the failure is logged as a warning but never crashes or blocks the ingestion loop

#### Scenario: Self-registration on first heartbeat
- **WHEN** the Switchboard receives a heartbeat from an unknown connector
- **THEN** it auto-creates a `connector_registry` row (no manual pre-configuration needed)

#### Scenario: Instance restart detection and counter deltas
- **WHEN** a heartbeat arrives with a different `instance_id` than the previous one from the same connector, or the instance matches the previous one
- **THEN** the Switchboard detects a restart; counter deltas are computed against zero (not the previous snapshot), for the different-instance branch
- **AND** **WHEN** the `instance_id` matches (the same-instance branch)
- **AND** **THEN** deltas = current - previous

#### Scenario: Gmail startup publishes unloaded before work

- **WHEN** a Gmail process starts with a new actual heartbeat instance and heartbeat is enabled
- **THEN** it attempts a bounded unloaded heartbeat before provider watch/poll/backfill/drop classification work
- **AND** ordinary ingestion still proceeds when publication fails and no successful classification admission is fabricated

#### Scenario: Accepted startup fences prior classification

- **WHEN** the server accepts Gmail unloaded startup evidence
- **THEN** it stores unknown classification and returns a new ordering epoch bound to the observed instance
- **AND** delayed loaded evidence carrying an older instance/epoch cannot certify that startup
- **AND** the startup acknowledgement echoes the sent instance, generation0 and null request epoch while returning a new server ordering epoch

#### Scenario: Snapshot generation cannot move backward

- **WHEN** Gmail evidence arrives out of order within the admitted process/epoch
- **THEN** an older generation does not replace current accepted classification
- **AND** a same-generation duplicate cannot mutate its fields or renew last-success time
- **AND** producer request assembly and acknowledgement adoption are serialized and replies match the exact sent instance, generation and request epoch
- **AND** late or retired success/refusal cannot regress admission or overwrite a newer local query snapshot, and the next request is assembled from the latest state

#### Scenario: Typed capability storage survives the actual codec

- **WHEN** the actual heartbeat producer/parser/writer stores a Gmail classification object through the configured JSONB codec
- **THEN** a separate database read returns an object containing the same fixed classification fields
- **AND** unrelated capability flags retain their meaning and no caller-provided JSON string certifies availability

#### Scenario: Invalid classification does not leak or certify

- **WHEN** new classification metadata is malformed or contains fields outside the fixed contract
- **THEN** its availability is unknown and new classification diagnostics use only bounded reasons
- **AND** contact identities, provider details, exception tails and ordering epochs are absent from new classification logs/metric labels

#### Scenario: Publication failure retains ingestion compatibility

- **WHEN** startup or refresh-transition classification publication fails or times out
- **THEN** ordinary heartbeat failure handling and ingestion/policy behavior remain compatible
- **AND** the failed publication does not become acknowledged availability
- **AND** an actual successful contact query and loaded drop-time history remain loaded with contacts, last-success and provider health unchanged
- **AND** accepted unknown or missing current admission remains unknown independently, and later recovery cannot rewrite an earlier unknown historical row

#### Scenario: Pre-observation evidence is not new-process proof

- **WHEN** no startup or transition observation has reached the server
- **THEN** the server treats stored evidence only as the last admitted instance within its freshness limit
- **AND** it does not claim immediate knowledge of an unobserved restart or failed refresh
- **AND** completely unobserved publication failure alone does not demote still-fresh prior admitted evidence, which expires independently after300-second heartbeat or900-second classification limits
