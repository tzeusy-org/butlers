# Google Health Connector

## Purpose

The Google Health connector is a standalone polling process that reads the owner's wellness data (sleep, heart rate, HRV, SpO2, breathing rate, steps, active minutes, VO2 max) from the Google Health API at `https://health.googleapis.com/v4/`, detects new or changed records via state diffing, normalizes events into `ingest.v1` envelopes, and submits them to the Switchboard. It reuses the existing Google OAuth infrastructure (`public.google_accounts`, `google-multi-account-oauth`). It is a pure polling-and-ingest connector and structurally mirrors `connector-spotify`.

## ADDED Requirements

### Requirement: Owner Account Discovery and Scope Verification

The connector SHALL operate against the primary Google account in `public.google_accounts` and SHALL verify that the required Google Health scopes are granted before ingesting.

#### Scenario: Startup with scopes granted

- **WHEN** the connector starts and the primary Google account has all three Google Health scopes in `granted_scopes` (`https://www.googleapis.com/auth/googlehealth.sleep`, `https://www.googleapis.com/auth/googlehealth.activity_and_fitness`, `https://www.googleapis.com/auth/googlehealth.health_metrics_and_measurements`)
- **THEN** the connector SHALL report health status `healthy`
- **AND** SHALL spawn per-resource polling loops

#### Scenario: Startup with scopes missing

- **WHEN** the connector starts and the primary Google account lacks one or more Google Health scopes
- **THEN** the connector SHALL report health status `degraded`
- **AND** SHALL emit no envelopes
- **AND** SHALL re-check `granted_scopes` every 300 seconds

#### Scenario: No primary Google account

- **WHEN** the connector starts and `public.google_accounts` has no row with `is_primary = true`
- **THEN** the connector SHALL report health status `degraded`
- **AND** SHALL periodically re-scan

#### Scenario: Non-primary accounts are ignored

- **WHEN** `public.google_accounts` contains multiple rows
- **THEN** the connector SHALL poll only the primary account's wellness scopes
- **AND** SHALL NOT poll non-primary accounts (single-owner v1 safety invariant)

#### Scenario: Scope revocation mid-run

- **WHEN** the connector is running and the primary account's `granted_scopes` loses Google Health scopes
- **THEN** the connector SHALL transition to `degraded` and stop emitting new envelopes

### Requirement: Owner Contact Info Registration

The pairing flow SHALL pre-register the owner's Google Health identity as a `public.contact_info` row so that downstream identity resolution resolves `sender.identity` to the owner entity.

#### Scenario: Contact-info upsert on successful pairing

- **WHEN** the OAuth callback for `scope_set=health` completes successfully
- **THEN** a row SHALL be upserted into `public.contact_info` with `type = "google_health"`, `value = <google_user_id>`, `entity_id = <owner_entity_id>`, `secured = false`
- **AND** re-running pairing for the same account SHALL be idempotent

### Requirement: OAuth Token Lifecycle via Shared Google Credential Pipeline

The connector SHALL NOT implement its own OAuth refresh logic. It SHALL delegate to the shared Google credential pipeline (using `resolve_owner_entity_info()` — NOT `CredentialStore.resolve()` or `os.environ.get`).

#### Scenario: Access token acquisition

- **WHEN** the connector needs to make a Google Health API call
- **THEN** it SHALL request a fresh access token from the shared Google credential helper
- **AND** the connector SHALL NOT read `GOOGLE_OAUTH_REFRESH_TOKEN` from `CredentialStore` or environment directly

#### Scenario: 401 response handling

- **WHEN** a Google Health API call returns HTTP 401
- **THEN** the connector SHALL invalidate its cached access token and retry once
- **AND** if the retry also returns 401, SHALL mark the account `status = 'revoked'` and transition to `degraded`

#### Scenario: Access tokens are never persisted

- **WHEN** the connector holds an access token
- **THEN** the token SHALL live only in memory and SHALL NOT be written to the database, logs, or any file

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

### Requirement: Reconciled Stream Consumption

#### Scenario: Reconciled stream preference

- **WHEN** the connector fetches a data type bundle that supports the Reconciled Stream
- **THEN** the connector SHALL use the Reconciled Stream (passes `view=reconciled`)

### Requirement: Ingest Envelope Construction

The connector SHALL produce `ingest.v1` envelopes conformant with the shared connector contract.

