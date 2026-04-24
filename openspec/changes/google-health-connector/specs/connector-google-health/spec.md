# Google Health Connector

## Purpose

The Google Health connector is a standalone polling process that reads the owner's wellness data (sleep, heart rate, HRV, SpO2, breathing rate, steps, active minutes, VO2 max, and related metrics) from the Google Health API at `https://health.googleapis.com/v4/`, detects new or changed records via state diffing, normalizes events into `ingest.v1` envelopes, and submits them to the Switchboard. It provides the Health butler with passive awareness of the owner's physiology — the richest low-effort health signal available.

Unlike messaging connectors, this connector has no discretion layer (all events are the owner's own data), no per-chat buffering (no "chats" exist), and no interactive routing (wellness events are not messages requiring a reply). It is a pure polling-and-ingest connector and structurally mirrors `connector-spotify`.

It reuses the existing Google OAuth infrastructure (`public.google_accounts`, `google-multi-account-oauth`). No new credential silo is introduced; the owner's already-linked Google account gains additional Google Health scopes through the existing re-consent flow.

## ADDED Requirements

### Requirement: Owner Account Discovery and Scope Verification

The connector SHALL operate against the primary Google account in `public.google_accounts` and SHALL verify that the required Google Health scopes are granted before ingesting.

#### Scenario: Startup with scopes granted

- **WHEN** the connector starts and the primary Google account has all three Google Health scopes in `granted_scopes`. `granted_scopes` stores **full scope URLs** as returned by Google's token response, so the check SHALL compare against the full strings: `https://www.googleapis.com/auth/googlehealth.sleep`, `https://www.googleapis.com/auth/googlehealth.activity_and_fitness`, and `https://www.googleapis.com/auth/googlehealth.health_metrics_and_measurements`
- **THEN** the connector SHALL report health status `healthy`
- **AND** SHALL spawn per-resource polling loops
- **AND** SHALL record `source.endpoint_identity = "google_health:user:<google_user_id>"` where `<google_user_id>` is Google's canonical user identifier for that account

#### Scenario: Startup with scopes missing

- **WHEN** the connector starts and the primary Google account lacks one or more Google Health scopes
- **THEN** the connector SHALL report health status `degraded`
- **AND** SHALL emit no envelopes
- **AND** SHALL re-check `granted_scopes` every 300 seconds
- **AND** SHALL transition to `healthy` once all required scopes are present

#### Scenario: No primary Google account

- **WHEN** the connector starts and `public.google_accounts` has no row with `is_primary = true`
- **THEN** the connector SHALL report health status `degraded`
- **AND** SHALL periodically re-scan

#### Scenario: Non-primary accounts are ignored (single-owner v1 invariant)

- **WHEN** `public.google_accounts` contains multiple rows but only one has `is_primary = true`
- **THEN** the connector SHALL poll only the primary account's wellness scopes
- **AND** SHALL NOT poll non-primary accounts even if they have Google Health scopes granted
- **AND** this restriction is a single-owner safety invariant for v1; multi-account wellness ingestion is explicitly out of scope

#### Scenario: Scope revocation mid-run

- **WHEN** the connector is running and the primary account's `granted_scopes` loses one or more Google Health scopes
- **THEN** the connector SHALL transition to `degraded`
- **AND** SHALL stop emitting new envelopes
- **AND** SHALL log a warning directing the owner to re-consent

### Requirement: Owner Contact Info Registration

The pairing flow SHALL pre-register the owner's Google Health identity as a `public.contact_info` row so that downstream identity resolution (per RFC 0004) resolves `sender.identity` to the owner entity without creating a temporary contact.

#### Scenario: Contact-info upsert on successful pairing

- **WHEN** the OAuth callback for `scope_set=health` completes successfully and the owner's Google user ID is available from the userinfo response
- **THEN** a row SHALL be upserted into `public.contact_info` with `type = "google_health"`, `value = <google_user_id>`, `entity_id = <owner_entity_id>`, `secured = false`
- **AND** re-running pairing for the same account SHALL be idempotent (no duplicate row)

#### Scenario: Identity resolution at ingest

- **WHEN** the Switchboard resolves `sender.identity = <google_user_id>` on a `wellness/google_health` envelope
- **THEN** it SHALL find the pre-registered `contact_info` row with `type = "google_health"` and return the owner entity
- **AND** SHALL NOT call `create_temp_contact()` for wellness envelopes
- **AND** wellness envelopes SHALL NOT produce disambiguation candidates

### Requirement: OAuth Token Lifecycle via Shared Google Credential Pipeline

The connector SHALL NOT implement its own OAuth refresh logic. It SHALL delegate access-token acquisition and refresh to the shared Google credential pipeline already used by Calendar and Drive. Per `about/heart-and-soul/security.md`, Tier-2 connectors MUST use `resolve_owner_entity_info()` (or the equivalent Google companion-entity resolver already used by `connector-gmail` and `connector-google-calendar`) — they MUST NOT use `CredentialStore.resolve()` or `os.environ.get` for refresh tokens.

#### Scenario: Access token acquisition

- **WHEN** the connector needs to make a Google Health API call
- **THEN** it SHALL request a fresh access token from the shared Google credential helper, keyed by the primary account's `entity_id`
- **AND** the helper SHALL be responsible for using the stored refresh token to mint a new access token if needed
- **AND** the connector SHALL NOT read `GOOGLE_OAUTH_REFRESH_TOKEN` from `CredentialStore` or the process environment directly

#### Scenario: 401 response from Google Health API

- **WHEN** a Google Health API call returns HTTP 401
- **THEN** the connector SHALL invalidate its cached access token and request a fresh one from the credential helper
- **AND** SHALL retry the call once
- **AND** if the retry also returns 401, SHALL mark the account `status = 'revoked'` via the existing credential pipeline and transition to `degraded`

#### Scenario: Access tokens are never persisted

- **WHEN** the connector holds an access token
- **THEN** the token SHALL live only in memory
- **AND** SHALL NOT be written to the database, to logs, or to any file

### Requirement: Per-Resource Polling Loops

The connector SHALL run independent polling loops per data type bundle.

#### Scenario: Default poll intervals

- **WHEN** the connector has no per-resource overrides configured
- **THEN** it SHALL use the following defaults:
  - Sleep sessions: every 1800 seconds (30 minutes)
  - Daily activity summary: every 1800 seconds
  - Daily resting HR: every 1800 seconds
  - Daily HRV: every 3600 seconds (1 hour — HRV updates less frequently)
  - Daily SpO2: every 3600 seconds
  - Daily breathing rate: every 3600 seconds
  - VO2 max: every 86400 seconds (once a day — VO2 max changes slowly)

#### Scenario: First-run backfill

- **WHEN** a per-resource polling loop runs for the first time (no cursor exists)
- **THEN** the connector SHALL request the trailing `GOOGLE_HEALTH_BACKFILL_DAYS` days of daily summaries for that resource (default 30 if the env var is unset)
- **AND** SHALL emit an `ingest.v1` envelope per distinct record found
- **AND** SHALL persist the most-recent record's identifier as the resource's initial cursor

#### Scenario: Steady-state polling

- **WHEN** a per-resource polling loop runs on a subsequent tick
- **THEN** the connector SHALL request data since the last cursor
- **AND** SHALL emit envelopes only for records not already observed
- **AND** SHALL advance the cursor to the most-recent observed record after successful submission

### Requirement: Reconciled Stream Consumption

The connector SHALL consume the Google Health API's Reconciled Stream for any data type that supports it, rather than per-source streams.

#### Scenario: Reconciled stream preference

- **WHEN** the connector fetches a data type bundle that Google Health exposes both as a Reconciled Stream and as per-source streams
- **THEN** the connector SHALL use the Reconciled Stream
- **AND** SHALL record the originating source list in `payload.raw` for transparency, but the event identity SHALL key off the reconciled record

### Requirement: Ingest Envelope Construction

The connector SHALL produce `ingest.v1` envelopes conformant with the shared connector contract.

#### Scenario: Wellness envelope shape

- **WHEN** the connector emits any envelope
- **THEN** the envelope SHALL have:
  - `source.channel = "wellness"`
  - `source.provider = "google_health"`
  - `source.endpoint_identity = "google_health:user:<google_user_id>"`
  - `sender.identity = "<google_user_id>"` — resolved to the owner entity via the pre-registered `contact_info` row (see "Owner Contact Info Registration" below)
  - `event.observed_at` = poll timestamp (RFC3339, timezone-aware)
  - `payload.raw` = full Google Health API response dict for the record
  - `control.idempotency_key = "google_health:<resource>:<record_id>"`
  - `control.policy_tier = "default"`
  - `control.ingestion_tier = "full"`

#### Scenario: Sleep session envelope

- **WHEN** the connector emits a sleep session event
- **THEN** `event.external_event_id = "google_health:sleep_session:<session_id>"`
- **AND** `payload.normalized_text` SHALL be `"Slept <Xh Ym> (<efficiency>% efficiency)"`
- **AND** `payload.raw` SHALL contain the full session record including `stages` breakdown

#### Scenario: Daily summary envelope

- **WHEN** the connector emits a daily summary event (e.g. daily steps, daily resting HR)
- **THEN** `event.external_event_id = "google_health:<resource>:<YYYY-MM-DD>"`
- **AND** `payload.normalized_text` SHALL be a human-readable summary such as `"Steps: 9342"` or `"Resting HR: 58 bpm"`

#### Scenario: Repeated observation of the same record

- **WHEN** consecutive polls return a record whose identifier matches the current cursor
- **THEN** the connector SHALL NOT emit a duplicate envelope
- **AND** the cursor SHALL be updated with the latest poll timestamp

### Requirement: Checkpoint Persistence

The connector SHALL persist resume cursors per resource via the shared `cursor_store`.

#### Scenario: Cursor persistence per resource

- **WHEN** a polling loop successfully submits an envelope
- **THEN** the connector SHALL persist the cursor via `cursor_store.save_cursor(pool, connector_type="google_health", endpoint_identity="google_health:user:<google_user_id>:<resource>", cursor_value=...)`
- **AND** the per-resource dimension SHALL be encoded into the `endpoint_identity` suffix (e.g. `google_health:user:<google_user_id>:sleep`) because `cursor_store` uses a 2-tuple `(connector_type, endpoint_identity)` key, not a 3-tuple
- **AND** the cursor value SHALL be sufficient to resume ingestion without gaps on restart

#### Scenario: Cursor restoration on startup

- **WHEN** a polling loop starts and a cursor exists
- **THEN** the connector SHALL load the cursor via `cursor_store.load_cursor(pool, connector_type="google_health", endpoint_identity="google_health:user:<google_user_id>:<resource>")` before issuing its first API call
- **AND** SHALL request data starting from the cursor's record or timestamp

### Requirement: Rate-Limit Discipline

The connector SHALL handle Google Health API rate limits without crashing or losing records.

#### Scenario: 429 response handling

- **WHEN** the Google Health API returns HTTP 429
- **THEN** the connector SHALL honour any `Retry-After` header as the sleep duration
- **AND** SHALL fall back to exponential backoff with jitter if no such header is returned
- **AND** SHALL NOT advance the cursor for the failed request

#### Scenario: Rate-limit header capture

- **WHEN** any Google Health API response carries rate-limit headers (e.g. `X-RateLimit-Remaining`, `X-RateLimit-Reset`)
- **THEN** the connector SHALL capture the values as metrics labelled by resource
- **AND** the metrics SHALL be exposed via the connector's Prometheus surface for dashboard visibility

### Requirement: Health Status Reporting

The connector SHALL report health status to the Switchboard connector registry via the shared heartbeat mechanism.

#### Scenario: Healthy heartbeat

- **WHEN** the connector has successfully fetched and submitted at least one envelope in the last 2 poll intervals
- **THEN** the heartbeat SHALL report `status = "healthy"`
- **AND** SHALL include counts: envelopes emitted, API calls made, 429s encountered, last successful fetch timestamp

#### Scenario: Degraded heartbeat

- **WHEN** the connector is running in degraded mode (missing scopes, no primary account, or consecutive API failures)
- **THEN** the heartbeat SHALL report `status = "degraded"` with a structured reason code

#### Scenario: Error heartbeat

- **WHEN** the connector has detected refresh-token invalidation (confirmed 401 after refresh)
- **THEN** the heartbeat SHALL report `status = "error"` (per `connector-base-spec` — allowed states are `healthy | degraded | error`)
- **AND** SHALL populate `error_message` with `"scope_revoked"` or `"token_invalid"`

### Requirement: Source Filter Gate

The connector SHALL evaluate every outbound envelope against the source filter gate, per `connector-base-spec`, before submission to the Switchboard. This is mandatory for all connectors — it is distinct from (and narrower than) the optional discretion/content-filter layer used by messaging connectors.

#### Scenario: Source filter gate evaluation

- **WHEN** the connector is about to submit an `ingest.v1` envelope
- **THEN** it SHALL invoke `IngestionPolicyEvaluator` with scope `connector:google_health:<endpoint_identity>`
- **AND** if the evaluator returns `drop`, the envelope SHALL be recorded in the filtered-events buffer (see Filtered Event Flush) and the cursor SHALL still advance (filtered events are intentionally dropped, not retried)
- **AND** if the evaluator returns `pass`, the envelope SHALL proceed to Switchboard submission

#### Scenario: Gate no-op when no filters configured

- **WHEN** no source filters are configured for `connector:google_health:<endpoint_identity>`
- **THEN** every envelope SHALL pass (opt-in model, per `connector-base-spec`)

### Requirement: Filtered Event Flush

The connector SHALL persist filtered and errored envelopes to `connectors.filtered_events` via a batch flush at the end of each poll cycle, per `connector-base-spec`.

#### Scenario: Filtered envelope recording

- **WHEN** the source filter gate drops an envelope
- **THEN** the connector SHALL buffer a record with: `connector_type="google_health"`, `endpoint_identity`, `external_message_id`, `source_channel="wellness"`, `sender_identity`, `subject_or_preview` (the normalized summary), `filter_reason`, `status="filtered"`, `full_payload=<ingest.v1 envelope>`
- **AND** the buffer SHALL be flushed to `connectors.filtered_events` at the end of the poll cycle

#### Scenario: Error envelope recording

- **WHEN** an envelope fails submission due to a processing or validation error
- **THEN** the buffer record SHALL use `status="error"` and `error_detail=<exception message>`

### Requirement: Replay Queue Drain

The connector SHALL drain the replay queue for pending re-ingestion requests, per `connector-base-spec`.

#### Scenario: Replay drain on each poll cycle

- **WHEN** a poll cycle begins
- **THEN** the connector SHALL first drain any pending replay requests targeting `connector_type="google_health"` and the current `endpoint_identity`
- **AND** replayed envelopes SHALL pass through the same source filter gate and submission path as fresh envelopes

### Requirement: Structural Cost Gates Not Applicable

The connector SHALL NOT invoke participant-count or chat-metadata structural cost gates. Wellness is a single-owner passive signal with no participant dimension; the architectural cost gates defined in `about/heart-and-soul/architecture.md` (§"Connectors Are the Computational Cost Boundary") do not apply.

#### Scenario: No participant-count check

- **WHEN** the connector emits wellness envelopes
- **THEN** it SHALL NOT invoke any participant-count or chat-metadata cost gate
- **AND** the connector's documentation SHALL record that cost-gate skipping is justified because wellness events have exactly one principal (the owner) by construction

### Requirement: Chronicler Compatibility Deferred

The connector SHALL explicitly defer Chronicler compatibility until the
Chronicler Time Butler proposal is accepted and the Google Health evidence
contract has been reviewed for privacy, precision, retention, and boundary
semantics.

#### Scenario: Google Health not projected by Chronicler initially

- **WHEN** the Google Health connector emits sleep, activity, or health metric envelopes
- **THEN** Chronicler SHALL NOT claim deterministic projection support for those records in its initial source set
- **AND** any future Chronicler adapter for Google Health SHALL require a compatibility declaration defining time fields, boundary semantics, source references, taxonomy mapping, confidence semantics, privacy tier, retention behavior, idempotency key, and projection path

#### Scenario: Health remains source of truth

- **WHEN** Google Health data is ingested
- **THEN** the Health butler remains the owner of health-domain facts
- **AND** any future Chronicler projection SHALL be derived lived-time evidence, not replacement health truth

## Source References

- Non-Negotiable Rule 3 (MCP-only inter-butler communication)
- Non-Negotiable Rule 7 (Transport is connector responsibility — the Health butler never calls `health.googleapis.com` directly)
- RFC 0003 (Switchboard routing and ingestion) — pending amendment for the `wellness/google_health` channel/provider pair
- RFC 0004 (Identity and contact resolution) — `sender.identity` resolution depends on the pre-registered `contact_info(type="google_health")` row
- RFC 0008 (Deployment network security) — connector service joins `db, backend, egress`; table amendment tracked in tasks §11.3
- RFC 0014 (Chronicler Time Butler, Draft) — compatibility deferred until accepted and reviewed
- `connector-base-spec`
- `connector-spotify` (architectural twin — single-owner passive polling)
- `google-multi-account-oauth` (credential pipeline; scope-set registry extended by this change)
- `about/heart-and-soul/security.md` — Tier-2 credential contract (MUST use `resolve_owner_entity_info()`)
- `docs/archive/health-wearable-draft.md` (historical research, now superseded by the Google Health API pivot)
