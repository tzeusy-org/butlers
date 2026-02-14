# Messenger Butler: Permanent Definition

Status: Normative (Target State)  
Last updated: 2026-02-13  
Primary owner: Platform/Core

## 1. Role
The Messenger Butler is the single external delivery execution plane for the butler system.

All non-messenger butlers communicate externally only through `notify.v1` requests routed by Switchboard. Messenger is the terminal role that executes channel sends/replies against provider APIs (for example Telegram, Email) and returns normalized delivery outcomes.

This document is the source of truth for Messenger target-state behavior.

## 2. Design Goals
- One delivery owner for all outbound user-channel effects.
- Deterministic behavior under at-least-once upstream fanout.
- Strong idempotency and deduplication for side-effecting channel operations.
- Bounded retries/timeouts with explicit, typed failure outcomes.
- Explicit rate-limit and backpressure policy that protects providers and system stability.
- Auditable lineage from `origin_butler` + `request_id` to external provider receipt.

## 2.1 Base Contract Overrides
Inherits unchanged:
- All clauses in `docs/roles/base_butler.md` apply unless explicitly listed in `Overrides`.

Overrides:
- `base_clause`: `6. Core Tool Surface Contract / notify`
  `override`: For Messenger, `notify` is not a recursive outbound path. Messenger is the execution termination point for Switchboard-dispatched `notify.v1` envelopes and must not self-route delivery back through Switchboard.
  `rationale`: Prevent delivery recursion loops and preserve a single execution boundary for user-channel effects.

Additions:
- Messenger owns channel-facing send/reply tool surfaces. Non-messenger butlers must not expose those surfaces.
- Messenger enforces stronger idempotency, rate-limit, and delivery-audit requirements than base butler defaults.

## 3. Scope and Boundaries
### In scope
- Execution of outbound `send` and `reply` intents for supported channels.
- Channel adapter ownership (Telegram/Email now; extensible to SMS/chat providers).
- Delivery validation, target resolution, idempotency, retries, rate limits, and audit logging.
- Provider outcome normalization into stable butler contracts.

### Out of scope
- Ingress classification/decomposition and fanout orchestration (Switchboard-owned).
- Specialist domain decisions about whether/what to send (origin butler-owned policy).
- Cross-channel business semantics beyond delivery safety and correctness.

## 4. Integration Topology Contract
- All non-messenger outbound user interaction enters Switchboard as `notify.v1`.
- Switchboard validates `notify.v1` control-plane policy and dispatches to Messenger.
- Messenger executes channel delivery and returns canonical `notify_response.v1` wrapped in `route_response.v1`.
- Switchboard returns the normalized result to the originating butler/session.

Ownership invariant:
- User-channel side effects must have exactly one execution owner: Messenger.
- Direct specialist-to-provider dispatch is prohibited.

### 4.1 Route/Notify Transport Mapping
Switchboard-to-Messenger dispatch uses the base routed execution contract:
- Transport envelope: `route.v1` via Messenger `route.execute`.
- Notify payload location: `input.context.notify_request`.
- Messenger-side validation target: `notify_request` must conform to `notify.v1`.
- Success response: Messenger returns `route_response.v1` with `result.notify_response` containing `notify_response.v1`.
- Failure response: Messenger returns `route_response.v1` with canonical route error class and may include partial `result.notify_response` only when useful for diagnostics.

Canonical mapping shape:

```json
{
  "schema_version": "route.v1",
  "request_context": {},
  "input": {
    "prompt": "Execute outbound delivery request through Messenger.",
    "context": {
      "notify_request": {
        "schema_version": "notify.v1"
      }
    }
  }
}
```

## 5. Tool Surface and Ownership Contract
Messenger must provide:
- One Switchboard-facing `route.execute` delivery entrypoint for normalized notify execution.
- Channel adapter tools for concrete provider operations.

Channel tool naming and identity scope rules:
- Channel tools must be identity-prefixed: `user_<channel>_<verb>` and `bot_<channel>_<verb>`.
- User-scoped send/reply outputs must default to `approval_default="always"`.
- Bot-scoped send/reply outputs may default to `approval_default="conditional"` and remain policy-gated.
- Legacy non-prefixed channel tool names are not allowed in target state.

Canonical Telegram/Email examples:
- Telegram inputs: `user_telegram_get_updates`, `bot_telegram_get_updates`
- Telegram outputs: `user_telegram_send_message`, `user_telegram_reply_to_message`, `bot_telegram_send_message`, `bot_telegram_reply_to_message`
- Email inputs: `user_email_search_inbox`, `bot_email_search_inbox`
- Email outputs: `user_email_send_message`, `user_email_reply_to_thread`, `bot_email_send_message`, `bot_email_reply_to_thread`

