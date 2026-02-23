# Base Butler: Permanent Definition

Status: Normative (Target State)
Last updated: 2026-02-13
Primary owner: Platform/Core

## 1. Role
The Base Butler specification defines the mandatory platform contract for all butlers other than role-specific overrides (for example Switchboard).

This document is the source of truth for shared butler behavior: identity, configuration, lifecycle, core tools, plugin/module architecture, persistence, scheduling, routing integration, and observability.

## 2. Applicability and Override Rules
Applicability:
- Applies to every butler role by default.
- Role-specific documents may add stricter requirements.
- Role-specific documents may override only where explicitly stated.

Override precedence:
- Role-specific contract override (highest).
- Base Butler contract (this document).
- Implementation details and runbooks (lowest).

Role-specific override section requirement:
- Every role-specific spec MUST include a `Base Contract Overrides` section.
- That section MUST include:
- `Inherits unchanged`: explicit statement that all base clauses apply unless listed below.
- `Overrides`: list of overridden base clauses (`base_clause`, `override`, `rationale`).
- `Additions`: stricter role-specific requirements that do not override base clauses.
- If no overrides exist, the section MUST explicitly say `Overrides: none`.
- Silent overrides are prohibited.

## 3. Design Goals
- Uniform butler lifecycle and operational semantics.
- Strict database isolation between butlers.
- Pluggable capability model with deterministic dependency ordering.
- Safe, auditable runtime invocation with bounded failure behavior.
- Deterministic scheduling and execution semantics.
- Stable integration contract with Switchboard routing.
- First-class observability and trace correlation across butlers.

## 4. Identity and Configuration Contract
Every butler must be defined by a git-backed config directory with:
- `butler.toml` (required)
- `CLAUDE.md` (required)
- `MANIFESTO.md` (required for product/role identity)
- `skills/` (optional)
- `AGENTS.md` (optional runtime/operator notes)

Required `butler.toml` fields:
- `[butler].name` (globally unique butler identity)
- `[butler].port` (runtime MCP listen port)

Strongly recommended fields:
- `[butler].description`
- `[butler.db].name` (defaults to `butler_<name>`)
- `[butler.runtime].type` and `[butler.runtime].model`
- `[butler.switchboard].url` for non-switchboard butlers
- `[butler.env].required` and `[butler.env].optional`
- `[butler.shutdown].timeout_s`
- `[modules.memory]` retrieval/context settings (when memory module is enabled)

Target-state `[butler.switchboard]` routing fields:
- `url`: Switchboard MCP endpoint URL.
- `advertise`: whether this butler should be advertised in Switchboard registry.
- `liveness_ttl_s`: expected registry staleness TTL for liveness policy.
- `route_contract_min`: minimum accepted route envelope version.
- `route_contract_max`: maximum accepted route envelope version.

Environment-variable reference rules:
- Config may reference env vars via `${VAR_NAME}`.
- Unresolved required references are startup-blocking configuration errors.
- Inline secrets in config values are prohibited.
- Module-scoped credential env declarations (for example `*_env`) must be validated before module startup.

## 5. Core Runtime Contract
Each butler daemon must implement the following startup phases:
1. Load and validate config.
2. Initialize telemetry.
3. Load modules and resolve module dependency order.
4. Validate module config schemas.
5. Validate required credentials and environment.
6. Provision database and run core/module migrations.
7. Run module startup hooks.
8. Initialize runtime spawner.
9. Sync static schedules from config to DB.
10. Register core and module MCP tools.
11. Start MCP server.

### 5.1 Model Selection and Cost Guidance
Model selection should be policy-driven by task complexity, latency targets, and invocation volume.

Recommended tiering:
- Small-payload, high-volume flows (classification, routing, lightweight transforms): default to low-cost fast models. Recommended baseline: Claude 4.5 Haiku (`claude-4.5-haiku`), GPT-5 mini (`gpt-5-mini`), or Gemini 3 Flash (`gemini-3-flash`).
- Medium-complexity reasoning (multi-step synthesis, moderate tool planning): use mid-tier models with stronger reasoning at moderate cost.
- High-complexity reasoning (deep planning, ambiguous arbitration, long-context synthesis): use highest-capability models selectively and only where lower tiers do not meet quality/SLO targets.

