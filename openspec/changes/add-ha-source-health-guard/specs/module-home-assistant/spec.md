## ADDED Requirements

### Requirement: HA Source Health Recording

The implementation SHALL provide the behavior described by this requirement.
`ha_entity_snapshot`'s `captured_at` is re-stamped every snapshot cycle
regardless of whether HA was actually contacted, so it cannot by itself tell
a reader whether the connection is currently healthy. The module SHALL
maintain a single keyed row per source in `ha_source_health` (`source` TEXT
PRIMARY KEY, `status` TEXT CHECK IN `'healthy'`/`'error'`,
`last_success_at`, `last_error_at`, `last_error`, `updated_at`), upserted on
every WebSocket authentication and every REST `/api/states` fetch (success or
failure), so downstream readers can distinguish "HA is reachable" from "the
cache merely looks fresh."

#### Scenario: Successful WebSocket authentication records health

- **WHEN** `_ws_connect` completes the HA WebSocket auth handshake
  successfully
- **THEN** the module SHALL upsert `ha_source_health` for `'home_assistant'`
  with `status='healthy'` and `last_success_at=now()`

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

#### Scenario: Health recording is idempotent and never blocks the caller

- **WHEN** `_record_ha_source_success` or `_record_ha_source_error` is called
  repeatedly, or the health upsert itself fails (e.g. no DB pool available)
- **THEN** the row SHALL remain a single keyed row per source (`ON CONFLICT
  (source) DO UPDATE`)
- **AND** a health-recording failure SHALL be logged and SHALL NOT raise into
  the caller's connect/poll flow
