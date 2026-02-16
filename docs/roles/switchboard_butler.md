# Switchboard Butler: Permanent Definition

Status: Normative
Last updated: 2026-02-16
Primary owner: Platform/Core

## 1. Role
The Switchboard Butler is the single ingress and orchestration control plane for the butler system.

All external interactions start here. Switchboard receives incoming data, assigns canonical request context, decides routing using an LLM runtime, fans out work to downstream butlers, and records the full request lifecycle.

This document is the source of truth for what Switchboard must do.

## 2. Design Goals
- One entrypoint for all channels and callers.
- Deterministic, durable request context across fanout.
- Safe LLM-driven routing with prompt-injection resistance.
- High-throughput asynchronous ingestion from concurrent sources.
- Clear user-visible lifecycle state for interactive channels.
- Durable but bounded retention of ingress content and routing outcomes.

## 2.1 Base Contract Overrides
Inherits unchanged:
- All clauses in `docs/roles/base_butler.md` apply unless explicitly listed in `Overrides`.

Overrides:
- `base_clause`: `6. Core Tool Surface Contract / notify`; `override`: For Switchboard, `notify` is not a self-routed outbound path. Switchboard is the `notify.v1` control-plane termination point and must validate/dispatch notify envelopes to `messenger_butler` delivery handlers.; `rationale`: Avoid ambiguous self-routing semantics and establish a single enforcement point for outbound delivery policy.
- `base_clause`: `11. User interaction and delivery contract / non-messenger butlers request outbound interaction via notify`; `override`: This clause applies to non-switchboard butlers only. Switchboard consumes `notify.v1` requests and performs dispatch/orchestration; it does not recursively invoke its own outbound notify path.; `rationale`: Clarifies role boundary between requestors and orchestrator.

Additions:
- This role defines ingress ownership, routing orchestration, and registry control-plane behavior that are stricter than base requirements for non-switchboard roles.

## 3. Scope and Boundaries
### In scope
- Ingress identity/context assignment.
- Registry ownership and runtime discovery.
- LLM-based message decomposition and routing decisions.
- Fanout to one or more downstream butlers.
- Delivery lifecycle signaling and user-visible status.
- Request/audit persistence in Switchboard-owned storage.

### Out of scope
- Specialist domain logic (health, relationship, etc.).
- Specialist persistence schemas.
- UI policy beyond explicit lifecycle/error signaling contracts.

## 4. Request Context Contract (Mandatory)
Every ingress request MUST receive a canonical request context before any routing decision.

Required fields:
- `request_id`: UUID7.
- `received_at`: UTC timestamp.
- `source_channel`: for example `telegram`, `email`, `mcp`, `api`.
- `source_endpoint_identity`: ingress identity that received the message.
- `source_sender_identity`: actor who sent the message.

Optional fields:
- `source_thread_identity`: conversation/thread identifier when available; otherwise `null`.
- `trace_context`: OTEL propagation payload when available; otherwise absent or `null`.

Channel-specific minimum identity requirements:
- Telegram: bot identity and source username/user-id.
- Email: receiving mailbox identity and RFC `From` identity.

Propagation rule:
- Context MUST be forwarded to all downstream butlers for every routed sub-request.
- If fanout occurs, all sub-requests share the same `request_id` and source context.
- Sub-request metadata can add `subrequest_id` and `segment_id` but cannot replace root context.

## 5. Ingestion and Retention Contract
Switchboard persists canonical ingress payloads in short-lived storage and projects operational/audit outcomes to long-term durable storage.

Retention model:
- PostgreSQL month-partitioned table family.
- Partitions by `received_at` month.
- Retention target: 1 month of hot data unless policy override exists.

Stored artifacts per request:
- Canonical request context.
- Raw incoming payload.
- Normalized message content used for routing.
- LLM routing/decomposition output.
- Downstream dispatch outcomes (per target success/failure and response/error).
- Aggregated user-facing reply or terminal error.
- Final lifecycle state.

