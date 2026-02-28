# Switchboard Butler

## Purpose
The Switchboard Butler is the single ingress and orchestration control plane for the butler system. It receives all external interactions, assigns canonical request context, performs LLM-driven routing to downstream specialist butlers, and records the full request lifecycle.

## ADDED Requirements

### Requirement: Request Context Contract
Every ingress request receives a canonical request context before any routing decision. Context is immutable and propagated to all downstream subrequests.

#### Scenario: Canonical context assignment on ingress
- **WHEN** an ingress request is accepted by Switchboard
- **THEN** Switchboard assigns a `request_id` (UUID7), `received_at` (UTC timestamp), `source_channel`, `source_endpoint_identity`, and `source_sender_identity`
- **AND** optional fields `source_thread_identity` and `trace_context` are populated when available

#### Scenario: Channel-specific minimum identity
- **WHEN** the source channel is Telegram
- **THEN** the context includes bot identity as `source_endpoint_identity` and source username/user-id as `source_sender_identity`
- **AND** `source_thread_identity` carries the chat_id

#### Scenario: Email channel minimum identity
- **WHEN** the source channel is email
- **THEN** the context includes receiving mailbox identity as `source_endpoint_identity` and RFC `From` identity as `source_sender_identity`

#### Scenario: Context propagation on fanout
- **WHEN** Switchboard fans out to one or more downstream butlers
- **THEN** the original `request_id` and source context are forwarded to all subrequests
- **AND** subrequest metadata may add `subrequest_id` and `segment_id` but cannot replace root context

### Requirement: Ingestion and Retention
Switchboard persists canonical ingress payloads in short-lived storage and projects operational/audit outcomes to long-term durable storage.

#### Scenario: Month-partitioned message inbox storage
- **WHEN** an ingress request is accepted
- **THEN** the request is persisted in `message_inbox`, a month-partitioned table keyed by `received_at`
- **AND** stored artifacts include canonical request context, raw payload, normalized message content, LLM routing output, dispatch outcomes, aggregated reply, and final lifecycle state

#### Scenario: One-month retention policy
- **WHEN** a `message_inbox` partition is older than 1 month
- **THEN** the partition is dropped on schedule unless a policy override exists
- **AND** partition creation is automated so new months are always available

#### Scenario: Recent-first operational queries
- **WHEN** an operator queries `message_inbox`
- **THEN** table/index design supports recent-first ordering for efficient operational access

### Requirement: LLM-Driven Routing Contract
Switchboard performs discretionary routing through a pluggable LLM CLI runtime. The router classifies incoming messages and decomposes multi-domain requests into segments routed to specialist butlers.

#### Scenario: Single-domain routing
- **WHEN** a message has one clear domain match (e.g., health, relationship, finance, travel)
- **THEN** Switchboard routes the entire message to that specialist butler via `route_to_butler`
- **AND** the sub-prompt is self-contained with all relevant entities and context

#### Scenario: Multi-domain decomposition
- **WHEN** a message spans multiple domains with clear boundaries
- **THEN** Switchboard decomposes into one sub-prompt per target butler
- **AND** each segment carries self-contained prompt text and segment metadata (sentence span references, character offset ranges, or decomposition rationale)

#### Scenario: Domain classification rules
- **WHEN** a message arrives for classification
- **THEN** the LLM classifier applies domain-specific rules: finance for payment/billing/subscription signals, travel for booking/itinerary/flight signals, relationship for contacts/interactions/social, health for medications/measurements/symptoms/diet/nutrition, and general as the catch-all fallback

#### Scenario: Finance vs Travel tie-break rules
- **WHEN** a message contains both financial and travel semantics
- **THEN** finance wins when the primary intent is billing/refund/payment resolution
- **AND** travel wins when the primary intent is itinerary/booking tracking

#### Scenario: Ambiguity fallback to general
- **WHEN** routing confidence is below the configured threshold or LLM output is ambiguous
- **THEN** Switchboard routes the full original message to the `general` butler
- **AND** the ambiguity-triggered fallback is tagged in lifecycle records and observable in metrics

#### Scenario: Classification failure fallback
- **WHEN** classification fails (LLM timeout, parse error, empty response)
- **THEN** Switchboard routes the entire message to the `general` butler with the original text intact

#### Scenario: Runtime model family support
- **WHEN** Switchboard spawns a routing LLM instance
- **THEN** the runtime supports Claude Code, Codex, and Opencode families
- **AND** lightweight, capable models are preferred for fast classification/decomposition

#### Scenario: Conversation history context for routing
- **WHEN** the source channel is a real-time messaging channel (Telegram, WhatsApp, Slack, Discord)
- **THEN** recent conversation history (last 15 minutes or last 30 messages, whichever is more) is provided to the router for context
- **AND** the router only routes the current message, using history only to improve routing accuracy

