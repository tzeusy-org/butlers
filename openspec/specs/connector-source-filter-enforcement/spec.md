# Connector Source Filter Enforcement

## Purpose
Defines the pre-ingest filter evaluation contract that ALL connectors MUST implement. After normalizing an event but before submitting to the Switchboard, each connector evaluates the message against its active source filters. Messages that fail the filter gate are dropped at the connector with a Prometheus counter increment and never reach the Switchboard or any butler. A shared `SourceFilterEvaluator` module provides the loading, caching, and evaluation logic for all connector types.

## Requirements

### Requirement: Pre-Ingest Filter Gate
Every connector MUST evaluate active source filters before submitting any message to the Switchboard.

#### Scenario: Filter gate position in the connector pipeline
- **WHEN** a connector processes an incoming message
- **THEN** the execution order MUST be: (1) fetch/normalize from source → (2) evaluate source filters → (3) if blocked, drop and increment counter → (4) if allowed, submit `ingest.v1` to Switchboard → (5) checkpoint
- **AND** the filter gate runs BEFORE any Switchboard MCP call for that message
- **AND** a connector with no active filters MUST pass all messages (opt-in model; no filters = no gate)

#### Scenario: Blocked message handling
- **WHEN** a message is blocked by the filter gate
- **THEN** the connector MUST NOT call the Switchboard ingest API for that message
- **AND** the connector MUST increment the `butlers_connector_source_filter_total` counter with labels `{endpoint_identity, action="blocked", filter_name, reason}`
- **AND** the connector MUST log at DEBUG level: `"Message blocked by source filter: filter=%s key_type=%s reason=%s"`
- **AND** the connector MUST advance its checkpoint past the blocked message (it is intentionally dropped, not retried)

#### Scenario: Allowed message handling
- **WHEN** a message passes the filter gate
- **THEN** the connector increments `butlers_connector_source_filter_total` with `{action="passed", filter_name="", reason="no_match_or_no_filters"}`
- **AND** processing continues normally to Switchboard submission

### Requirement: SourceFilterEvaluator Shared Module
A shared `src/butlers/connectors/source_filter.py` module provides filter loading, TTL caching, and evaluation logic for all connector types.

#### Scenario: SourceFilterSpec dataclass
- **WHEN** a filter is loaded from DB
- **THEN** it is represented as `SourceFilterSpec(id: UUID, name: str, filter_mode: Literal["blacklist","whitelist"], source_key_type: str, patterns: list[str], priority: int)`
- **AND** pattern strings are stored as-is; normalization is applied during evaluation per `source_key_type` semantics

#### Scenario: Filter loading from DB
- **WHEN** `SourceFilterEvaluator` loads filters for a connector
- **THEN** it queries `connector_source_filters JOIN source_filters` filtering on `(connector_type, endpoint_identity, enabled=true)` ordered by `priority ASC`
- **AND** it uses the same asyncpg pool already established by the connector for credential resolution (switchboard DB)
- **AND** on DB query failure it logs a WARNING and retains the previous cached filter set (fail-open: do not drop all messages due to a transient DB error)

#### Scenario: TTL-based cache refresh
- **WHEN** `CONNECTOR_FILTER_REFRESH_INTERVAL_S` seconds have elapsed since the last load
- **THEN** the next call to `SourceFilterEvaluator.evaluate()` triggers a background async reload
- **AND** during the reload the previous cached set remains active (no gap in enforcement)
- **AND** the default TTL is 300 seconds; configurable via `CONNECTOR_FILTER_REFRESH_INTERVAL_S` env var
- **AND** the initial load happens at connector startup before the first message is processed

#### Scenario: Evaluation API
- **WHEN** `SourceFilterEvaluator.evaluate(key_value: str) -> FilterResult` is called
- **THEN** it applies the active filter set using the composition rules and returns `FilterResult(allowed: bool, reason: str, filter_name: str | None)`
- **AND** `reason` is one of: `"no_filters"`, `"blacklist_match:<filter_name>"`, `"whitelist_no_match"`, `"passed"`

### Requirement: Filter Evaluation Composition Rules
When multiple filters are active on a connector, they are evaluated using deterministic composition rules.

#### Scenario: No active filters — pass all
- **WHEN** a connector has no active (enabled) source filters
- **THEN** `evaluate()` returns `FilterResult(allowed=True, reason="no_filters")`

#### Scenario: Blacklist evaluation
- **WHEN** one or more blacklist filters are active
- **THEN** filters are evaluated in `priority ASC` order; the first filter whose pattern set matches the key value causes a `FilterResult(allowed=False, reason="blacklist_match:<filter_name>")`
- **AND** if no blacklist filter matches, the message is tentatively allowed (subject to whitelist check)

