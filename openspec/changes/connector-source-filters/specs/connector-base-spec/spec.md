# Connectors — Shared Interface Contract (delta)

## MODIFIED Requirements

### Requirement: Connector as Ingestion Primitive
A connector is a long-running process (separate from any butler daemon) that bridges an external messaging system into the butler ecosystem. It is transport-only: read, normalize, filter, submit, checkpoint.

#### Scenario: Connector responsibilities boundary
- **WHEN** a connector processes external events
- **THEN** it reads source events from the external system, normalizes each to an `ingest.v1` envelope, evaluates active source filters (dropping messages that fail the filter gate before any Switchboard call), submits passing envelopes to the Switchboard's canonical ingest API via MCP, persists a crash-safe resume checkpoint, enforces rate limiting against both source API and Switchboard, sends periodic heartbeats for liveness tracking, and exports Prometheus metrics
- **AND** the connector does NOT classify messages, route to specialist butlers, mint canonical `request_id` values (Switchboard does this), or bypass the Switchboard ingestion path
- **AND** a connector with no active source filters MUST pass all messages (opt-in model; the filter gate is a no-op when no filters are configured)

#### Scenario: Connector as standalone process
- **WHEN** a connector runs
- **THEN** it is a separate OS process from any butler daemon (not an in-daemon module)
- **AND** it communicates with the Switchboard exclusively via MCP tool calls over SSE
- **AND** it has no direct database access to butler schemas (it may access the shared credential store and the switchboard DB for filter loading via DB-first resolution)

#### Scenario: At-least-once delivery guarantee
- **WHEN** a connector submits events
- **THEN** it guarantees at-least-once delivery via checkpoint-after-acceptance semantics
- **AND** the Switchboard's deduplication layer (advisory lock + dedupe key) makes replays idempotent and harmless
- **AND** duplicate submissions return the same canonical `request_id` (not a new request)
- **AND** messages blocked by source filters are intentionally dropped and their checkpoints advanced; they are NOT retried

## ADDED Requirements

### Requirement: Source Filter Gate (Base Contract)
All connectors MUST implement the source filter gate as specified in `connector-source-filter-enforcement`. This is a mandatory pipeline step, not optional.

#### Scenario: Filter gate is mandatory for all connector types
- **WHEN** a new connector type is implemented
- **THEN** it MUST instantiate a `SourceFilterEvaluator` for its `(connector_type, endpoint_identity)` pair
- **AND** it MUST call `SourceFilterEvaluator.evaluate(key_value)` for every normalized message before submitting to Switchboard
- **AND** it MUST pass the appropriate key value for its source type (see `connector-source-filter-enforcement` for per-source-type key extraction rules)

#### Scenario: Filter state at startup
- **WHEN** a connector starts up
- **THEN** it MUST perform an initial filter load from DB before processing the first message
- **AND** if the initial filter load fails (e.g. DB unavailable), the connector MUST log a WARNING and proceed with an empty filter set (fail-open) rather than aborting startup