Operational cost warning:
- Model choice is an operational cost control, not only a quality choice.
- High-capability models on high-throughput paths can dominate spend and degrade latency under load.
- Fanout workflows multiply model spend; one ingress request can trigger multiple routed invocations.
- Every runtime invocation should emit token/latency metrics so policy can enforce budget and error-budget goals.
- Default posture should be cheapest model that meets quality and reliability targets for that path.

Shutdown contract:
1. Stop accepting new incoming requests.
2. Drain in-flight runtime sessions within timeout.
3. Close integration clients (for example Switchboard client).
4. Shut down modules in reverse dependency order.
5. Close DB pool.

## 6. Core Tool Surface Contract
Every butler must expose the shared core MCP tools:
- `status`
- `trigger`
- `route.execute`
- `tick`
- `state_get`, `state_set`, `state_delete`, `state_list`
- `schedule_list`, `schedule_create`, `schedule_update`, `schedule_delete`
- `sessions_list`, `sessions_get`, `sessions_summary`, `sessions_daily`, `top_sessions`, `schedule_costs`
- `notify`

Tool behavior rules:
- `status` returns identity, health, loaded modules, and uptime.
- `trigger` starts one direct runtime session with explicit trigger source.
- `route.execute` is the canonical routed execution entrypoint for Switchboard fanout and must validate route envelopes.
- `tick` executes due scheduled tasks and returns dispatch summary.
- State and schedule tools are deterministic and auditable.
- `notify` routes outbound delivery requests through Switchboard to `messenger_butler`.
- `notify` request/response payloads must follow the versioned envelope contract in section `11.1 Notify Envelope Contract`.

## 7. Trigger and Session Contract
Spawner contract:
- Runtime invocations are serialized per butler lock by default.
- Self-invocation deadlocks must be prevented.
- Trigger rejection must be explicit when spawner is unavailable or locked by policy.

Valid trigger sources:
- `tick`
- `external`
- `trigger`
- `schedule:<task-name>`

Trigger source ownership rules:
- `trigger` is the canonical `trigger_source` for invocations entering through either the `trigger` MCP tool or `route.execute`.
- `external` is reserved for non-route external/programmatic invocations that bypass `route.execute`.
- Routed lineage is identified by `request_context` presence, not by introducing new `trigger_source` values.
- Additional ad-hoc `trigger_source` values are not allowed by the base contract.

Session lifecycle:
- Create session row before runtime invocation begins.
- Complete session row on success/failure with duration and outcome.
- Session records are append-oriented; completion updates the open session.

Minimum persisted session fields:
- `id`, `prompt`, `trigger_source`, `started_at`, `completed_at`
- `result`, `tool_calls`, `success`, `error`, `duration_ms`
- `trace_id`, `model`
- `input_tokens`, `output_tokens`
- `parent_session_id` when applicable
- `request_id`, `subrequest_id`, `segment_id` when execution originates from a routed request context

## 8. Scheduler Contract
Scheduler rules:
- Static schedules in config are synced to DB on startup.
- Runtime-created schedules are supported via core schedule tools.
- `tick` evaluates due tasks by `next_run_at`, dispatches each, advances `next_run_at`, and records `last_result`.
- Failure of one scheduled task must not block remaining due tasks.

Scheduler integration rules:
- Every butler must support external `tick` calls (for debugging and manual triggering).
- `tick` handlers must be idempotent and safe under repeated calls.
- A butler should complete `tick` within a bounded operational timeout for scheduler stability.
- Scheduler-triggered failures must be observable in session/log surfaces.

## 9. Module (Plugin) Contract
Modules are opt-in capability plugins and must conform to the `Module` interface:
- `name`
- `config_schema`
- `dependencies`
- `register_tools(mcp, config, db)`
- `migration_revisions()`
- `on_startup(config, db)`
- `on_shutdown()`

Module loading rules:
- Unknown module names are startup-blocking errors.
- Dependencies are resolved by deterministic topological sort.
- Cycles are startup-blocking errors.
- Module config validation uses strict schemas (unknown fields rejected unless explicitly allowed).