#### Scenario: Email conversation history
- **WHEN** the source channel is email
- **THEN** the full email chain is provided, truncated to 50,000 tokens (preserving newest messages)
- **AND** the router uses chain context to improve routing but only routes the current message

### Requirement: Prompt Injection Safety
Ingress content is always untrusted. The routing pipeline enforces strict isolation between user content and executable instructions.

#### Scenario: User content isolation
- **WHEN** user content is passed to the routing LLM
- **THEN** it is passed as an isolated data payload, not executable instructions
- **AND** the router prompt explicitly forbids obeying instructions inside user content

#### Scenario: Output schema validation
- **WHEN** the LLM produces a routing decision
- **THEN** the output is constrained to a strict schema and validated against registry-known butlers only
- **AND** invalid or malformed output triggers safe fallback routing to `general`

### Requirement: Downstream Route Response Consumption
Switchboard is the canonical consumer of downstream `route_response.v1` envelopes.

#### Scenario: Successful downstream response
- **WHEN** a downstream butler returns a `route_response.v1` envelope with `status=ok`
- **THEN** Switchboard records the `result` and `timing.duration_ms`
- **AND** the `request_context.request_id` in the response must match the dispatched request lineage

#### Scenario: Downstream failure response
- **WHEN** a downstream butler returns a `route_response.v1` envelope with `status=error`
- **THEN** Switchboard records `error.class`, `error.message`, and `error.retryable`
- **AND** both raw downstream response payloads and normalized failure class are persisted for auditability

#### Scenario: Unknown schema version
- **WHEN** a downstream response has an unknown or missing `schema_version`
- **THEN** Switchboard fails deterministically with error class `validation_error`

#### Scenario: Route timeout with no response
- **WHEN** no response arrives before the route timeout
- **THEN** Switchboard synthesizes a `timeout`-class terminal failure
- **AND** when transport fails before a valid envelope is returned, Switchboard synthesizes `target_unavailable`

### Requirement: Asynchronous Ingestion
Switchboard supports multiple simultaneous ingress channels without blocking ingestion.

#### Scenario: Non-blocking ingress acceptance
- **WHEN** a new message arrives on any channel
- **THEN** ingress acceptance is non-blocking with bounded work admission
- **AND** routing/dispatch work executes asynchronously from transport ingestion loops

#### Scenario: DurableBuffer hot path
- **WHEN** a message is accepted and persisted in `message_inbox`
- **THEN** a lightweight `_MessageRef` is enqueued to a tier-specific in-memory queue (hot path)
- **AND** the queue has bounded capacity per tier (`queue_capacity` from `[buffer]` config, default 100)

#### Scenario: DurableBuffer cold path (crash recovery)
- **WHEN** a message remains in `accepted` state past the scanner grace period (default 10s)
- **THEN** the periodic scanner (every 30s by default) re-enqueues the message to the appropriate tier queue
- **AND** messages with empty `normalized_text` are marked as `errored` and skipped

#### Scenario: Backpressure on queue full
- **WHEN** the tier queue is full during hot-path enqueue
- **THEN** the enqueue is skipped (backpressure) and the message remains durably in `message_inbox`
- **AND** the scanner recovers it on the next sweep; a `backpressure_total` counter is incremented

#### Scenario: Concurrent channel isolation
- **WHEN** requests arrive from independent channels simultaneously
- **THEN** they do not starve each other
- **AND** a long-running route for one request does not block new request acceptance

### Requirement: Priority Tier Queuing
The DurableBuffer implements three-tier priority queuing with starvation prevention.

#### Scenario: Tier ordering
- **WHEN** messages are enqueued with different policy tiers
- **THEN** workers dequeue in priority order: `high_priority` > `interactive` > `default`
- **AND** each tier has an independent queue with `queue_capacity` max items

#### Scenario: Starvation guard
- **WHEN** a worker has dequeued `max_consecutive_same_tier` (default 10) consecutive messages from the same tier
- **THEN** if a lower-priority tier is non-empty, the next dequeue is forced from the highest available lower tier
- **AND** after the forced lower-tier dequeue, the counter resets and re-evaluates from the highest tier

#### Scenario: Unknown policy tier fallback
- **WHEN** a message has an unrecognized `policy_tier` value
- **THEN** it falls back to the `default` tier with a warning log

### Requirement: Interactive Lifecycle Contract
For interactive channels, Switchboard emits user-visible lifecycle states.

