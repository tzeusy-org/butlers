## MODIFIED Requirements

### Requirement: Heartbeat Protocol

All connectors SHALL send periodic heartbeats to the Switchboard for liveness tracking, operational statistics collection, and capability advertisement. Heartbeats SHALL be the sole mechanism for connector self-registration — no manual pre-configuration is needed.

For the strengthened history contract, accepted handler heartbeats SHALL commit their existing history append and registry persistence atomically before an adoptable ACK, under Durable heartbeat recording coverage. This changes the current best-effort log commit boundary explicitly; it SHALL NOT add per-heartbeat DML statements, block connector ingestion on submission failure, change wire schema/intervals/counters/provider health, or certify preactivation history.

ID: REQ-connector-base-spec-003
Source: bu-s11n0s.5 complete-protocol D1-D6; preserved governing requirement at a6f342bd54c207b9e672d7d4e51be6af6d4f2876
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
- **WHEN** a heartbeat arrives with a different `instance_id` than the previous one from the same connector
- **THEN** the Switchboard detects a restart; counter deltas are computed against zero (not the previous snapshot)
- **WHEN** the `instance_id` matches
- **THEN** deltas = current - previous

#### Scenario: Atomic accepted heartbeat survives independent readback

- **WHEN** a valid admitted heartbeat completes the strengthened receiver transaction
- **THEN** the same transaction SHALL durably contain the database-timed append and registry update before accepted ACK
- **AND** coverage SHALL start no earlier than its server-derived activation boundary
- **AND** ordinary connector submission failure SHALL remain non-blocking for ingestion

#### Scenario: Append or registry failure produces no accepted ACK

- **WHEN** required append, registry persistence or commit fails
- **THEN** both existing writes SHALL roll back and no adoptable ACK SHALL be issued
- **AND** the next normal scheduled heartbeat SHALL remain retryable
- **AND** a local successful Gmail contact-query snapshot SHALL remain loaded despite publication failure

#### Scenario: Gmail refused admission cannot renew recording coverage

- **WHEN** a Gmail request has wrong instance, epoch or generation under existing admission
- **THEN** its bounded refusal ACK SHALL be produced before history and registry mutation
- **AND** endpoint advisory then row ordering and exact-request ACK adoption SHALL remain intact
- **AND** the seven-day log SHALL not become known-contact classification authority


### Requirement: Producers write their own operational role

The role SHALL be written from the provenance of the write — which producer
created or claimed the row — and SHALL NOT be derived from the content or shape
of the opaque `endpoint_identity` string.

Direct SQL heartbeat role promotion SHALL remain compatible. An unpaired direct heartbeat refresh SHALL invalidate complete recording coverage within its existing registry write, rather than claim complete history or reject an otherwise supported producer. Settings and cursor writes SHALL not mint coverage or demote runtime ownership.

ID: REQ-connector-base-spec-004
Source: bu-s11n0s.5 complete-protocol D1-D6; preserved governing requirement at a6f342bd54c207b9e672d7d4e51be6af6d4f2876
Scope: v1-mandatory

#### Scenario: A heartbeat claims the row

- **WHEN** the `connector.heartbeat` tool persists a heartbeat for
  `(connector_type, endpoint_identity)`
- **THEN** that row's `operational_role` SHALL be set to `runtime_instance`,
  whether the row is newly registered or already existed

#### Scenario: A checkpoint save declares what kind of row it is creating

`save_cursor` SHALL require its caller to declare the cursor's ownership; the
declaration has no default, so a connector cannot create a row without saying
which kind it is.

- **WHEN** `save_cursor` inserts a row that did not exist, and the caller names
  a `parent_endpoint_identity`
- **THEN** that row's `operational_role` SHALL be `checkpoint`
- **AND** its `parent_endpoint_identity` SHALL be that identity

- **WHEN** `save_cursor` inserts a row that did not exist, and the caller
  declares that the cursor key IS the connector's own runtime identity
- **THEN** that row's `operational_role` SHALL be `unknown` — unclaimed until a
  heartbeat proves a process owns it
- **AND** it SHALL NOT be `checkpoint`, because it is not storage state
  belonging to a parent

- **AND** `save_cursor` SHALL NOT write `runtime_instance` in either case:
  persisting a cursor is not evidence that a process is running

A row created by `save_cursor` therefore SHALL NOT be a `checkpoint` with a NULL
`parent_endpoint_identity`. A NULL parent on a `checkpoint` row means the row is a
legacy orphan, and never that its writer omitted a value.

#### Scenario: A checkpoint save never demotes a runtime instance

- **WHEN** `save_cursor` writes to a row that already exists
- **THEN** the row's `operational_role` SHALL be left unchanged
- **AND** a previously recorded `parent_endpoint_identity` SHALL NOT be cleared

Most connectors checkpoint under the same identity they heartbeat with. Were the
conflict branch to re-stamp the role, a live connector would demote itself out of
the fleet roster on its next cursor save. Role ownership is therefore one-way: a
heartbeat promotes, and nothing demotes.

#### Scenario: A connector with multi-dimensional cursor keys names its parent

- **WHEN** a connector persists cursors under a key that carries dimensions
  beyond its heartbeat identity — for example one cursor per account and per
  resource
- **THEN** it SHALL pass its canonical heartbeat identity as the cursor's
  `parent_endpoint_identity`