Approval behavior by identity:
- User-scoped send/reply outputs are always approval-gated by default.
- Bot-scoped outputs are not default-gated; they become gated only when policy/config opts in.

Switchboard dispatch policy:
- Default outbound notify execution should target bot-scoped channel outputs unless policy explicitly requests user-scoped identity.

### 5.1 Ownership Boundary Matrix
- Switchboard owns ingress connectors, canonical ingest normalization, request-context assignment, and routing orchestration.
- Messenger owns outbound channel delivery execution (`send`/`reply`) and provider-facing delivery adapters.
- Non-messenger butlers must not expose direct outbound delivery tools and must request delivery through `notify.v1`.

## 6. Delivery Contract
### 6.1 Accepted Request Envelope
Messenger `route.execute` entrypoint accepts routed payloads from Switchboard and performs strict revalidation of embedded `notify.v1` before side effects.

Canonical request shape:

```json
{
  "schema_version": "notify.v1",
  "origin_butler": "health",
  "delivery": {
    "intent": "send|reply",
    "channel": "telegram|email|sms|chat",
    "message": "user-visible content",
    "recipient": "optional explicit recipient",
    "subject": "optional channel-specific subject"
  },
  "request_context": {
    "request_id": "uuid7",
    "received_at": "RFC3339 timestamp",
    "source_channel": "telegram|email|slack|api|mcp",
    "source_endpoint_identity": "ingress identity",
    "source_sender_identity": "sender identity",
    "source_thread_identity": "thread/chat id or null"
  }
}
```

Mandatory validation:
- `schema_version` must be supported.
- `origin_butler` must be present and must match the origin identity asserted in Switchboard-authenticated routed lineage metadata.
- `delivery.intent`, `delivery.channel`, and non-empty `delivery.message` are required.
- `reply` requires `request_context.request_id`, `source_channel`, `source_endpoint_identity`, and `source_sender_identity`.
- When target channel requires thread targeting, `source_thread_identity` is required for `reply`.

### 6.2 Target Resolution Rules
- `send` intent:
  - Uses explicit `delivery.recipient` when provided.
  - May use policy-defined default recipient only when channel policy allows it.
- `reply` intent:
  - Destination must derive from `request_context` lineage first.
  - Explicit recipient overrides are allowed only when consistent with policy and lineage checks.
- Missing required targeting fields must fail as `validation_error` with no side effect.

### 6.3 Content and Identity Presentation Rules
- Outbound content must include user-visible origin identity:
  - Email: subject must include `[origin_butler]` token.
  - Non-subject channels: message must include `[origin_butler]` prefix unless an equivalent explicit identity surface exists.
- Messenger may normalize formatting per channel (subject fallback, line wrapping, markdown/plaintext transforms) but must preserve semantic message meaning.

### 6.4 Response Contract
Messenger returns `notify_response.v1`:

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

Rules:
- Echo `request_context.request_id` when present.
- On success, include stable `delivery.delivery_id`.
- On error, include canonical class/message and explicit `retryable`.

### 6.5 Response-Class Normalization
- Messenger route-level failures use route-executor classes (`validation_error`, `target_unavailable`, `timeout`, `overload_rejected`, `internal_error`).
- When Switchboard or non-messenger callers consume `notify_response.v1`, class values must preserve the same canonical class set.
- Normalization guidance:
  - Local admission overflow or queue saturation -> `overload_rejected` (`retryable=true`).
  - Provider throttling/temporary unavailability (including `429`) -> `target_unavailable` (`retryable=true`).
  - Caller/schema/targeting violations -> `validation_error` (`retryable=false`).

## 7. Idempotency and Deduplication Contract
Delivery is side-effecting and must be idempotent across retries/replays/duplicate fanout.

Canonical idempotency key requirements:
- If `request_context.request_id` exists, key derivation must include:
  - `request_id`
  - `origin_butler`
  - `delivery.intent`
  - `delivery.channel`
  - normalized resolved target identity
  - normalized content hash (and subject hash when applicable)
- If `request_id` is absent, a caller-provided idempotency key is required for exactly-once semantics.

Duplicate handling:
- Duplicate of terminal success must return original success payload with original `delivery_id`.
- Duplicate of terminal non-retryable failure must return the same failure class/message.
- Duplicate while original is in-flight must coalesce to the same execution path; do not emit parallel sends.