#### Scenario: PROGRESS state on receipt
- **WHEN** an interactive-channel message is received
- **THEN** lifecycle transitions to `PROGRESS`
- **AND** on Telegram, a :eye: emoji reaction is set

#### Scenario: PARSED state on success
- **WHEN** fanout completes and all downstream targets succeed
- **THEN** lifecycle transitions to `PARSED`
- **AND** on Telegram, a :done: (checkmark) emoji reaction is set

#### Scenario: ERRORED state on failure
- **WHEN** at least one downstream target fails or terminal processing fails
- **THEN** lifecycle transitions to `ERRORED`
- **AND** on Telegram, a :space invader: (alien) emoji reaction is set
- **AND** Switchboard sends a user-visible error reply containing actionable failure context

#### Scenario: Best-effort Telegram reactions
- **WHEN** Telegram rejects a reaction with expected 400 unsupported/unavailable cases
- **THEN** processing continues and a warning is logged (non-fatal)

### Requirement: Butler Registry Ownership
Switchboard owns the authoritative butler registry including identity, triggers, capabilities, and liveness.

#### Scenario: Registry metadata for routing
- **WHEN** the LLM routing prompt is assembled
- **THEN** it consumes registry metadata (identity, trigger conditions, required information, capability declarations) at decision time
- **AND** registry updates are reflected without code changes to router logic

#### Scenario: Available specialist butlers
- **WHEN** a message needs routing
- **THEN** the following butlers are available: `finance` (receipts, invoices, bills, subscriptions), `relationship` (contacts, interactions, reminders, gifts), `health` (medications, measurements, conditions, symptoms, diet), `travel` (flight bookings, hotel reservations, itineraries), `general` (catch-all)

### Requirement: Registry Lifecycle and Staleness
Registry membership includes liveness lifecycle behavior, not only static registration.

#### Scenario: Heartbeat-based liveness
- **WHEN** a butler has a heartbeat/last-seen timestamp
- **THEN** its eligibility is determined by TTL-based liveness checks
- **AND** stale targets are set to a non-routable state (`stale` or `quarantined`)

#### Scenario: Quarantine auto-recovery via heartbeat
- **WHEN** a quarantined butler sends a heartbeat
- **THEN** its eligibility transitions from `quarantined` to `active`
- **AND** `quarantined_at` and `quarantine_reason` are cleared
- **AND** the transition is audited with reason `heartbeat_recovery`
- **AND** a CAS guard (`WHERE eligibility_state = 'quarantined'`) prevents TOCTOU races with concurrent operator actions

#### Scenario: Quarantine cleared on re-registration
- **WHEN** a quarantined butler re-registers via `register_butler()`
- **THEN** its eligibility is unconditionally set to `active`
- **AND** `quarantined_at` and `quarantine_reason` are cleared
- **AND** the transition is audited with reason `re_registered`

#### Scenario: Stale target routing exclusion
- **WHEN** a target butler is in `stale` or `quarantined` state
- **THEN** it is not selected for new routes unless explicitly allowed by policy
- **AND** state transitions in target eligibility are traceable/auditable

#### Scenario: Eligibility sweep schedule
- **WHEN** the `eligibility-sweep` cron fires (every 5 minutes per `butler.toml`)
- **THEN** it executes in native mode directly against the Switchboard DB pool
- **AND** it bypasses runtime/LLM invocation (`spawner.trigger`)

### Requirement: Delivery Semantics
Switchboard delivery semantics are `at-least-once` at request fanout boundaries.

#### Scenario: At-least-once delivery
- **WHEN** Switchboard dispatches subrequests to downstream butlers
- **THEN** downstream butlers must tolerate duplicate subrequests for the same `request_id`/`segment_id`
- **AND** Switchboard preserves enough identity metadata for deterministic deduplication downstream

#### Scenario: No silent drops
- **WHEN** Switchboard accepts a request
- **THEN** it never silently drops that request
- **AND** every request has a durable lifecycle record if storage is available

#### Scenario: Notify dispatch via Messenger
- **WHEN** a user-facing outbound interaction is needed
- **THEN** `notify` intents are dispatched to `messenger_butler` with preserved `request_context` and `origin_butler`
- **AND** dispatch uses `route.execute` (`route.v1`) with `notify.v1` carried in `input.context.notify_request`

### Requirement: Idempotency and Deduplication Keys
Ingress deduplication is mandatory and channel-aware. Dedupe is evaluated at ingress only.

#### Scenario: Telegram dedup key
- **WHEN** a Telegram message is ingested
- **THEN** the dedup key is `update_id` + receiving bot identity

#### Scenario: Email dedup key
- **WHEN** an email message is ingested
- **THEN** the dedup key is RFC `Message-ID` + receiving mailbox identity

