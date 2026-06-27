# Home Assistant Connector

## Purpose
The Home Assistant connector is a standalone process that subscribes to a Home Assistant instance's WebSocket event stream, filters events through a three-layer pipeline (domain allowlist, significance thresholds, LLM-based discretion), normalizes significant events to `ingest.v1` envelopes, and submits them to the Switchboard. It provides the real-time ingestion pathway for home automation events into the butler ecosystem. REST API polling serves as a degraded fallback when the WebSocket connection is unavailable.

## Requirements

### Requirement: Connector Identity and Role
The Home Assistant connector bridges real-time HA events into the butler ecosystem as a device-state ingestion channel.

#### Scenario: Connector as home automation event interface
- **WHEN** the Home Assistant connector runs
- **THEN** it subscribes to the HA WebSocket event stream for real-time state change events and submits significant events to the Switchboard
- **AND** the connector owns the full pipeline from event subscription through discretion filtering; the Switchboard owns routing and classification

#### Scenario: Connector identity
- **WHEN** the Home Assistant connector starts
- **THEN** `source.channel = "home_assistant"`, `source.provider = "home_assistant"`, and `source.endpoint_identity` SHALL be `"home_assistant:<ha_host>:<ha_port>"` derived from the configured HA base URL

#### Scenario: Single process, single HA instance
- **WHEN** the connector is configured
- **THEN** it connects to exactly one HA instance as a single OS process
- **AND** it shares the MCP client, metrics registry, health server, and heartbeat task across the WebSocket and REST fallback paths

### Requirement: Authentication and Connection Configuration
The connector authenticates with Home Assistant using a long-lived access token configured via the Butlers dashboard.

#### Scenario: Dashboard settings UX
- **WHEN** the user navigates to the Butlers dashboard settings page at `/butlers/settings`
- **THEN** a dedicated "Home Assistant" settings section SHALL be displayed
- **AND** the section SHALL contain input fields for: HA instance URL (e.g., `http://homeassistant.local:8123`) and long-lived access token (generated in HA under Profile -> Long-Lived Access Tokens)
- **AND** the access token field SHALL be masked (password-type input) with a reveal toggle

#### Scenario: Connection validation before saving
- **WHEN** the user submits HA connection settings via the dashboard
- **THEN** the system SHALL validate the connection by making a test API call (`GET /api/` with the provided token as `Authorization: Bearer <token>`)
- **AND** if the test call succeeds (HTTP 200 with valid HA API response), the URL and token SHALL be stored in the CredentialStore (DB-first)
- **AND** if the test call fails (connection refused, timeout, HTTP 401/403, invalid response), the system SHALL display a specific error message and SHALL NOT save the credentials
- **AND** the error message SHALL distinguish between: unreachable host ("Cannot connect to <url> — verify the URL and that Home Assistant is running"), authentication failure ("Invalid access token — generate a new token in Home Assistant under Profile -> Long-Lived Access Tokens"), and unexpected errors ("Unexpected response from Home Assistant: <status_code>")

#### Scenario: Credential storage
- **WHEN** HA connection settings are validated and saved
- **THEN** the HA base URL SHALL be stored as owner `entity_info` key `home_assistant_url` (non-secured)
- **AND** the HA access token SHALL be stored as owner `entity_info` key `home_assistant_token` with `secured=true`

#### Scenario: Credential resolution at startup
- **WHEN** the connector process starts
- **THEN** it SHALL resolve `home_assistant_url` (non-secured) and `home_assistant_token` (`secured=true`) from the owner's `entity_info`
- **AND** if either credential is missing, the connector SHALL log an ERROR and exit with a non-zero status
- **AND** environment variables `HA_BASE_URL` and `HA_ACCESS_TOKEN` MAY override CredentialStore values for development/testing