Provider idempotency:
- When provider API supports idempotency keys, Messenger must propagate canonical key.
- When provider API lacks idempotency keys, Messenger must enforce dedupe through persisted key uniqueness and delivery-state reconciliation.

## 8. Rate Limits, Backpressure, and Admission Control
Messenger must enforce layered delivery throttling.

Required limit dimensions:
- Global delivery admission budget.
- Per-channel + identity budget (`telegram.bot`, `email.bot`, and user scopes when enabled).
- Per-recipient/per-thread anti-flood budget.

Required behaviors:
- Reply intents take precedence over non-reply sends under contention.
- Admission overflow must be explicit (no silent drop).
- Rate-limit/admission rejections must return retryable typed errors (`overload_rejected` for local admission; `target_unavailable` for transient provider throttling).
- Provider `429` responses must honor `Retry-After` when present.

Fairness rule:
- One noisy origin butler/channel must not starve unrelated origins/channels.

## 9. Timeout, Retry, and Circuit-Breaking Contract
Required policy dimensions:
- Per-channel operation timeout.
- Bounded retry count with exponential backoff + jitter.
- Per-provider circuit breaker (`closed`, `open`, `half-open`).

Retry policy:
- Retry only retryable failures (network/transient provider failures, timeout-class, rate-limit-class).
- Validation/auth/permission/content-policy failures are non-retryable and fail fast.

Error normalization:
- Invalid input/targeting -> `validation_error`.
- Provider/channel unavailable or throttled -> `target_unavailable` (retryable when transient).
- Timeout budget exceeded -> `timeout` (retryable by policy).
- Local admission overflow/saturation -> `overload_rejected` (retryable by policy).
- Unexpected internal failures -> `internal_error`.

## 10. Ordering and Conversation Semantics
- Per-thread causal ordering must be preserved for reply-capable channels.
- Cross-thread global ordering is not guaranteed.
- Retries/replays must not reorder already-confirmed deliveries within the same `(channel, thread)` sequence.
- Delivery history must preserve enough metadata to reconstruct causal order.

## 11. Configuration and Environment Contract
### 11.1 Config Surfaces
Messenger channel credentials must be identity-scoped in module config:
- `[modules.telegram.bot]`, `[modules.telegram.user]`
- `[modules.email.bot]`, `[modules.email.user]`

Environment variable name fields (`*_env`) must:
- be non-empty,
- be valid env-var identifiers,
- be validated at startup.

Secrets policy:
- Secrets must come from environment variables only.
- Inline secret literals in `butler.toml` are prohibited.

### 11.2 Typical Default Environment Variables
Typical bot-scoped production defaults:
- `BUTLER_TELEGRAM_TOKEN`
- `BUTLER_EMAIL_ADDRESS`
- `BUTLER_EMAIL_PASSWORD`

Optional user-scoped defaults when enabled:
- `USER_TELEGRAM_TOKEN`
- `USER_EMAIL_ADDRESS`
- `USER_EMAIL_PASSWORD`

Startup requirements:
- The authoritative required env-var names come from configured `*_env` fields.
- Enabled credential scopes with missing env values are startup-blocking errors.
- Disabled scopes must not be treated as required credentials.

### 11.3 Delivery Policy Configuration
Target-state Messenger config must include explicit delivery policy controls:
- max in-flight deliveries.
- per-channel timeout/retry/backoff policy.
- per-channel and per-recipient rate limits.
- dedupe retention window.
- dead-letter replay policy.

## 12. Persistence and Audit Contract
Messenger must keep durable delivery records independent of transient runtime memory.

Required durable surfaces:
- `delivery_requests`: canonical normalized request, idempotency key, lineage metadata, terminal status.
- `delivery_attempts`: each provider attempt with timestamp, outcome, latency, error class, retryability.
- `delivery_receipts`: provider delivery ids, webhook confirmations/read receipts when available.
- `delivery_dead_letter`: exhausted or manually quarantined deliveries with replay metadata.

Required lineage fields across audit surfaces:
- `request_id` (when present)
- `origin_butler`
- `channel`
- resolved target identity (or approved redacted equivalent)
- `intent`
- `delivery_id` (once assigned)
- `error_class` and failure summary when failed
- timestamps for create/attempt/terminal transition

Idempotency invariant:
- DB uniqueness must prevent duplicate terminal side effects for one canonical idempotency key.