#### Scenario: Wellness envelope shape

- **WHEN** the connector emits any envelope
- **THEN** the envelope SHALL have: `source.channel = "wellness"`, `source.provider = "google_health"`, `source.endpoint_identity = "google_health:user:<google_user_id>"`, `control.idempotency_key = "google_health:<resource>:<record_id>"`, `control.policy_tier = "default"`, `control.ingestion_tier = "full"`

#### Scenario: Sleep session envelope

- **WHEN** the connector emits a sleep session event
- **THEN** `event.external_event_id = "google_health:sleep_session:<session_id>"`
- **AND** `payload.normalized_text` SHALL be `"Slept <Xh Ym> (<efficiency>% efficiency)"`

#### Scenario: Daily summary envelope

- **WHEN** the connector emits a daily summary event
- **THEN** `event.external_event_id = "google_health:<resource>:<YYYY-MM-DD>"`
- **AND** `payload.normalized_text` SHALL be a human-readable summary

### Requirement: Checkpoint Persistence

#### Scenario: Cursor persistence per resource

- **WHEN** a polling loop successfully submits an envelope
- **THEN** the connector SHALL persist via `cursor_store.save_cursor(pool, connector_type="google_health", endpoint_identity="google_health:user:<google_user_id>:<resource>", cursor_value=...)`
- **AND** the per-resource dimension SHALL be encoded into the `endpoint_identity` suffix

### Requirement: Rate-Limit Discipline

#### Scenario: 429 response handling

- **WHEN** the Google Health API returns HTTP 429
- **THEN** the connector SHALL honour any `Retry-After` header
- **AND** SHALL fall back to exponential backoff with jitter if no such header is returned
- **AND** SHALL NOT advance the cursor for the failed request

#### Scenario: Rate-limit header capture

- **WHEN** any Google Health API response carries rate-limit headers
- **THEN** the connector SHALL capture the values as Prometheus metrics labelled by resource

### Requirement: Health Status Reporting

The connector SHALL report health status via the shared heartbeat mechanism. Allowed states are `healthy | degraded | error` (no `broken` state).

#### Scenario: Healthy heartbeat

- **WHEN** the connector has successfully fetched and submitted at least one envelope in the last 2 poll intervals
- **THEN** the heartbeat SHALL report `status = "healthy"`

#### Scenario: Degraded heartbeat

- **WHEN** the connector is running in degraded mode (missing scopes, no primary account, or consecutive API failures)
- **THEN** the heartbeat SHALL report `status = "degraded"` with a structured reason code

#### Scenario: Error heartbeat

- **WHEN** the connector has detected refresh-token invalidation (confirmed 401 after refresh)
- **THEN** the heartbeat SHALL report `status = "error"` with `error_message` set to `"scope_revoked"` or `"token_invalid"`

### Requirement: Source Filter Gate

#### Scenario: Source filter gate evaluation

- **WHEN** the connector is about to submit an `ingest.v1` envelope
- **THEN** it SHALL invoke `IngestionPolicyEvaluator` with scope `connector:google_health:<endpoint_identity>`
- **AND** if the evaluator returns `drop`, the envelope SHALL be recorded in the filtered-events buffer and the cursor SHALL still advance

### Requirement: Filtered Event Flush

#### Scenario: Filtered envelope recording

- **WHEN** the source filter gate drops an envelope
- **THEN** the connector SHALL buffer a record with `connector_type="google_health"`, `source_channel="wellness"`, `status="filtered"`, and flush to `connectors.filtered_events` at end of poll cycle

### Requirement: Replay Queue Drain

#### Scenario: Replay drain on each poll cycle

- **WHEN** a poll cycle begins
- **THEN** the connector SHALL first drain any pending replay requests targeting `connector_type="google_health"`

### Requirement: Structural Cost Gates Not Applicable

Wellness is a single-owner passive signal. The connector SHALL NOT invoke participant-count or chat-metadata structural cost gates.

### Requirement: Chronicler Compatibility Deferred

The connector SHALL explicitly defer Chronicler compatibility until the Chronicler Time Butler proposal is accepted and the Google Health evidence contract has been reviewed.

#### Scenario: Google Health not projected by Chronicler initially

- **WHEN** the Google Health connector emits wellness envelopes
- **THEN** Chronicler SHALL NOT claim deterministic projection support for those records in its initial source set
