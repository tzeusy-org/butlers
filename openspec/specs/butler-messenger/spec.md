# Messenger Staffer Role

## Purpose
The Messenger (port 41104) is the outbound delivery execution plane for Telegram, Email, and WhatsApp (WhatsApp tools are registered but disabled by default via `send_enabled = false`, pending account ban-risk assessment). It is a staffer, an infrastructure agent that serves the ecosystem by owning all outbound channel delivery. It does not perform classification or domain logic.

## ADDED Requirements

### Requirement: Messenger Butler Identity and Runtime
The messenger is a staffer — a delivery-only execution plane with no domain logic. It serves the ecosystem by owning all outbound channel delivery.

#### Scenario: Identity and port
- **WHEN** the messenger daemon starts
- **THEN** it operates on port 41104 with description "Outbound delivery execution plane for Telegram and Email"
- **AND** it also enables the `whatsapp` module with `send_tools = true` and `send_enabled = false` (tools registered but functionally disabled by default)
- **AND** its `butler.toml` has `type = "staffer"`
- **AND** it uses the `codex` runtime adapter with a maximum of 3 concurrent sessions
- **AND** its database schema is `messenger` within the consolidated `butlers` database

#### Scenario: Module profile
- **WHEN** the messenger daemon starts
- **THEN** it loads modules: `calendar` (Google provider, suggest conflicts policy), `telegram` (bot-only, user disabled, token from `BUTLER_TELEGRAM_TOKEN`), and `email` (bot-only, user disabled, address from `BUTLER_EMAIL_ADDRESS`, password from `BUTLER_EMAIL_PASSWORD`)

#### Scenario: Cross-butler permissions
- **WHEN** the messenger daemon starts
- **THEN** its `butler.toml` declares `[butler.permissions]` with `cross_butler_access = ["*"]`
- **AND** the messenger is authorized to act on behalf of any butler for outbound delivery

#### Scenario: Messenger excluded from user-message routing
- **WHEN** the switchboard classifies an incoming user message
- **THEN** the messenger SHALL NOT be a routing candidate because `type = "staffer"`

#### Scenario: Messenger excluded from daily briefing
- **WHEN** the daemon syncs scheduled tasks at startup
- **THEN** the messenger SHALL NOT register any `daily_briefing_contribution` job
- **AND** the briefing aggregation SHALL NOT attempt to collect a contribution from the messenger

### Requirement: Messenger Channel Ownership
The messenger butler owns all external user-channel delivery tools. No other butler may call channel send/reply tools directly.

#### Scenario: Channel tool surface
- **WHEN** the messenger butler receives a `notify.v1` delivery intent
- **THEN** it executes delivery through its owned channel tools: `telegram_send_message`, `telegram_reply_to_message`, `telegram_react_to_message`, `email_send_message`, `email_reply_to_thread`, and (when enabled) `whatsapp_send_message`, `whatsapp_reply_to_message`
- **AND** non-messenger butlers must never call channel send/reply tools directly

#### Scenario: Delivery validation and lineage
- **WHEN** processing a delivery request
- **THEN** the messenger validates the `notify.v1` envelope, resolves destination and channel intent (`send` vs `reply`), preserves `origin_butler` and `request_context` lineage, and returns deterministic status/error payloads
- **AND** it must not recursively call `notify` for outbound sends

### Requirement: Approval-Gated Delivery
Sensitive delivery tools require approval before execution.

#### Scenario: Gated tools
- **WHEN** the messenger butler starts with the `approvals` module enabled
- **THEN** the following tools are gated by the approval subsystem: `telegram_send_message`, `telegram_reply_to_message`, `telegram_react_to_message`, `email_send_message`, `email_reply_to_thread`, `whatsapp_send_message`, `whatsapp_reply_to_message`, and `notify`
- **AND** the LLM CLI must obtain approval before invoking these tools in production

### Requirement: Idempotent Delivery Requests
Every delivery request has a deterministic idempotency key derived from its content, preventing duplicate deliveries.

