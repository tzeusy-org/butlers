## MODIFIED Requirements

### Requirement: Environment Variables

Operational configuration SHALL be provided via environment variables extending the base connector variables. Static identity SHALL come from the canonical descriptor.

ID: REQ-connector-home-assistant-003
Source: bu-dss7he.9 released S3; openspec/specs/connector-home-assistant/spec.md requirement Environment Variables
Scope: v1-mandatory

#### Scenario: Required variables
- **WHEN** the Home Assistant connector starts
- **THEN** `SWITCHBOARD_MCP_URL` MUST be set; the canonical descriptor SHALL supply provider and primary channel `home_assistant`, retaining secondary channel `wellness`, independently of identity environment variables
- **AND** `HA_BASE_URL` and `HA_ACCESS_TOKEN` are resolved from CredentialStore by default, with environment variable overrides for development

#### Scenario: Optional variables
- **WHEN** the connector starts
- **THEN** the following SHALL be optionally configurable: `HA_DOMAIN_ALLOWLIST` (comma-separated additions; the effective allowlist always retains `light,switch,sensor,climate,lock,cover,binary_sensor,automation,script,person`), `HA_POLL_INTERVAL_S` (default: 60), `HA_CHECKPOINT_OVERLAP_S` (default: 30), `HA_WS_PING_INTERVAL_S` (default: 30), `HA_WS_PONG_TIMEOUT_S` (default: 10), `HA_DISCRETION_TIMEOUT_S` (default: 5), `HA_EVENT_QUEUE_MAX` (default: 100)
- **AND** the wellness-promotion knobs SHALL be optionally configurable: `HA_WELLNESS_PROMOTION_ENABLED` (default: `true`), `HA_WELLNESS_RULES_EXTRA` (JSON list of `{device_class?, unit?, entity_token?, metric}` rules appended to the default table; malformed JSON fails connector startup with a clear error), `HA_WELLNESS_ENTITY_DENYLIST` (comma-separated entity_ids never promoted)
