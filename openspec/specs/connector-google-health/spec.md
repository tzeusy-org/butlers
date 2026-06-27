# Google Health Connector

## Purpose

The Google Health connector is a standalone polling process that reads the owner's wellness data (sleep, heart rate, HRV, SpO2, breathing rate, steps, active minutes, VO2 max) from the Google Health API at `https://health.googleapis.com/v4/`, detects new or changed records via state diffing, normalizes events into `ingest.v1` envelopes, and submits them to the Switchboard. It reuses the existing Google OAuth infrastructure (`public.google_accounts`, `google-multi-account-oauth`). It is a pure polling-and-ingest connector and structurally mirrors `connector-spotify`.

## Requirements

### Requirement: Owner Account Discovery and Scope Verification

The connector SHALL operate against **every** `public.google_accounts` row whose `status = 'active'` and whose `granted_scopes` contains all three Google Health scopes (`https://www.googleapis.com/auth/googlehealth.sleep`, `https://www.googleapis.com/auth/googlehealth.activity_and_fitness`, `https://www.googleapis.com/auth/googlehealth.health_metrics_and_measurements`). It SHALL maintain independent per-account polling state and emit a heartbeat per account.

#### Scenario: Startup with one or more accounts granting Health scopes

- **WHEN** the connector starts and `public.google_accounts` contains one or more `status = 'active'` rows with all three Google Health scopes in `granted_scopes`
- **THEN** the connector SHALL spawn per-resource polling loops for each such account
- **AND** SHALL emit one heartbeat per account labelled `endpoint_identity = google_health:user:<email>`
- **AND** SHALL report aggregate health status `healthy`

#### Scenario: Startup with no health-scoped accounts

- **WHEN** no active `public.google_accounts` row carries all three Google Health scopes
- **THEN** the connector SHALL report aggregate health status `degraded`
- **AND** SHALL emit no envelopes
- **AND** SHALL re-check every 300 seconds

#### Scenario: Account added while running

- **WHEN** the connector is running and a new `google_accounts` row appears (status=active, all three Health scopes granted)
- **THEN** within `scope_recheck_s` (default 300s) the connector SHALL spawn per-resource polling loops for the new account
- **AND** SHALL emit a new heartbeat row for it

#### Scenario: Account loses health scopes mid-run

- **WHEN** the connector is running and one of the polled accounts loses any Google Health scope
- **THEN** within `scope_recheck_s` the connector SHALL stop polling that account and close its heartbeat
- **AND** SHALL continue polling any other still-eligible accounts uninterrupted

### Requirement: Owner Contact Info Registration

The pairing flow SHALL pre-register each owner Google Health identity as its own `public.contact_info` row so that downstream identity resolution resolves `sender.identity` to the owner entity. One row per Google account.

#### Scenario: Contact-info upsert per account

- **WHEN** the OAuth callback for `scope_set=health` completes successfully for a given Google account
- **THEN** a row SHALL be upserted into `public.contact_info` with `type = "google_health"`, `value = <google_user_id_for_that_account>`, `entity_id = <owner_entity_id>`, `secured = false`
- **AND** re-running pairing for the same account SHALL be idempotent
- **AND** multiple health-scoped accounts SHALL produce multiple `(type='google_health', value=<email_n>)` rows that share the same `entity_id`

### Requirement: OAuth Token Lifecycle via Shared Google Credential Pipeline

The connector SHALL NOT implement its own OAuth refresh logic. It SHALL delegate to the shared Google credential pipeline, resolving OAuth app credentials (client_id, client_secret) via `load_google_credentials()` and each account's refresh token via `google_credentials._resolve_entity_refresh_token()` (keyed by the account's companion entity_id). It SHALL NOT read `GOOGLE_OAUTH_REFRESH_TOKEN` from `CredentialStore.resolve()` or `os.environ`.

#### Scenario: Access token acquisition

- **WHEN** the connector needs to make a Google Health API call
- **THEN** it SHALL request a fresh access token from the shared Google credential helper
- **AND** the connector SHALL NOT read `GOOGLE_OAUTH_REFRESH_TOKEN` from `CredentialStore` or environment directly

#### Scenario: 401 response handling

- **WHEN** a Google Health API call returns HTTP 401
- **THEN** the connector SHALL invalidate its cached access token and retry once
- **AND** if the retry also returns 401, SHALL set that account's heartbeat to `error` (error_message `token_invalid`) and stop polling it, leaving other accounts unaffected
- **AND** the shared `public.google_accounts.status` SHALL be flipped to `revoked` only when Google's token endpoint returns `invalid_grant` (a confirmed grant revocation), so a transient or scope-local API 401 never knocks the sibling Google connectors (Drive, Calendar, Gmail) offline