#### Scenario: Key derivation
- **WHEN** a `notify.v1` envelope arrives
- **THEN** a canonical idempotency key is derived from `request_id`, `origin_butler`, `intent`, `channel`, `target_identity`, and a SHA-256 hash of message content
- **AND** input fields are normalized (lowercased, trimmed) before hashing

#### Scenario: Duplicate detection
- **WHEN** a delivery request arrives with an idempotency key matching an existing delivery
- **THEN** the existing delivery status is returned (terminal result for completed, current status for in-flight)
- **AND** no new delivery request is created

#### Scenario: Replay lineage
- **WHEN** a dead letter is replayed
- **THEN** the new delivery preserves the original idempotency key with a `::replay-N` suffix
- **AND** the dead letter's replay count is incremented

### Requirement: Rate Limiting
A three-tier rate limiter controls delivery admission to prevent overload and per-recipient flooding.

#### Scenario: Global admission control
- **WHEN** a delivery request is admitted
- **THEN** it is checked against global rate (default 60/min) and global in-flight (default 100) limits
- **AND** exceeding either limit rejects with `overload_rejected` error class and a `retry_after_seconds` hint

#### Scenario: Channel-level limits
- **WHEN** global admission passes
- **THEN** channel+identity-scope limits are applied (telegram.bot: 30/min, email.bot: 20/min, etc.)
- **AND** reply intents consume fewer tokens (divided by a priority multiplier, default 2.0)

#### Scenario: Per-recipient anti-flood
- **WHEN** channel admission passes
- **THEN** per-recipient rate limits (default 10/min) prevent flooding a single recipient
- **AND** rejection returns `overload_rejected` with retry hint

#### Scenario: Provider throttle passthrough
- **WHEN** a provider reports a throttle (e.g., HTTP 429)
- **THEN** the rate limiter records the throttle and rejects subsequent admissions for that channel until the retry window expires

### Requirement: Circuit Breaker
Per-channel circuit breakers prevent cascading failures when a delivery provider is unhealthy.

#### Scenario: Closed to open transition
- **WHEN** a channel accumulates consecutive failures exceeding the failure threshold (default 5)
- **THEN** the circuit opens and all subsequent deliveries to that channel are rejected with `CircuitOpenError`
- **AND** validation errors are never counted as failures

#### Scenario: Recovery via half-open
- **WHEN** the recovery timeout (default 60s) elapses after circuit opening
- **THEN** the circuit transitions to half-open, admitting limited probe requests
- **AND** if probes succeed (default 2 successes), the circuit closes; otherwise it re-opens

#### Scenario: Circuit status visibility
- **WHEN** the `messenger_circuit_status` tool is called
- **THEN** it returns per-channel state (closed/open/half-open), consecutive failures, trip reason, and recovery config

### Requirement: Retry with Exponential Backoff
Failed delivery attempts are retried with exponential backoff and jitter.

#### Scenario: Retry policy
- **WHEN** a delivery attempt fails with a retryable error
- **THEN** retries occur up to `max_attempts` (default 3) with exponential backoff: `base_delay * 2^(retry - 1)`, capped at `max_delay` (default 60s), with jitter (default 0.3 factor)

#### Scenario: Non-retryable errors
- **WHEN** a delivery fails with `validation_error` or `internal_error`
- **THEN** no retry is attempted and the failure is immediately terminal

#### Scenario: Per-channel timeouts
- **WHEN** a delivery attempt is dispatched
- **THEN** a per-channel timeout is applied (telegram: 15s, email: 45s, default: 30s)

### Requirement: Dead Letter Management
Deliveries that exhaust all retries are quarantined for operator review and optional replay.

#### Scenario: Dead letter listing
- **WHEN** the `messenger_dead_letter_list` tool is called
- **THEN** it returns paginated dead letters (newest first) with quarantine reason, error class, attempt count, and replay eligibility
- **AND** results can be filtered by channel, origin butler, and error class

