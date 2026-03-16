# Connectors — Shared Interface Contract

## Purpose
Defines the shared interface contract that ALL connectors must implement. Connectors are standalone, transport-only adapter processes that poll or subscribe to external messaging systems, normalize events into the canonical `ingest.v1` envelope, and submit them to the Switchboard's ingestion API via MCP. They are the sole ingestion pathway into the butler ecosystem — no message reaches a butler without first passing through a connector and the Switchboard. Individual connector profiles live in `connector-{name}/spec.md`.

## ADDED Requirements

### Requirement: Connector as Ingestion Primitive
A connector is a long-running process (separate from any butler daemon) that bridges an external messaging system into the butler ecosystem. It is transport-only: read, normalize, filter, submit, checkpoint.

#### Scenario: Connector responsibilities boundary
- **WHEN** a connector processes external events
- **THEN** it reads source events from the external system, normalizes each to an `ingest.v1` envelope, evaluates active source filters (dropping messages that fail the filter gate before any Switchboard call), submits passing envelopes to the Switchboard's canonical ingest API via MCP, persists a crash-safe resume checkpoint, enforces rate limiting against both source API and Switchboard, sends periodic heartbeats for liveness tracking, exports Prometheus metrics, persists filtered/errored events to `connectors.filtered_events` via batch flush, and drains the replay queue for pending re-ingestion requests
- **AND** the connector does NOT classify messages, route to specialist butlers, mint canonical `request_id` values (Switchboard does this), or bypass the Switchboard ingestion path
- **AND** a connector with no active source filters MUST pass all messages (opt-in model; the filter gate is a no-op when no filters are configured)

#### Scenario: Connector as standalone process
- **WHEN** a connector runs
- **THEN** it is a separate OS process from any butler daemon (not an in-daemon module)
- **AND** it communicates with the Switchboard exclusively via MCP tool calls over SSE
- **AND** it has direct database access to the `connectors` schema (for filtered event persistence and replay queue) and read access to the `shared` schema (for credential and contact resolution)

#### Scenario: At-least-once delivery guarantee
- **WHEN** a connector submits events
- **THEN** it guarantees at-least-once delivery via checkpoint-after-acceptance semantics
- **AND** the Switchboard's deduplication layer (advisory lock + dedupe key) makes replays idempotent and harmless
- **AND** duplicate submissions return the same canonical `request_id` (not a new request)
- **AND** messages blocked by source filters are intentionally dropped and their checkpoints advanced; they are NOT retried
- **AND** filtered/errored messages are persisted to `connectors.filtered_events` for operator visibility and optional replay

### Requirement: Filtered Event Batch Flush Obligation
All connectors SHALL persist filtered and errored events to `connectors.filtered_events` via a batch flush at the end of each poll cycle.

#### Scenario: Connector records filtered events
- **WHEN** a connector filters a message (label exclusion, connector-scope rule, global-scope rule skip)
- **THEN** it SHALL record the event in an in-memory buffer with: connector_type, endpoint_identity, external_message_id, source_channel, sender_identity, subject_or_preview, filter_reason, status='filtered', and full_payload

#### Scenario: Connector records error events
- **WHEN** a connector encounters a processing error for a message (validation failure, submission error)
- **THEN** it SHALL record the event in the buffer with status='error', error_detail containing the exception message, and full_payload containing whatever envelope fields were available

#### Scenario: Flush after poll cycle
- **WHEN** a connector's poll cycle completes
- **THEN** the buffer SHALL be flushed to `connectors.filtered_events` in a single batch INSERT
- **AND** the buffer SHALL be cleared after successful flush
- **AND** flush failure SHALL be logged as a warning but SHALL NOT prevent cursor advancement

### Requirement: Replay Queue Drain Loop
All connectors SHALL check for pending replay requests after each poll cycle and process them through the standard ingestion pipeline.

#### Scenario: Drain loop executes after poll cycle
- **WHEN** a connector completes a poll cycle (including filtered event flush)
- **THEN** it SHALL query `connectors.filtered_events` for rows with `status = 'replay_pending'` matching its `connector_type` and `endpoint_identity`
- **AND** it SHALL process up to 10 replay items per cycle using `FOR UPDATE SKIP LOCKED`