Operational requirements:
- Partition creation must be automated.
- Expired partitions must be dropped on schedule.
- Table/index design must support recent-first operational queries.

## 6. Routing Contract
Switchboard performs discretionary routing through a pluggable LLM CLI runtime.

Supported runtime family:
- Claude Code.
- Codex.
- Opencode.

Model tier intent:
- Use lightweight, capable models for fast classification/decomposition loops.

### 6.1 Prompt Injection Safety Requirements
Ingress content is always untrusted.

Mandatory controls:
- User content passed as isolated data payload, not executable instructions.
- Router prompt explicitly forbids obeying instructions inside user content.
- Router output constrained to strict schema.
- Output validated against registry-known butlers only.
- Invalid/malformed output triggers safe fallback.

Fallback requirement:
- On parse/validation/runtime failure, route the full request to `general` as fail-safe.

### 6.2 Decomposition Semantics
Router output supports one-to-many fanout with possible overlap.

Requirements:
- One request may produce multiple target segments.
- Segments may overlap in content when intentionally needed.
- Each segment must carry self-contained prompt text plus segment metadata.
- Segment metadata should include at least one of: sentence span references, character offset ranges, or explicit decomposition rationale.

Routing execution:
- Switchboard calls downstream tools via its `route` interface.
- `route` injects request context and trace metadata before dispatch.
- Routed dispatch to downstream non-switchboard butlers uses their `route.execute` entrypoint. Per base contract, downstream sessions should persist this path as `trigger_source="trigger"` with routed lineage carried in `request_context`.

### 6.3 Downstream Route Response Consumption
Switchboard is the canonical consumer of downstream `route_response.v1` envelopes.

Minimum accepted downstream response shape:
- `schema_version` = `route_response.v1`
- `request_context.request_id`
- `status` = `ok|error`
- `result` on success
- `error.class`, `error.message`, and `error.retryable` on failure
- `timing.duration_ms`

Consumption rules:
- `request_context.request_id` in the response must match the dispatched request lineage.
- Unknown response schema versions must fail deterministically as `validation_error`.
- Missing/invalid required response fields must fail deterministically as `validation_error`.
- If no response arrives before route timeout, Switchboard must synthesize a timeout-class terminal failure.
- If transport fails before a valid envelope is returned, Switchboard must synthesize `target_unavailable` (or `timeout` when timeout-class).
- Raw downstream response payloads and normalized failure class must both be persisted for auditability.

## 7. Asynchronous Ingestion Contract
Switchboard must support multiple simultaneous ingress channels without blocking ingestion.

Requirements:
- Ingress acceptance must be non-blocking with bounded work admission.
- Routing/dispatch work should execute asynchronously from transport ingestion loops.
- Per-request lifecycle must be durable even if workers fail/restart.
- Idempotency strategy required for duplicate delivery events.

Concurrency guarantees:
- Concurrent requests from independent channels must not starve each other.
- A long-running route for one request must not block new request acceptance.

## 8. Interactive Lifecycle Contract
For interactive channels, Switchboard must emit user-visible lifecycle states.

Canonical states:
- `PROGRESS`: message received and processing started.
- `PARSED`: fanout completed and all downstream targets succeeded.
- `ERRORED`: at least one downstream target failed or terminal processing failed.

State transition contract:
- Received message -> `PROGRESS`.
- Fanout complete and all success -> `PARSED`.
- Any target failure -> `ERRORED`.

Error disclosure rule:
- On `ERRORED`, Switchboard must send a user-visible error reply containing actionable failure context.

Telegram lifecycle mapping:
- `PROGRESS` -> `:eye`.
- `PARSED` -> `:done`.
- `ERRORED` -> `:space invader`.

## 9. Registry Ownership Contract
Switchboard owns the authoritative butler registry.

Each registered butler must provide:
- Interface-level identity and endpoint details.
- Trigger conditions in natural language.
- Required information in natural language.
- Capability/module declarations.