#### Scenario: Dead letter inspection
- **WHEN** the `messenger_dead_letter_inspect` tool is called with a dead letter ID
- **THEN** it returns the full record including original request envelope, all attempt outcomes, and a replay eligibility assessment

#### Scenario: Dead letter replay
- **WHEN** the `messenger_dead_letter_replay` tool is called
- **THEN** a new delivery request is created with status "pending", preserving idempotency key lineage
- **AND** only replay-eligible, non-discarded dead letters can be replayed

#### Scenario: Dead letter discard
- **WHEN** the `messenger_dead_letter_discard` tool is called with a reason
- **THEN** the dead letter is marked as discarded (replay-ineligible) with the given reason
- **AND** discarded dead letters are excluded from list queries by default

### Requirement: Delivery Tracking and Tracing
Full delivery lifecycle is observable through tracking and tracing tools.

#### Scenario: Delivery status lookup
- **WHEN** the `messenger_delivery_status` tool is called with a delivery ID
- **THEN** it returns current status, timestamps, latest attempt details (latency, outcome, error), and provider delivery ID when available

#### Scenario: Delivery search
- **WHEN** the `messenger_delivery_search` tool is called
- **THEN** it returns paginated delivery summaries filtered by origin butler, channel, intent, status, and time range

#### Scenario: Attempt log
- **WHEN** the `messenger_delivery_attempts` tool is called with a delivery ID
- **THEN** it returns the ordered list of all attempts with latency, outcome, error class, and provider response

#### Scenario: End-to-end trace
- **WHEN** the `messenger_delivery_trace` tool is called with a request ID
- **THEN** it returns the full delivery lineage: all delivery requests for that upstream request, each enriched with attempts and provider receipts

### Requirement: Operational Health Monitoring
The messenger exposes operational health tools for envelope validation, dry-run testing, and delivery pipeline observability.

#### Scenario: Envelope validation
- **WHEN** the `messenger_validate_notify` tool is called
- **THEN** it validates a `notify.v1` envelope and returns structured field-level errors without side effects

#### Scenario: Dry run
- **WHEN** the `messenger_dry_run` tool is called
- **THEN** it runs full validation, resolves target identity and channel adapter, checks rate limit headroom, and reports whether the request would be admitted — without executing delivery or persisting state

#### Scenario: Queue depth monitoring
- **WHEN** the `messenger_queue_depth` tool is called
- **THEN** it returns counts of in-flight deliveries (pending + in_progress), broken down by status and optionally by channel

#### Scenario: Delivery statistics
- **WHEN** the `messenger_delivery_stats` tool is called
- **THEN** it returns aggregated metrics (success rate, failure count, dead-lettered count, p50/p95 latency, retry rate) over a configurable time window with optional grouping by channel, intent, origin butler, outcome, or error class

### Requirement: Messenger Has No Schedules or Skills
The messenger butler is a pure delivery executor with no autonomous behavior.

#### Scenario: No scheduled tasks
- **WHEN** the messenger butler daemon is running
- **THEN** it has no `[[butler.schedule]]` entries and does not execute any cron-driven tasks

#### Scenario: No custom skills
- **WHEN** the messenger butler operates
- **THEN** it has no butler-specific skills directory; it relies solely on its core tool surface and channel modules

### Requirement: Messenger Infrastructure Contract
The messenger's MANIFESTO.md uses infrastructure-contract framing rather than user-relationship framing.

#### Scenario: Infrastructure contract content
- **WHEN** the messenger's `MANIFESTO.md` is authored
- **THEN** it defines: service responsibilities (outbound delivery ownership), SLAs (delivery latency, availability), failure modes (channel unavailable, rate limiting, auth failures), recovery procedures (retry, fallback, escalation), dependency graph (depends on switchboard for routing, telegram/email APIs for delivery), and capacity limits (concurrent sessions, queue depth)