#### Scenario: Replay uses standard ingestion path
- **WHEN** a connector processes a replay item
- **THEN** it SHALL deserialize `full_payload` from the row
- **AND** it SHALL construct a complete `ingest.v1` envelope from the stored payload
- **AND** it SHALL submit the envelope to the Switchboard's `ingest_v1` MCP tool using the same code path as normal ingestion
- **AND** it SHALL NOT re-evaluate connector-side filter rules (the operator explicitly requested replay)

#### Scenario: Replay status update on success
- **WHEN** a replay submission succeeds
- **THEN** the connector SHALL update the row's status to `replay_complete` and set `replay_completed_at`

#### Scenario: Replay status update on failure
- **WHEN** a replay submission fails
- **THEN** the connector SHALL update the row's status to `replay_failed` and set `error_detail` with the failure reason
- **AND** it SHALL continue processing remaining replay items

### Requirement: Source Filter Gate (Base Contract)

All connectors MUST implement the ingestion policy gate as a mandatory pipeline step. After normalizing an event and before submitting to the Switchboard, each connector evaluates the message against its active connector-scoped ingestion rules via `IngestionPolicyEvaluator`. Messages that receive a `block` action are dropped at the connector and never reach the Switchboard.

The evaluator is instantiated with `scope = 'connector:<connector_type>:<endpoint_identity>'` and loads only rules matching that scope from the unified `ingestion_rules` table.

#### Scenario: Filter gate position in the connector pipeline
- **WHEN** a connector processes an incoming message
- **THEN** the connector evaluates the message against its `IngestionPolicyEvaluator` AFTER normalization and BEFORE submitting to the Switchboard

#### Scenario: Blocked message handling
- **WHEN** the evaluator returns `PolicyDecision(action='block')`
- **THEN** the message is NOT submitted to the Switchboard, the Prometheus counter is incremented, and the connector advances its checkpoint

#### Scenario: Filter state at startup
- **WHEN** a connector starts its ingestion loop
- **THEN** it MUST call `evaluator.ensure_loaded()` before processing the first message

#### Scenario: DB error fail-open behavior
- **WHEN** the evaluator cannot reach the database during a cache refresh
- **THEN** it retains its previous cache and logs a warning; ingestion is NOT blocked

#### Scenario: IngestionPolicyEvaluator contract
- **WHEN** a connector instantiates its evaluator
- **THEN** it passes `scope = 'connector:<connector_type>:<endpoint_identity>'` and a shared DB pool; the evaluator loads only connector-scoped rules for that scope

### Requirement: ingest.v1 Envelope Schema
The `ingest.v1` envelope is the canonical format for all messages entering the butler ecosystem. It is a Pydantic model (`IngestEnvelopeV1`) with five required sub-models validated at parse time.

#### Scenario: Top-level envelope structure
- **WHEN** a connector constructs an ingest envelope
- **THEN** it contains: `schema_version` (must be `"ingest.v1"`), `source` (IngestSourceV1), `event` (IngestEventV1), `sender` (IngestSenderV1), `payload` (IngestPayloadV1), `control` (IngestControlV1)

#### Scenario: Source identity (IngestSourceV1)
- **WHEN** `source` is populated
- **THEN** `channel` is a `SourceChannel` enum value (`telegram`, `slack`, `email`, `api`, `mcp`, `voice`), `provider` is a `SourceProvider` enum value (`telegram`, `slack`, `gmail`, `imap`, `internal`, `live-listener`), and `endpoint_identity` is a non-empty string uniquely identifying the connector instance (e.g., `"gmail:user:alice@gmail.com"`, `"telegram:bot:mybot"`, `"live-listener:mic:kitchen"`)

#### Scenario: Channel-provider pair validation
- **WHEN** `source.channel` and `source.provider` are set
- **THEN** valid pairings are enforced: `telegram`/`telegram`, `email`/`gmail`, `email`/`imap`, `api`/`internal`, `mcp`/`internal`, `voice`/`live-listener`
- **AND** invalid pairings fail Pydantic validation

