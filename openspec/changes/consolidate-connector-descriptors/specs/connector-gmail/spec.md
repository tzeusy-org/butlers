## MODIFIED Requirements

### Requirement: Multiple Concurrent Connectors

Multiple Gmail connector processes SHALL be able to run concurrently for horizontal scaling or policy isolation.

ID: REQ-connector-gmail-002
Source: bu-dss7he.9 released S3; openspec/specs/connector-gmail/spec.md requirement Multiple Concurrent Connectors
Scope: v1-mandatory

#### Scenario: Per-account isolation across processes
- **WHEN** multiple Gmail connector processes run
- **THEN** each process discovers its own set of accounts from `public.google_accounts`
- **AND** if two processes discover the same account, they share the same endpoint identity and cursor — explicit coordination/lease ownership is required to avoid duplicate processing

#### Scenario: Uniqueness boundary
- **WHEN** deduplication is evaluated
- **THEN** the boundary is `(descriptor.provider, descriptor.channel, endpoint_identity, external_event_id)`

#### Scenario: Horizontal replicas
- **WHEN** multiple process instances share the same endpoint identity for the same account
- **THEN** explicit coordination/lease ownership for the cursor is required
- **AND** duplicate accepted ingest responses are treated as success

### Requirement: Environment Variables

The Gmail connector SHALL take operational configuration from environment variables and static identity from its canonical descriptor.
The variables identified below as required MUST be set for the connector to
run; the remainder are optional process-level defaults, which per-account
metadata MAY override.

ID: REQ-connector-gmail-003
Source: bu-dss7he.9 released S3; openspec/specs/connector-gmail/spec.md requirement Environment Variables
Scope: v1-mandatory

#### Scenario: Required variables
- **WHEN** the Gmail connector starts
- **THEN** `SWITCHBOARD_MCP_URL` must be set; the descriptor SHALL supply provider `gmail` and channel `email`, independently of identity environment variables
- **AND** `endpoint_identity` is auto-resolved per-account at startup from the authenticated email (not set via env var)
- **AND** database connectivity (`DATABASE_URL` or `POSTGRES_HOST`/`POSTGRES_PORT`/`POSTGRES_USER`/`POSTGRES_PASSWORD`) must be configured for account discovery and credential resolution

#### Scenario: Process-level default variables (optional)
- **WHEN** the connector starts
- **THEN** `GMAIL_POLL_INTERVAL_S` (default 60), `GMAIL_WATCH_RENEW_INTERVAL_S` (default 86400), `GMAIL_LABEL_INCLUDE`, `GMAIL_LABEL_EXCLUDE`, `GMAIL_PUBSUB_ENABLED` (default false), `GMAIL_PUBSUB_TOPIC`, `CONNECTOR_MAX_INFLIGHT` (default 8), `CONNECTOR_HEALTH_PORT` (default 40082), `GMAIL_ACCOUNT_RESCAN_INTERVAL_S` (default 300) are optionally configurable as process-level defaults
- **AND** per-account overrides in `google_accounts.metadata.gmail` take precedence

#### Scenario: Backfill variables
- **WHEN** backfill is configured
- **THEN** `CONNECTOR_BACKFILL_ENABLED` (default true), `CONNECTOR_BACKFILL_POLL_INTERVAL_S` (default 60), `CONNECTOR_BACKFILL_PROGRESS_INTERVAL` (default 50) are optionally configurable