Module tool contract:
- Base contract enforces channel delivery ownership boundaries in section `11` (`messenger_butler` owns external user-channel delivery). Module tool naming details remain role-specific.
- Modules may declare tool I/O descriptors via `user_inputs`, `user_outputs`, `bot_inputs`, `bot_outputs` when identity-scoped I/O surfaces are part of that role contract.
- For roles that define identity-scoped channel tools (for example `messenger_butler`), user-vs-bot identity split must be explicit in descriptor metadata and tool names (for example `user_<channel>_<verb>` / `bot_<channel>_<verb>`).
- When descriptors are declared, registered tool names must match declared descriptors.
- When descriptors are declared, missing declared tools or undeclared registered tools are startup-blocking errors.

Approval and sensitivity contract:
- Output tools may define `approval_default` semantics (`none`, `conditional`, `always`).
- Sensitive argument metadata may be declared per tool.
- Approval gating policy must be centrally enforceable without module code changes.

Migration contract:
- Modules with persistent data must provide a migration chain.
- Migration chains must be deterministic, conflict-free, and runnable at startup.

### 9.1 Approval Contract
Approval gating is a centralized execution-control surface, not per-tool ad hoc logic.

Approval policy/config rules:
- Approval gating is configured under `[modules.approvals]` and applied after module tool registration.
- Gated-tool config is authoritative (`gated_tools` with optional per-tool expiry overrides).
- Identity-aware defaults are mandatory:
  - User-scoped send/reply outputs (`approval_default="always"` and user send/reply safety fallback) are default-gated.
  - Bot-scoped outputs are gated only when explicitly configured.

Gate interception rules:
- A gated tool invocation must be intercepted before calling the original tool handler.
- Intercepted invocations must be serialized as pending actions with tool name, args, status, requested/expiry timestamps, and auditable summary metadata.
- If no standing rule matches, the invocation must return a structured `pending_approval` response with a stable `action_id` and must not execute the original tool.
- Standing rules (tool name + arg constraints + active/expiry/use-limit checks) may auto-approve matching invocations.
- Standing-rule auto-approval MUST be treated as pre-approval delegated by the authenticated human rule owner; it is not an autonomous non-human decision.
- Decision-bearing approval operations (`approve`, `reject`, rule create/revoke) MUST require authenticated human actor context and MUST reject non-human actor paths with explicit machine-readable error semantics.
- Auto-approved and manually approved actions must execute through a shared executor path so status transitions and audit logging are consistent.

Approval data/state rules:
- Approval state machines must be explicit and deterministic (`pending -> approved|rejected|expired`, `approved -> executed`).
- Approval storage must include durable surfaces equivalent to:
- pending actions queue
- standing approval rules (including `use_count`/active lifecycle)
- Executed-action audit history must remain queryable for operator review.

### 9.2 Memory Contract
Memory is a module contract implemented locally inside each butler that enables it; direct cross-butler DB memory access is prohibited.

Memory integration rules:
- Memory tools are registered on the hosting butler MCP server by the memory module.
- Runtime context retrieval uses `memory_context(trigger_prompt, butler, token_budget)` semantics and should be injected into system prompt context when available.
- Session episode capture should call `memory_store_episode` on successful session completion, including originating butler identity and session linkage when available.
- Memory retrieval/storage failures must be fail-open for runtime execution (log and continue; do not fail the primary user task solely due to memory unavailability).

Memory model rules:
- Memory types are `episodes`, `facts`, and `rules`.
- Episode memory is high-volume and TTL-managed (short-lived observational history).
- Fact memory supports validity lifecycle (for example active/superseded/retracted) and supersession linking for corrections/updates.
- Rule memory supports maturity progression (candidate -> established -> proven) and helpful/harmful feedback tracking.
- Memory scope is butler-local and supports `global` plus role-local scopes.

Memory retrieval/config rules:
- Butler memory integration config is defined in `[modules.memory]` (enable flag, retrieval defaults, scoring weights, and context token budget).
- Retrieval modes and limits must be deterministic and configurable (for example hybrid/semantic/keyword mode, recall/search limits).
- Retrieval surfaces must preserve request lineage metadata where available so memory reads/writes remain trace-correlated.

## 10. Persistence and Data Isolation Contract
Isolation invariants:
- Each butler has its own PostgreSQL schema.
- Direct cross-butler schema access is prohibited.
- Inter-butler communication happens only via MCP/Switchboard contracts.

### 10.1 Data Schema Contract
Standardized core tables (required in every butler DB):
- `state`
- `scheduled_tasks`
- `sessions`