Runtime behavior:
- LLM routing prompts consume registry metadata at decision time.
- Registry metadata is treated as policy input for decomposition and target selection.
- Registry updates must be reflected without code changes to router logic.

Integration note:
- Butler interface details are defined in `docs/roles/base_butler.md`.

## 10. Additional Core Specifications
### 10.1 Delivery Semantics
Switchboard delivery semantics are `at-least-once` at request fanout boundaries.

Implications:
- Downstream butlers must tolerate duplicate subrequests for the same `request_id`/`segment_id`.
- Switchboard must preserve enough identity metadata to allow deterministic deduplication downstream.
- Switchboard must never silently drop accepted requests.
- User-facing outbound channel delivery is centralized through `messenger_butler`.
- `notify` intents for external user interaction must be dispatched to `messenger_butler` with preserved `request_context` and `origin_butler`.
- Dispatch transport to Messenger must use `route.execute` (`route.v1`) with `notify.v1` carried in `input.context.notify_request`.
- Switchboard should consume Messenger `route_response.v1` and, when present, treat `result.notify_response` as the canonical normalized delivery result.
- Direct specialist-to-channel delivery bypassing `messenger_butler` must be rejected or blocked by policy.

### 10.2 Idempotency and Deduplication Keys
Ingress deduplication is mandatory and channel-aware.

Authoritative dedupe decision point:
- Dedupe is evaluated at ingress only.
- Accepted canonical requests are routed exactly once per ingress-accepted request plan.
- Route-level dedupe is not a separate decision layer in default policy.

Canonical dedup keys:
- Telegram: update identifier (`update_id`) plus receiving bot identity.
- Email: RFC `Message-ID` plus receiving mailbox identity.
- API/MCP: caller-provided idempotency key when available; otherwise deterministic hash of normalized payload + source identity + bounded time window.

Rules:
- Duplicate ingress events must map to the same canonical request record whenever dedup identity matches.
- Replays must be observable as deduplicated events, not treated as new requests.
- Dedup decisions must be logged with the resolved key and action (`accepted`, `deduped`).

### 10.3 Timeout, Retry, and Circuit-Breaker Policy
Switchboard must enforce bounded downstream failure behavior.

Required policy dimensions:
- Per-target route timeout.
- Retry policy with bounded attempts and backoff strategy.
- Circuit-breaker state machine per target (`closed`, `open`, `half-open`).

Rules:
- Retries apply only to retryable failure classes.
- Non-retryable validation/policy failures fail fast.
- Open circuits must fail quickly with explicit target-unavailable errors.
- Circuit transitions must be observable in structured logs/metrics.

### 10.4 Backpressure and Admission Control
Switchboard must protect ingress under overload.

Required controls:
- Bounded in-memory/queue admission for routing work.
- Explicit overflow behavior (`shed`, `defer`, or `reject`) with configured policy.
- Fairness policy across source channels to prevent starvation.

Rules:
- Admission outcomes must be explicit and observable.
- Overload must degrade gracefully without process instability.
- Interactive sources should prefer fast failure/feedback over indefinite queueing.

### 10.5 Request and Route Schema Versioning
Switchboard request/decomposition payloads are versioned contracts.

Required versioned surfaces:
- Canonical request context schema.
- LLM decomposition output schema (including overlap segment metadata).
- Route dispatch envelope schema.

Rules:
- Every persisted payload must include schema version metadata.
- Breaking schema changes require explicit version bump and migration guidance.
- Parser behavior must be deterministic for unknown/newer versions.

### 10.6 Error Taxonomy and Terminal State Mapping
Switchboard must use a stable, typed error taxonomy for routing lifecycle decisions.

Minimum error classes:
- `classification_error`
- `validation_error`
- `routing_error`
- `target_unavailable`
- `timeout`
- `overload_rejected`
- `internal_error`

