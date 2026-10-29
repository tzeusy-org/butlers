## MODIFIED Requirements

### Requirement: Environment Variables

The Google Calendar connector SHALL take operational configuration from environment
variables and static identity from its canonical descriptor. The variables identified below as required MUST be set for the
connector to run; the remainder are optional.

ID: REQ-connector-google-calendar-001
Source: bu-dss7he.9 released S3; openspec/specs/connector-google-calendar/spec.md requirement Environment Variables
Scope: v1-mandatory

#### Scenario: Required variables
- **WHEN** the Google Calendar connector starts
- **THEN** `SWITCHBOARD_MCP_URL` MUST be set. The canonical descriptor SHALL supply channel and provider `google_calendar`; deployment SHALL NOT need redundant identity environment variables
- **AND** database connectivity (`DATABASE_URL` or `POSTGRES_HOST`/`POSTGRES_PORT`/`POSTGRES_USER`/`POSTGRES_PASSWORD`) MUST be configured for account discovery and credential resolution

#### Scenario: Optional variables
- **WHEN** the connector starts
- **THEN** `GCAL_POLL_INTERVAL_S` (default 60), `GCAL_STARTING_SOON_LEAD_MINUTES` (default 15), `GCAL_ACCOUNT_RESCAN_INTERVAL_S` (default 300), `CONNECTOR_MAX_INFLIGHT` (default 8), `CONNECTOR_HEALTH_PORT` (default 40085), `CONNECTOR_HEARTBEAT_INTERVAL_S` (default 120) are optionally configurable
