## MODIFIED Requirements

### Requirement: Google Calendar Connector Identity and Authentication

The implementation SHALL provide the behavior described by this requirement.
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

The implementation SHALL provide the behavior described by this requirement.
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

The implementation SHALL provide the behavior described by this requirement.
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

The implementation SHALL provide the behavior described by this requirement.
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

### Requirement: SyncToken Cursor Persistence

The implementation SHALL provide the behavior described by this requirement.
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

The implementation SHALL provide the behavior described by this requirement.
The Google Calendar connector implements the ingestion policy gate using `IngestionPolicyEvaluator`.

#### Scenario: IngestionPolicyEvaluator instantiation
- **WHEN** the Google Calendar connector initializes
- **THEN** it creates an `IngestionPolicyEvaluator` with `scope = 'connector:google_calendar:<endpoint_identity>'` and the shared switchboard DB pool

#### Scenario: Filter gate position in pipeline
- **WHEN** the Google Calendar connector processes an incoming event change
- **THEN** it evaluates the event via `IngestionPolicyEvaluator` AFTER normalization and BEFORE Switchboard submission

#### Scenario: Envelope construction from calendar event
- **WHEN** the Google Calendar connector builds an `IngestionEnvelope`
- **THEN** `sender_address` is the event organizer email, `source_channel = "google_calendar"`, and `raw_key` is the Google Calendar event ID

### Requirement: Multi-Account Connector Architecture

The implementation SHALL provide the behavior described by this requirement.
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