#### Scenario: WebSocket URL derivation
- **WHEN** the connector resolves the HA base URL
- **THEN** it SHALL derive the WebSocket URL by replacing the `http://` or `https://` scheme with `ws://` or `wss://` respectively and appending `/api/websocket`
- **AND** for example, `http://homeassistant.local:8123` becomes `ws://homeassistant.local:8123/api/websocket`

### Requirement: WebSocket Event Subscription
The connector maintains a persistent WebSocket connection to Home Assistant for real-time event streaming.

#### Scenario: WebSocket authentication handshake
- **WHEN** the connector establishes a WebSocket connection
- **THEN** it SHALL wait for the HA `auth_required` message, send `{"type": "auth", "access_token": "<token>"}`, and wait for `auth_ok` or `auth_invalid`
- **AND** if `auth_invalid` is received, the connector SHALL log an ERROR, set health to `error`, and attempt reconnection with exponential backoff

#### Scenario: Event subscription
- **WHEN** the WebSocket authentication succeeds
- **THEN** the connector SHALL send `{"id": <N>, "type": "subscribe_events", "event_type": "state_changed"}` to subscribe to state change events
- **AND** it SHALL also subscribe to `automation_triggered` and `call_service` event types as separate subscriptions
- **AND** each subscription uses a unique incrementing message `id`

#### Scenario: Event reception
- **WHEN** the connector receives a WebSocket event message
- **THEN** the event SHALL be parsed as JSON and dispatched to the filtering pipeline
- **AND** the `event.data.entity_id`, `event.data.new_state`, `event.data.old_state`, and `event.time_fired` fields SHALL be extracted
- **AND** malformed messages SHALL be logged at WARNING level and skipped

#### Scenario: WebSocket reconnection
- **WHEN** the WebSocket connection drops (close frame, network error, ping timeout)
- **THEN** the connector SHALL attempt reconnection with exponential backoff (1s, 2s, 4s, ... capped at 60s)
- **AND** during reconnection, the connector SHALL transition to REST polling fallback if configured
- **AND** the connector's health state SHALL transition to `degraded` during reconnection
- **AND** on successful reconnection, event subscriptions SHALL be re-established

#### Scenario: WebSocket ping/pong keepalive
- **WHEN** the WebSocket connection is active
- **THEN** the connector SHALL send a `{"id": <N>, "type": "ping"}` message every 30 seconds
- **AND** if no `pong` response is received within 10 seconds, the connection SHALL be considered dead and reconnection initiated

### Requirement: REST API Polling Fallback
The connector falls back to REST API polling when the WebSocket connection is unavailable.

#### Scenario: Fallback activation
- **WHEN** the WebSocket connection has been down for more than 3 consecutive reconnection attempts
- **THEN** the connector SHALL activate REST polling as a fallback
- **AND** REST polling SHALL query `GET /api/states` at an interval of `HA_POLL_INTERVAL_S` (default 60 seconds)
- **AND** the connector SHALL continue WebSocket reconnection attempts in parallel

#### Scenario: REST polling state diffing
- **WHEN** the connector polls `GET /api/states`
- **THEN** it SHALL compare each entity's current state against the last known state from the previous poll (cached in memory)
- **AND** only entities with changed states SHALL be processed through the filtering pipeline
- **AND** the first poll after fallback activation SHALL treat all entities as "changed" (no previous state to compare)

#### Scenario: Fallback deactivation
- **WHEN** the WebSocket connection is re-established while REST polling is active
- **THEN** REST polling SHALL be stopped
- **AND** a brief overlap is acceptable (Switchboard dedup handles duplicate events)
- **AND** the connector's health state SHALL transition back to `healthy`

### Requirement: Three-Layer Filtering Pipeline
The connector implements a three-layer filtering pipeline to reduce HA event noise before Switchboard submission.