Core schema rules:
- Every butler DB MUST include the standardized core tables above with compatible core-tool semantics.
- Core tables are platform-owned schema surfaces and MUST remain compatible with shared runtime/API contracts.
- Custom/domain migrations MUST NOT repurpose or redefine core-table meaning.

Custom table freedom:
- Butler roles may define any additional role-specific tables needed for domain behavior.
- Custom schemas must be isolated to that butler's DB and managed through that butler's migration chain.
- Custom tables may reference core-session identifiers for lineage/audit when needed.
- Cross-butler foreign keys or direct reads/writes into another butler DB are prohibited.

Data quality rules:
- DB writes must be parameterized and schema-validated.
- JSON payload surfaces must remain versionable.
- Non-critical auxiliary writes should fail gracefully when possible.

## 11. Switchboard Integration Contract
Routing boundary:
- Non-switchboard butlers are service executors, not primary user-ingress routers.
- Routed requests from Switchboard are first-class trigger sources and must preserve context lineage.

Registry participation:
- Butler identity, endpoint, and capability/module declarations must be discoverable by Switchboard registry.
- Registry staleness and liveness policies are owned by Switchboard; butlers must support health/status probes.

Minimum registry record shape:
- `name`
- `endpoint_url`
- `description`
- `modules`
- `capabilities` (if distinct from modules)
- `last_seen_at`
- `route_contract_min`
- `route_contract_max`

Registry publication rules:
- Registration updates must be idempotent upserts.
- Registration should occur on startup and liveness refresh intervals.
- Non-advertised/system-internal butlers must be explicitly marked as non-routable.

Inbound route envelope:
- Butlers must accept versioned routing envelopes through `route.execute` from Switchboard (see Switchboard role spec `route.v*` contracts).
- Unknown required envelope versions must fail deterministically with typed validation errors.
- Route-envelope acceptance must use a deterministic compatibility check:
- Parse inbound `schema_version` (for example `route.v1`) to integer version `v`.
- Accept only when `route_contract_min <= v <= route_contract_max`.
- If `route_contract_min`/`route_contract_max` are unset, default support is `v1` only.
- Rejections must return a typed validation error with supported min/max versions.

Dispatch interface rule:
- Canonical routed execution entrypoint is `route.execute`.
- `trigger` is a direct/manual invocation surface and must not be the primary routed fanout contract.
- Legacy unprefixed handler aliases are backward-compatibility shims and must not be the long-term contract.

Canonical `route.execute` request envelope shape (target-state):

```json
{
  "schema_version": "route.v1",
  "request_context": {
    "request_id": "uuid7",
    "received_at": "RFC3339 timestamp",
    "source_channel": "telegram|email|slack|api|mcp",
    "source_endpoint_identity": "ingress-identity",
    "source_sender_identity": "sender-identity",
    "source_thread_identity": "thread/chat id",
    "subrequest_id": "uuid",
    "segment_id": "seg-1",
    "trace_context": {}
  },
  "input": {
    "prompt": "self-contained segment prompt",
    "context": "optional additional execution context"
  },
  "source_metadata": {
    "channel": "telegram|email|slack|api|mcp",
    "identity": "ingress identity",
    "tool_name": "logical source tool name"
  }
}
```

`route.execute` request rules:
- `schema_version`, `request_context`, and `input.prompt` are required.
- `request_context` required fields are defined below and must be validated before runtime invocation.
- On successful validation, routed execution should run through the same runtime/session pipeline used by `trigger`, preserving `request_context` lineage in session/log/audit surfaces.
- `route.execute` must return `route_response.v1` envelopes for both success and failure paths.

Request context handling contract:
- Switchboard-routed requests must include a `request_context` object with immutable lineage identifiers.
- `request_context.request_id` must be a UUID7 and must be treated as immutable for the full request lineage.
- Required context fields:
- `request_id`
- `received_at`
- `source_channel`
- `source_endpoint_identity`
- `source_sender_identity`
- Optional context fields:
- `source_thread_identity`
- `subrequest_id`
- `segment_id`
- `trace_context`
- Missing required context fields must produce deterministic validation errors (not implicit defaults).
- All internal execution stages must preserve `request_id` unchanged; child operations may add scoped identifiers but must not replace root identity.