Rules:
- Every terminal failure must map to one canonical error class.
- Interactive terminal state (`ERRORED`) must include class + actionable message.
- Partial fanout failures must preserve per-target error classes in persisted results.
- Downstream non-switchboard butlers are expected to emit only route-executor classes (`validation_error`, `target_unavailable`, `timeout`, `overload_rejected`, `internal_error`) in `route_response.v1`.
- `classification_error` and `routing_error` are Switchboard-owned classes for failures in Switchboard's own classification/decomposition/dispatch planning layers.
- Unknown downstream error classes must be normalized to `internal_error` while preserving original class as non-user-facing metadata.

### 10.7 Registry Lifecycle and Staleness Rules
Registry membership must include liveness lifecycle behavior, not only static registration.

Required lifecycle rules:
- Heartbeat/last-seen TTL for active eligibility.
- Stale-target handling (`stale`, `quarantined`, or equivalent non-routable state).
- Recovery path for re-registration/health restoration.

Rules:
- Stale targets must not be selected for new routes unless explicitly allowed by policy.
- State transitions in target eligibility must be traceable/auditable.

### 10.8 SLO/SLI and Error Budget Contract
Switchboard must define and track operational SLOs with explicit error budgets.

Baseline runtime targets and alert policy are defined in
`docs/switchboard_observability_slo.md`.

Minimum SLI set:
- Ingress acceptance latency.
- End-to-end fanout completion latency.
- Route success rate.
- Interactive terminal-state latency (`PROGRESS` to `PARSED`/`ERRORED`).

Rules:
- SLO targets and alert thresholds must be defined in runtime operations docs/config.
- Error-budget burn must drive automatic escalation and/or protective policy changes.

### 10.9 Ordering and Causality Contract
Switchboard must explicitly define message ordering guarantees.

Rules:
- Per-source-thread causal ordering must be preserved when channel identity supports it.
- Cross-thread global ordering is not guaranteed.
- Parallel fanout execution does not imply deterministic completion order across targets.
- Persisted lifecycle records must allow causal reconstruction even when execution is concurrent.

### 10.10 Channel-Facing Tool Ownership Contract
Outbound channel-delivery tool surfaces are owned by `messenger_butler` under Switchboard policy.

Rules:
- Outbound external delivery tools (send/reply) for email, Telegram, SMS, and chat must be exposed only by `messenger_butler`.
- Ingress connectors/entrypoint adapters remain Switchboard-owned and must feed the canonical ingest boundary.
- Non-messenger butlers must not expose direct user-channel tools for external delivery and must use `notify.v1` intents routed through Switchboard.
- Switchboard must enforce this ownership boundary in routing/registry policy and reject or quarantine non-messenger channel-tool surfaces when detected.
- Identity-scoped channel tool naming conventions (for example `user_*` / `bot_*`) are defined for messenger/channel integration surfaces and are not a generic base-butler requirement.

## 11. Persistence Surfaces
Switchboard persistence has two classes: long-term durable storage and short-lived ingress lifecycle storage.

Long-term durable tables and purposes:
- `butler_registry`: authoritative registry of butlers, endpoints, capability declarations, and last-seen timestamps.
- `routing_log`: append-oriented record of route attempts (source, target, tool, success/failure, duration, error, timestamp).
- `extraction_queue`: pending/confirmed/dismissed/expired extraction decisions with per-entry expiry metadata.
- `extraction_log`: durable audit of extraction-originated writes and undo inputs.
- `notifications`: durable notification delivery history including status/error and trace/session linkage.
- `dashboard_audit_log`: durable audit surface for dashboard/API operations.

Connector heartbeat and statistics tables:
- `connector_registry`: current state of each known connector (self-registered, never auto-pruned).
- `connector_heartbeat_log`: append-only heartbeat history (7-day retention, month-partitioned).
- `connector_stats_hourly`: pre-aggregated hourly volume and health metrics (30-day retention).
- `connector_stats_daily`: pre-aggregated daily volume, health, and uptime (1-year retention).
- `connector_fanout_daily`: per-connector per-target-butler daily message counts (1-year retention).