#### Scenario: Layer 1 — Domain allowlist
- **WHEN** an HA event is received
- **THEN** the connector SHALL check if the entity's domain (prefix of `entity_id` before the first `.`) is in the configured domain allowlist
- **AND** the default allowlist SHALL be: `light`, `switch`, `sensor`, `climate`, `lock`, `cover`, `binary_sensor`, `automation`, `script`
- **AND** events from domains not in the allowlist SHALL be dropped immediately and recorded as filtered with `filter_reason = "domain_excluded:<domain>"`
- **AND** the allowlist SHALL be configurable via `HA_DOMAIN_ALLOWLIST` (comma-separated) or `connector_registry.settings.domain_allowlist` (JSON array)

#### Scenario: Layer 2 — Significance filter
- **WHEN** an event passes the domain allowlist
- **THEN** for numeric sensor entities (device_class in `temperature`, `humidity`, `energy`, `power`, `illuminance`, `pressure`, `battery`, `voltage`, `current`, `carbon_dioxide`, `carbon_monoxide`, `pm25`, `pm10`, `volatile_organic_compounds`), the connector SHALL compare the numeric delta between `old_state.state` and `new_state.state` against a per-device-class threshold
- **AND** default thresholds SHALL be: temperature ±0.5, humidity ±2.0, energy ±0.1, power ±10.0, illuminance ±50.0, pressure ±1.0, battery ±5.0
- **AND** events with delta below the threshold SHALL be dropped with `filter_reason = "insignificant_delta:<device_class>:<delta>"`
- **AND** non-numeric entities (binary sensors, locks, lights, switches, automations, scripts) SHALL always pass this filter
- **AND** entities transitioning to/from `unavailable` or `unknown` state SHALL always pass (regardless of domain or threshold)
- **AND** thresholds SHALL be configurable via `connector_registry.settings.significance_thresholds` (JSON object mapping device_class to threshold)

#### Scenario: Layer 3 — Discretion evaluation
- **WHEN** an event passes domain and significance filters
- **THEN** the connector SHALL evaluate the event using the shared `DiscretionEvaluator` from `butlers.connectors.discretion`
- **AND** the evaluator SHALL receive a context window of recent events from the same entity domain (not just the same entity) to enable cross-entity pattern recognition
- **AND** all HA events SHALL use `weight=1.0` (owner-equivalent, no sender identity for device events)
- **AND** the discretion model SHALL be resolved from the shared model catalog at the `discretion` complexity tier

#### Scenario: Filter pipeline metrics
- **WHEN** events are processed through the pipeline
- **THEN** the connector SHALL increment `connector_ha_events_total{stage, outcome}` where stage is `domain_filter`, `significance_filter`, `discretion` and outcome is `passed` or `filtered`
- **AND** the connector SHALL record the overall pass rate in `connector_ha_filter_pass_rate` (gauge, 0.0 to 1.0)

### Requirement: ingest.v1 Field Mapping
Each event that passes all three filter layers is normalized to the canonical `ingest.v1` envelope.

#### Scenario: Field mapping for state_changed events
- **WHEN** a `state_changed` event is constructed as an `ingest.v1` envelope
- **THEN** the mapping SHALL be:
  - `source.channel` = `"home_assistant"`
  - `source.provider` = `"home_assistant"`
  - `source.endpoint_identity` = `"home_assistant:<ha_host>:<ha_port>"`
  - `event.external_event_id` = `"ha:<entity_id>:<time_fired_unix_ms>"`
  - `event.external_thread_id` = `"ha:entity:<entity_id>"` (groups events by entity)
  - `event.observed_at` = `time_fired` from the HA event (timezone-aware)
  - `sender.identity` = the HA entity ID (e.g., `"sensor.living_room_temperature"`)
  - `payload.raw` = `{"entity_id": str, "old_state": dict, "new_state": dict, "event_type": str, "domain": str, "device_class": str | null, "area": str | null, "friendly_name": str | null, "discretion_reason": str}`
  - `payload.normalized_text` = human-readable description (e.g., `"Living Room Temperature changed from 21.5°C to 22.0°C"`, `"Front Door Lock unlocked"`, `"Bedroom Light turned on"`)
  - `control.idempotency_key` = `"ha:<endpoint_identity>:<entity_id>:<time_fired_unix_ms>"`
  - `control.policy_tier` = `"default"`
  - `control.ingestion_tier` = `"full"`