Route response and error propagation contract:
- Every `route.execute` invocation must return a terminal response envelope to Switchboard (success or error). Silent drops are prohibited.
- Unhandled exceptions in routed execution must be converted into structured error responses and propagated back to Switchboard.
- Error responses must preserve enough detail for routing/audit decisions while remaining safe for user exposure policy.

Minimum response envelope shape (target-state):

```json
{
  "schema_version": "route_response.v1",
  "request_context": {
    "request_id": "uuid7",
    "received_at": "RFC3339 timestamp",
    "source_channel": "telegram|email|slack|api|mcp",
    "source_endpoint_identity": "ingress-identity",
    "source_sender_identity": "sender-identity",
    "subrequest_id": "uuid",
    "segment_id": "seg-1"
  },
  "status": "ok|error",
  "result": {},
  "error": {
    "class": "validation_error|target_unavailable|timeout|overload_rejected|internal_error",
    "message": "human-readable error summary",
    "retryable": false
  },
  "timing": {
    "duration_ms": 42
  }
}
```

Route response rules:
- `request_id` lineage fields must be echoed in responses when present in the inbound route envelope.
- Successful responses set `status="ok"` and include `result`; `error` must be null/absent.
- Failed responses set `status="error"` and include canonical `error.class` and actionable `error.message`.
- Non-switchboard butlers must use only route-executor classes:
- `validation_error`
- `target_unavailable`
- `timeout`
- `overload_rejected`
- `internal_error`
- `classification_error` and `routing_error` are Switchboard-owned lifecycle classes and must not be emitted by non-switchboard butlers.
- Transport-layer failures and runtime exceptions must map to canonical error classes; raw exception type may be attached as non-user-facing metadata.
- Response emission must be bounded by route timeout policy; timeout must produce a timeout-class error response to Switchboard.
- `request_context.request_id` must be logged in structured logs for both success and failure paths.
- If request context is absent because execution is not Switchboard-routed (for example local schedule tick), responses may omit request lineage fields.

User interaction and delivery contract:
- All external user-channel interaction (for example email replies, Telegram messages, SMS, chat) MUST be executed by `messenger_butler`.
- Non-messenger butlers MUST NOT call channel send/reply tools directly for user-facing output.
- Non-messenger butlers request outbound user interaction via `notify`; Switchboard routes delivery to `messenger_butler`.
- Whether to communicate externally for a given request is discretionary runtime behavior (LLM/tool policy). A butler may choose to send no user-facing message when not warranted by context/policy.
- For replies tied to routed ingress, outbound delivery requests MUST carry `request_context` lineage so destination is unambiguous.
- At minimum, reply-capable delivery requires `request_context.source_channel`, `request_context.source_endpoint_identity`, and `request_context.source_sender_identity`; include `request_context.source_thread_identity` when the channel uses thread/chat reply targeting.
- Missing required request-context targeting fields for a requested reply MUST produce deterministic validation errors and MUST NOT emit a blind/broadcast send.
- Every outbound interaction MUST include originating butler identity as structured metadata (`origin_butler`).
- Every outbound interaction MUST include originating butler identity in user-visible content.
- Email: subject MUST include an explicit butler identifier token (for example `[health]`).
- Non-subject channels (for example Telegram): message text MUST include an explicit butler identifier prefix (for example `[health]`) unless the channel presentation has an equivalent explicit identity surface configured by policy.
- Messenger delivery and audit surfaces MUST preserve both `origin_butler` and `request_context.request_id` for traceability.

### 11.1 Notify Envelope Contract
`notify` is a versioned compatibility surface for outbound user interaction requests.

Canonical notify request envelope (target-state):

```json
{
  "schema_version": "notify.v1",
  "origin_butler": "health",
  "delivery": {
    "intent": "send|reply|react",
    "channel": "telegram|email|sms|chat",
    "message": "user-visible content",
    "recipient": "optional explicit recipient identity",
    "subject": "optional, channel-specific (for example email)",
    "emoji": "optional emoji for react intent (required when intent=react)"
  },
  "request_context": {
    "request_id": "uuid7",
    "received_at": "RFC3339 timestamp",
    "source_channel": "telegram|email|slack|api|mcp",
    "source_endpoint_identity": "ingress-identity",
    "source_sender_identity": "sender-identity",
    "source_thread_identity": "thread/chat id"
  }
}
```