Long-term storage summary:
- `butler_registry`, `routing_log`, `extraction_queue`, `extraction_log`, `notifications`, and `dashboard_audit_log` are persistent operational tables.
- `extraction_queue` is operational-state storage with expiry metadata; entries remain persisted unless explicit expiry/cleanup logic runs.
- Long-term tables act as operational and audit projections, not as the canonical short-lived ingress payload store.

Ingress lifecycle retention model:
- `message_inbox` is a month-partitioned short-lived lifecycle storage surface with one-month retention policy unless policy override exists.
- `message_inbox` stores ingress context, raw content, classification payload, routing result payload, response summary, and completion metadata.
- `message_inbox` is the canonical short-lived payload store for ingress requests.

Minimum invariant:
- Every request must have a durable lifecycle record if Switchboard storage is available.

## 12. Observability Contract
### 12.1 OpenTelemetry Requirement
Switchboard must emit OpenTelemetry metrics and traces as first-class runtime outputs.

Rules:
- Every accepted message must produce both telemetry signals: metrics and traces.
- Trace continuity must be preserved from ingress through all downstream routed subrequests.
- Metric dimensions must stay low-cardinality by default.
- Request lifecycle state must be reconstructable from telemetry plus persisted records.

### 12.2 Metrics Contract
Target metric namespace:
- `butlers.switchboard.*`

Core counters:
- `butlers.switchboard.message_received`
- `butlers.switchboard.message_deduplicated`
- `butlers.switchboard.message_overload_rejected`
- `butlers.switchboard.fallback_to_general`
- `butlers.switchboard.ambiguity_to_general`
- `butlers.switchboard.router_parse_failure`
- `butlers.switchboard.subroute_dispatched`
- `butlers.switchboard.subroute_result`
- `butlers.switchboard.lifecycle_transition`
- `butlers.switchboard.retry_attempt`
- `butlers.switchboard.circuit_transition`

Core histograms:
- `butlers.switchboard.ingress_accept_latency_ms`
- `butlers.switchboard.routing_decision_latency_ms`
- `butlers.switchboard.subroute_latency_ms`
- `butlers.switchboard.fanout_completion_latency_ms`
- `butlers.switchboard.end_to_end_latency_ms`

Core gauges:
- `butlers.switchboard.queue_depth`
- `butlers.switchboard.inflight_requests`
- `butlers.switchboard.circuit_open_targets`

Required low-cardinality attributes (tags) to use where relevant:
- `source`
- `destination_butler`
- `outcome`
- `lifecycle_state`
- `error_class`
- `policy_tier`
- `fanout_mode`
- `model_family`
- `prompt_version`
- `schema_version`

Cardinality rules:
- Do not tag metrics with high-cardinality identifiers such as `request_id`, full username, full email, message id, or raw text.
- If sender identity breakdown is needed in metrics, use bounded category labels rather than raw identities.

### 12.3 Trace Contract
Root span requirement:
- Every accepted message must create a root span named `butlers.switchboard.message`.

Recommended child spans:
- `butlers.switchboard.ingress.normalize`
- `butlers.switchboard.ingress.dedupe`
- `butlers.switchboard.routing.llm_decision`
- `butlers.switchboard.routing.plan_fanout`
- `butlers.switchboard.route.dispatch`
- `butlers.switchboard.route.aggregate`
- `butlers.switchboard.lifecycle.signal`
- `butlers.switchboard.persistence.write`

Root span required attributes:
- `request.id`
- `request.received_at`
- `request.source_channel`
- `request.source_endpoint_identity`
- `request.source_thread_identity`
- `request.schema_version`
- `switchboard.policy_tier`
- `switchboard.prompt_version`
- `switchboard.model_family`

Dispatch span required attributes:
- `request.id`
- `routing.destination_butler`
- `routing.segment_id`
- `routing.fanout_mode`
- `routing.attempt`
- `routing.outcome`
- `error.class` when failed

Propagation rules:
- `traceparent`/`tracestate` must be propagated to downstream butlers on every subroute.
- Switchboard request context fields must be attached as span attributes and route envelope metadata.