#### Scenario: Whitelist evaluation
- **WHEN** one or more whitelist filters are active AND the message was not already blocked by a blacklist
- **THEN** the message MUST match at least one whitelist filter to be allowed
- **AND** if the message matches no whitelist filter, `evaluate()` returns `FilterResult(allowed=False, reason="whitelist_no_match")`

#### Scenario: Mixed blacklist + whitelist composition
- **WHEN** both blacklist and whitelist filters are active
- **THEN** evaluation order is: (1) check all blacklists → block if any match; (2) check all whitelists → block if none match; (3) allow
- **AND** a message that matches a blacklist is blocked regardless of any whitelist match

#### Scenario: Unknown source_key_type
- **WHEN** the evaluator encounters a filter whose `source_key_type` is not supported by this connector's channel
- **THEN** the filter is skipped with a one-time WARNING log per filter ID
- **AND** skipped filters do not affect the allowed/blocked outcome

### Requirement: Per-Source-Type Key Extraction
Each connector is responsible for extracting the correct key value to pass to `SourceFilterEvaluator.evaluate()`.

#### Scenario: Email connectors (Gmail, IMAP)
- **WHEN** an email message is evaluated
- **THEN** the connector extracts the key value from the normalized `From` header:
  - For `source_key_type="domain"`: extract domain part of sender address (after `@`, lowercased)
  - For `source_key_type="sender_address"`: extract full normalized email address (lowercase, angle-bracket stripped)
  - For `source_key_type="substring"`: pass the raw `From` header string verbatim

#### Scenario: Telegram connectors (bot and user-client)
- **WHEN** a Telegram update is evaluated
- **THEN** the connector extracts `str(message.chat.id)` or `str(message.from.id)` as the key value for `source_key_type="chat_id"`
- **AND** group chats use `chat.id`; private messages use `from.id` (they are the same for private chats)

#### Scenario: Discord connector
- **WHEN** a Discord message is evaluated
- **THEN** the connector extracts `str(message.channel_id)` for `source_key_type="channel_id"`

### Requirement: Voice Connector Key Extraction
The live-listener connector extracts the microphone device name as the filter key, enabling location-based source filtering.

#### Scenario: Key extraction for mic_id filters
- **WHEN** a transcribed utterance from a voice connector is evaluated against source filters
- **THEN** the connector extracts the device name from the mic pipeline configuration as the key value for `source_key_type="mic_id"`
- **AND** the key value matches the `name` field from the `LIVE_LISTENER_DEVICES` JSON configuration (e.g., `"kitchen"`, `"office"`)
- **AND** the key value is always lowercase

#### Scenario: Valid source key types for live-listener connector
- **WHEN** source filters are configured for a live-listener connector
- **THEN** the only valid `source_key_type` is `"mic_id"`
- **AND** filters with any other `source_key_type` are skipped with a one-time WARNING log per filter ID

#### Scenario: SourceFilterEvaluator instantiation for voice
- **WHEN** the live-listener connector starts
- **THEN** it instantiates `SourceFilterEvaluator(connector_type="live-listener", endpoint_identity=<configured mic endpoint identity>, db_pool=<shared switchboard pool>)` for each mic pipeline
- **AND** performs the initial filter load before beginning audio capture

#### Scenario: Filter gate position in voice pipeline
- **WHEN** a transcribed utterance is ready for discretion evaluation
- **THEN** source filter evaluation runs AFTER transcription but BEFORE the discretion layer
- **AND** this ordering ensures filtered-out utterances do not consume discretion LLM calls
- **AND** a blocked utterance is discarded with counter increment and checkpoint advance

### Requirement: Prometheus Telemetry
Filter enforcement MUST emit Prometheus counters for observability.

#### Scenario: Counter definition
- **WHEN** the connector module initializes
- **THEN** it registers `butlers_connector_source_filter_total` as a `Counter` with labels `["endpoint_identity", "action", "filter_name", "reason"]`
- **AND** `action` is `"passed"` or `"blocked"`
- **AND** `filter_name` is the name of the matching filter (empty string for `"passed"`)
- **AND** `reason` is one of `"no_filters"`, `"blacklist_match"`, `"whitelist_no_match"`, `"passed"`

#### Scenario: Counter incremented for every evaluated message
- **WHEN** every message passes through the filter gate (whether blocked or allowed)
- **THEN** exactly one counter increment is emitted
