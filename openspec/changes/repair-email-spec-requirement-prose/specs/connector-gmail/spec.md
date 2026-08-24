## MODIFIED Requirements

### Requirement: ingest.v1 Field Mapping

The Gmail connector SHALL normalize every ingested Gmail message to the
`ingest.v1` envelope using exactly the field mapping defined below.

#### Scenario: Gmail field mapping
- **WHEN** a Gmail email is normalized to `ingest.v1`
- **THEN** the mapping is:
  - `source.channel` = `"email"`
  - `source.provider` = `"gmail"` (must be `gmail`, not `imap`)
  - `source.endpoint_identity` = `"gmail:user:<email_address>"`
  - `event.external_event_id` = the RFC822 `Message-ID` header value (falls back to the Gmail message ID when the header is absent)
  - `event.external_thread_id` = Gmail `threadId`
  - `event.observed_at` = connector-observed timestamp (RFC3339)
  - `sender.identity` = normalized sender address from `From` header
  - `sender.display_name` = the raw display-name part of the `From` header (e.g. `"John Doe"` from `"John Doe <john@example.com>"`), or `null` when the header carried no display name; stored verbatim (not normalized) so identity enrichment can use the real name instead of guessing one from the address local-part
  - `payload.raw` = full Gmail API message payload (Tier 1) or `null` (Tier 2)
  - `payload.normalized_text` = normalized subject + body text (Tier 1) or subject only (Tier 2)
  - `control.idempotency_key` = `"gmail:<endpoint_identity>:<message_id>"`

### Requirement: Aggregated Health Status

The Gmail connector SHALL expose a single aggregated health status covering
every account loop, reporting the worst-case status across accounts together
with per-account detail.

#### Scenario: Health model (multi-account)
- **WHEN** the Gmail connector's health is queried
- **THEN** it returns: `status` (worst-case across all account loops), `uptime_seconds`, `active_accounts` (count), `account_health` (array of per-account status objects)
- **AND** each per-account status includes: `email`, `endpoint_identity`, `status` (`healthy`/`degraded`/`error`), `last_checkpoint_save_at`, `last_ingest_submit_at`, `source_api_connectivity`, `error` (if any)

### Requirement: Environment Variables

The Gmail connector SHALL take its configuration from environment variables.
The variables identified below as required MUST be set for the connector to
run; the remainder are optional process-level defaults, which per-account
metadata MAY override.

#### Scenario: Required variables
- **WHEN** the Gmail connector starts
- **THEN** `SWITCHBOARD_MCP_URL`, `CONNECTOR_PROVIDER=gmail`, `CONNECTOR_CHANNEL=email` must be set
- **AND** `endpoint_identity` is auto-resolved per-account at startup from the authenticated email (not set via env var)
- **AND** database connectivity (`DATABASE_URL` or `POSTGRES_HOST`/`POSTGRES_PORT`/`POSTGRES_USER`/`POSTGRES_PASSWORD`) must be configured for account discovery and credential resolution

#### Scenario: Process-level default variables (optional)
- **WHEN** the connector starts
- **THEN** `GMAIL_POLL_INTERVAL_S` (default 60), `GMAIL_WATCH_RENEW_INTERVAL_S` (default 86400), `GMAIL_LABEL_INCLUDE`, `GMAIL_LABEL_EXCLUDE`, `GMAIL_PUBSUB_ENABLED` (default false), `GMAIL_PUBSUB_TOPIC`, `CONNECTOR_MAX_INFLIGHT` (default 8), `CONNECTOR_HEALTH_PORT` (default 40082), `GMAIL_ACCOUNT_RESCAN_INTERVAL_S` (default 300) are optionally configurable as process-level defaults
- **AND** per-account overrides in `google_accounts.metadata.gmail` take precedence

#### Scenario: Backfill variables
- **WHEN** backfill is configured
- **THEN** `CONNECTOR_BACKFILL_ENABLED` (default true), `CONNECTOR_BACKFILL_POLL_INTERVAL_S` (default 60), `CONNECTOR_BACKFILL_PROGRESS_INTERVAL` (default 50) are optionally configurable