### 12.4 Logs and Correlation
Structured logs must include:
- `request_id`
- `source`
- `destination_butler` when applicable
- `lifecycle_state`
- `error_class` when applicable
- latency fields for major stages

Correlation rule:
- Logs, traces, and persisted lifecycle records must be joinable by `request_id`.

Minimum auditability:
- Ability to reconstruct, for a request id, what was received, how it was decomposed, where it was routed, and what succeeded/failed.

## 13. Safety and Reliability Invariants
- Switchboard is always fail-safe, never fail-closed.
- Unknown/invalid routing outputs cannot cause arbitrary tool execution.
- Downstream failure must be isolated per target and explicitly surfaced.
- Context propagation is mandatory and immutable across subroutes.
- Interactive channels must always end in `PARSED` or `ERRORED` terminal state.

## 14. Change Control Rules
Any change to Switchboard contracts must update, in the same change:
- this document,
- migration/schema artifacts for changed contracts,
- integration tests covering ingress, routing, fanout, and lifecycle behavior.

No contract-breaking changes without explicit versioning and migration guidance.

## 15. Advanced Operational Contracts
### 15.1 Ambiguity Resolution Contract
Switchboard must not guess under ambiguity.

Rules:
- If routing confidence is below configured threshold, route to `general`.
- No clarification round-trip is required for ambiguous ingress in default policy.
- Ambiguity-triggered fallbacks must be observable and tagged in lifecycle records.

### 15.2 Routing Precedence Rules
Routing decisions must be deterministic under mixed policy inputs.

Rules:
- Hard policy/rule constraints take precedence over LLM discretionary routing.
- Registry eligibility and safety constraints are applied before final target selection.
- LLM output cannot bypass explicit deny/allow policy layers.

### 15.3 Conflict Arbitration Contract
Switchboard must define deterministic handling when downstream outputs conflict.

Rules:
- Conflicts are detected at aggregation time when incompatible outcomes are returned.
- Arbitration policy must declare winning precedence or conflict surfacing behavior.
- User-facing responses must disclose unresolved conflicts when no deterministic winner exists.
- Current deterministic winner rule for grouped conflicts: highest explicit arbitration priority, then lexical butler name, then lexical subrequest id.

### 15.4 Fanout Dependency Model
Fanout execution must support explicit dependency semantics.

Required modes:
- `parallel`: independent subroutes run concurrently.
- `ordered`: subroutes execute in defined order.
- `conditional`: downstream subroutes run only if upstream conditions succeed.

Rules:
- Join policy and abort policy must be explicit per fanout plan.
- Execution metadata must record which dependency mode was used.
- Fanout execution records persist mode/policy plus per-subrequest dependency outcomes in `fanout_execution_log`.

### 15.5 Partial-Success Response Policy
Switchboard must define stable user-facing behavior for mixed outcomes.

Rules:
- Successes are acknowledged even when some subroutes fail.
- Failed targets are surfaced with actionable error class/message.
- Terminal state remains `ERRORED` when any required subroute fails.

### 15.6 Dead-Letter and Replay Contract
Non-terminally recoverable failures must be captured for controlled replay.

Rules:
- Failed requests/subrequests beyond retry policy move to a dead-letter surface.
- Replay must preserve original `request_id` lineage and be idempotent.
- Replay eligibility is determined from ingress-level dedupe lineage rather than a separate route-level dedupe decision.
- Replay actions must be audited (`who`, `when`, `why`, `result`).

### 15.7 Per-Request Budget Contract
Switchboard must enforce bounded execution budgets per request.

Required budget dimensions:
- Wall-clock latency budget.
- Model/tool invocation budget.
- Optional cost/token budget.

Rules:
- Budget exhaustion must produce explicit terminal errors.
- Budget policy must be configurable by channel/policy tier.

### 15.8 Source/Urgency Policy Contract
Switchboard must support policy differentiation by ingress source and urgency.