#### Scenario: API/MCP dedup key
- **WHEN** an API or MCP call is ingested
- **THEN** the dedup key is the caller-provided idempotency key when available
- **AND** otherwise a deterministic hash of normalized payload + source identity + bounded time window

#### Scenario: Duplicate event handling
- **WHEN** a duplicate ingress event matches an existing canonical request
- **THEN** the same canonical request reference is returned (not a new request)
- **AND** the dedup decision is logged with resolved key and action (`accepted` or `deduped`)

### Requirement: Timeout, Retry, and Circuit-Breaker Policy
Switchboard enforces bounded downstream failure behavior.

#### Scenario: Per-target route timeout
- **WHEN** a downstream route exceeds its configured timeout
- **THEN** Switchboard synthesizes a `timeout`-class terminal failure

#### Scenario: Retry policy
- **WHEN** a retryable failure class is returned
- **THEN** Switchboard retries with bounded attempts and backoff strategy
- **AND** non-retryable validation/policy failures fail fast without retry

#### Scenario: Circuit breaker
- **WHEN** a target butler exceeds failure thresholds
- **THEN** the circuit transitions to `open` and new routes fail quickly with `target_unavailable` errors
- **AND** circuit transitions (`closed`, `open`, `half-open`) are observable in structured logs/metrics

### Requirement: Backpressure and Admission Control
Switchboard protects ingress under overload.

#### Scenario: Bounded admission
- **WHEN** all tier queues are full
- **THEN** explicit overflow behavior (`shed`, `defer`, or `reject`) is applied with configured policy
- **AND** interactive sources prefer fast failure/feedback over indefinite queueing

#### Scenario: Channel fairness
- **WHEN** multiple source channels submit simultaneously under overload
- **THEN** fairness policy prevents starvation of any single channel
- **AND** admission outcomes are explicit and observable

### Requirement: Schema Versioning
Switchboard request/decomposition payloads are versioned contracts.

#### Scenario: Ingest envelope versioning
- **WHEN** a connector submits an ingest payload
- **THEN** the envelope carries `schema_version: "ingest.v1"`
- **AND** every persisted payload includes schema version metadata

#### Scenario: Route envelope versioning
- **WHEN** Switchboard dispatches to a downstream butler
- **THEN** the dispatch uses a `route.v1` envelope with `request_context`, `subrequest`, `target`, `input`, and `trace_context`

#### Scenario: Route response versioning
- **WHEN** a downstream butler responds
- **THEN** the response carries `schema_version: "route_response.v1"` with `request_context.request_id`, `status`, `result` or `error`, and `timing`

#### Scenario: Breaking schema changes
- **WHEN** a schema change is introduced
- **THEN** an explicit version bump and migration guidance are required
- **AND** parser behavior is deterministic for unknown/newer versions

### Requirement: Error Taxonomy
Switchboard uses a stable, typed error taxonomy for routing lifecycle decisions.

#### Scenario: Seven error classes
- **WHEN** a terminal failure occurs
- **THEN** it maps to one of: `classification_error`, `validation_error`, `routing_error`, `target_unavailable`, `timeout`, `overload_rejected`, `internal_error`
- **AND** `classification_error` and `routing_error` are Switchboard-owned; the remaining five are shared with downstream butlers

#### Scenario: Unknown downstream error class normalization
- **WHEN** a downstream butler returns an unrecognized error class
- **THEN** Switchboard normalizes it to `internal_error`
- **AND** the original class is preserved as non-user-facing metadata

#### Scenario: ERRORED terminal state includes error detail
- **WHEN** the interactive lifecycle reaches `ERRORED`
- **THEN** the terminal state includes the error class and an actionable message
- **AND** partial fanout failures preserve per-target error classes in persisted results

### Requirement: Persistence Surfaces
Switchboard persistence has two classes: long-term durable storage and short-lived ingress lifecycle storage.

#### Scenario: Long-term durable tables
- **WHEN** operational data is generated during request processing
- **THEN** it is persisted in long-term tables: `butler_registry`, `routing_log`, `extraction_queue`, `extraction_log`, `notifications`, `dashboard_audit_log`, and `backfill_jobs`

#### Scenario: Audit log writes
- **WHEN** a daemon-level activity occurs (e.g., session completion)
- **THEN** an audit entry is written to `dashboard_audit_log` with butler name, operation, request summary, result, and error
- **AND** audit writes are fire-and-forget (exceptions are logged and swallowed)

#### Scenario: Route inbox lifecycle
- **WHEN** a `route.execute` is called on a downstream butler
- **THEN** the request is persisted in `route_inbox` with lifecycle states: `accepted` -> `processing` -> `processed` or `errored`
- **AND** on startup, the daemon scans for rows in `accepted` or `processing` state and re-dispatches them (crash recovery with grace period)