#### Scenario: Access tokens are never persisted

- **WHEN** the connector holds an access token
- **THEN** the token SHALL live only in memory and SHALL NOT be written to the database, logs, or any file

### Requirement: Per-Account Token Cache

The connector SHALL maintain one access-token cache per account, keyed by `google_accounts.id`. Tokens MUST be minted via the scope-restricted refresh-token grant (per the existing `Scope-Restricted Access Token Minting` requirement) using that account's own refresh token.

#### Scenario: Refresh token resolution per account

- **WHEN** the connector needs a fresh access token for a given account
- **THEN** it SHALL read the refresh token from `public.entity_info` keyed by that account's `companion_entity_id` (the existing companion-entity pattern in `google_credentials._resolve_entity_refresh_token`)
- **AND** SHALL NOT use the shared `resolve_owner_entity_info` "primary account" helper for non-primary accounts

#### Scenario: Mint isolation

- **WHEN** a mint or refresh call fails for one account (transient network error, invalidated refresh token, etc.)
- **THEN** that failure SHALL be confined to the failing account's polling loop
- **AND** SHALL NOT block mints or polls for other accounts
- **AND** the per-account heartbeat SHALL transition to `error` or `degraded` for the failing account only

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
- **THEN** the connector SHALL use the Reconciled Stream by calling the resource's `dataPoints:reconcile` method endpoint (daily-summary and sleep bundles poll `:reconcile` paths; the activity bundle uses `:dailyRollUp`)

### Requirement: Ingest Envelope Construction

The connector SHALL produce `ingest.v1` envelopes conformant with the shared connector contract, whose identity fields disambiguate by account.

#### Scenario: External event identity includes account scope

- **WHEN** the connector emits any envelope
- **THEN** `event.external_event_id` SHALL be `google_health:<email>:<resource>:<record_id>` (sleep) or `google_health:<email>:<resource>:<YYYY-MM-DD>` (daily summaries)
- **AND** `source.endpoint_identity` SHALL be `google_health:user:<email>`
- **AND** `control.idempotency_key` SHALL be `google_health:<email>:<resource>:<record_id>`

#### Scenario: Wellness envelope shape

- **WHEN** the connector emits any envelope
- **THEN** the envelope SHALL have: `source.channel = "wellness"`, `source.provider = "google_health"`, `source.endpoint_identity = "google_health:user:<email>"`, `control.idempotency_key = "google_health:<email>:<resource>:<record_id>"`, `control.policy_tier = "default"`, `control.ingestion_tier = "full"`

#### Scenario: Sleep session envelope

- **WHEN** the connector emits a sleep session event
- **THEN** `event.external_event_id = "google_health:<email>:sleep_session:<session_id>"`
- **AND** `payload.normalized_text` SHALL be `"Slept <Xh Ym> (<efficiency>% efficiency)"`

#### Scenario: Daily summary envelope

- **WHEN** the connector emits a daily summary event
- **THEN** `event.external_event_id = "google_health:<email>:<resource>:<YYYY-MM-DD>"`
- **AND** `payload.normalized_text` SHALL be a human-readable summary

### Requirement: Checkpoint Persistence

The connector SHALL persist per-resource cursors that disambiguate by account, so per-account polling state survives restarts without cross-account collisions.

#### Scenario: Cursor key includes account identifier

- **WHEN** a polling loop successfully submits an envelope
- **THEN** the connector SHALL persist via `cursor_store.save_cursor(pool, connector_type="google_health", endpoint_identity="google_health:user:<email>:<account_uuid>:<resource>", cursor_value=...)`
- **AND** the per-account dimension SHALL be encoded into the `endpoint_identity` between the email and the resource

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

The connector SHALL report health status via the shared heartbeat mechanism. Allowed states are `healthy | degraded | error` (no `broken` state). With multiple accounts, the connector SHALL report one heartbeat row per account AND a connector-level aggregate.

#### Scenario: Per-account heartbeat

- **WHEN** the connector runs polling loops for a given account
- **THEN** it SHALL emit a heartbeat row to `switchboard.connector_registry` with `connector_type = "google_health"` and `endpoint_identity = "google_health:user:<email>"`
- **AND** the per-account `state` and `error_message` fields SHALL reflect only that account's polling outcome

#### Scenario: Aggregate state computation

- **WHEN** computing the connector-level aggregate `state` exposed on the connector's `/health` endpoint
- **THEN** it SHALL be the worst-of state across all per-account heartbeats (`error` > `degraded` > `healthy`)
- **AND** the per-account heartbeat rows SHALL remain individually queryable

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
