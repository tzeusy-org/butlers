## ADDED Requirements

### Requirement: HA Source Health Recording

The implementation SHALL provide the behavior described by this requirement.
`ha_entity_snapshot`'s `captured_at` is re-stamped every snapshot cycle
regardless of whether HA was actually contacted, so it cannot by itself tell
a reader whether the connection is currently healthy. The module SHALL
maintain a single keyed row per source in `ha_source_health` (`source` TEXT
PRIMARY KEY, `status` TEXT CHECK IN `'healthy'`/`'error'`,
`last_success_at`, `last_error_at`, `last_error`, `updated_at`), upserted on
successful WebSocket liveness and REST `/api/states` contact and revoked on
transport/setup failure, so downstream readers can distinguish "HA is
reachable" from "the cache merely looks fresh." A persisted healthy verdict
is a bounded five-minute lease, not an indefinite readiness claim.
The lease is evaluated against PostgreSQL's clock because PostgreSQL writes
the contact timestamp.

#### Scenario: Successful WebSocket authentication records health

- **WHEN** `_ws_connect` completes the HA WebSocket auth handshake
  successfully
- **THEN** the module SHALL upsert `ha_source_health` for `'home_assistant'`
  with `status='healthy'` and `last_success_at=now()`

#### Scenario: WebSocket pong renews the health lease

- **WHEN** the authenticated WebSocket receives a pong
- **THEN** the module SHALL refresh `last_success_at` with a healthy upsert

#### Scenario: Successful REST poll records health

- **WHEN** `_seed_entity_cache_from_rest` successfully fetches and caches
  `GET /api/states`
- **THEN** the module SHALL upsert `ha_source_health` for `'home_assistant'`
  with `status='healthy'` and `last_success_at=now()`

#### Scenario: WebSocket connect failure records an error

- **WHEN** `_ws_connect` raises during the initial connect-and-seed sequence
- **THEN** the module SHALL upsert `ha_source_health` for `'home_assistant'`
  with `status='error'`, `last_error_at=now()`, and `last_error` describing
  the failure, in addition to falling back to REST polling as before

#### Scenario: REST poll failure records an error

- **WHEN** the REST polling fallback loop's `_seed_entity_cache_from_rest`
  call raises
- **THEN** the module SHALL upsert `ha_source_health` for `'home_assistant'`
  with `status='error'`, `last_error_at=now()`, and `last_error` describing
  the failure, instead of only logging a warning

#### Scenario: Live WebSocket failure revokes health immediately

- **WHEN** the WebSocket closes, its message or keepalive loop fails, a pong
  times out, or post-authentication setup/reconnect hydration fails
- **THEN** the module SHALL upsert `status='error'` before relying on fallback
  polling or reconnect
- **AND** a later successful REST contact or WebSocket pong MAY restore the
  healthy lease

#### Scenario: Health recording is idempotent and never blocks the caller

- **WHEN** `_record_ha_source_success` or `_record_ha_source_error` is called
  repeatedly, or the health upsert itself fails (e.g. no DB pool available)
- **THEN** the row SHALL remain a single keyed row per source (`ON CONFLICT
  (source) DO UPDATE`)
- **AND** a health-recording failure SHALL be logged and SHALL NOT raise into
  the caller's connect/poll flow

## REMOVED Requirements

### Requirement: Entity Snapshot Persistence

Superseded by `HA Entity Snapshot Cache`, which records the restored bounded
table writer and removes the contradictory disabled-fact-writer contract.

### Requirement: Database Schema Migration

Superseded by `Home Assistant Database Schema`, which records both active
snapshot-cache persistence and its source-health lease.

## ADDED Requirements

### Requirement: HA Entity Snapshot Cache

The Home Assistant module SHALL maintain a bounded current-state cache in
`ha_entity_snapshot`. The module is its sole writer; each persistence cycle
upserts one row per entity key instead of accumulating temporal fact history.

#### Scenario: Snapshot loop persistence target

- **WHEN** the snapshot loop runs at `snapshot_interval_seconds`
- **THEN** the module SHALL UPSERT the populated in-memory entity cache into
  `ha_entity_snapshot`, updating state, attributes, source timestamps, and
  `captured_at`
- **AND** a later observation of the same entity SHALL replace that entity's
  row in place

#### Scenario: Shutdown

- **WHEN** `on_shutdown` is called
- **THEN** the module SHALL attempt one final bounded
  `ha_entity_snapshot` persistence before closing its connections

### Requirement: Home Assistant Database Schema

The implementation SHALL provide the behavior described by this requirement.
The module provides Alembic migrations for its home-domain tables.

#### Scenario: Migration creates tables

- **WHEN** the Alembic migrations run
- **THEN** `ha_entity_snapshot` (entity_id TEXT PK, state TEXT, attributes JSONB, last_updated TIMESTAMPTZ, captured_at TIMESTAMPTZ) SHALL be created as the active bounded live-state cache written by the Home Assistant module
- **AND** `ha_source_health` (source TEXT PK, status TEXT, last_success_at TIMESTAMPTZ, last_error_at TIMESTAMPTZ, last_error TEXT, updated_at TIMESTAMPTZ) SHALL be created for the module's source-health lease
- **AND** `ha_command_log` (id BIGSERIAL PK, domain TEXT, service TEXT, target JSONB, data JSONB, result JSONB, context_id TEXT, issued_at TIMESTAMPTZ) SHALL be created
- **AND** `maintenance_items` SHALL be created (backing the maintenance tool suite)
- **AND** the `ha_state` predicate SHALL be seeded into `predicate_registry`
- **AND** index `ix_ha_command_log_issued_at` on `ha_command_log(issued_at)` SHALL be created
- **AND** the command log SHALL carry nullable legacy-compatible actuation receipt columns for attempt id, risk, actor, session id, approval id, requested/observed state, status, rollback hint, failure reason, and completion time

#### Scenario: Migration branch label

- **WHEN** `migration_revisions()` is called
- **THEN** it SHALL return `"home"` as the Alembic branch label