#### Scenario: Field mapping for automation_triggered events
- **WHEN** an `automation_triggered` event is constructed as an `ingest.v1` envelope
- **THEN** `sender.identity` SHALL be the automation entity ID (e.g., `"automation.motion_light_hallway"`)
- **AND** `event.external_thread_id` SHALL be `"ha:automation:<automation_entity_id>"`
- **AND** `payload.normalized_text` SHALL describe the automation and its trigger (e.g., `"Automation 'Motion Light Hallway' triggered by motion sensor"`)

#### Scenario: Normalized text generation
- **WHEN** the connector generates `payload.normalized_text`
- **THEN** it SHALL use the entity's `friendly_name` attribute when available, falling back to the raw `entity_id`
- **AND** it SHALL include the old and new state values for `state_changed` events
- **AND** it SHALL include units of measurement from the entity's `unit_of_measurement` attribute when available

### Requirement: Checkpoint Semantics
The connector persists checkpoint state for crash-safe resumption.

#### Scenario: Checkpoint contents
- **WHEN** the connector saves a checkpoint
- **THEN** it SHALL write to `cursor_store`: `{"last_event_ts": "<time_fired ISO 8601>", "last_entity_id": str, "transport": "websocket" | "rest_fallback"}`
- **AND** the checkpoint SHALL be keyed by `(provider="home_assistant", endpoint_identity=<ha endpoint>)`

#### Scenario: Checkpoint timing
- **WHEN** an event is successfully submitted to the Switchboard (accepted or duplicate)
- **THEN** the checkpoint SHALL be updated with the event's `time_fired` timestamp

#### Scenario: Safe resume on restart
- **WHEN** the connector restarts
- **THEN** it SHALL load the checkpoint and subtract a safety margin of `HA_CHECKPOINT_OVERLAP_S` (default 30 seconds) from the `last_event_ts`
- **AND** events with `time_fired` at or before the adjusted checkpoint SHALL be skipped (Switchboard dedup provides additional safety)
- **AND** if no checkpoint exists (first run), the connector SHALL begin ingesting from the current time (no historical backfill)

### Requirement: Health State Derivation
The connector reports health based on HA connection and service availability.

#### Scenario: Health states
- **WHEN** the connector's health is queried
- **THEN** `error` when the HA instance is unreachable and REST fallback is also failing
- **AND** `degraded` when the WebSocket connection is down but REST polling is active, or when the discretion LLM is unreachable
- **AND** `healthy` when the WebSocket connection is active and all pipeline services are responsive

#### Scenario: Transport mode in heartbeat
- **WHEN** a heartbeat is assembled
- **THEN** `status.error_message` SHALL include the current transport mode (e.g., `"transport=websocket"` or `"transport=rest_fallback, ws_reconnect_attempts=5"`)

### Requirement: Prometheus Metrics
The connector exports HA-specific metrics in addition to the standard `ConnectorMetrics`.

#### Scenario: HA-specific counters
- **WHEN** the connector processes HA events
- **THEN** it SHALL export: `connector_ha_events_total{stage, outcome}` (Counter — events processed at each filter stage), `connector_ha_ws_reconnects_total` (Counter — WebSocket reconnection attempts), `connector_ha_rest_polls_total{status}` (Counter — REST fallback poll outcomes), `connector_ha_discretion_total{verdict}` (Counter — discretion verdicts)

#### Scenario: HA-specific gauges
- **WHEN** the connector is running
- **THEN** it SHALL export: `connector_ha_filter_pass_rate` (Gauge — ratio of events forwarded vs. total received), `connector_ha_transport_mode` (Gauge — 1 for websocket, 0 for rest_fallback), `connector_ha_entities_tracked` (Gauge — number of entities in the domain allowlist that have been seen)