#### Scenario: Connector persistence surfaces
- **WHEN** connector heartbeats and statistics are processed
- **THEN** data is persisted in `connector_registry` (never auto-pruned), `connector_heartbeat_log` (7-day retention, month-partitioned), `connector_stats_hourly` (30-day retention), `connector_stats_daily` (1-year retention), and `connector_fanout_daily` (1-year retention)

### Requirement: Backfill Jobs Table Contract
Switchboard owns the canonical backfill orchestration table for MCP-mediated connector backfill.

#### Scenario: Backfill job creation
- **WHEN** a dashboard API handler calls `create_backfill_job` via Switchboard MCP
- **THEN** a row is created in `switchboard.backfill_jobs` with `status=pending`, `connector_type`, `endpoint_identity`, `target_categories`, `date_from`, `date_to`, `rate_limit_per_hour` (default 100), and `daily_cost_cap_cents` (default 500)
- **AND** the `(connector_type, endpoint_identity)` is validated to exist in `connector_registry` and be currently online

#### Scenario: Backfill job status transitions
- **WHEN** a backfill job lifecycle event occurs
- **THEN** the allowed status values are: `pending`, `active`, `paused`, `completed`, `cancelled`, `cost_capped`, `error`
- **AND** `backfill.resume` is valid only from `paused` or `cost_capped` states

### Requirement: Dashboard-Facing Backfill MCP Tools
Dashboard API handlers control backfill lifecycle through Switchboard MCP tools.

#### Scenario: Create backfill job
- **WHEN** `create_backfill_job(connector_type, endpoint_identity, target_categories, date_from, date_to, rate_limit_per_hour?, daily_cost_cap_cents?)` is called
- **THEN** a new `backfill_jobs` row is created with `status=pending`
- **AND** the response includes `{job_id, status}`

#### Scenario: Pause backfill job
- **WHEN** `backfill.pause(job_id)` is called
- **THEN** the job status transitions to `paused`

#### Scenario: Cancel backfill job
- **WHEN** `backfill.cancel(job_id)` is called
- **THEN** the job status transitions to `cancelled`

#### Scenario: Resume backfill job
- **WHEN** `backfill.resume(job_id)` is called from `paused` or `cost_capped` state
- **THEN** the job status transitions to `pending` for re-queueing

#### Scenario: List backfill jobs
- **WHEN** `backfill.list(connector_type?, endpoint_identity?, status?)` is called
- **THEN** matching jobs are returned with full summary fields including `job_id`, `connector_type`, `endpoint_identity`, `target_categories`, `date_from`, `date_to`, `rate_limit_per_hour`, `daily_cost_cap_cents`, `status`, `rows_processed`, `rows_skipped`, `cost_spent_cents`, `error`, timestamps

### Requirement: Connector-Facing Backfill MCP Tools
Connector processes coordinate backfill execution through Switchboard MCP tools.

#### Scenario: Poll for pending backfill job
- **WHEN** `backfill.poll(connector_type, endpoint_identity)` is called
- **THEN** the oldest `pending` job for that connector identity is returned with `{job_id, params, cursor}`, or `null` when none is available
- **AND** on assignment, the job transitions to `active` and `started_at` is initialized

#### Scenario: Report backfill progress
- **WHEN** `backfill.progress(job_id, rows_processed, rows_skipped, cost_spent_cents, cursor?, status?, error?)` is called
- **THEN** counters are updated using per-batch deltas and the current authoritative job status is returned
- **AND** if `cost_spent_cents >= daily_cost_cap_cents`, Switchboard sets `status=cost_capped` and returns `cost_capped`

### Requirement: Connector Heartbeat Ingestion
Switchboard owns the connector heartbeat ingestion boundary.

#### Scenario: Heartbeat acceptance
- **WHEN** a `connector.heartbeat.v1` envelope is received via `connector.heartbeat` MCP tool
- **THEN** Switchboard upserts `connector_registry` with latest state, counters, and checkpoint
- **AND** appends to `connector_heartbeat_log` for historical tracking

#### Scenario: Connector self-registration on first heartbeat
- **WHEN** Switchboard receives a heartbeat from an unknown `(connector_type, endpoint_identity)` pair
- **THEN** a new connector record is created in `connector_registry` with `first_seen_at` set to current time and `registered_via=self`

#### Scenario: Liveness derivation from heartbeat recency
- **WHEN** connector liveness is evaluated
- **THEN** `online` is derived when last heartbeat < 2 minutes ago, `stale` when 2-4 minutes, `offline` when > 4 minutes
- **AND** Switchboard never auto-deregisters connectors; cleanup is an operator action