Rules:
- Channel/urgency tiers may vary timeout, retry, model tier, and fanout strictness.
- Policy selection must be deterministic and observable in request metadata.
- Policy mismatches/fallback to defaults must be logged.

### 15.9 Capability Compatibility Checks
Switchboard must validate dispatch compatibility before routing.

Rules:
- Target must advertise required capability/tool for planned subroute.
- Required argument shape/fields must be validated pre-dispatch when schema is available.
- Compatibility failures are classified as validation/policy errors, not transport errors.

### 15.10 Prompt and Model Rollout Policy
Router prompts/models must be versioned and rollout-controlled.

Rules:
- Prompt version and model version must be recorded per request.
- Rollouts should support canary phases and deterministic rollback.
- Rollback criteria must be tied to routing quality and error-rate signals.

### 15.11 Quality Drift Monitoring
Switchboard must monitor routing quality over time for regression detection.

Minimum monitored dimensions:
- Target selection correctness proxy metrics.
- Decomposition quality stability.
- Fallback-to-general rate.
- Partial/total failure rate by source and by target butler.

Rules:
- Drift thresholds must trigger alerts/escalation.
- Quality metrics must be sliceable by model/prompt version.

### 15.12 Human Override and Operator Controls
Switchboard operations must support supervised intervention paths.

Required controls:
- Manual reroute of a request/subrequest.
- Cancel/abort in-flight request where safe.
- Controlled retry/replay initiation.
- Force-complete with explicit operator annotation when policy permits.

Rules:
- All overrides are auditable and attributable.
- Override outcomes must be reflected in final lifecycle records.

## 17. Data Sources and Ingestion Surfaces
### 17.1 Canonical Ingestion Boundary
Switchboard ingestion is API-first.

Rules:
- All sources must submit through the same canonical ingest contract.
- Source-specific connectors/adapters are transport integration layers only.
- Normalization, deduplication, request-context assignment, and persistence happen at the canonical ingest boundary.
- Connectors may run in-process with the Switchboard daemon, but they must call the same canonical ingest handler as external connectors.

Connector contract reference:
- Connector-facing operational expectations (resume safety, rate limiting, env/config conventions, and run model) are specified in `docs/connectors/interface.md`.

### 17.2 Source Connector Types
Supported source types:
- Push-webhook sources (for example Telegram, Slack events, email webhooks).
- Pull/polling sources (for example IMAP polling, periodic inbox scans).
- Direct API callers (first-party or explicitly approved clients).

Ingestion granularity:
- Each newly observed message/event is ingested as one canonical ingress record.

### 17.3 Canonical Ingest Event Shape
Minimum ingest envelope:

```json
{
  "schema_version": "ingest.v1",
  "source": {
    "channel": "telegram|slack|email|api|mcp",
    "provider": "telegram|slack|imap|internal",
    "endpoint_identity": "bot-or-mailbox-or-client-id"
  },
  "event": {
    "external_event_id": "provider-event-id",
    "external_thread_id": "thread-or-conversation-id-or-null",
    "observed_at": "RFC3339 timestamp"
  },
  "sender": {
    "identity": "provider-sender-identity"
  },
  "payload": {
    "raw": {},
    "normalized_text": "text used for routing"
  },
  "control": {
    "idempotency_key": "optional caller key",
    "trace_context": {},
    "policy_tier": "default|interactive|high_priority"
  }
}
```

### 17.4 Canonical Route Envelope Shape
All downstream dispatches must use a versioned route envelope:

```json
{
  "schema_version": "route.v1",
  "request_context": {
    "request_id": "uuid7",
    "received_at": "RFC3339 timestamp",
    "source_channel": "telegram",
    "source_endpoint_identity": "switchboard-bot",
    "source_sender_identity": "user-123",
    "source_thread_identity": "chat-456"
  },
  "subrequest": {
    "subrequest_id": "uuid",
    "segment_id": "seg-1",
    "fanout_mode": "parallel|ordered|conditional"
  },
  "target": {
    "butler": "health",
    "tool": "route.execute"
  },
  "input": {
    "prompt": "self-contained segment prompt"
  },
  "trace_context": {}
}
```

