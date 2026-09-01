## MODIFIED Requirements

### Requirement: ingest.v1 Field Mapping

The Google Drive connector SHALL normalize every ingested Drive file change to
the `ingest.v1` envelope using exactly the field mapping defined below.

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

### Requirement: Aggregated Health Status

The Google Drive connector SHALL expose a single aggregated health status
covering every account loop, reporting the worst-case status across accounts
together with per-account detail.

#### Scenario: Health model (multi-account)
- **WHEN** the Google Drive connector's health is queried
- **THEN** it returns: `status` (worst-case across all account loops), `uptime_seconds`, `active_accounts` (count), `account_health` (array of per-account status objects)
- **AND** each per-account status includes: `email`, `endpoint_identity`, `status` (`healthy`/`degraded`/`error`), `last_checkpoint_save_at`, `last_ingest_submit_at`, `source_api_connectivity`, `error` (if any)

### Requirement: Environment Variables

The Google Drive connector SHALL take its configuration from environment
variables. The variables identified below as required MUST be set for the
connector to run; the remainder are optional process-level defaults, which
per-account metadata MAY override.

#### Scenario: Required variables
- **WHEN** the Google Drive connector starts
- **THEN** `SWITCHBOARD_MCP_URL`, `CONNECTOR_PROVIDER=google_drive`, `CONNECTOR_CHANNEL=google_drive` must be set
- **AND** `endpoint_identity` is auto-resolved per-account at startup from the authenticated email (not set via env var)
- **AND** database connectivity (`DATABASE_URL` or `POSTGRES_HOST`/`POSTGRES_PORT`/`POSTGRES_USER`/`POSTGRES_PASSWORD`) must be configured for account discovery and credential resolution

#### Scenario: Process-level default variables (optional)
- **WHEN** the connector starts
- **THEN** `GDRIVE_POLL_INTERVAL_S` (default 300), `GDRIVE_BATCH_WINDOW_S` (default 0, batch-digest mode disabled), `CONNECTOR_MAX_INFLIGHT` (default 8), `CONNECTOR_HEALTH_PORT` (default 40088, since 40085 belongs to the Google Calendar connector), `CONNECTOR_HEARTBEAT_INTERVAL_S` (default 120), `GDRIVE_ACCOUNT_RESCAN_INTERVAL_S` (default 300) are optionally configurable as process-level defaults
- **AND** per-account overrides in `google_accounts.metadata.google_drive` take precedence