#### Scenario: HA-specific histograms
- **WHEN** the connector processes events through the pipeline
- **THEN** it SHALL export: `connector_ha_event_latency_seconds` (Histogram — time from `time_fired` to Switchboard submission), `connector_ha_filter_pipeline_seconds` (Histogram — time spent in the three-layer filter pipeline)

### Requirement: Environment Variables
Configuration via environment variables extending the base connector variables.

#### Scenario: Required variables
- **WHEN** the Home Assistant connector starts
- **THEN** `SWITCHBOARD_MCP_URL`, `CONNECTOR_PROVIDER=home_assistant`, and `CONNECTOR_CHANNEL=home_assistant` MUST be set
- **AND** `HA_BASE_URL` and `HA_ACCESS_TOKEN` are resolved from CredentialStore by default, with environment variable overrides for development

#### Scenario: Optional variables
- **WHEN** the connector starts
- **THEN** the following SHALL be optionally configurable: `HA_DOMAIN_ALLOWLIST` (comma-separated, default: `light,switch,sensor,climate,lock,cover,binary_sensor,automation,script`), `HA_POLL_INTERVAL_S` (default: 60), `HA_CHECKPOINT_OVERLAP_S` (default: 30), `HA_WS_PING_INTERVAL_S` (default: 30), `HA_WS_PONG_TIMEOUT_S` (default: 10), `HA_DISCRETION_TIMEOUT_S` (default: 5), `HA_EVENT_QUEUE_MAX` (default: 100)
- **AND** the wellness-promotion knobs SHALL be optionally configurable: `HA_WELLNESS_PROMOTION_ENABLED` (default: `true`), `HA_WELLNESS_RULES_EXTRA` (JSON list of `{device_class?, unit?, entity_token?, metric}` rules appended to the default table; malformed JSON fails connector startup with a clear error), `HA_WELLNESS_ENTITY_DENYLIST` (comma-separated entity_ids never promoted)

### Requirement: Wellness channel promotion for health-shaped sensor events
The connector SHALL classify each `state_changed` event that survives the three-layer filter pipeline against a deterministic, metadata-driven wellness rule table (matching on `device_class`, `unit_of_measurement`, and optional entity-id tokens — never vendor or integration names). Events matching a rule SHALL be emitted on the `wellness` channel with `source.provider = "home_assistant"` IN ADDITION TO the unchanged `home_assistant` channel emission. Classification SHALL involve no LLM call.