## 13. Security and Safety Invariants
- Origin spoofing prevention: Messenger must verify `origin_butler` against authenticated routed caller metadata, not payload alone.
- Caller authentication: `route.execute` enforces `request_context.source_endpoint_identity` against `trusted_route_callers` (default: `["switchboard"]`) before any business logic or delivery side effects. Unknown callers receive a deterministic `validation_error` with `retryable=false`.
- Caller authorization: Only callers listed in `trusted_route_callers` may terminate `notify.v1` delivery requests through Messenger `route.execute`. This is a hard security boundary that prevents unauthenticated network callers from triggering outbound delivery adapters directly.
- Least privilege: credential scopes (`bot`, `user`) must be isolated and only used by corresponding tool surfaces.
- Sensitive data hygiene: credentials, tokens, and full raw message payloads must not be logged.
- Policy enforcement: recipient allow/deny checks must run before provider calls.
- No blind broadcast on missing targeting context.

### 13.1 Route Execution Authentication Contract
All butlers (not just Messenger) enforce `trusted_route_callers` on `route.execute`:
- Default trusted callers: `["switchboard"]`.
- Configurable via `[butler.security].trusted_route_callers` in `butler.toml`.
- Rejection is deterministic: unauthorized callers always receive `validation_error` with a message identifying the rejected `source_endpoint_identity`.
- The check runs after route envelope parsing but before any spawner trigger or delivery adapter call.

### 13.2 Rollout and Compatibility
- **Backward compatible**: The default `trusted_route_callers = ["switchboard"]` matches the existing Switchboard-only dispatch topology. No butler.toml changes are required for standard deployments.
- **Custom deployments**: Operators who route through non-Switchboard control planes must add their caller identities to `[butler.security].trusted_route_callers`.
- **Empty list**: Setting `trusted_route_callers = []` rejects all `route.execute` callers, effectively disabling routed execution. This is useful for butlers that should never accept routed requests.
- **Migration path**: Existing deployments using the default Switchboard topology require no changes. The guardrail is transparent to authorized callers.

## 14. Observability Contract
### 14.1 Metrics
Target namespace:
- `butlers.messenger.*`

Core counters:
- `butlers.messenger.delivery_requested`
- `butlers.messenger.delivery_deduplicated`
- `butlers.messenger.delivery_sent`
- `butlers.messenger.delivery_failed`
- `butlers.messenger.retry_attempt`
- `butlers.messenger.rate_limited`
- `butlers.messenger.admission_rejected`

Core histograms:
- `butlers.messenger.delivery_latency_ms`
- `butlers.messenger.provider_latency_ms`
- `butlers.messenger.queue_wait_ms`

Required low-cardinality attributes:
- `channel`
- `intent`
- `identity_scope`
- `outcome`
- `error_class`
- `origin_butler`

### 14.2 Traces and Logs
Root span:
- `butlers.messenger.delivery`

Recommended child spans:
- `butlers.messenger.validate`
- `butlers.messenger.idempotency_check`
- `butlers.messenger.rate_limit_check`
- `butlers.messenger.provider_send`
- `butlers.messenger.persistence_write`

Correlation rule:
- Logs, traces, and delivery tables must be joinable by request lineage (`request_id` when present) plus Messenger delivery identifiers.

## 15. SLO/SLI Contract
Minimum target SLI set:
- Delivery success rate by channel.
- p95 end-to-end delivery latency by channel/intent.
- Duplicate side-effect rate (must trend to zero under canonical dedupe key).
- Rate-limit rejection rate and queue saturation.
- Dead-letter volume and replay success rate.

Operational rule:
- Error-budget burn should automatically tighten retry/admission policies before causing provider abuse or systemic instability.

## 16. Design Choices and Rationale
- Centralized delivery ownership reduces policy drift and duplicate channel logic across specialist butlers.
- At-least-once upstream fanout requires strong idempotency at Messenger to avoid duplicate user-facing sends.
- Layered rate limits are required because provider quotas, user anti-spam constraints, and local runtime capacity are different control problems.
- Identity-scoped credentials (`user_*` vs `bot_*`) keep approval and auditing semantics explicit and enforceable.
- Durable delivery audit surfaces are required for post-incident reconstruction and safe replay.

## 17. Target-State Deltas from Current Implementation
- Switchboard-target resolution must move from "any butler with matching channel module" to explicit Messenger-only channel execution ownership.
- Outbound notify handling must be fully envelope-driven (`notify.v1`/`notify_response.v1`) with strict schema validation.
- Delivery idempotency must be enforced by canonical keys + DB uniqueness, not best-effort retries alone.
- Rate-limit and admission-control policy must become first-class configurable behavior, not adapter-local heuristics.
- Delivery audit model must include attempt-level durability and dead-letter replay surfaces.

