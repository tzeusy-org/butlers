## MODIFIED Requirements

### Requirement: ingest.v1 Field Mapping

The Google Calendar connector SHALL normalize every ingested calendar event
change to the `ingest.v1` envelope using exactly the field mappings defined
below.

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

### Requirement: Aggregated Health Status

The Google Calendar connector SHALL expose a single aggregated health status
covering every account loop, reporting the worst-case status across accounts
together with per-account detail.

#### Scenario: Health model (multi-account)
- **WHEN** the Google Calendar connector's health is queried
- **THEN** it returns: `status` (worst-case across all account loops), `uptime_seconds`, `active_accounts` (count), `account_health` (array of per-account status objects)
- **AND** each per-account status includes: `email`, `endpoint_identity`, `status` (`healthy`/`degraded`/`error`), `last_checkpoint_save_at`, `last_ingest_submit_at`, `source_api_connectivity`, `error` (if any)
- **AND** the aggregated status also includes a `timestamp` field (RFC3339)

### Requirement: Environment Variables

The Google Calendar connector SHALL take its configuration from environment
variables. The variables identified below as required MUST be set for the
connector to run; the remainder are optional.

#### Scenario: Required variables
- **WHEN** the Google Calendar connector starts
- **THEN** `SWITCHBOARD_MCP_URL` MUST be set. The channel and provider are fixed internally to `google_calendar` (the `CONNECTOR_PROVIDER` and `CONNECTOR_CHANNEL` env vars are set in deployment for consistency but are not read by the connector)
- **AND** database connectivity (`DATABASE_URL` or `POSTGRES_HOST`/`POSTGRES_PORT`/`POSTGRES_USER`/`POSTGRES_PASSWORD`) MUST be configured for account discovery and credential resolution

#### Scenario: Optional variables
- **WHEN** the connector starts
- **THEN** `GCAL_POLL_INTERVAL_S` (default 60), `GCAL_STARTING_SOON_LEAD_MINUTES` (default 15), `GCAL_ACCOUNT_RESCAN_INTERVAL_S` (default 300), `CONNECTOR_MAX_INFLIGHT` (default 8), `CONNECTOR_HEALTH_PORT` (default 40085), `CONNECTOR_HEARTBEAT_INTERVAL_S` (default 120) are optionally configurable
