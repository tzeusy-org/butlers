## MODIFIED Requirements

### Requirement: Gmail Connector Identity and Authentication
The Gmail connector runs as a single process that discovers and manages all connected Google accounts. It authenticates each account independently via Google OAuth, resolving per-account credentials from the butler database.

#### Scenario: Multi-account discovery at startup
- **WHEN** the Gmail connector starts
- **THEN** it SHALL query `shared.google_accounts` for all rows with `status = 'active'` and `gmail.modify` or `gmail.readonly` in `granted_scopes`
- **AND** for each qualifying account, it SHALL resolve credentials (`client_id`, `client_secret` from `butler_secrets`; `refresh_token` from the account's companion entity in `entity_info`)
- **AND** it SHALL spawn an independent watch/poll loop per account
- **AND** startup SHALL succeed even if some accounts fail credential resolution (degraded mode — failed accounts are logged and skipped)

#### Scenario: OAuth bootstrap requirement
- **WHEN** deploying the Gmail connector
- **THEN** the dashboard OAuth bootstrap flow must be completed first for at least one Google account with Gmail scopes
- **AND** the connector has no env-var-based OAuth credential fallback — DB-only

#### Scenario: Per-account connector identity
- **WHEN** a watch/poll loop runs for account `work@gmail.com`
- **THEN** `source.channel="email"`, `source.provider="gmail"`, and `source.endpoint_identity = "gmail:user:work@gmail.com"`
- **AND** the endpoint identity is derived from the account email, not from a process-level env var

#### Scenario: Per-account scope validation
- **WHEN** the connector evaluates a Google account for loop creation
- **THEN** it SHALL verify that the account's `granted_scopes` include `gmail.modify` (or `gmail.readonly` at minimum)
- **AND** accounts missing required scopes SHALL be skipped with a warning log (not fatal to the process)

#### Scenario: No qualifying accounts
- **WHEN** the connector starts and no active Google accounts have Gmail scopes
- **THEN** the connector SHALL start in idle mode (health = `degraded`, no active loops)
- **AND** it SHALL periodically re-scan for new accounts (see dynamic account discovery)

### Requirement: Multi-Account Connector Architecture
A single Gmail connector process manages concurrent watch/poll loops for all connected Google accounts.

#### Scenario: Independent per-account loops
- **WHEN** the connector manages accounts `personal@gmail.com` and `work@gmail.com`
- **THEN** each account SHALL have its own:
  - Credential set (independent refresh token and access token cache)
  - History cursor (persisted independently, keyed by endpoint identity)
  - Label filter configuration (from account metadata or process-level defaults)
  - Watch subscription (if Pub/Sub enabled for that account)
  - Backfill state (independent backfill jobs per account)
- **AND** the loops SHALL run as concurrent asyncio tasks within the single process

#### Scenario: Per-account error isolation
- **WHEN** account `work@gmail.com` encounters a token refresh failure or API error
- **THEN** only that account's loop SHALL enter backoff/retry
- **AND** account `personal@gmail.com` SHALL continue processing unaffected
- **AND** the failed account's error SHALL be recorded in per-account health status

#### Scenario: Per-account configuration via metadata
- **WHEN** a `google_accounts` row has `metadata.gmail` containing override fields
- **THEN** the account's loop SHALL use those overrides instead of process-level defaults
- **AND** supported override fields are: `label_include`, `label_exclude`, `poll_interval_s`, `pubsub_enabled`, `pubsub_topic`
- **AND** fields not present in metadata fall back to process-level env var defaults

#### Scenario: Process-level defaults
- **WHEN** an account's `metadata.gmail` does not specify a config field
- **THEN** the process-level env vars SHALL apply: `GMAIL_POLL_INTERVAL_S`, `GMAIL_LABEL_INCLUDE`, `GMAIL_LABEL_EXCLUDE`, `GMAIL_PUBSUB_ENABLED`, etc.

### Requirement: Dynamic Account Discovery
The connector SHALL support discovering new or removed accounts without a full process restart.

#### Scenario: Periodic re-scan
- **WHEN** the connector is running
- **THEN** it SHALL re-query `shared.google_accounts` at a configurable interval (`GMAIL_ACCOUNT_RESCAN_INTERVAL_S`, default 300)
- **AND** newly active accounts with Gmail scopes SHALL have loops spawned
- **AND** accounts that are no longer active (revoked, deleted) SHALL have their loops gracefully stopped

#### Scenario: MCP-triggered reload
- **WHEN** a `connector_reload_accounts` MCP tool call is received (or SIGHUP signal)
- **THEN** an immediate re-scan SHALL be triggered outside the periodic schedule
- **AND** the response SHALL report: accounts added, accounts removed, accounts unchanged

#### Scenario: Graceful loop shutdown on account removal
- **WHEN** an account is removed during a re-scan
- **THEN** the account's loop SHALL complete any in-flight ingest operations
- **AND** the cursor SHALL be checkpointed
- **AND** the loop SHALL be stopped without affecting other account loops

### Requirement: Multiple Concurrent Connectors
Multiple Gmail connector processes can still run concurrently for horizontal scaling or policy isolation.

#### Scenario: Per-account isolation across processes
- **WHEN** multiple Gmail connector processes run
- **THEN** each process discovers its own set of accounts from `shared.google_accounts`
- **AND** if two processes discover the same account, they share the same endpoint identity and cursor — explicit coordination/lease ownership is required to avoid duplicate processing

#### Scenario: Uniqueness boundary
- **WHEN** deduplication is evaluated
- **THEN** the boundary is `(CONNECTOR_PROVIDER, CONNECTOR_CHANNEL, CONNECTOR_ENDPOINT_IDENTITY, external_event_id)`

#### Scenario: Horizontal replicas
- **WHEN** multiple process instances share the same endpoint identity for the same account
- **THEN** explicit coordination/lease ownership for the cursor is required
- **AND** duplicate accepted ingest responses are treated as success

### Requirement: Aggregated Health Status

#### Scenario: Health model (multi-account)
- **WHEN** the Gmail connector's health is queried
- **THEN** it returns: `status` (worst-case across all account loops), `uptime_seconds`, `active_accounts` (count), `account_health` (array of per-account status objects)
- **AND** each per-account status includes: `email`, `endpoint_identity`, `status` (`healthy`/`degraded`/`error`), `last_checkpoint_save_at`, `last_ingest_submit_at`, `source_api_connectivity`, `error` (if any)

### Requirement: Environment Variables

#### Scenario: Required variables
- **WHEN** the Gmail connector starts
- **THEN** `SWITCHBOARD_MCP_URL`, `CONNECTOR_PROVIDER=gmail`, `CONNECTOR_CHANNEL=email` must be set
- **AND** `CONNECTOR_ENDPOINT_IDENTITY` is NOT required at the process level (derived per-account)
- **AND** database connectivity (`DATABASE_URL` or `POSTGRES_HOST`/`POSTGRES_PORT`/`POSTGRES_USER`/`POSTGRES_PASSWORD`) must be configured for account discovery and credential resolution

#### Scenario: Process-level default variables (optional)
- **WHEN** the connector starts
- **THEN** `GMAIL_POLL_INTERVAL_S` (default 60), `GMAIL_WATCH_RENEW_INTERVAL_S` (default 86400), `GMAIL_LABEL_INCLUDE`, `GMAIL_LABEL_EXCLUDE`, `GMAIL_PUBSUB_ENABLED` (default false), `GMAIL_PUBSUB_TOPIC`, `CONNECTOR_MAX_INFLIGHT` (default 8), `CONNECTOR_HEALTH_PORT` (default 40082), `GMAIL_ACCOUNT_RESCAN_INTERVAL_S` (default 300) are optionally configurable as process-level defaults
- **AND** per-account overrides in `google_accounts.metadata.gmail` take precedence

#### Scenario: Backfill variables
- **WHEN** backfill is configured
- **THEN** `CONNECTOR_BACKFILL_ENABLED` (default true), `CONNECTOR_BACKFILL_POLL_INTERVAL_S` (default 60), `CONNECTOR_BACKFILL_PROGRESS_INTERVAL` (default 50) are optionally configurable

## REMOVED Requirements

### Requirement: GMAIL_ACCOUNT environment variable
**Reason**: Replaced by DB-driven multi-account discovery from `shared.google_accounts`. The single-process multi-account architecture eliminates the need for per-account env var configuration.
**Migration**: Remove `GMAIL_ACCOUNT` from deployment configs. Accounts are managed exclusively through the dashboard OAuth flow and `shared.google_accounts` table.