#### Scenario: A heartbeat written by SQL claims the role like any other

- **WHEN** a connector keeps a registry row's `last_heartbeat_at` current by
  writing the column directly rather than through the `connector.heartbeat` tool
- **THEN** that write SHALL also set `operational_role` to `runtime_instance`

Writing a heartbeat is what claims a row, regardless of which code path writes
it. A producer that refreshes liveness but leaves the role alone strands its own
identities in whatever state something else happened to create them in.

#### Scenario: Unpaired SQL refresh remains supported without completeness

- **WHEN** an existing direct SQL producer refreshes its own runtime registry heartbeat without a same-transaction history append
- **THEN** its existing runtime_instance promotion and recency behavior SHALL remain supported
- **AND** complete recording coverage SHALL become unavailable
- **AND** its positive exact-endpoint current observation SHALL not certify earlier empty hours

#### Scenario: Settings or cursor data cannot mint coverage

- **WHEN** a normal settings or cursor write changes unrelated registry fields or supplies a forged coverage object
- **THEN** the existing operational role SHALL remain unchanged and no new complete-history authority SHALL be minted
- **AND** the protected marker SHALL be preserved only for a valid unrelated write, or conservatively invalidated when heartbeat provenance changes


## ADDED Requirements

### Requirement: Durable heartbeat recording coverage

Complete heartbeat recording SHALL be established only by post-serialization database-clock-stamped paired existing writes under a catalog-verified closed trusted migration-owned trigger chain, and SHALL not be a caller-writable timestamp, capability key or stale matching row. Normal permitted runtime DML SHALL not backdate or forge coverage, mutate accepted history, or bypass guard validation. Legacy unpaired writes SHALL remain supported but invalidate coverage. No extra per-heartbeat DML statements, new roles/grants or retention policy SHALL be introduced. Trusted migration/admin DDL and backup authority remain outside this ordinary-runtime guarantee.

ID: REQ-connector-base-spec-005
Source: bu-s11n0s.5 complete-protocol D1-D6; heart-and-soul/vision.md failure and staleness honesty; owner-timezone-context and dashboard-design-language temporal contracts
Scope: v1-mandatory

#### Scenario: Paired append establishes forward coverage

- **WHEN** the admitted handler appends a database-timed log row and updates the same endpoint registry in one transaction
- **THEN** only the current xid8 matching row SHALL establish or preserve the server-derived coverage boundary
- **AND** the existing two DML writes SHALL suffice
- **AND** independent postcommit readback SHALL observe both or neither

#### Scenario: Runtime marker and trigger tampering cannot backdate coverage

- **WHEN** an existing ordinary runtime identity supplies a marker timestamp, stale row, recording_xid or an additional BEFORE trigger
- **THEN** actual guard-context and closed-chain catalog validation SHALL refuse the interference or make listening UNKNOWN; a second moving clock call SHALL NOT be claimed to reconstruct the original receipt
- **AND** direct-child/COPY and extra AFTER/deferred trigger or lookalike attachment paths SHALL have the same protected-context checks
- **AND** ALTER/DISABLE/guard replacement/TRUNCATE must remain unavailable to that normal role
- **AND** no positive privilege assumption SHALL be inferred from a mocked pool

#### Scenario: Unpaired direct write invalidates coverage atomically

- **WHEN** a supported direct registry heartbeat has no same-transaction appended witness
- **THEN** its same registry write SHALL clear completeness without preventing role promotion
- **AND** another connection SHALL observe no stale certified boundary after commit

#### Scenario: Rollback unknown acknowledgement and replay preserve truth

- **WHEN** the transaction rolls back or its committed ACK is lost and another request arrives
- **THEN** rollback SHALL establish no coverage and unknown ACK SHALL not undo a real committed observation
- **AND** replay or old instance/generation cannot retroactively certify an earlier gap
- **AND** Gmail exact-request refusal and local successful-query truth SHALL remain unchanged

#### Scenario: Endpoint races preserve one complete transition

- **WHEN** same-endpoint submissions and a legacy refresh race
- **THEN** actual history guards SHALL acquire the exact endpoint lock before clock_timestamp receipt assignment, including a direct SQL statement started before a reader wait
- **AND** compatible relation locks plus post-lock catalog checks SHALL fence trigger DDL while allowing unrelated endpoint heartbeat DML
- **AND** reader as_of SHALL follow its endpoint/row/catalog locks, so a later committed observation cannot be backdated into that closed interval
- **AND** reversed paired work SHALL refuse/roll back without resurrecting completeness, while supported unpaired registry-only work remains accepted and clears it
- **AND** other endpoint locks SHALL remain independent

#### Scenario: Partition replacement cannot impersonate empty complete history

- **WHEN** a recorded partition is pruned or recreated with the same name and a different OID
- **THEN** its old receipt SHALL no longer authorize DEAF
- **AND** legitimate MONTHLY pruning SHALL remain unchanged
- **AND** a later paired activation SHALL start a new forward boundary

#### Scenario: Old data and downgrade preserve accepted rows

- **WHEN** the recording migration upgrades or downgrades a disposable schema
- **THEN** all preexisting registry/history rows and original received_at values SHALL remain preserved
- **AND** preactivation rows SHALL not be backfilled as complete authority
- **AND** removing only new guards/metadata SHALL return incomplete history to UNKNOWN