Notify request rules:
- `schema_version` is required and must be `notify.v1` unless a newer explicitly supported version is negotiated.
- `origin_butler` is required and must match the requesting butler identity.
- `delivery.intent`, `delivery.channel`, and `delivery.message` are required.
- `delivery.intent="reply"` requires `request_context` with at least:
  - `request_context.request_id`
  - `request_context.source_channel`
  - `request_context.source_endpoint_identity`
  - `request_context.source_sender_identity`
  - If the target channel requires thread targeting, `request_context.source_thread_identity` is required for `reply`.
- `delivery.intent="react"` requires:
  - `delivery.emoji` (the reaction emoji, e.g. "👍", "❤️", "🔥")
  - `request_context` with `source_thread_identity` (for telegram: `<chat_id>:<message_id>`)
  - `delivery.channel` must be `telegram` (currently the only channel supporting reactions)
  - `delivery.message` is not required for react intent
- Unknown required schema versions or missing required fields must fail deterministically with typed validation errors.

Notify transport mapping rule:
- For Messenger delivery, `notify.v1` is carried inside Switchboard-routed `route.v1` payloads and executed by Messenger `route.execute`.
- Messenger must return `route_response.v1`; when notify execution succeeds/fails, normalized delivery output should be included as `result.notify_response` (`notify_response.v1`).

Canonical notify response envelope (target-state):

```json
{
  "schema_version": "notify_response.v1",
  "request_context": {
    "request_id": "uuid7"
  },
  "status": "ok|error",
  "delivery": {
    "channel": "telegram|email|sms|chat",
    "delivery_id": "provider-or-messenger id"
  },
  "error": {
    "class": "validation_error|target_unavailable|timeout|overload_rejected|internal_error",
    "message": "human-readable summary",
    "retryable": false
  }
}
```

Notify response rules:
- Success responses set `status="ok"` and include `delivery`; `error` must be null/absent.
- Failure responses set `status="error"` and include canonical `error.class` and actionable `error.message`.
- When inbound notify includes `request_context.request_id`, it must be echoed in notify responses and downstream messenger audit records.
- Local admission/overload failures must map to `overload_rejected`; transient provider throttling/unavailability should map to `target_unavailable`.

Idempotency and replay:
- Because fanout is at-least-once, butlers must tolerate duplicate routed subrequests where request lineage matches.
- Domain tools should expose deterministic dedupe keys where business semantics require exactly-once effects.

## 12. Runtime Isolation and Execution Contract
Runtime invocation rules:
- Each invocation uses an ephemeral runtime context.
- Runtime access is limited to the butler's MCP server plus explicitly configured modules/services (for example the memory module when enabled).
- Runtime working directory is the butler config directory.
- Runtime environment includes only validated env vars and module credentials.

Safety rules:
- Runtime failures must never corrupt core scheduler/session state.
- Timeouts, cancellations, and adapter errors must produce explicit failure records.

## 13. Observability Contract
Tracing:
- All core and module tool handlers must emit spans with butler identity attributes.
- Session creation/completion must be trace-correlated.
- Inter-butler calls must propagate trace context.

Logging:
- Structured logs must include butler name, operation, outcome, and relevant latency.
- Sensitive payloads and secret values must not be logged.
- For Switchboard-routed execution, logs must include `request_id` and, when present, `subrequest_id` and `segment_id`.
- Source identity values in logs should be minimized/redacted by policy (for example hashed sender identifiers) unless full identity is required for audit/compliance.

Audit metadata contract:
- If a butler has an audit table/surface, each routed operation record must include:
- `request_id`
- `subrequest_id` (if present)
- `segment_id` (if present)
- `source_channel`
- `source_endpoint_identity`
- `source_sender_identity` (or approved redacted equivalent)
- `operation` or `tool_name`
- `status`/`outcome`
- `error_class` and error summary when failed
- `duration_ms`
- `created_at`
- If a role does not own a dedicated audit table, equivalent metadata must be recoverable from session/log records.

