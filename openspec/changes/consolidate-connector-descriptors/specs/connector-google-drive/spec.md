## MODIFIED Requirements

### Requirement: Environment Variables

The Google Drive connector SHALL take operational configuration from environment
variables and static identity from its canonical descriptor. The variables identified below as required MUST be set for the
connector to run; the remainder are optional process-level defaults, which
per-account metadata MAY override.

ID: REQ-connector-google-drive-001
Source: bu-dss7he.9 released S3; openspec/specs/connector-google-drive/spec.md requirement Environment Variables
Scope: v1-mandatory

#### Scenario: Required variables
- **WHEN** the Google Drive connector starts
- **THEN** `SWITCHBOARD_MCP_URL` must be set; the canonical descriptor SHALL supply channel and provider `google_drive`, independently of identity environment variables
- **AND** `endpoint_identity` is auto-resolved per-account at startup from the authenticated email (not set via env var)
- **AND** database connectivity (`DATABASE_URL` or `POSTGRES_HOST`/`POSTGRES_PORT`/`POSTGRES_USER`/`POSTGRES_PASSWORD`) must be configured for account discovery and credential resolution

#### Scenario: Process-level default variables (optional)
- **WHEN** the connector starts
- **THEN** `GDRIVE_POLL_INTERVAL_S` (default 300), `GDRIVE_BATCH_WINDOW_S` (default 0, batch-digest mode disabled), `CONNECTOR_MAX_INFLIGHT` (default 8), `CONNECTOR_HEALTH_PORT` (default 40088, since 40085 belongs to the Google Calendar connector), `CONNECTOR_HEARTBEAT_INTERVAL_S` (default 120), `GDRIVE_ACCOUNT_RESCAN_INTERVAL_S` (default 300) are optionally configurable as process-level defaults
- **AND** per-account overrides in `google_accounts.metadata.google_drive` take precedence