#### Scenario: Event metadata (IngestEventV1)
- **WHEN** `event` is populated
- **THEN** `external_event_id` is a non-empty string (the provider's stable event ID, required for deduplication), `external_thread_id` is an optional non-empty string (email thread ID, Telegram chat ID), and `observed_at` is a timezone-aware datetime (RFC3339, when the connector observed the event)

#### Scenario: Sender identity (IngestSenderV1)
- **WHEN** `sender` is populated
- **THEN** `identity` is a non-empty string representing the sender (email address, Telegram user ID, etc.)

#### Scenario: Payload with tiered content (IngestPayloadV1)
- **WHEN** `payload` is populated
- **THEN** `raw` is the full provider payload dict (required non-None for Tier 1 "full", must be None for Tier 2 "metadata"), `normalized_text` is a non-empty string (the best available human-readable text), and `attachments` is an optional tuple of `IngestAttachment` records

#### Scenario: Attachment metadata (IngestAttachment)
- **WHEN** an attachment is included
- **THEN** it contains: `media_type` (MIME type string), `storage_ref` (storage reference for lazy fetch), `size_bytes` (uncompressed size), `filename` (optional), `width` and `height` (optional, for images)

#### Scenario: Control directives (IngestControlV1)
- **WHEN** `control` is populated
- **THEN** `idempotency_key` is an optional explicit dedup key (overrides default computation), `trace_context` is a dict of tracing metadata, `policy_tier` is a `PolicyTier` enum (`default`, `interactive`, `high_priority`) for queue ordering, and `ingestion_tier` is an `IngestionTier` enum (`full` for Tier 1, `metadata` for Tier 2)

#### Scenario: Tier-dependent payload validation
- **WHEN** `control.ingestion_tier` is `"full"` (Tier 1)
- **THEN** `payload.raw` must be a non-None dict containing the complete provider payload
- **WHEN** `control.ingestion_tier` is `"metadata"` (Tier 2)
- **THEN** `payload.raw` must be None and `payload.normalized_text` contains only the subject line or summary

### Requirement: Deduplication Strategy
The Switchboard computes a stable deduplication key for each ingest submission using a priority-based strategy. Advisory locking prevents race conditions on concurrent submissions with the same key.

#### Scenario: Priority 1 — Explicit idempotency key
- **WHEN** `control.idempotency_key` is provided
- **THEN** the dedupe key is `"idem:{channel}:{endpoint_identity}:{idempotency_key}"`

#### Scenario: Priority 2 — External event ID
- **WHEN** no explicit idempotency key is provided and `event.external_event_id` is non-placeholder (not `"placeholder"`, `"unknown"`, `"none"`, or empty)
- **THEN** the dedupe key is `"event:{channel}:{provider}:{endpoint_identity}:{external_event_id}"`

#### Scenario: Priority 3 — Content hash fallback
- **WHEN** neither explicit key nor usable event ID is available
- **THEN** the dedupe key is `"hash:{channel}:{endpoint_identity}:{sender}:{hour_bucket}:{content_hash[:16]}"` where `content_hash` is SHA256 of `normalized_text:sender_identity` and `hour_bucket` is the hourly time window (`YYYYMMDDHH`)

#### Scenario: Advisory lock serialization
- **WHEN** the Switchboard processes an ingest submission
- **THEN** it acquires `pg_advisory_xact_lock(hashtext(dedupe_key))` within a transaction
- **AND** inside the lock: re-checks for an existing record with the same dedupe key, and either returns the existing `request_id` with `duplicate=true` or inserts a new row into both `message_inbox` and `shared.ingestion_events` atomically within the same transaction

#### Scenario: Ingest accepted response
- **WHEN** the Switchboard accepts an ingest submission
- **THEN** it returns `IngestAcceptedResponse` with: `request_id` (UUID7, canonical reference), `status` (`"accepted"`), `duplicate` (bool), `triage_decision` (string or None), `triage_target` (butler name or None)

### Requirement: Request Context Assignment
The Switchboard builds an immutable request context from each accepted ingest envelope. This context travels with the message through classification, routing, and butler processing. The `request_id` is the UUID7 primary key of the corresponding `shared.ingestion_events` row.

#### Scenario: Request context fields
- **WHEN** a message is accepted for processing
- **THEN** the Switchboard assigns: `request_id` (UUID7, equals `shared.ingestion_events.id`), `received_at` (server timestamp), `source_channel`, `source_endpoint_identity`, `source_sender_identity`, `source_thread_identity` (from `external_thread_id`), `idempotency_key`, `trace_context`, `ingestion_tier`, `dedupe_key`, `dedupe_strategy` (`"connector_api"`)
- **AND** if triage was evaluated: `triage_decision`, `triage_target`, `triage_rule_id`, `triage_rule_type`
- **AND** the `request_id` is passed through to the spawned butler session as both `session.request_id` and `session.ingestion_event_id`

### Requirement: Triage Integration

Connector-side and server-side ingestion rules gate ingestion and early routing decisions before LLM classification. Connector-scoped rules (`block` action) are evaluated at the connector. Global rules (all other actions) are evaluated post-ingest by the Switchboard.

#### Scenario: Thread affinity lookup (email only)
- **WHEN** an email message is ingested with a thread_id
- **THEN** Switchboard checks thread affinity BEFORE evaluating global ingestion rules

#### Scenario: Deterministic rule evaluation
- **WHEN** a message passes connector-scoped evaluation and is accepted by the Switchboard
- **THEN** global ingestion rules are evaluated in priority order; the first match determines routing/action

#### Scenario: Ingestion tier classification
- **WHEN** no global ingestion rule matches (pass_through)
- **THEN** the message proceeds to LLM classification

### Requirement: CachedMCPClient Transport
All connector-to-Switchboard communication uses a lazy, reconnecting MCP client over SSE.

#### Scenario: Lazy connection management
- **WHEN** a connector calls an MCP tool for the first time
- **THEN** the `CachedMCPClient` establishes an SSE connection to `SWITCHBOARD_MCP_URL`
- **AND** the connection is cached for subsequent calls within the same process

#### Scenario: Single-retry reconnect on failure
- **WHEN** an MCP call fails due to a connection error
- **THEN** the client reconnects once and retries the call
- **AND** if the retry also fails, a `ConnectionError` is raised
- **AND** application-level MCP errors (`is_error=True` on the result) are NOT retried — they propagate immediately

#### Scenario: Result parsing
- **WHEN** an MCP tool returns a result
- **THEN** the client extracts structured data: FastMCP 2.x `.data` attribute first, then falls back to parsing JSON from text content blocks
- **AND** error results raise `RuntimeError` with the tool name and error content

#### Scenario: MCP tool surface for connectors
- **WHEN** a connector interacts with Switchboard
- **THEN** it uses three MCP tools: `ingestion.ingest` (submit ingest.v1 envelope), `connector.heartbeat` (submit heartbeat.v1 envelope), and `backfill.poll` / `backfill.progress` (backfill orchestration)

### Requirement: Safe Resuming
Connectors are crash-safe and restart-safe via checkpoint-after-acceptance semantics.

#### Scenario: Checkpoint persistence pattern
- **WHEN** a connector processes a batch of events
- **THEN** it persists a resume cursor/checkpoint to the DB via `cursor_store`
- **AND** the checkpoint is advanced only after successful ingest acceptance (or accepted duplicate)

#### Scenario: Checkpoint persistence
- **WHEN** a connector saves a checkpoint
- **THEN** it writes the cursor to the DB (keyed by provider + endpoint identity)
- **AND** on restart, it replays from the last safe checkpoint (replays are harmless due to ingest dedup)

### Requirement: Rate Limiting and Backpressure
Connectors implement two independent rate-limiting controls: source API protection and Switchboard ingest protection.

#### Scenario: Source API limit handling
- **WHEN** a source provider returns rate-limit signals (HTTP 429)
- **THEN** the connector honors `Retry-After` when present, uses exponential backoff with jitter, and respects provider quotas

#### Scenario: Switchboard ingest protection
- **WHEN** submitting events to Switchboard
- **THEN** the connector caps concurrent ingest submissions via `CONNECTOR_MAX_INFLIGHT` semaphore (default 8)
- **AND** overload outcomes are surfaced in logs and metrics — no silent drops

### Requirement: Connector Prometheus Metrics
All connectors export standardized Prometheus metrics via a `ConnectorMetrics` class and expose them on a `/metrics` HTTP endpoint.

#### Scenario: Ingest submission metrics
- **WHEN** a connector submits to Switchboard
- **THEN** `connector_ingest_submissions_total` (Counter, labels: `connector_type`, `endpoint_identity`, `status` = `success`/`error`/`duplicate`) is incremented
- **AND** `connector_ingest_latency_seconds` (Histogram, buckets: 5ms to 10s) records end-to-end submission latency

#### Scenario: Source API call metrics
- **WHEN** a connector calls the source provider API
- **THEN** `connector_source_api_calls_total` (Counter, labels: `connector_type`, `endpoint_identity`, `api_method`, `status`) is incremented

#### Scenario: Checkpoint and error metrics
- **WHEN** checkpoint operations or errors occur
- **THEN** `connector_checkpoint_saves_total` and `connector_errors_total` are incremented
- **AND** `error_type` is semantically extracted from exceptions: `http_error`, `timeout`, `connection_error`, etc.

#### Scenario: Auto-timing context manager
- **WHEN** connector code submits an ingest envelope
- **THEN** it can use `ConnectorMetrics.track_ingest_submission()` context manager which automatically times the operation and records both the counter and histogram

#### Scenario: Health and metrics HTTP server
- **WHEN** a connector is running
- **THEN** it exposes a FastAPI health server on `CONNECTOR_HEALTH_PORT` with `/health` (JSON status) and `/metrics` (Prometheus text format) endpoints

### Requirement: Environment Variables (Base)
All connectors share a common set of environment variables defining identity, transport, and operational parameters.

#### Scenario: Required base environment variables
- **WHEN** a connector starts
- **THEN** `SWITCHBOARD_MCP_URL` (Switchboard SSE endpoint), `CONNECTOR_PROVIDER` (e.g., `gmail`, `telegram`), and `CONNECTOR_CHANNEL` (e.g., `email`, `telegram`) must be set
- **AND** each connector auto-resolves its `endpoint_identity` at startup (e.g., by calling the provider's "get me" API) rather than requiring it as an env var
- **AND** checkpoint persistence is DB-backed via `cursor_store` (no file path env var needed)

#### Scenario: Optional environment variables
- **WHEN** a connector starts
- **THEN** `CONNECTOR_MAX_INFLIGHT` (default 8), `CONNECTOR_HEALTH_PORT`, `CONNECTOR_POLL_INTERVAL_S`, `CONNECTOR_HEARTBEAT_INTERVAL_S` (default 120), and `CONNECTOR_HEARTBEAT_ENABLED` (default true) are optionally configurable

---

### Requirement: Heartbeat Protocol
All connectors send periodic heartbeats to the Switchboard for liveness tracking, operational statistics collection, and capability advertisement. Heartbeats are the sole mechanism for connector self-registration — no manual pre-configuration is needed.

#### Scenario: Heartbeat envelope structure (connector.heartbeat.v1)
- **WHEN** a connector sends a heartbeat
- **THEN** the envelope contains: `schema_version` (`"connector.heartbeat.v1"`), `connector` (identity block), `status` (health block), `counters` (operational metrics), `checkpoint` (optional resume cursor), `capabilities` (optional feature flags), `sent_at` (RFC3339 timestamp)

#### Scenario: Connector identity block
- **WHEN** the `connector` section is populated
- **THEN** it contains: `connector_type` (e.g., `"gmail"`, `"telegram_bot"`, `"telegram_user_client"`), `endpoint_identity` (auto-resolved at startup), `instance_id` (UUID4, stable per process lifetime — a new ID indicates restart), `version` (optional software version)

#### Scenario: Health status block
- **WHEN** the `status` section is populated
- **THEN** `state` is one of `healthy` (normal operation), `degraded` (issues but still ingesting), or `error` (unable to ingest)
- **AND** `error_message` is present when state is `degraded` or `error`
- **AND** `uptime_s` is seconds since process start

#### Scenario: Operational counters block
- **WHEN** the `counters` section is populated
- **THEN** it contains monotonically increasing counters since process start: `messages_ingested`, `messages_failed`, `source_api_calls`, `checkpoint_saves`, `dedupe_accepted`
- **AND** counters are read from the Prometheus registry at heartbeat assembly time

#### Scenario: Checkpoint and capabilities advertisement
- **WHEN** the connector has a resume cursor
- **THEN** `checkpoint` contains: `cursor` (opaque string), `updated_at` (last checkpoint save time)
- **AND** optional `capabilities` dict advertises features (e.g., `{"backfill": true}`)

#### Scenario: Heartbeat interval and bounds
- **WHEN** the heartbeat task runs
- **THEN** it fires every `CONNECTOR_HEARTBEAT_INTERVAL_S` (default 120 seconds)
- **AND** the interval is bounded between 30 seconds (minimum) and 300 seconds (maximum)
- **AND** `CONNECTOR_HEARTBEAT_ENABLED=false` disables the task entirely (development only)

#### Scenario: Non-blocking heartbeat failures
- **WHEN** a heartbeat submission fails
- **THEN** the failure is logged as a warning but never crashes or blocks the ingestion loop

#### Scenario: Self-registration on first heartbeat
- **WHEN** the Switchboard receives a heartbeat from an unknown connector
- **THEN** it auto-creates a `connector_registry` row (no manual pre-configuration needed)

#### Scenario: Instance restart detection and counter deltas
- **WHEN** a heartbeat arrives with a different `instance_id` than the previous one from the same connector
- **THEN** the Switchboard detects a restart; counter deltas are computed against zero (not the previous snapshot)
- **WHEN** the `instance_id` matches
- **THEN** deltas = current - previous

### Requirement: Connector Liveness and Eligibility
The Switchboard derives connector liveness from heartbeat recency and manages eligibility state transitions.

#### Scenario: Liveness thresholds
- **WHEN** a connector's liveness is evaluated
- **THEN** `online` when last heartbeat age < 5 minutes, `stale` when 5-15 minutes, `offline` when > 15 minutes or no heartbeat ever received

#### Scenario: Eligibility states
- **WHEN** a connector's eligibility is evaluated
- **THEN** it is one of: `active` (heartbeat within liveness TTL), `stale` (no heartbeat within TTL), `quarantined` (explicitly flagged)
- **AND** quarantine takes precedence over any heartbeat recency

#### Scenario: Eligibility transition auditing
- **WHEN** a connector's eligibility state changes
- **THEN** an audit log entry is written with: connector name, previous state, new state, reason, timestamps

#### Scenario: No automatic deregistration
- **WHEN** a connector goes offline
- **THEN** the record persists in `connector_registry` for historical visibility — cleanup is operator-only

### Requirement: Statistics Pipeline (OTel/Prometheus)
Connector statistics are exported via the OTel/Prometheus metrics pipeline. The pre-aggregated SQL rollup tables (`connector_stats_hourly`, `connector_stats_daily`, `connector_fanout_daily`) were dropped by migration sw_025 (butlers-ufzc).

#### Scenario: Volume metrics
- **WHEN** connectors emit OTel metrics
- **THEN** per-connector volume metrics (messages_ingested, messages_failed, source_api_calls, dedupe_accepted) are available in Prometheus for dashboard time-series queries

#### Scenario: Fanout metrics
- **WHEN** connector messages are routed by Switchboard
- **THEN** per-connector per-target-butler fanout metrics are available in Prometheus for dashboard distribution queries

---

### Requirement: Dashboard Connector Page
Dashboard frontend exposes connector fleet monitoring at `/connectors`.

#### Scenario: Connector overview cards
- **WHEN** the `/connectors` page is loaded
- **THEN** each registered connector shows: type icon, endpoint identity, liveness badge, health state, uptime percentage, last heartbeat age, today's ingestion count

#### Scenario: Volume time series chart
- **WHEN** a time period is selected (24h/7d/30d)
- **THEN** a chart shows ingestion volume per connector

#### Scenario: Fanout distribution matrix
- **WHEN** fanout data is viewed
- **THEN** a table/heatmap shows connector × butler routing distribution

#### Scenario: Error log view
- **WHEN** the error log is viewed
- **THEN** recent connector errors are shown with timestamp, identity, state, and error message

### Requirement: Pydantic Response Models
Core API response models for the connectors dashboard and API endpoints.

#### Scenario: ConnectorSummary model
- **WHEN** a connector list response is serialized
- **THEN** each entry includes: `connector_type`, `endpoint_identity`, `liveness`, `state`, `error_message`, `version`, `uptime_s`, `last_heartbeat_at`, `first_seen_at`, and optional `today` summary

#### Scenario: ConnectorDetail model
- **WHEN** a connector detail response is serialized
- **THEN** it extends ConnectorSummary with: `instance_id`, `registered_via`, `checkpoint`, `counters`, `settings`
- **AND** `settings` is an optional JSONB dict containing runtime-configurable connector settings (e.g. discretion thresholds)

#### Scenario: ConnectorStats model
- **WHEN** a statistics response is serialized
- **THEN** it includes: `connector_type`, `endpoint_identity`, `period`, `summary`, `timeseries`

#### Scenario: ConnectorFanoutEntry model
- **WHEN** a fanout response is serialized
- **THEN** it includes: `connector_type`, `endpoint_identity`, `targets` (butler_name → message_count)

### Requirement: Connector Settings API
Runtime-configurable connector settings are stored in `connector_registry.settings` (JSONB) and managed via a dashboard API endpoint.

#### Scenario: Settings storage
- **WHEN** a connector has runtime-configurable settings
- **THEN** they are stored in the `settings` JSONB column of `connector_registry`
- **AND** NULL means no settings overrides; non-NULL holds a JSON object
- **AND** settings are shallow-merged on update (top-level keys replaced, not deep-merged)

#### Scenario: Settings update API
- **WHEN** a PATCH request is sent to `/connectors/{connector_type}/{endpoint_identity}/settings`
- **THEN** the body `{"settings": {...}}` is shallow-merged into the existing settings
- **AND** the updated `ConnectorEntry` is returned
- **AND** settings take effect on next connector restart (same semantics as cursor updates)

#### Scenario: Discretion settings schema
- **WHEN** a connector uses the shared discretion layer
- **THEN** its `settings.discretion` object may contain: `weight_bypass` (float, default 1.0), `weight_fail_open` (float, default 0.5)
- **AND** these thresholds are editable from the connector detail page in the dashboard

### Requirement: Shared Discretion Layer
An LLM-based filter (`butlers.connectors.discretion`) that evaluates messages in context and decides whether they warrant butler attention (FORWARD) or should be silently discarded (IGNORE). Used by connectors that need noise filtering before Switchboard ingestion.

The discretion layer uses the project's RuntimeAdapter interface via a dedicated `DiscretionDispatcher` (`butlers.connectors.discretion_dispatcher`), which resolves models from the shared model catalog at the `discretion` complexity tier. This unifies all LLM interaction within the repository under explicit adapter resolution — the same Settings UI and model catalog that manages butler session models also manages discretion models.

#### Scenario: Discretion module location
- **WHEN** a connector integrates the discretion layer
- **THEN** it imports `DiscretionEvaluator` from `butlers.connectors.discretion` (shared module, not connector-specific)
- **AND** it imports `DiscretionDispatcher` from `butlers.connectors.discretion_dispatcher`
- **AND** it creates a `DiscretionDispatcher(pool=db_pool)` and injects it into each `DiscretionEvaluator`

#### Scenario: Discretion model resolution via catalog
- **WHEN** a discretion evaluation requires an LLM call
- **THEN** the `DiscretionDispatcher` resolves the model from `shared.model_catalog` using `complexity_tier='discretion'`
- **AND** the resolved `(runtime_type, model_id, extra_args)` tuple is passed to the appropriate `RuntimeAdapter` via the standard adapter registry
- **AND** the default seed entry is `discretion-qwen3.5-9b` (runtime_type=`opencode`, model_id=`ollama/qwen3.5:9b`) — a local Ollama model accessed via the OpenCode adapter
- **AND** operators can change the discretion model at any time via the Settings UI at `/butlers/settings` without code changes or restarts

#### Scenario: Discretion dispatcher concurrency
- **WHEN** a `DiscretionDispatcher` is instantiated
- **THEN** it maintains its own `asyncio.Semaphore` (default 4 concurrent calls), independent from any butler's spawner semaphore
- **AND** the concurrency limit is configurable per dispatcher instance

#### Scenario: Discretion configuration
- **WHEN** a connector uses the discretion layer
- **THEN** the `DiscretionEvaluator` accepts configuration for: `window_size` (default: 10), `window_seconds` (default: 300), `weight_bypass` (default: 1.0), `weight_fail_open` (default: 0.5), and `system_prompt`
- **AND** model selection (previously via `{PREFIX}DISCRETION_LLM_URL` / `{PREFIX}DISCRETION_LLM_MODEL` env vars) is handled entirely by the model catalog

#### Scenario: Fail-open by default
- **WHEN** the discretion LLM call fails (timeout, connection error, malformed response)
- **THEN** the default behaviour depends on the sender's weight: weight >= `weight_fail_open` threshold → FORWARD (fail-open), weight < threshold → IGNORE (fail-closed)

### Requirement: Identity-Based Discretion Weight
The discretion layer supports sender-relationship weighting that controls bypass and fail behaviour.

#### Scenario: Weight tiers
- **WHEN** a sender's identity is resolved via `shared.contact_info → shared.contacts → shared.entities`
- **THEN** the weight is determined by the entity's roles: `owner` → 1.0, `family` or `close-friends` → 0.9, known contact (any role) → 0.7, unknown sender (no contact match) → 0.3

#### Scenario: Weight bypass
- **WHEN** the sender's weight >= `weight_bypass` threshold (default 1.0)
- **THEN** the discretion LLM is skipped entirely and the message always FORWARDs
- **AND** the message is still appended to the context window for future evaluations

#### Scenario: Weight fail behaviour
- **WHEN** the sender's weight >= `weight_fail_open` threshold (default 0.5)
- **THEN** LLM errors default to FORWARD (fail-open)
- **WHEN** the sender's weight < `weight_fail_open` threshold
- **THEN** LLM errors default to IGNORE (fail-closed)

#### Scenario: ContactWeightResolver
- **WHEN** a connector has database access
- **THEN** it uses `ContactWeightResolver(db_pool)` to resolve sender identity to a weight
- **AND** results are cached in-memory with a configurable TTL (default 5 minutes)
- **AND** DB errors return the `unknown` tier weight (fail-safe)

#### Scenario: Voice connectors without identity
- **WHEN** a connector has no sender identity (e.g. live-listener with ambient audio)
- **THEN** all messages use weight=1.0 (owner-equivalent, preserving fail-open behaviour)

### Requirement: Authentication and Token Management
Connector authentication with the Switchboard uses bearer tokens with scope enforcement.

#### Scenario: [TARGET-STATE] Token scope enforcement
- **WHEN** a connector authenticates with `SWITCHBOARD_API_TOKEN`
- **THEN** the token scope must match the connector's source identity

#### Scenario: [TARGET-STATE] Token security requirements
- **WHEN** connector tokens are managed
- **THEN** tokens are stored in secret managers, rotated every 90 days (production) or 7 days (development), and revoked immediately if compromised

### Requirement: [TARGET-STATE] Horizontal Scaling Patterns
Architecture for scaling connectors beyond single-instance deployment.

#### Scenario: Lease-based coordination (HA)
- **WHEN** active-standby HA is needed
- **THEN** a lease-based coordination pattern provides exactly-one-active semantics with automatic failover

#### Scenario: Partition-based scaling
- **WHEN** high-throughput parallel ingestion is needed
- **THEN** source-native partitioning (e.g., Gmail label sharding, Telegram chat ID ranges) allows multiple instances to process non-overlapping subsets

#### Scenario: Checkpoint storage backends
- **WHEN** scaling beyond single-instance DB-backed checkpoints
- **THEN** supported backends include: PostgreSQL via `cursor_store` (v1 default), Redis, etcd, with CAS-based conflict resolution