Metrics:
- At minimum, emit request counts, error counts, and duration distributions.
- Minimum metric coverage includes runtime sessions, core tool calls, and scheduled task dispatch.
- Metrics must use low-cardinality attributes; required attributes where relevant:
- `butler`
- `tool_name`
- `outcome`
- `trigger_source`
- `error_class`
- `source_channel`
- Metrics must not include high-cardinality identifiers such as `request_id`, raw sender identity, raw thread IDs, or message text.

Trace and span attribute contract:
- Root and child spans for routed execution must include `request.id` (`request_id`) as a trace attribute.
- When available, spans should include:
- `request.subrequest_id`
- `request.segment_id`
- `request.source_channel`
- `request.source_endpoint_identity`
- `request.source_thread_identity`
- `routing.contract_version`
- `error.class` on failures
- Trace context from inbound route envelopes must be propagated to downstream tool calls.

## 14. Reliability and Safety Invariants
- Butler startup must fail fast on invalid config/schema/credentials.
- Butler shutdown must be graceful and bounded.
- One failed scheduled task must not block unrelated scheduled tasks.
- One module failure must be isolated to that module's scope whenever possible.
- Unknown inbound tool/routing payloads must fail safely, not execute fallback arbitrary actions.

## 15. Versioning and Compatibility Contract
- Core tool contracts are versioned compatibility surfaces.
- Route-envelope and request-context schemas are versioned compatibility surfaces.
- Breaking changes require explicit version bumps and migration guidance.
- Backward-compat shims must be time-bounded and observable.

## 16. Change Control Rules
Any change to base butler contracts must update, in the same change:
- this document,
- migration/schema artifacts if relevant,
- conformance/integration tests for affected contracts,
- role-specific documents where override relationships are impacted.

No contract-breaking change without explicit versioning and migration path.

## 17. Example Implementations (Non-Normative)
The examples below illustrate butler roles that conform to this base contract.

### 17.1 Health Butler
Role summary:
- Tracks medications, symptoms, measurements, conditions, and care routines.
- Produces reminders, trend summaries, and follow-up prompts.

Typical module profile:
- Domain tools module (health-specific schema/tools).
- Optional `calendar` module (appointments/follow-ups).
- Optional `email` or `telegram` output path via `notify` through Switchboard.

Typical schedules:
- Daily medication adherence check.
- Morning symptom/measurement prompt.
- Weekly trend and risk-summary review.

Example outcomes:
- Logs blood pressure and glucose readings over time.
- Flags missing doses and sends a routed reminder.
- Summarizes worsening trends for user escalation.

### 17.2 Relationship Butler
Role summary:
- Maintains personal CRM context: contacts, interactions, dates, reminders, gifts, and follow-ups.

Typical module profile:
- Domain tools module (relationship schema/tools).
- Optional `email`/`telegram` modules for communication workflows.

Typical schedules:
- Upcoming important-dates check.
- Stale-contact outreach suggestion cycle.

Example outcomes:
- Surfaces birthdays/anniversaries due soon.
- Proposes outreach messages using recent interaction context.

### 17.3 General Butler
Role summary:
- Catch-all assistant for tasks that do not map to a specialist role.

Typical module profile:
- General-purpose collections/state tooling.
- Optional connectors (email, calendar) for broad utility.

Typical schedules:
- Daily planning and inbox summary.
- Weekly cleanup and follow-up task rollup.

Example outcomes:
- Handles ad-hoc user requests without specialist schema assumptions.
- Delegates outbound notifications through Switchboard `notify`.

### 17.4 Ops/Automation Butler
Role summary:
- Runs recurring operational workflows (monitoring, runbooks, health checks, remediation drafts).

Typical module profile:
- Integration modules for incident/ticket/alerts systems.
- Optional approvals module for high-impact actions.

Typical schedules:
- Frequent service-health probes.
- Daily incident digest and risk review.

Example outcomes:
- Detects repeated failures and drafts remediation steps.
- Triggers human approval before executing risky actions.

Implementation quality checklist for all examples:
- Uses identity-scoped module credentials via env vars.
- Accepts and preserves Switchboard request context for routed execution.
- Returns structured success/error response envelopes to Switchboard.
- Emits trace/log/metric metadata with required low-cardinality attributes.

## 18. Non-Normative Note
Role-specific docs (for example Switchboard) should focus on role behavior and only restate base contracts when introducing stricter constraints or explicit overrides. Shared modules (for example memory) should define module-specific contracts in `docs/modules/`.