#### Scenario: Non-blocking heartbeat processing
- **WHEN** heartbeats are received
- **THEN** they are processed asynchronously and do not block the ingestion path

### Requirement: Connector Statistics and Aggregation
Switchboard owns pre-aggregated connector statistics derived from heartbeat logs and `message_inbox` routing outcomes.

#### Scenario: Hourly statistics rollup
- **WHEN** the `connector-stats-hourly-rollup` job fires (cron `5 * * * *`)
- **THEN** counter deltas between consecutive heartbeats are computed for each connector
- **AND** results are upserted into `connector_stats_hourly` with volume (messages_ingested, messages_failed, source_api_calls, dedupe_accepted) and health (heartbeat_count, healthy_count, degraded_count, error_count)

#### Scenario: Daily statistics rollup
- **WHEN** the `connector-stats-daily-rollup` job fires (cron `15 0 * * *`)
- **THEN** `connector_stats_hourly` rows for the previous day are summed into `connector_stats_daily`
- **AND** `uptime_pct` is computed as `healthy_count / heartbeat_count * 100`

#### Scenario: Fanout distribution rollup
- **WHEN** the daily rollup job runs
- **THEN** `message_inbox` rows from the previous day are grouped by `(source_channel, source_endpoint_identity, target_butler)` from `dispatch_outcomes`
- **AND** results are upserted into `connector_fanout_daily`

#### Scenario: Statistics pruning
- **WHEN** the `connector-stats-pruning` job fires (cron `30 1 * * *`)
- **THEN** `connector_heartbeat_log` partitions older than 7 days are dropped, `connector_stats_hourly` rows older than 30 days are deleted, and `connector_stats_daily`/`connector_fanout_daily` rows older than 1 year are deleted
- **AND** pruning logs what was removed (row counts, date ranges)

#### Scenario: Rollup idempotency
- **WHEN** a rollup job is re-run
- **THEN** it produces the same result (idempotent upsert)

### Requirement: Canonical Ingest Event Shape
All sources submit through the same canonical ingest contract.

#### Scenario: Ingest envelope structure
- **WHEN** a connector submits an event
- **THEN** the envelope follows `ingest.v1` schema with `source` (channel, provider, endpoint_identity), `event` (external_event_id, external_thread_id, observed_at), `sender` (identity), `payload` (raw, normalized_text), and `control` (idempotency_key, trace_context, policy_tier)

#### Scenario: Ingestion API response
- **WHEN** an ingest is accepted
- **THEN** the response is `202 Accepted` with canonical `request_id`
- **AND** duplicate submissions for the same dedupe identity return the same canonical request reference

#### Scenario: Canonical route envelope shape
- **WHEN** Switchboard dispatches to a downstream butler
- **THEN** the `route.v1` envelope includes `request_context` (request_id, received_at, source fields), `subrequest` (subrequest_id, segment_id, fanout_mode), `target` (butler, tool=route.execute), `input` (prompt), and `trace_context`

### Requirement: Switchboard Butler Configuration
The switchboard butler has specific runtime, module, and buffer configuration.

#### Scenario: Butler identity and runtime
- **WHEN** the switchboard butler starts
- **THEN** it runs on port 40100 with max 3 concurrent sessions
- **AND** the runtime type is `codex` with model `gpt-5.3-codex-spark`
- **AND** the database schema is `switchboard` in the `butlers` database

#### Scenario: Enabled modules
- **WHEN** the switchboard butler starts
- **THEN** the following modules are enabled: `calendar` (Google provider, suggest conflicts policy), `telegram` (bot-only, user disabled), `email` (bot-only, user disabled), and `memory`

#### Scenario: Buffer configuration
- **WHEN** the DurableBuffer is initialized
- **THEN** it uses `queue_capacity=100`, `worker_count=3`, `scanner_interval_s=30`, `scanner_grace_s=10`, `scanner_batch_size=50`

### Requirement: Switchboard Scheduled Tasks
The switchboard runs six native-mode cron jobs for statistics, memory, and registry maintenance.

#### Scenario: Scheduled task inventory
- **WHEN** the switchboard daemon is running
- **THEN** it executes six scheduled jobs: `connector-stats-hourly-rollup` (`5 * * * *`), `connector-stats-daily-rollup` (`15 0 * * *`), `connector-stats-pruning` (`30 1 * * *`), `memory-consolidation` (`0 */6 * * *`), `memory-episode-cleanup` (`0 4 * * *`), and `eligibility-sweep` (`*/5 * * * *`)
- **AND** all are dispatched as native jobs via `dispatch_mode = "job"`

