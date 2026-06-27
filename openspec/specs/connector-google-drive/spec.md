# Google Drive Connector

## Purpose
The Google Drive connector ingests file metadata events from a user's Google Drive in near real-time via polling. It watches for file lifecycle events (created, modified, trashed, renamed, moved, shared) across all connected Google accounts, normalizing each into an `ingest.v1` metadata-tier envelope and submitting to the Switchboard. The connector does NOT download file contents — only metadata (filenames, modified times, folder structure, MIME types, sharing status). It follows the connector-base-spec contract: heartbeat, checkpoint persistence, filtered event batch flush, replay queue, source filter gate, and Prometheus metrics.

## Requirements

### Requirement: Google Drive Connector Identity and Authentication
The Google Drive connector runs as a single process that discovers and manages all connected Google accounts with Drive scopes. It authenticates each account independently via Google OAuth, resolving per-account credentials from the butler database. No new OAuth flow is needed — it piggybacks on the existing Google OAuth infrastructure.

#### Scenario: Multi-account discovery at startup
- **WHEN** the Google Drive connector starts
- **THEN** it SHALL query `public.google_accounts` for all rows with `status = 'active'` and `drive.readonly` or `drive` in `granted_scopes`
- **AND** for each qualifying account, it SHALL resolve credentials (`client_id`, `client_secret` from `butler_secrets`; `refresh_token` from the account's companion entity in `entity_info`)
- **AND** it SHALL spawn an independent poll loop per account
- **AND** startup SHALL succeed even if some accounts fail credential resolution (degraded mode — failed accounts are logged and skipped)

#### Scenario: Required OAuth scopes for the connector
- **WHEN** the connector evaluates a Google account for poll loop creation
- **THEN** it SHALL verify that the account's `granted_scopes` include `drive.readonly` (minimum) or `drive` (full access)
- **AND** accounts missing both scopes SHALL be skipped with a warning log (not fatal to the process)
- **AND** the `drive.readonly` scope is sufficient for the connector since it only reads metadata, never writes

#### Scenario: Scope addition via existing OAuth flow
- **WHEN** a Google account does not have Drive scopes and the user wants to enable Drive integration
- **THEN** the user SHALL re-authorize the account via the existing dashboard OAuth flow at `/api/oauth/google/start?account_hint=<email>&force_consent=true`
- **AND** the requested scopes SHALL include `drive.readonly` (or `drive` if module write access is also desired)
- **AND** no new OAuth endpoints are required — the existing `/api/oauth/google/start` and callback endpoints handle scope upgrades

#### Scenario: OAuth bootstrap requirement
- **WHEN** deploying the Google Drive connector
- **THEN** the dashboard OAuth bootstrap flow must be completed first for at least one Google account with Drive scopes
- **AND** the connector has no env-var-based OAuth credential fallback — DB-only

#### Scenario: Per-account connector identity
- **WHEN** a poll loop runs for account `user@gmail.com`
- **THEN** `source.channel="google_drive"`, `source.provider="google_drive"`, and `source.endpoint_identity = "google_drive:user:user@gmail.com"`
- **AND** the endpoint identity is auto-resolved per-account from the authenticated email, not from an env var

#### Scenario: No qualifying accounts
- **WHEN** the connector starts and no active Google accounts have Drive scopes
- **THEN** the connector SHALL start in idle mode (health = `degraded`, no active loops)
- **AND** it SHALL periodically re-scan for new accounts (see dynamic account discovery)

### Requirement: Polling via changes.list API
The connector polls Google Drive's `changes.list` endpoint with a persistent `pageToken` checkpoint. This is the only ingestion mode for v1 — no push notifications.

#### Scenario: Polling mode
- **WHEN** the connector runs a poll cycle for an account
- **THEN** it SHALL call `changes.list` with the persisted `pageToken`, `fields = "changes(fileId,file(id,name,mimeType,modifiedTime,trashed,parents,shared,sharingUser,owners),removed,type),newStartPageToken,nextPageToken"`, and `includeRemoved=true`
- **AND** for each change returned, it SHALL construct an `ingest.v1` metadata-tier envelope and submit to the Switchboard
- **AND** it SHALL poll at `GDRIVE_POLL_INTERVAL_S` (default 300 seconds) intervals

#### Scenario: Initial pageToken acquisition
- **WHEN** a poll loop starts for an account with no persisted checkpoint
- **THEN** it SHALL call `changes.getStartPageToken` to acquire the initial token
- **AND** this means the connector starts watching from "now" — no backfill of historical changes on first start

#### Scenario: Pagination within a poll cycle
- **WHEN** `changes.list` returns a `nextPageToken` (more changes available)
- **THEN** the connector SHALL continue fetching with the next page token until `newStartPageToken` is returned
- **AND** checkpoint SHALL be advanced only after all pages in the cycle are processed and submitted

### Requirement: PageToken Cursor Persistence
The connector tracks its position in Drive's change stream via a persistent cursor.

#### Scenario: Cursor model
- **WHEN** the Google Drive connector processes changes
- **THEN** it persists a `GDriveCursor` containing `page_token` (Drive's opaque page token) and `last_updated_at` (ISO 8601 timestamp) to the DB via `cursor_store`

#### Scenario: Checkpoint-after-acceptance
- **WHEN** changes are ingested
- **THEN** the cursor advances only after successful ingest acceptance from Switchboard
- **AND** on restart, it replays from the last safe page token (harmless due to dedup)

### Requirement: ingest.v1 Field Mapping

#### Scenario: Google Drive field mapping
- **WHEN** a Drive file change is normalized to `ingest.v1`
- **THEN** the mapping is:
  - `source.channel` = `"google_drive"`
  - `source.provider` = `"google_drive"`
  - `source.endpoint_identity` = `"google_drive:user:<email_address>"`
  - `event.external_event_id` = `"gdrive:<file_id>:<change_sequence>"` where `change_sequence` is a monotonic counter per poll cycle
  - `event.external_thread_id` = file ID (groups changes to the same file)
  - `event.observed_at` = connector-observed timestamp (RFC3339)
  - `sender.identity` = file owner's email address (from `file.owners[0].emailAddress`)
  - `payload.raw` = `null` (metadata tier only)
  - `payload.normalized_text` = structured metadata summary (see event normalization)
  - `control.ingestion_tier` = `"metadata"`
  - `control.idempotency_key` = `"gdrive:<endpoint_identity>:<file_id>:<modified_time_epoch>"`

### Requirement: Event Normalization
The connector normalizes Drive changes into human-readable metadata summaries by detecting the type of change via comparison with a local metadata cache.

#### Scenario: File created event
- **WHEN** a change references a file ID not in the local metadata cache
- **AND** the file is not trashed or removed
- **THEN** `payload.normalized_text` SHALL be `"file_created: <filename> (<mime_type>) in <parent_folder_name>"`

#### Scenario: File modified event
- **WHEN** a change references a file ID that exists in the local metadata cache
- **AND** the file's `modifiedTime` has changed but name and parent are unchanged
- **THEN** the connector SHALL increment the `file_modified` metric and update its cache, but SHALL NOT submit an `ingest.v1` envelope for pure content-modification events (suppressed as high-noise). The `"file_modified: <filename> (<mime_type>) at <modified_time_iso>"` normalized_text form is retained for the change classifier only.

#### Scenario: Shared-with-me change suppression
- **WHEN** a change references a file whose primary owner (`file.owners[0].emailAddress`) is not the connected account email
- **THEN** the connector SHALL skip the change (no ingest submission) and increment a `shared_with_me_skipped` counter label, so files merely shared with the owner do not flood ingestion

#### Scenario: File trashed event
- **WHEN** a change has `file.trashed = true` or `removed = true`
- **THEN** `payload.normalized_text` SHALL be `"file_trashed: <filename>"`

#### Scenario: File renamed event
- **WHEN** a change references a file whose `name` differs from the cached name
- **AND** the parent folder has not changed
- **THEN** `payload.normalized_text` SHALL be `"file_renamed: <old_name> -> <new_name>"`

#### Scenario: File moved event
- **WHEN** a change references a file whose `parents[0]` differs from the cached parent
- **THEN** `payload.normalized_text` SHALL be `"file_moved: <filename> from <old_parent_name> to <new_parent_name>"`

#### Scenario: Sharing changed event
- **WHEN** a change references a file whose `shared` status differs from the cached value
- **THEN** `payload.normalized_text` SHALL be `"sharing_changed: <filename> (shared=<true|false>)"`

#### Scenario: Fallback for undetectable change type
- **WHEN** a change cannot be classified into a specific event type
- **THEN** `payload.normalized_text` SHALL be `"file_changed: <filename> (<mime_type>)"`

#### Scenario: Local metadata cache update
- **WHEN** a change is successfully processed
- **THEN** the connector SHALL update its local metadata cache with the file's current state: `file_id`, `name`, `mime_type`, `parents`, `shared`, `modified_time`
- **AND** the cache SHALL be stored in the state store as a JSONB blob keyed by endpoint identity
- **AND** trashed/removed files SHALL be deleted from the cache

### Requirement: Source Filter Integration (Google Drive)
The Google Drive connector implements the ingestion policy gate using `IngestionPolicyEvaluator` with `scope = 'connector:google_drive:<endpoint_identity>'`.

#### Scenario: IngestionPolicyEvaluator instantiation
- **WHEN** the Google Drive connector initializes a poll loop for an account
- **THEN** it creates an `IngestionPolicyEvaluator` with `scope = 'connector:google_drive:<endpoint_identity>'` and the shared DB pool

#### Scenario: Filter gate position in pipeline
- **WHEN** the connector processes a Drive change
- **THEN** it evaluates the change via `IngestionPolicyEvaluator` AFTER normalization and BEFORE Switchboard submission

#### Scenario: Envelope construction for filter evaluation
- **WHEN** the connector builds an `IngestionEnvelope` for filter evaluation
- **THEN** `sender_address` is the file owner's email, `source_channel = "google_drive"`, and `raw_key` is the filename

### Requirement: Multi-Account Connector Architecture
A single Google Drive connector process manages concurrent poll loops for all connected Google accounts.

#### Scenario: Independent per-account loops
- **WHEN** the connector manages accounts `personal@gmail.com` and `work@gmail.com`
- **THEN** each account SHALL have its own:
  - Credential set (independent refresh token and access token cache)
  - PageToken cursor (persisted independently, keyed by endpoint identity)
  - File metadata cache (independent, keyed by endpoint identity)
  - Source filter evaluator (scoped to its endpoint identity)
- **AND** the loops SHALL run as concurrent asyncio tasks within the single process

#### Scenario: Per-account error isolation
- **WHEN** account `work@gmail.com` encounters a token refresh failure or API error
- **THEN** only that account's loop SHALL enter backoff/retry
- **AND** account `personal@gmail.com` SHALL continue processing unaffected

#### Scenario: Per-account configuration via metadata
- **WHEN** a `google_accounts` row has `metadata.google_drive` containing override fields
- **THEN** the account's loop SHALL use those overrides instead of process-level defaults
- **AND** supported override fields are: `poll_interval_s`
- **AND** fields not present in metadata fall back to process-level env var defaults

### Requirement: Dynamic Account Discovery
The connector SHALL support discovering new or removed accounts without a full process restart.

#### Scenario: Periodic re-scan
- **WHEN** the connector is running
- **THEN** it SHALL re-query `public.google_accounts` at a configurable interval (`GDRIVE_ACCOUNT_RESCAN_INTERVAL_S`, default 300)
- **AND** newly active accounts with Drive scopes SHALL have loops spawned
- **AND** accounts that are no longer active (revoked, deleted) SHALL have their loops gracefully stopped

#### Scenario: MCP-triggered reload
- **WHEN** a `connector_reload_accounts` MCP tool call is received (or SIGHUP signal)
- **THEN** an immediate re-scan SHALL be triggered outside the periodic schedule

#### Scenario: Graceful loop shutdown on account removal
- **WHEN** an account is removed during a re-scan
- **THEN** the account's loop SHALL complete any in-flight ingest operations
- **AND** the cursor SHALL be checkpointed
- **AND** the loop SHALL be stopped without affecting other account loops

### Requirement: Aggregated Health Status

#### Scenario: Health model (multi-account)
- **WHEN** the Google Drive connector's health is queried
- **THEN** it returns: `status` (worst-case across all account loops), `uptime_seconds`, `active_accounts` (count), `account_health` (array of per-account status objects)
- **AND** each per-account status includes: `email`, `endpoint_identity`, `status` (`healthy`/`degraded`/`error`), `last_checkpoint_save_at`, `last_ingest_submit_at`, `source_api_connectivity`, `error` (if any)

### Requirement: Environment Variables

#### Scenario: Required variables
- **WHEN** the Google Drive connector starts
- **THEN** `SWITCHBOARD_MCP_URL`, `CONNECTOR_PROVIDER=google_drive`, `CONNECTOR_CHANNEL=google_drive` must be set
- **AND** `endpoint_identity` is auto-resolved per-account at startup from the authenticated email (not set via env var)
- **AND** database connectivity (`DATABASE_URL` or `POSTGRES_HOST`/`POSTGRES_PORT`/`POSTGRES_USER`/`POSTGRES_PASSWORD`) must be configured for account discovery and credential resolution

#### Scenario: Process-level default variables (optional)
- **WHEN** the connector starts
- **THEN** `GDRIVE_POLL_INTERVAL_S` (default 300), `GDRIVE_BATCH_WINDOW_S` (default 0, batch-digest mode disabled), `CONNECTOR_MAX_INFLIGHT` (default 8), `CONNECTOR_HEALTH_PORT` (default 40088, since 40085 belongs to the Google Calendar connector), `CONNECTOR_HEARTBEAT_INTERVAL_S` (default 120), `GDRIVE_ACCOUNT_RESCAN_INTERVAL_S` (default 300) are optionally configurable as process-level defaults
- **AND** per-account overrides in `google_accounts.metadata.google_drive` take precedence

### Requirement: Google Drive Connector Prometheus Metrics
The connector exports standardized Prometheus metrics via `ConnectorMetrics` plus Drive-specific counters.

#### Scenario: Standard connector metrics
- **WHEN** the connector operates
- **THEN** it exports all standard `ConnectorMetrics` counters and histograms as defined in connector-base-spec (ingest submissions, source API calls, checkpoint saves, errors, ingest latency)

#### Scenario: Drive-specific event type metrics
- **WHEN** the connector processes a change event
- **THEN** `connector_gdrive_event_type_total` (Counter, labels: `endpoint_identity`, `event_type`) SHALL be incremented
- **AND** valid `event_type` values are: `file_created`, `file_modified`, `file_trashed`, `file_renamed`, `file_moved`, `sharing_changed`, `file_changed`

#### Scenario: Metadata cache size metric
- **WHEN** the metadata cache is updated
- **THEN** `connector_gdrive_metadata_cache_size` (Gauge, labels: `endpoint_identity`) SHALL reflect the current number of cached file entries

### Requirement: Rate Limiting
The connector respects Google Drive API rate limits.

#### Scenario: Source API rate limit handling
- **WHEN** the Drive API returns HTTP 403 (rate limit) or 429
- **THEN** the connector SHALL honor `Retry-After` when present, use exponential backoff with jitter (base 1s, max 60s), and cap retries at 5 per request

#### Scenario: User quota awareness
- **WHEN** the connector polls Drive APIs
- **THEN** it SHALL respect the Drive API default quota of 20,000 requests per 100 seconds per user
- **AND** the conservative default poll interval (300s) is designed to stay well within quota limits