#### Scenario: Blood-pressure reading promoted
- **WHEN** a `state_changed` event for an entity with `unit_of_measurement = "mmHg"` and entity_id containing the token `systolic` survives the filter pipeline
- **THEN** the connector SHALL emit the usual `home_assistant`-channel envelope
- **AND** SHALL additionally emit a `wellness`-channel envelope with `source.provider = "home_assistant"`, the same `external_event_id` (`ha:{entity_id}:{time_ms}`), and a `payload.raw.wellness_measurement` object containing `metric` (`blood_pressure_systolic`), numeric `value`, `unit`, `valid_at` (the event's `time_fired`), and `source_entity_id`
- **AND** `payload.normalized_text` SHALL be a human-readable rendering of the measurement

#### Scenario: Non-health sensor not promoted
- **WHEN** a `state_changed` event matches no wellness rule (e.g. a `device_class = "temperature"` room sensor, which is deliberately absent from the default rule table)
- **THEN** the connector SHALL emit only the `home_assistant`-channel envelope
- **AND** SHALL NOT emit on the `wellness` channel

#### Scenario: Non-numeric state not promoted
- **WHEN** an entity matches a wellness rule but its new state is non-numeric (`unknown`, `unavailable`, or unparseable)
- **THEN** the connector SHALL NOT emit a `wellness`-channel envelope for that event
- **AND** SHALL count the skip in the classifier metrics

#### Scenario: New health sensor requires no configuration
- **WHEN** a new HA entity appears whose metadata matches an existing wellness rule (e.g. a second blood-pressure cuff from a different vendor)
- **THEN** its readings SHALL be promoted with no connector configuration, code, or restart required beyond the entity existing in HA

### Requirement: Wellness classifier configuration
The classifier SHALL be configured via environment variables consistent with the connector's existing config surface: `HA_WELLNESS_PROMOTION_ENABLED` (default `true`), `HA_WELLNESS_RULES_EXTRA` (JSON list of `{device_class?, unit?, entity_token?, metric}` rules appended to the default table), and `HA_WELLNESS_ENTITY_DENYLIST` (comma-separated entity_ids never promoted). The default rule table SHALL cover blood pressure (systolic/diastolic via `mmHg` + token), weight (`weight` device_class or `kg`/`lb` with weight device_class), heart rate (`bpm`), blood glucose (`mg/dL`, `mmol/L`), and steps (`steps`); it SHALL NOT include ambient-ambiguous signatures (temperature, humidity, bare `%`).

#### Scenario: Promotion disabled
- **WHEN** `HA_WELLNESS_PROMOTION_ENABLED=false`
- **THEN** the connector SHALL emit no `wellness`-channel envelopes
- **AND** `home_assistant`-channel behavior SHALL be unchanged

#### Scenario: Owner extends the rule table
- **WHEN** `HA_WELLNESS_RULES_EXTRA` contains `[{"entity_token": "spo2", "unit": "%", "metric": "spo2"}]` and a matching entity reports a numeric state
- **THEN** the connector SHALL promote that reading with `metric = "spo2"`

#### Scenario: Denylisted entity never promoted
- **WHEN** an entity_id appears in `HA_WELLNESS_ENTITY_DENYLIST`
- **THEN** its events SHALL never be emitted on the `wellness` channel even if they match a rule

### Requirement: Promotion observability
The connector SHALL export Prometheus counters for classifier outcomes (promoted, skipped-non-numeric, denylisted) labeled by `metric`, and the existing per-envelope submission metrics SHALL distinguish the two channels.

#### Scenario: Promoted reading visible in metrics
- **WHEN** a reading is promoted to the `wellness` channel
- **THEN** the promotion counter for its `metric` label SHALL increment
- **AND** the envelope submission metrics SHALL attribute the submission to the `wellness` channel

### Requirement: Checkpoint unification across channels
The connector checkpoint SHALL remain keyed by `(provider, endpoint_identity)` only. One HA event SHALL advance the checkpoint exactly once, after all of its channel emissions have been submitted.

#### Scenario: Replay after dual emission
- **WHEN** the connector restarts and replays events already submitted on both channels
- **THEN** re-submissions SHALL be deduplicated by the Switchboard per-channel dedup key
- **AND** no duplicate ingestion events SHALL be recorded on either channel

### Requirement: Idempotency and Safety
The connector guarantees at-least-once delivery with HA-derived event identifiers.

#### Scenario: Dedup identity
- **WHEN** an HA event is submitted
- **THEN** the idempotency key SHALL be `"ha:<endpoint_identity>:<entity_id>:<time_fired_unix_ms>"` combining source, entity, and timestamp
- **AND** duplicate accepted ingest responses SHALL be treated as success, not failures

#### Scenario: Event ordering
- **WHEN** multiple events arrive via WebSocket
- **THEN** the connector SHALL process and submit them in `time_fired` order
- **AND** out-of-order WebSocket events (rare but possible during HA internal batching) SHALL be reordered before submission

### Requirement: Filtered Event Persistence
The connector persists filtered events per the base connector contract.

#### Scenario: HA-specific filter reasons
- **WHEN** an HA event is filtered
- **THEN** the `filter_reason` SHALL include the filter layer and specifics: `"domain_excluded:<domain>"` for layer 1, `"insignificant_delta:<device_class>:<delta>"` for layer 2, `"discretion_ignore"` for layer 3
- **AND** the `full_payload` SHALL contain the complete HA event data for potential replay