#### Scenario: Native mode execution
- **WHEN** a schedule has a native handler
- **THEN** execution runs directly against the Switchboard DB pool
- **AND** runtime/LLM invocation is bypassed

#### Scenario: Runtime mode fallback for schedules
- **WHEN** a Switchboard schedule has no native handler
- **THEN** it falls back to runtime mode through `spawner.trigger(prompt, trigger_source="schedule:<task-name>")`

### Requirement: Switchboard Skills
The switchboard has two specialized skills for message triage and relationship extraction.

#### Scenario: Skill inventory
- **WHEN** the switchboard operates
- **THEN** it has access to `message-triage` (classification and routing with confidence scoring) and `relationship-extractor` (structured relationship data extraction for 8 signal types: contact, interaction, life_event, date, fact, sentiment, gift, loan)

### Requirement: [TARGET-STATE] Ambiguity Resolution Contract
Switchboard routing behavior under ambiguity.

#### Scenario: Low-confidence routing fallback
- **WHEN** routing confidence is below the configured threshold
- **THEN** the request is routed to `general` without a clarification round-trip
- **AND** the fallback is observable and tagged in lifecycle records

### Requirement: [TARGET-STATE] Routing Precedence Rules
Routing decisions are deterministic under mixed policy inputs.

#### Scenario: Policy over LLM discretion
- **WHEN** hard policy/rule constraints conflict with LLM routing output
- **THEN** hard policy constraints take precedence
- **AND** registry eligibility and safety constraints are applied before final target selection

### Requirement: [TARGET-STATE] Conflict Arbitration Contract
Deterministic handling when downstream outputs conflict.

#### Scenario: Conflict detection and arbitration
- **WHEN** incompatible outcomes are returned from multiple downstream targets
- **THEN** the deterministic winner is selected by: highest explicit arbitration priority, then lexical butler name, then lexical subrequest id
- **AND** user-facing responses disclose unresolved conflicts when no deterministic winner exists

### Requirement: [TARGET-STATE] Fanout Dependency Model
Fanout execution supports explicit dependency semantics.

#### Scenario: Parallel fanout mode
- **WHEN** fanout mode is `parallel`
- **THEN** independent subroutes run concurrently

#### Scenario: Ordered fanout mode
- **WHEN** fanout mode is `ordered`
- **THEN** subroutes execute in defined order

#### Scenario: Conditional fanout mode
- **WHEN** fanout mode is `conditional`
- **THEN** downstream subroutes run only if upstream conditions succeed
- **AND** join policy and abort policy are explicit per fanout plan

### Requirement: [TARGET-STATE] Partial-Success Response Policy
Stable user-facing behavior for mixed outcomes.

#### Scenario: Mixed success and failure
- **WHEN** some subroutes succeed and others fail
- **THEN** successes are acknowledged and failed targets are surfaced with actionable error class/message
- **AND** terminal state remains `ERRORED` when any required subroute fails

### Requirement: [TARGET-STATE] Dead-Letter and Replay Contract
Non-terminally recoverable failures are captured for controlled replay.

#### Scenario: Dead-letter capture
- **WHEN** failed requests/subrequests exceed retry policy
- **THEN** they move to a dead-letter surface

#### Scenario: Replay with lineage preservation
- **WHEN** a dead-lettered request is replayed
- **THEN** the original `request_id` lineage is preserved and replay is idempotent
- **AND** replay actions are audited (who, when, why, result)

### Requirement: [TARGET-STATE] Per-Request Budget Contract
Bounded execution budgets per request.

#### Scenario: Budget dimensions
- **WHEN** a request is processed
- **THEN** wall-clock latency budget, model/tool invocation budget, and optional cost/token budget are enforced
- **AND** budget exhaustion produces explicit terminal errors

### Requirement: [TARGET-STATE] Source/Urgency Policy Contract
Policy differentiation by ingress source and urgency.

#### Scenario: Policy tier differentiation
- **WHEN** a request arrives with a specific `policy_tier` (default, interactive, high_priority)
- **THEN** timeout, retry, model tier, and fanout strictness may vary by tier
- **AND** policy selection is deterministic and observable in request metadata

### Requirement: [TARGET-STATE] Capability Compatibility Checks
Dispatch compatibility validation before routing.

#### Scenario: Pre-dispatch capability validation
- **WHEN** Switchboard plans a subroute to a target butler
- **THEN** it validates the target advertises the required capability/tool
- **AND** compatibility failures are classified as `validation_error`, not transport errors

### Requirement: [TARGET-STATE] Prompt and Model Rollout Policy
Router prompts and models are versioned and rollout-controlled.

