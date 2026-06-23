# Google Calendar Connector

## Purpose
The Google Calendar connector ingests calendar event changes (created, updated, deleted) and synthesizes "event starting soon" notifications, keeping butlers current with the user's schedule in near real-time. It runs as a standalone process, polls Google Calendar via incremental sync (`events.list` with `syncToken`), normalizes events into `ingest.v1` envelopes, and submits them to the Switchboard. The connector supports multi-account operation via `public.google_accounts` (same pattern as the Gmail connector) and reuses existing Google OAuth infrastructure.

## ADDED Requirements

### Requirement: Google Calendar Connector Identity and Authentication
The Google Calendar connector runs as a single process that discovers and manages all connected Google accounts with calendar scope. It authenticates each account independently via Google OAuth, resolving per-account credentials from the butler database.

#### Scenario: Multi-account discovery at startup
- **WHEN** the Google Calendar connector starts
- **THEN** it SHALL query `public.google_accounts` for all rows with `status = 'active'` and `calendar` in `granted_scopes`
- **AND** for each qualifying account, it SHALL resolve credentials (`client_id`, `client_secret` from `butler_secrets`; `refresh_token` from the account's companion entity in `entity_info`)
- **AND** it SHALL spawn an independent poll loop per account
- **AND** startup SHALL succeed even if some accounts fail credential resolution (degraded mode — failed accounts are logged and skipped)

#### Scenario: OAuth bootstrap requirement
- **WHEN** deploying the Google Calendar connector
- **THEN** the dashboard OAuth bootstrap flow MUST be completed first for at least one Google account with Calendar scope
- **AND** the connector has no env-var-based OAuth credential fallback — DB-only

#### Scenario: Per-account connector identity
- **WHEN** a poll loop runs for account `work@gmail.com`
- **THEN** `source.channel = "google_calendar"`, `source.provider = "google_calendar"`, and `source.endpoint_identity = "google_calendar:user:work@gmail.com"`
- **AND** the endpoint identity is auto-resolved per-account from the authenticated email, not from an env var

#### Scenario: Per-account scope validation
- **WHEN** the connector evaluates a Google account for loop creation
- **THEN** it SHALL verify that the account's `granted_scopes` include `calendar`
- **AND** accounts missing required scopes SHALL be skipped with a warning log (not fatal to the process)

#### Scenario: No qualifying accounts
- **WHEN** the connector starts and no active Google accounts have calendar scope
- **THEN** the connector SHALL start in idle mode (health = `degraded`, no active loops)
- **AND** it SHALL periodically re-scan for new accounts (see dynamic account discovery)

### Requirement: Incremental Sync via syncToken
The connector uses Google Calendar API's incremental sync mechanism to detect changes efficiently.

#### Scenario: Initial full sync
- **WHEN** the connector starts for an account with no persisted cursor
- **THEN** it SHALL perform a full `events.list` call (no syncToken) to establish the baseline
- **AND** it SHALL persist the returned `nextSyncToken` as the cursor via `cursor_store`
- **AND** it SHALL NOT ingest events from the initial full sync (baseline establishment only — avoids flooding Switchboard with historical events)

#### Scenario: Incremental sync poll cycle
- **WHEN** the connector polls for an account with a persisted syncToken
- **THEN** it SHALL call `events.list(syncToken=<token>)` to fetch only changed events
- **AND** for each changed event, it SHALL normalize to an `ingest.v1` envelope and submit to Switchboard
- **AND** after all changed events are processed and accepted, it SHALL persist the new `nextSyncToken` via `cursor_store`

#### Scenario: Expired syncToken handling
- **WHEN** Google returns HTTP 410 (Gone) for a syncToken
- **THEN** the connector SHALL discard the invalid token, perform a full sync to re-establish a baseline token, and resume incremental sync
- **AND** events from the recovery full sync SHALL be ingested (they represent the current state delta since the last valid checkpoint)

#### Scenario: Pagination of sync results
- **WHEN** an incremental sync returns a `nextPageToken` (large change set)
- **THEN** the connector SHALL paginate through all pages before advancing the cursor
- **AND** the cursor SHALL only advance after the final page is fully processed

### Requirement: Event Change Classification
The connector classifies each calendar change into an event type for the ingest envelope.

#### Scenario: Event created
- **WHEN** an event appears in the sync response that was not previously known
- **THEN** the event type SHALL be `event_created`

#### Scenario: Event updated
- **WHEN** an event appears in the sync response with status not `cancelled` and was previously known
- **THEN** the event type SHALL be `event_updated`

#### Scenario: Event deleted (cancelled)
- **WHEN** an event appears in the sync response with `status = "cancelled"`
- **THEN** the event type SHALL be `event_deleted`

#### Scenario: Event type determination without local state
- **WHEN** the connector cannot determine whether an event is new or updated (no local event cache)
- **THEN** it SHALL default to `event_updated` for non-cancelled events
- **AND** the Switchboard's deduplication layer handles any resulting duplicates

### Requirement: Event Starting Soon Notifications
The connector synthesizes time-triggered notifications for upcoming events.

#### Scenario: Lead time configuration
- **WHEN** the connector is configured
- **THEN** the lead time for "starting soon" notifications SHALL be configurable via `GCAL_STARTING_SOON_LEAD_MINUTES` (default 15 minutes)
- **AND** setting the lead time to 0 SHALL disable starting-soon notifications

#### Scenario: Starting soon detection
- **WHEN** the connector completes a sync cycle for an account
- **THEN** it SHALL scan upcoming events within the lead-time window
- **AND** for each event entering the window for the first time, it SHALL emit an `event_starting_soon` ingest envelope

#### Scenario: Deduplication of starting soon notifications
- **WHEN** the connector considers emitting a starting-soon notification
- **THEN** it SHALL check an in-memory seen-set keyed by `(event_id, lead_time_minutes)`
- **AND** events already in the seen-set SHALL NOT trigger duplicate notifications
- **AND** the seen-set SHALL be pruned of past events periodically to prevent unbounded growth

#### Scenario: Missed notifications on restart
- **WHEN** the connector restarts
- **THEN** it SHALL check upcoming events within the lead-time window and emit starting-soon notifications for events that have not yet started
- **AND** the Switchboard's deduplication layer provides additional protection against duplicates

### Requirement: ingest.v1 Field Mapping

#### Scenario: Google Calendar event field mapping
- **WHEN** a Google Calendar event change is normalized to `ingest.v1`
- **THEN** the mapping SHALL be:
  - `source.channel` = `"google_calendar"`
  - `source.provider` = `"google_calendar"`
  - `source.endpoint_identity` = `"google_calendar:user:<email_address>"`
  - `event.external_event_id` = Google Calendar event ID
  - `event.external_thread_id` = Google Calendar event ID (events are their own thread)
  - `event.observed_at` = connector-observed timestamp (RFC3339)
  - `sender.identity` = event organizer email address (or the account email for self-created events)
  - `payload.raw` = full Google Calendar API event payload
  - `payload.normalized_text` = structured summary (see normalized text format)
  - `control.idempotency_key` = `"gcal:<endpoint_identity>:<event_id>:<updated_timestamp>"`
  - `control.ingestion_tier` = `"full"`
  - `control.policy_tier` = `"default"`

#### Scenario: Starting soon event field mapping
- **WHEN** an "event starting soon" notification is normalized to `ingest.v1`
- **THEN** the mapping SHALL follow the standard mapping with these overrides:
  - `event.external_event_id` = `"starting_soon:<event_id>"`
  - `control.idempotency_key` = `"gcal:<endpoint_identity>:starting_soon:<event_id>:<lead_minutes>"`
  - `control.policy_tier` = `"interactive"` (time-sensitive notification)

#### Scenario: Normalized text format
- **WHEN** `payload.normalized_text` is constructed
- **THEN** it SHALL contain a human-readable summary including: event type (`created`, `updated`, `deleted`, `starting_soon`), event title, start time, end time, location (if present), attendee count, and organizer
- **AND** the format SHALL be: `"[Calendar: <event_type>] <title> | <start> - <end> | <location> | <attendee_count> attendees | Organizer: <organizer>"`

### Requirement: SyncToken Cursor Persistence
The connector tracks its position in Google Calendar's change stream via a persistent cursor.

#### Scenario: Cursor model
- **WHEN** the Google Calendar connector processes events
- **THEN** it persists a cursor containing `sync_token` (Google's opaque sync token) and `last_updated_at` (ISO 8601 timestamp) to the DB via `cursor_store`
- **AND** the cursor key SHALL be `"google_calendar:user:<email>"`

#### Scenario: Checkpoint-after-acceptance
- **WHEN** events are ingested
- **THEN** the cursor advances only after successful ingest acceptance from Switchboard
- **AND** on restart, it replays from the last safe sync token (harmless due to dedup)

### Requirement: Source Filter Integration (Google Calendar)
The Google Calendar connector implements the ingestion policy gate using `IngestionPolicyEvaluator`.

#### Scenario: IngestionPolicyEvaluator instantiation
- **WHEN** the Google Calendar connector initializes
- **THEN** it creates an `IngestionPolicyEvaluator` with `scope = 'connector:google_calendar:<endpoint_identity>'` and the shared switchboard DB pool

#### Scenario: Filter gate position in pipeline
- **WHEN** the Google Calendar connector processes an incoming event change
- **THEN** it evaluates the event via `IngestionPolicyEvaluator` AFTER normalization and BEFORE Switchboard submission

#### Scenario: Envelope construction from calendar event
- **WHEN** the Google Calendar connector builds an `IngestionEnvelope`
- **THEN** `sender_address` is the event organizer email (lowercase), `source_channel = "google_calendar"`, and `raw_key` is the event organizer email

### Requirement: Multi-Account Connector Architecture
A single Google Calendar connector process manages concurrent poll loops for all connected Google accounts.

#### Scenario: Independent per-account loops
- **WHEN** the connector manages accounts `personal@gmail.com` and `work@gmail.com`
- **THEN** each account SHALL have its own:
  - Credential set (independent refresh token and access token cache)
  - Sync token cursor (persisted independently, keyed by endpoint identity)
  - Starting-soon seen-set (independent per account)
  - Poll interval (from account metadata or process-level defaults)
- **AND** the loops SHALL run as concurrent asyncio tasks within the single process

#### Scenario: Per-account error isolation
- **WHEN** account `work@gmail.com` encounters a token refresh failure or API error
- **THEN** only that account's loop SHALL enter backoff/retry
- **AND** account `personal@gmail.com` SHALL continue processing unaffected

#### Scenario: Per-account configuration via metadata
- **WHEN** a `google_accounts` row has `metadata.calendar` containing override fields
- **THEN** the account's loop SHALL use those overrides instead of process-level defaults
- **AND** supported override fields are: `poll_interval_s`, `starting_soon_lead_minutes`, `calendar_ids` (list of calendar IDs to watch, default: primary calendar only)

### Requirement: Dynamic Account Discovery
The connector SHALL support discovering new or removed accounts without a full process restart.

#### Scenario: Periodic re-scan
- **WHEN** the connector is running
- **THEN** it SHALL re-query `public.google_accounts` at a configurable interval (`GCAL_ACCOUNT_RESCAN_INTERVAL_S`, default 300)
- **AND** newly active accounts with calendar scope SHALL have loops spawned
- **AND** accounts that are no longer active SHALL have their loops gracefully stopped

#### Scenario: Graceful loop shutdown on account removal
- **WHEN** an account is removed during a re-scan
- **THEN** the account's loop SHALL complete any in-flight ingest operations
- **AND** the cursor SHALL be checkpointed
- **AND** the loop SHALL be stopped without affecting other account loops

### Requirement: Aggregated Health Status

#### Scenario: Health model (multi-account)
- **WHEN** the Google Calendar connector's health is queried
- **THEN** it returns: `status` (worst-case across all account loops), `uptime_seconds`, `active_accounts` (count), `account_health` (array of per-account status objects)
- **AND** each per-account status includes: `email`, `endpoint_identity`, `status` (`healthy`/`degraded`/`error`), `last_checkpoint_save_at`, `last_sync_at`, `source_api_connectivity`, `error` (if any)

### Requirement: Environment Variables

#### Scenario: Required variables
- **WHEN** the Google Calendar connector starts
- **THEN** `SWITCHBOARD_MCP_URL`, `CONNECTOR_PROVIDER=google_calendar`, `CONNECTOR_CHANNEL=google_calendar` MUST be set
- **AND** database connectivity (`DATABASE_URL` or `POSTGRES_HOST`/`POSTGRES_PORT`/`POSTGRES_USER`/`POSTGRES_PASSWORD`) MUST be configured for account discovery and credential resolution

#### Scenario: Optional variables
- **WHEN** the connector starts
- **THEN** `GCAL_POLL_INTERVAL_S` (default 60), `GCAL_STARTING_SOON_LEAD_MINUTES` (default 15), `GCAL_ACCOUNT_RESCAN_INTERVAL_S` (default 300), `CONNECTOR_MAX_INFLIGHT` (default 8), `CONNECTOR_HEALTH_PORT` (default 40084), `CONNECTOR_HEARTBEAT_INTERVAL_S` (default 120) are optionally configurable

### Requirement: Free/Busy Scope Coverage

The `calendar` OAuth scope the connector already requires for event access (`https://www.googleapis.com/auth/calendar`, validated as `calendar` in `granted_scopes`) also authorizes Google Calendar `/freeBusy` queries. The availability finder built on free/busy therefore SHALL require no additional OAuth scope or re-authorization.

#### Scenario: Existing calendar scope authorizes free/busy

- **WHEN** a Google account is connected with `calendar` in its `granted_scopes`
- **THEN** that grant is sufficient to query Google Calendar `/freeBusy` for the account's calendars
- **AND** no additional scope SHALL be requested solely to support free/busy availability queries

#### Scenario: No re-authorization prompt for availability

- **WHEN** the availability finder queries free/busy for an already-connected account
- **THEN** the user SHALL NOT be prompted to re-authorize, because the required scope was granted at connection time
