## MODIFIED Requirements

### Requirement: Environment Variables

Operational configuration SHALL be provided via environment variables extending the base connector variables. Static identity SHALL come from the canonical descriptor.

ID: REQ-connector-live-listener-001
Source: bu-dss7he.9 released S3; openspec/specs/connector-live-listener/spec.md requirement Environment Variables
Scope: v1-mandatory

#### Scenario: Required variables
- **WHEN** the live-listener connector starts
- **THEN** `SWITCHBOARD_MCP_URL` and `LIVE_LISTENER_DEVICES` (JSON device list) MUST be set; the canonical descriptor SHALL supply provider `live-listener`, channel `voice` and connector type `live_listener`, retaining stored-event type `live-listener`, independently of identity environment variables
- **AND** `LIVE_LISTENER_TRANSCRIPTION_URL` MUST be set. There is no default (the transcription service location is deployment-specific). An unset or empty value is a fatal startup error

#### Scenario: Optional audio variables
- **WHEN** the connector starts
- **THEN** `LIVE_LISTENER_VAD_ONSET_THRESHOLD` (default: 0.5), `LIVE_LISTENER_VAD_OFFSET_THRESHOLD` (default: 0.3), `LIVE_LISTENER_VAD_ONSET_FRAMES` (default: 3), `LIVE_LISTENER_VAD_OFFSET_FRAMES` (default: 10), `LIVE_LISTENER_MIN_SEGMENT_MS` (default: 300), `LIVE_LISTENER_MAX_SEGMENT_MS` (default: 30000), `LIVE_LISTENER_MIN_CONFIDENCE` (default: 0.3) are optionally configurable

#### Scenario: Optional transcription variables
- **WHEN** the connector starts
- **THEN** `LIVE_LISTENER_TRANSCRIPTION_PROTOCOL` (default: `"wyoming"`), `LIVE_LISTENER_LANGUAGE` (default: `"en"`) are optionally configurable

#### Scenario: Discretion model selection
- **WHEN** the connector starts
- **THEN** the discretion model is resolved from the shared model catalog at the `discretion` complexity tier (managed via the Settings UI at `/butlers/settings`)

#### Scenario: Optional session variables
- **WHEN** the connector starts
- **THEN** `LIVE_LISTENER_SESSION_GAP_S` (default: 120) is optionally configurable