## 18. Conformance Checklist (Target State)
- `route.execute` rejects missing/invalid `input.context.notify_request` with deterministic `validation_error`.
- Duplicate deliveries for the same canonical idempotency key return the original terminal payload and do not emit a second provider send.
- In-flight duplicates coalesce to one execution path.
- Missing required reply lineage fields fail with `validation_error` and no side effect.
- Local admission overflow fails with retryable `overload_rejected`.
- Provider `429` handling honors `Retry-After` and maps to retryable `target_unavailable`.
- Successful delivery responses include stable `delivery.delivery_id`.
- Delivery audit tables persist request, attempt, and terminal lineage fields with `request_id` when present.

## 19. Target-State Rollout Phases
1. Ownership enforcement:
   - Enforce Messenger-only outbound delivery execution in Switchboard registry/routing policy.
2. Envelope alignment:
   - Require `route.v1` transport with embedded `notify.v1`, and `route_response.v1` with embedded `notify_response.v1`.
3. Idempotency + durability:
   - Add canonical idempotency key derivation, DB uniqueness, attempt-level persistence, and dead-letter storage.
4. Reliability policy:
   - Add explicit admission/rate-limit policy, retry/timeout/circuit controls, and typed error normalization.
5. Operations hardening:
   - Enforce telemetry/SLI coverage and alerting tied to error-budget burn and delivery stability.

## 20. Channel Egress Ownership Enforcement (Migration Guidance)

### 20.1 Current Enforcement Mechanism

The daemon module loader (`ButlerDaemon._register_module_tools`) enforces
Messenger-only channel egress ownership at startup. For any butler whose
`config.name` is not `"messenger"`, the loader:

1. Inspects each module's declared I/O tools (`user_inputs()`,
   `user_outputs()`, `bot_inputs()`, and `bot_outputs()`) defensively.
2. Identifies tools matching the channel egress pattern:
   `^(?:user|bot)_<channel>_<action>$` where `<action>` is a channel
   send or reply verb (for example `user_telegram_send_message`,
   `bot_email_reply_to_thread`).
3. Strips matching tools from the declared set and suppresses their
   registration via the `_SpanWrappingMCP` filtered-tool mechanism.
4. Logs the stripped tools at INFO level with a reminder to use `notify.v1`.

Modules loaded by non-messenger butlers (including Switchboard) retain full
ingress capability; only outbound egress tools are filtered.

### 20.2 Compatibility Shims

| Shim | Behavior | Removal Target |
|---|---|---|
| Silent egress stripping | Egress tools on non-messenger modules are silently filtered instead of raising a hard startup error. Modules can still declare egress descriptors without causing crashes. | Phase 2 (hard error) |
| Ingress-only module loading | Switchboard and other butlers can load channel modules (telegram, email) for ingress connectors without triggering egress enforcement. | Permanent (by design) |

### 20.3 Migration Path

**Phase 1 (current):** Silent filter/strip with INFO-level logging.
- Non-messenger butlers that previously relied on direct channel send/reply
  tools will find those tools absent at runtime.
- Migrate outbound delivery to the `notify.v1` envelope contract
  (see `docs/roles/base_butler.md` section 11.1).

**Phase 2 (target):** Hard startup error.
- Once all non-messenger modules have removed egress output descriptors,
  upgrade enforcement from silent stripping to `ChannelEgressOwnershipError`
  at startup.
- Timeline: After all roster butlers pass clean startup with zero stripped
  tools logged.

**Phase 3 (cleanup):** Remove compatibility shims.
- Remove the silent-strip code path from `_register_module_tools`.
- Remove `ChannelEgressOwnershipError` (no longer reachable).
- Egress output descriptors on non-messenger modules become a schema
  validation error in module registration.

### 20.4 Developer Checklist for Module Migration

1. Remove channel egress tool descriptors from `user_outputs()` and
   `bot_outputs()` in non-messenger module classes.
2. Replace direct `user_telegram_send_message` / `bot_email_send_email`
   calls with `notify.v1` envelope submissions through the butler's
   `notify` core tool.
3. Verify the module loads cleanly with zero "Stripping channel egress
   tools" log lines on non-messenger butlers.
4. Update module tests to assert egress tools are absent from the
   non-messenger tool surface.

## 21. Non-Goals
- Replacing Switchboard as ingress orchestration owner.
- Embedding specialist domain decision logic in Messenger.
- Exposing unrestricted direct provider send tools to non-messenger roles.
- Guaranteeing global total message order across all channels and threads.