### 17.5 Connector Heartbeat Ingestion

Switchboard owns the connector heartbeat ingestion boundary.

Rules:
- Switchboard exposes a `connector.heartbeat` MCP tool that accepts `connector.heartbeat.v1` envelopes from connector processes.
- Heartbeats are processed asynchronously and MUST NOT block the ingestion path.
- On first heartbeat from an unknown `(connector_type, endpoint_identity)` pair, Switchboard auto-registers the connector (self-registration).
- Connector liveness is derived from heartbeat recency: `online` (< 2 min), `stale` (2–4 min), `offline` (> 4 min).
- Switchboard persists heartbeat state in `connector_registry` (current state) and `connector_heartbeat_log` (append-only history, 7-day retention, month-partitioned).
- Counter deltas between consecutive heartbeats are computed and used as rollup input.
- Switchboard MUST NOT auto-deregister connectors. Cleanup is an operator action.

Full heartbeat protocol specification: `docs/connectors/heartbeat.md`.

### 17.6 Connector Statistics and Aggregation

Switchboard owns pre-aggregated connector statistics derived from heartbeat logs and `message_inbox` routing outcomes.

Rollup tables:
- `connector_stats_hourly`: Per-connector hourly volume and health metrics.
- `connector_stats_daily`: Per-connector daily volume, health, and uptime percentage.
- `connector_fanout_daily`: Per-connector per-target-butler daily message counts, derived from `message_inbox.dispatch_outcomes`.

Rollup schedule:
- Hourly rollup: runs at minute 5 of every hour.
- Daily rollup + fanout rollup: runs at 00:15 UTC daily.

Retention and pruning:
- `connector_heartbeat_log`: 7 days (partition drop).
- `connector_stats_hourly`: 30 days (row delete).
- `connector_stats_daily`: 1 year (row delete).
- `connector_fanout_daily`: 1 year (row delete).
- `connector_registry`: never auto-pruned.

Rules:
- Rollup jobs are Switchboard scheduled tasks (cron-based).
- Rollups MUST be idempotent and safe to re-run.
- Pruning MUST log what was removed.

Full statistics specification: `docs/connectors/statistics.md`.

### 17.7 Connector Dashboard API

Switchboard connector state and statistics are exposed via core dashboard API endpoints (not butler-specific routes).

Required endpoints:
- `GET /api/connectors` — list all known connectors with liveness and today's summary.
- `GET /api/connectors/{connector_type}/{endpoint_identity}` — full detail for a single connector.
- `GET /api/connectors/{connector_type}/{endpoint_identity}/stats?period=24h|7d|30d` — time-series volume and health statistics.
- `GET /api/connectors/summary?period=24h|7d|30d` — aggregate cross-connector summary.
- `GET /api/connectors/fanout?period=7d|30d` — connector-to-butler routing distribution matrix.

Rules:
- Endpoints query Switchboard database directly (rollup tables and `connector_registry`).
- Liveness is derived at query time from `last_heartbeat_at` using staleness thresholds.
- Fanout data is derived from pre-aggregated `connector_fanout_daily`, not live `message_inbox` queries.
- Response models follow the standard `ApiResponse[T]` / `PaginatedResponse[T]` wrappers.

Full endpoint specification and response schemas: `docs/connectors/statistics.md`.

### 17.8 Ingestion API Semantics
Rules:
- Accepted ingest returns `202 Accepted` with canonical `request_id`.
- Deduplication is evaluated at ingestion using channel-aware keys.
- Duplicate events return the same canonical request reference (not a new request).
- Ingestion acceptance is decoupled from routing execution.
- The direct ingestion API should be private by default; public exposure requires explicit authn/authz policy and rate limits.

## 18. Non-Normative Note
Implementation mapping is intentionally maintained outside this normative role document.