#### Scenario: Prompt/model versioning per request
- **WHEN** a routing decision is made
- **THEN** prompt version and model version are recorded per request
- **AND** rollouts support canary phases and deterministic rollback tied to routing quality and error-rate signals

### Requirement: [TARGET-STATE] Quality Drift Monitoring
Routing quality monitoring for regression detection.

#### Scenario: Quality drift dimensions
- **WHEN** routing quality is evaluated over time
- **THEN** monitored dimensions include target selection correctness, decomposition quality stability, fallback-to-general rate, and partial/total failure rate by source and target butler
- **AND** drift thresholds trigger alerts/escalation and metrics are sliceable by model/prompt version

### Requirement: [TARGET-STATE] Human Override and Operator Controls
Supervised intervention paths for operations.

#### Scenario: Operator override capabilities
- **WHEN** an operator intervenes in a request lifecycle
- **THEN** available actions include manual reroute, cancel/abort in-flight, controlled retry/replay, and force-complete with annotation
- **AND** all overrides are auditable, attributable, and reflected in final lifecycle records

### Requirement: [TARGET-STATE] Ordering and Causality Contract
Explicit message ordering guarantees.

#### Scenario: Per-thread causal ordering
- **WHEN** messages arrive from the same source thread
- **THEN** per-source-thread causal ordering is preserved when channel identity supports it
- **AND** cross-thread global ordering is not guaranteed

### Requirement: [TARGET-STATE] Channel-Facing Tool Ownership
Outbound channel-delivery tool surfaces are owned by `messenger_butler`.

#### Scenario: Ownership enforcement
- **WHEN** a non-messenger butler exposes direct user-channel delivery tools
- **THEN** Switchboard rejects or quarantines those tool surfaces
- **AND** non-messenger butlers must use `notify.v1` intents routed through Switchboard

### Requirement: [TARGET-STATE] SLO/SLI and Error Budget Contract
Operational SLOs with explicit error budgets.

#### Scenario: Minimum SLI tracking
- **WHEN** Switchboard is running
- **THEN** it tracks ingress acceptance latency, end-to-end fanout completion latency, route success rate, and interactive terminal-state latency (PROGRESS to PARSED/ERRORED)

### Requirement: [TARGET-STATE] Observability Contract
OpenTelemetry metrics and traces as first-class runtime outputs.

#### Scenario: Core metrics emitted
- **WHEN** messages are processed
- **THEN** counters (message_received, message_deduplicated, subroute_dispatched, etc.), histograms (ingress_accept_latency_ms, routing_decision_latency_ms, etc.), and gauges (queue_depth, inflight_requests, circuit_open_targets) are emitted under `butlers.switchboard.*` namespace

#### Scenario: DurableBuffer metrics
- **WHEN** the DurableBuffer processes messages
- **THEN** it emits `butlers.buffer.queue_depth` (gauge), `butlers.buffer.enqueue_total` (counter, path=hot|cold), `butlers.buffer.backpressure_total` (counter), `butlers.buffer.scanner_recovered_total` (counter), `butlers.buffer.process_latency_ms` (histogram), and `butlers.switchboard.queue.dequeue_by_tier` (counter, with policy_tier and starvation_override labels)

### Requirement: Connector Dashboard API
Switchboard connector state and statistics are exposed via core dashboard API endpoints.

#### Scenario: [TARGET-STATE] List connectors endpoint
- **WHEN** `GET /api/connectors` is called
- **THEN** all known connectors are returned with liveness (derived from `last_heartbeat_at`), state, version, uptime, and today's summary

#### Scenario: [TARGET-STATE] Connector detail endpoint
- **WHEN** `GET /api/connectors/{connector_type}/{endpoint_identity}` is called
- **THEN** full detail is returned including instance_id, registered_via, checkpoint, and all counters

#### Scenario: [TARGET-STATE] Connector statistics endpoint
- **WHEN** `GET /api/connectors/{connector_type}/{endpoint_identity}/stats?period=24h|7d|30d` is called
- **THEN** time-series volume and health statistics are returned with hourly buckets (24h/7d from `connector_stats_hourly`) or daily buckets (30d from `connector_stats_daily`)

#### Scenario: [TARGET-STATE] Cross-connector summary endpoint
- **WHEN** `GET /api/connectors/summary?period=24h|7d|30d` is called
- **THEN** aggregate statistics across all connectors are returned with online/stale/offline counts and per-connector breakdown

#### Scenario: [TARGET-STATE] Fanout distribution endpoint
- **WHEN** `GET /api/connectors/fanout?period=7d|30d` is called
- **THEN** the connector-to-butler routing distribution matrix is returned from pre-aggregated `connector_fanout_daily`
