# Notify Contract

## Purpose
Defines the `notify` MCP tool and its versioned envelope contract (`notify.v1`) for outbound user interaction requests from non-messenger butlers, routed through Switchboard to the Messenger butler for delivery.

## Requirements

### Requirement: Notify Tool Registration
Every butler daemon SHALL register a `notify(channel?, message, entity_id?, recipient?, subject?, intent?, emoji?, request_context?, priority?)` MCP tool during startup. Runtime instances MUST be able to call this tool to send outbound notifications. The tool MUST be available in every butler's MCP tool surface regardless of which modules are enabled.

Note: target resolution is keyed on `entity_id` (a `public.entities` UUID), resolved against `relationship.entity_facts`. An earlier design used a `contact_id` keyed on `public.contacts` / `public.contact_info`; that identity path was retired in favor of the entity graph. The requirements below reflect the entity-graph reality.

The `priority` parameter (enum: `high`, `medium`, `low`, default `medium`) is added to support time-aware delivery. Priority determines quiet-hours behavior: high-priority notifications always deliver immediately; medium and low-priority notifications are subject to quiet-hours deferral when delivery preferences are configured.

`channel` is OPTIONAL. When the caller omits it, the notify tool resolves it
before any channel-dependent validation runs (see the **Preferred-Channel
Resolution on Omitted Channel** requirement): an `entity_id`-targeted call
honours the entity's `prefers-channel` fact when deliverable, else falls back
to telegram → email; a call with no `entity_id` defaults to telegram. A
caller-forced `channel` is never overridden by preference resolution.

#### Scenario: Tool available to runtime instance
- **WHEN** a runtime instance spawned by a butler lists available MCP tools
- **THEN** the `notify` tool MUST appear in the tool list with parameters `channel` (optional string; resolved when omitted), `message` (required string), `entity_id` (optional UUID string), `recipient` (optional string), `subject` (optional string), `intent` (optional string), `emoji` (optional string), `request_context` (optional object), and `priority` (optional string, default `medium`)

#### Scenario: Tool registered at startup
- **WHEN** a butler daemon starts up
- **THEN** the `notify` tool MUST be registered as a core MCP tool before the butler accepts any triggers

### Requirement: Quiet Hours Delivery Gate
Before constructing the notification envelope, the `notify()` tool SHALL check the butler's `delivery_preferences` for quiet hours enforcement. If the current time (in the user's configured timezone) falls within quiet hours and the notification's priority is not `high`, the notification SHALL be deferred to the `deferred_notifications` table instead of being delivered immediately.

#### Scenario: Notification deferred during quiet hours
- **WHEN** `notify(channel="telegram", message="Weekly report", priority="medium")` is called
- **AND** delivery preferences have `quiet_hours_start="22:00"`, `quiet_hours_end="07:00"`, `timezone="America/New_York"`
- **AND** the current time in America/New_York is 23:15
- **THEN** the notification is stored in `deferred_notifications` with `deliver_at` set to the next 07:00 America/New_York
- **AND** the tool returns `{"status": "deferred", "deliver_at": "<ISO timestamp>", "notification_id": "<uuid>"}`

#### Scenario: High-priority bypasses quiet hours
- **WHEN** `notify(channel="telegram", message="Critical alert", priority="high")` is called during quiet hours
- **THEN** the notification is delivered immediately via the standard envelope pipeline
- **AND** quiet hours are NOT applied

#### Scenario: No delivery preferences configured
- **WHEN** `notify()` is called and no `delivery_preferences` row exists for this butler
- **THEN** the notification is delivered immediately regardless of time or priority (backward compatible)

#### Scenario: Quiet hours with channel override
- **WHEN** `notify(channel="email", message="Report", priority="medium")` is called
- **AND** delivery preferences have `override_channels={"email": {quiet_hours_start: "20:00", quiet_hours_end: "09:00"}}`
- **AND** the current time is 21:00 local
- **THEN** the email-specific quiet hours apply and the notification is deferred

### Requirement: Owner-Default-Page Deferred Delivery
After the earlier `delivery_preferences` gate, `notify()` SHALL durably defer
an eligible routine owner-default notification when the `approvals_policy`
quiet-hours window or an active suppressing context applies. Eligibility is
exactly: no `entity_id`, no explicit `recipient`, intent `send` or `insight`,
priority other than `high`, and an available notification pool. The originating
butler's `deferred_notifications` table SHALL store the full resolved
`notify.v1` envelope; message content SHALL NOT be copied into
`public.attention_ledger`.

The approvals-policy check SHALL run before the context check. A policy hold
uses the first whole local hour after the existing inclusive quiet-window end as
its UTC `deliver_at`. A context-only hold uses the latest expiry among all
active `dnd`/`sleeping` suppressors as its UTC `deliver_at`. When both holds
apply, the later anchor SHALL win so the envelope cannot flush while either
hold remains active. A queued result SHALL return `status="deferred"` with its
`notification_id`, `deliver_at`, `channel`, and `priority`.

The earlier `delivery_preferences` mechanism SHALL remain first and unchanged.
High-priority, explicitly targeted, and other-intent notifications SHALL retain
their existing behavior. Policy/context read failures SHALL retain the existing
fail-open immediate path.

#### Scenario: Approvals-policy quiet hours parks the full envelope
- **WHEN** `notify(message="Heads up", priority="medium")` is called with no
  `entity_id` and no `recipient`
- **AND** the current time falls inside the `approvals_policy` quiet-hours
  window
- **THEN** the fully resolved `notify.v1` envelope is inserted into the
  originating butler's `deferred_notifications` table with `status="pending"`
  and a policy-derived UTC `deliver_at`
- **AND** the tool returns `{"status": "deferred", "notification_id": "<uuid>",
  "deliver_at": "<ISO timestamp>", ...}` without calling Switchboard

#### Scenario: Context hold parks until every active suppressor expires
- **WHEN** an eligible `notify()` call is outside approvals-policy quiet hours
- **AND** both a DND signal and a sleeping signal are active with different
  expiry times
- **THEN** the tool stores the full envelope with `deliver_at` equal to the
  later expiry and a deterministic `context_bus:dnd` reason
- **AND** the tool returns `status="deferred"` without immediate delivery

#### Scenario: Concurrent policy and context holds use the later anchor
- **WHEN** approvals-policy quiet hours and an active DND/sleeping signal both
  select a durable hold
- **THEN** the context bus is consulted after the policy check
- **AND** the row's `deliver_at` is the later of the policy and context anchors
- **AND** the ledger reason records both active hold reasons

#### Scenario: High priority and targeted notifications are exempt
- **WHEN** `notify(..., priority="high")` is called, OR `entity_id` or
  `recipient` is provided, OR intent is neither `send` nor `insight`
- **THEN** this requirement does not apply and the existing delivery path is
  preserved

#### Scenario: Deferred persistence failure is retryable and never sends
- **WHEN** an eligible policy or context hold is selected but inserting the
  deferred row fails
- **THEN** `notify()` returns `status="error"` with `retryable=true`
- **AND** it does not call Switchboard or return a suppressed status

#### Scenario: Ledger failure after queueing preserves the hold
- **WHEN** the deferred row is inserted successfully but its ledger write fails
- **THEN** the queued row remains pending and `notify()` still returns the
  deferred result

### Requirement: Attention Ledger Recording at the notify() Boundary
Every terminal decision the `notify()` owner-default quiet-hours gate makes SHALL be recorded to `public.attention_ledger` with a closed outcome vocabulary (`delivered`, `coalesced`, `deferred`, `suppressed`, `failed`) and a machine-readable `reason`. A ledger-write failure MUST NOT block or fail the notification it describes (best-effort, fail-open).

`deferred` and `failed` are distinct and MUST NOT be conflated: `deferred` is a benign, chosen hold that resolves on its own (a quiet-hours window ending, a coalescing flush tick) with no caller action required. `failed` is a genuine terminal failure at this attempt — no recipient configured, a transport/delivery error, an unexpected exception — that nothing automatically retries unless the caller explicitly enqueues a retry envelope (e.g. via `insert_deferred_notification`) and records the resulting row id as `notification_ref`. This distinction applies identically to every caller that composes the same gating/dispatch primitives `notify()` uses from outside a butler daemon's own MCP closure (e.g. `butlers.jobs.secrets_lifecycle`, `butlers.jobs.home._send_notify`, `butlers.jobs.decision_review._deliver`, `butlers.core.fleet_halt_attention`) — see those modules' docstrings for why each is a process-boundary-forced consumer rather than a direct `notify()` caller.

#### Scenario: A genuine delivery failure is recorded as failed, not deferred
- **WHEN** any notify-boundary caller (the `notify()` tool itself, or a process-boundary-forced consumer composing the same primitives) cannot resolve a recipient, or the underlying `deliver()` dispatch returns `status="failed"`, or an unexpected exception occurs mid-dispatch
- **THEN** a `public.attention_ledger` row is written with `outcome="failed"` and a `reason` identifying the failure class (e.g. `"no_recipient_configured"`, `"delivery_error:<detail>"`, `"unexpected_error:<ExceptionType>"`)
- **AND** this row is NEVER written with `outcome="deferred"` — that value is reserved for a benign hold the system will retry on its own

#### Scenario: A retried failed delivery records its retry envelope
- **WHEN** a caller enqueues a retry envelope for a transport-failed delivery (e.g. via `insert_deferred_notification` on a `deferred_notifications` table that a scheduler tick will flush)
- **THEN** the corresponding `outcome="failed"` ledger row's `notification_ref` is set to the enqueued row's id, so the failure is traceable to its retry attempt rather than a dead end

#### Scenario: An unexpected exception retries only when enough state is resolved
- **WHEN** a process-boundary consumer's per-credential dispatch raises an unexpected exception AFTER the message AND recipient have been resolved (e.g. `deliver()` itself raises instead of returning `status="failed"`, or a ledger write faults post-resolution)
- **THEN** the failure is treated as retryable: a retry envelope is enqueued on the SAME single deferral path the transport-failed case uses (`_enqueue_deferred_envelope` / `insert_deferred_notification`, with supersede-at-enqueue dedup), and the `outcome="failed"` ledger row records `reason="unexpected_error_retry:<ExceptionType>"` with `notification_ref` set to the enqueued row's id
- **AND WHEN** the exception raises BEFORE the message/recipient are resolvable (e.g. inside the last-notified-state or suppression lookups)
- **THEN** there is nothing safe to enqueue, so the row is stamped honestly as `reason="unexpected_error:<ExceptionType>"` with `notification_ref` null — never a half-built or mis-addressed envelope
- **AND** the retry's `deliver_at` honors any resolved quiet-hours deferral so the retry is not redelivered inside quiet hours (the flush path gates purely on `deliver_at`), and the debounce marker is NOT advanced on the retry path (only a confirmed direct delivery advances it)

#### Scenario: Owner-default policy deferral is linked in the ledger
- **WHEN** `notify()` parks an eligible owner-default call because of
  `public.approvals_policy` quiet hours
- **THEN** it writes `outcome="deferred"`, `reason="policy_quiet_hours"`, and
  the inserted deferred row id as `notification_ref`

#### Scenario: Owner-default context deferral is linked in the ledger
- **WHEN** `notify()` parks an eligible owner-default call because of an active
  suppressing context signal
- **THEN** it writes `outcome="deferred"`,
  `reason="context_bus:<signal_type>"`, and the inserted deferred row id as
  `notification_ref`

#### Scenario: Deferred persistence failure is recorded as failed
- **WHEN** policy or context parking is selected but the deferred-row INSERT
  fails
- **THEN** the tool makes a best-effort `outcome="failed"` ledger record with a
  `deferred_persistence_error:<ExceptionType>` reason
- **AND** the ledger attempt cannot convert the result into immediate delivery

#### Scenario: Delivery-preferences defer is recorded
- **WHEN** any notify-boundary caller (the `notify()` tool itself, or a process-boundary-forced consumer composing the same primitives) defers a notification via the per-butler `delivery_preferences` quiet-hours mechanism
- **THEN** a `public.attention_ledger` row is written with `outcome="deferred"` and `reason="delivery_preferences_quiet_hours"`, and `notification_ref` set to the enqueued `deferred_notifications` row id
- **AND** the `delivery_preferences` gate is checked FIRST, ahead of the approvals-policy and context-bus gates, mirroring `notify()`'s own gate ordering
- **AND** a process-boundary consumer that has no butler identity of its own (e.g. `butlers.jobs.secrets_lifecycle`) keys the `delivery_preferences` lookup on the identity it already delivers under (`"switchboard"`) — `delivery_preferences` is a per-schema table, so the lookup uses that identity's own pool

#### Scenario: Successful delivery is recorded
- **WHEN** `notify()` successfully delivers a notification (either via direct Switchboard self-delivery or via the switchboard client)
- **THEN** a `public.attention_ledger` row is written with `outcome="delivered"`, and `notification_ref` set to the delivery's `notification_id` when the delivery result provides one

#### Scenario: Ledger write failure never blocks delivery
- **WHEN** the `public.attention_ledger` table is unavailable (e.g. an unmigrated database) or the INSERT otherwise fails
- **THEN** `notify()` proceeds exactly as it would without this requirement — the ledger write is logged at WARNING and swallowed, never raised

### Requirement: Attention Ledger Recording at the Deferred-Notification Flush
Every successful flush-time delivery SHALL be recorded to `public.attention_ledger` with `source="notify"`: `outcome="delivered"` for a solo-row send, `outcome="coalesced"` (one ledger row per underlying notification) for a composed digest send (see the **Same-Window Coalescing of Deferred Notifications** requirement below). A ledger-write failure MUST NOT block or fail the notification it describes (best-effort, fail-open — same contract as every other ledger-recording call site).

#### Scenario: Composed digest records one coalesced row per underlying notification
- **WHEN** the flush pass delivers a composed digest of 3 due notifications
- **THEN** 3 `public.attention_ledger` rows are written, each with
  `source="notify"`, `outcome="coalesced"`, and its own row's
  `notification_ref`

### Requirement: Same-Window Coalescing of Deferred Notifications
When the deferred-notification flush pass (`_tick_deferred_notification_pass`) finds more than one due (`status='pending' AND deliver_at <= now`) row targeting the same delivery target (channel + recipient), it SHALL compose them into ONE message and deliver them via a single `notify_fn` call instead of one send per row. A delivery target with exactly one due row SHALL be delivered unchanged (its stored envelope, verbatim).

#### Scenario: Multiple same-target due notifications compose into one send
- **WHEN** the flush pass runs and finds 3 due notifications all addressed to
  the same (channel, recipient) pair
- **THEN** exactly one `notify_fn` call is made, carrying a composed message
  that includes all 3 underlying messages
- **AND** all 3 underlying rows are marked `status='delivered'` with the same
  `delivered_at`

#### Scenario: A solo due notification is delivered unchanged
- **WHEN** the flush pass finds exactly one due notification for a given
  delivery target
- **THEN** `notify_fn` is called with that row's stored envelope, unmodified
- **AND** the row is marked `delivered` exactly as it was before this change

#### Scenario: Different recipients are never coalesced
- **WHEN** two due notifications target different explicit recipients (or one
  targets an explicit recipient and the other targets none, i.e. the owner's
  default channel)
- **THEN** each is delivered via its own `notify_fn` call — never folded into
  one composed message together

#### Scenario: A failed composed send leaves the whole group pending
- **WHEN** `notify_fn` raises for a composed multi-row digest
- **THEN** every row in that group remains `status='pending'` for retry on
  the next tick — no row in the group is marked `delivered` while others are
  not

### Requirement: Priority Normalization for Ledger Comparability
`notify()`'s 3-level `priority` enum (`high`/`medium`/`low`) SHALL be normalized onto the same 1-100 `priority_score` scale the insight pipeline uses (RFC 0011 Priority Scoring Convention), so `public.attention_ledger` rows from both boundaries are comparable. `"high"` MUST normalize to a score at or above `URGENT_PRIORITY_THRESHOLD` (90).

#### Scenario: high/medium/low map to comparable scores
- **WHEN** a ledger row is recorded for `notify(priority="high")`, `notify(priority="medium")`, and `notify(priority="low")`
- **THEN** the recorded `priority_score` values are 90, 50, and 20 respectively, and `priority_label` preserves the original string

### Requirement: Attention Ledger Reader
The dashboard API SHALL expose a windowed, filterable reader over `public.attention_ledger` and a per-source delivery-vs-suppression summary, so that a source silently failing at either choke point (`notify()` or `delivery_cycle()`) is observable instead of requiring direct DB access.

`GET /api/attention/ledger` SHALL return a paginated, newest-first list of ledger rows, filterable by `intent`, `source` (the ledger's own choke-point column — `notify`/`insight` for proactive egress, plus `discretion` for the connector discretion layer's failover-exhausted inbound suppression, see connector-base-spec), `outcome`, and `origin_butler`, and windowed by `since`/`until` (`occurred_at` bounds). `GET /api/attention/ledger/summary` SHALL return, for a `since`/`until` window (defaulting to the last 7 days when `since` is omitted), one row per distinct `origin_butler` with `delivered`/`coalesced`/`deferred`/`suppressed`/`failed`/`total` counts and a `suppressed_never_delivered` boolean: `true` when that `origin_butler` has `suppressed > 0` and `delivered == 0` in the window. Both endpoints MUST follow the repo's degraded-envelope convention (`butlers/CLAUDE.md` API Conventions): a genuinely unreachable ledger pool renders `source_available=false` on an otherwise-empty/zero payload, never a truthful "no suppression" or "no rows".

The Trust Console panel that renders this summary (`AttentionLedgerPanel`) MUST render a non-zero `failed` count in a visually distinct, red/alerting tone — never the same neutral tone as `coalesced`/`deferred`/`total` — since a `failed` count represents genuine, un-retried delivery breakage in the exact surface built to prove silence is chosen.

Naming note: the summary's "per source" grouping is `origin_butler` (which butler/job attempted the egress — e.g. `secrets_lifecycle`, `home`), a distinct dimension from the ledger's own `source` column (the `notify`/`insight`/`discretion` choke-point literal). Both are independently exposed: `origin_butler` as the summary's grouping key and an optional list-endpoint filter, `source` as a list/summary filter on the choke-point column.

#### Scenario: Suppressed-but-never-delivered source is flagged
- **WHEN** `GET /api/attention/ledger/summary` is called for a window in which `origin_butler="secrets_lifecycle"` has 120 rows with `outcome="suppressed"` and 0 rows with `outcome="delivered"`
- **THEN** the response's `by_source` includes an entry for `secrets_lifecycle` with `suppressed=120`, `delivered=0`, and `suppressed_never_delivered=true`
- **AND** `"secrets_lifecycle"` appears in the response's `flagged_sources` list

#### Scenario: A healthy source is not flagged
- **WHEN** an `origin_butler` has both `delivered > 0` and `suppressed > 0` rows in the window
- **THEN** its `suppressed_never_delivered` is `false`

#### Scenario: Failed deliveries are counted separately from deferred
- **WHEN** `GET /api/attention/ledger/summary` is called for a window in which `origin_butler="secrets_lifecycle"` has 21 rows with `outcome="failed"` and 0 rows with `outcome="deferred"`
- **THEN** the response's `by_source` entry for `secrets_lifecycle` has `failed=21` and `deferred=0` — the two counts are never merged into one bucket

#### Scenario: List endpoint is windowed and filterable
- **WHEN** `GET /api/attention/ledger?since=<t1>&until=<t2>&outcome=suppressed&origin_butler=secrets_lifecycle` is called
- **THEN** only rows with `occurred_at` between `t1` and `t2`, `outcome="suppressed"`, and `origin_butler="secrets_lifecycle"` are returned, newest-first, paginated

#### Scenario: Unreachable ledger pool degrades honestly
- **WHEN** the ledger's DB pool is unreachable
- **THEN** both endpoints return HTTP 200 with an empty/zero payload and `source_available=false` — never a truthful-looking "no suppression happened" or "no rows match"

#### Scenario: Unmigrated table is a true empty result, not a degraded one
- **WHEN** `public.attention_ledger` does not exist yet (pre-migration database)
- **THEN** both endpoints return an empty/zero payload with `source_available=true` — this is a genuinely-empty state, not a source failure

### Requirement: notify.v1 Envelope Schema
The notify envelope includes `schema_version` ("notify.v1"), `origin_butler` (requesting butler's name), `delivery` (intent, channel, message, optional recipient/subject/emoji), and optional `request_context` for reply/react targeting.

#### Scenario: Send intent envelope
- **WHEN** `notify(channel="telegram", message="Hello", intent="send")` is called
- **THEN** a `notify.v1` envelope is constructed with `delivery.intent="send"`, `delivery.channel="telegram"`, and `delivery.message="Hello"`
- **AND** `origin_butler` matches the calling butler's name

### Requirement: Delivery Intent Validation
Four delivery intents are supported: `send`, `reply`, `react`, and `insight`. Each has specific field requirements.

#### Scenario: Send intent
- **WHEN** `intent="send"` is used
- **THEN** `message` is required and must be non-empty
- **AND** `request_context` is optional

#### Scenario: Reply intent requires request_context
- **WHEN** `intent="reply"` is used
- **THEN** `message` is required
- **AND** `request_context` must include `request_id`, `source_channel`, `source_endpoint_identity`, and `source_sender_identity`
- **AND** for telegram, `source_thread_identity` is required for reply targeting

#### Scenario: React intent requires emoji and thread identity
- **WHEN** `intent="react"` is used
- **THEN** `emoji` is required
- **AND** `request_context` must include `source_thread_identity` (for telegram: `<chat_id>:<message_id>`)
- **AND** `message` is not required

#### Scenario: Insight intent
- **WHEN** `intent="insight"` is used
- **THEN** `message` is required and must be non-empty
- **AND** `request_context` is optional
- **AND** the Messenger butler SHALL treat this as functionally equivalent to `intent="send"` for delivery mechanics
- **AND** the Messenger MAY apply visual differentiation for insight messages (e.g., formatting, labels)

#### Scenario: Missing message for send/reply/insight
- **WHEN** `intent` is `"send"`, `"reply"`, or `"insight"` and `message` is `None` or empty
- **THEN** the tool returns `{"status": "error", "error": "Missing required 'message' parameter..."}`

#### Scenario: Unsupported intent
- **WHEN** `intent` is not one of `send`, `reply`, `react`, `insight`
- **THEN** the tool returns an error response

### Requirement: Channel Validation
Only `telegram` and `email` channels are currently supported. Unsupported channels produce an immediate error response.

#### Scenario: Supported channel
- **WHEN** `channel="telegram"` or `channel="email"` is passed
- **THEN** the notify tool proceeds with envelope construction

#### Scenario: Unsupported channel
- **WHEN** `channel="sms"` is passed
- **THEN** the tool returns `{"status": "error", "error": "Unsupported channel 'sms'..."}`

### Requirement: Preferred-Channel Resolution on Omitted Channel
When the caller omits `channel`, the notify tool SHALL resolve it before any
channel-dependent validation runs, and a caller-forced `channel` SHALL never be
overridden by this resolution:
- if `entity_id` is provided, resolve via `resolve_outbound_channel()` against
  the entity's active `prefers-channel` fact (see the `relationship-facts`
  spec), constrained to the deliverable set (`telegram`, `email`); if the
  preferred channel is not deliverable, or no preference exists, or the entity
  or database is unavailable, fall back to telegram, then email (first
  deliverable and reachable);
- if `entity_id` is not provided, default to `telegram` (the historical
  owner-page channel), preserving behavior for callers that relied on a
  channel always being present.

This requirement adds no new deliverable channels; the deliverable set remains
as defined by the Channel Validation requirement above.

#### Scenario: Preference honored when deliverable
- **WHEN** a notification targets an `entity_id` whose entity has an active
  `prefers-channel="telegram"` fact, the entity has a telegram handle, and the
  caller did not force a different channel
- **THEN** the notification is sent on telegram

#### Scenario: Preference skipped when not deliverable
- **WHEN** a notification targets an `entity_id` whose entity prefers a channel
  not in the deliverable set (e.g. `prefers-channel="discord"`)
- **THEN** the preference is ignored without error
- **AND** channel selection falls back to telegram, then email

#### Scenario: No preference falls back unchanged
- **WHEN** a notification targets an `entity_id` whose entity has no active
  `prefers-channel` fact
- **THEN** channel selection falls back to telegram, then email, exactly as
  before the `prefers-channel` fact existed

#### Scenario: Explicit channel still wins
- **WHEN** the caller forces a specific deliverable channel and the entity has a
  different `prefers-channel` preference
- **THEN** the forced channel is used and the preference is not consulted

#### Scenario: No entity_id defaults to telegram
- **WHEN** `notify(message="Alert")` is called with no `channel` and no
  `entity_id`
- **THEN** `channel` resolves to `telegram`

### Requirement: Request Context Propagation
For `reply` and `react` intents, the `request_context` must carry lineage from the originating inbound request. This enables the Messenger butler to route the delivery to the correct conversation thread.

#### Scenario: Request context forwarded to envelope
- **WHEN** `notify(intent="reply", request_context={...})` is called with valid context
- **THEN** the `request_context` is included in the `notify.v1` envelope as-is

#### Scenario: Request context from runtime session
- **WHEN** a notify call happens during a routed session
- **THEN** the runtime can pass the `request_context` from its session's routing lineage

### Requirement: NotifyRequestContextInput Schema
The `request_context` parameter follows the `NotifyRequestContextInput` TypedDict with required fields (`request_id`, `source_channel`, `source_endpoint_identity`, `source_sender_identity`) and optional fields (`source_thread_identity`, `received_at`).

#### Scenario: Valid request context
- **WHEN** `request_context` includes all required fields
- **THEN** the notify tool proceeds with envelope construction

#### Scenario: Missing required context field for reply
- **WHEN** `intent="reply"` and `request_context` is missing `request_id`
- **THEN** the tool returns a validation error

### Requirement: Default Recipient Resolution
The `notify` tool accepts `entity_id` (UUID) and `recipient` (string) as optional parameters for specifying the target. Resolution priority SHALL be: (1) if `entity_id` is provided, resolve the target's channel identifier from `relationship.entity_facts` (active triple preferred) for the channel predicate (e.g. a `telegram:<id>` fact for the telegram channel); (2) if `recipient` string is provided, use it as-is; (3) if neither is provided, default to the owner and the channel's default order (telegram, then email).

#### Scenario: Entity-based recipient resolution
- **WHEN** a runtime instance calls `notify(channel='telegram', message='Your dental appointment is tomorrow', entity_id='abc-123')`
- **AND** entity `abc-123` has an active `relationship.entity_facts` triple for the telegram channel with value `12345`
- **THEN** the butler daemon MUST resolve the Telegram chat ID to `12345` and deliver to that recipient

#### Scenario: Entity-based resolution prefers the active triple
- **WHEN** an entity has multiple `relationship.entity_facts` triples for the email channel
- **AND** `notify(channel='email', message='...', entity_id=...)` is called
- **THEN** the daemon MUST use the active triple's value
- **AND** if no active triple exists for the channel, the notify call MUST follow the Missing Channel Identifier Fallback below

#### Scenario: Omitted entity_id and recipient defaults to system owner
- **WHEN** a runtime instance calls `notify(channel='telegram', message='Alert')` without `entity_id` or `recipient`
- **THEN** the butler daemon MUST resolve the owner's channel identifier from the owner entity's `relationship.entity_facts`
- **AND** MUST deliver to that resolved identifier

#### Scenario: Explicit recipient string provided
- **WHEN** a runtime instance calls `notify(channel='email', message='Report', recipient='user@example.com')`
- **THEN** the butler daemon MUST forward the call to the Switchboard with `recipient='user@example.com'`
- **AND** `entity_id`-based resolution MUST NOT be attempted

### Requirement: Missing Channel Identifier Fallback
When `entity_id` is provided but the entity has no `relationship.entity_facts` triple for the requested channel, the `notify` tool MUST NOT silently fail. Instead, it MUST park the notification as a `pending_action` in the approval system and notify the owner to provide the missing channel identifier.

#### Scenario: Entity missing Telegram identifier
- **WHEN** a runtime instance calls `notify(channel='telegram', message='Reminder', entity_id='abc-123')`
- **AND** entity `abc-123` has no `relationship.entity_facts` triple for the telegram channel
- **THEN** the notify tool MUST create a `pending_action` with `tool_name='notify'`, `status='pending'`, and `agent_summary` explaining that the entity has no Telegram identifier on file
- **AND** the tool MUST return `{"status": "pending_missing_identifier", "action_id": "...", "message": "Cannot deliver telegram notification -- no telegram identifier on file."}`

#### Scenario: Owner notified of missing identifier
- **WHEN** a notification is parked due to a missing channel identifier
- **THEN** the owner MUST be notified via their preferred channel with the missing identifier details and a link to the contact's page

### Requirement: Role-Based Approval Gating for Notify
The `notify` tool SHALL apply approval gating based on whether the target is the owner. Notifications to the owner MUST bypass the approval gate. Notifications to a non-owner entity MUST be subject to the approval gate (checking standing rules, else pending).

#### Scenario: Notification to owner bypasses approval
- **WHEN** `notify(channel='telegram', message='Alert')` is called with no entity_id (defaults to owner)
- **THEN** the notification MUST be delivered without requiring approval

#### Scenario: Notification to non-owner requires approval
- **WHEN** `notify(channel='telegram', message='Reminder', entity_id='abc-123')` is called
- **AND** entity `abc-123` is not the owner
- **THEN** the notification MUST be checked against standing approval rules
- **AND** if no rule matches, it MUST be parked as a pending action

#### Scenario: Standing rule auto-approves non-owner notification
- **WHEN** a standing approval rule exists matching `tool_name='notify'` with constraint `entity_id='abc-123'`
- **AND** `notify(channel='telegram', message='Hi', entity_id='abc-123')` is called
- **THEN** the notification MUST be auto-approved and delivered immediately

#### Scenario: Unresolvable target requires approval
- **WHEN** `notify(channel='telegram', message='Hi', recipient='unknown@example.com')` is called
- **AND** reverse-lookup of `('email', 'unknown@example.com')` returns no contact
- **THEN** the notification MUST require approval (conservative default)

### Requirement: [TARGET-STATE] Messenger Routing via Switchboard
The `notify.v1` envelope is carried inside a Switchboard-routed `route.v1` payload and executed by the Messenger butler's `route.execute`. The Messenger returns `route_response.v1` with a `notify_response.v1` nested result.

#### Scenario: Notify routed through Switchboard
- **WHEN** a butler calls `notify()`
- **THEN** the daemon routes the `notify.v1` envelope through the Switchboard MCP client to the Messenger butler

### Requirement: Messenger route.execute Approval Gate (Defense-in-Depth)
The Messenger's `route.execute` handler calls channel module methods directly (`_send_email()`, `_send_message()`, `_reply_to_message()`), bypassing MCP tool wrappers. Because the MCP-level approval gate is not in this code path, `route.execute` MUST independently re-enforce role-based approval gating before invoking any outbound channel adapter.

This requirement exists because the delivery architecture has two layers: `notify()` MCP tool → Switchboard `deliver()` → Messenger `route.execute` → direct module call. If only the first layer gates, any bypass of the MCP tool layer (e.g., direct `route.execute` invocation) would allow ungated delivery.

#### Scenario: Messenger route.execute blocks non-owner email without rule
- **WHEN** the Messenger's `route.execute` processes a `notify.v1` envelope with `channel="email"`
- **AND** the email target resolves to a non-owner contact (or is unknown)
- **AND** no standing approval rule matches `email_send_message` or `email_reply_to_thread` for that target
- **THEN** delivery MUST be blocked and a descriptive error returned

#### Scenario: Messenger route.execute blocks non-owner telegram without rule
- **WHEN** the Messenger's `route.execute` processes a `notify.v1` envelope with `channel="telegram"` and `intent` in `("send", "reply")`
- **AND** the telegram target resolves to a non-owner contact (or is unknown)
- **AND** no standing approval rule matches `telegram_send_message` or `telegram_reply_to_message` for that target
- **THEN** delivery MUST be blocked and a descriptive error returned

#### Scenario: Messenger route.execute permits owner delivery without rule
- **WHEN** the Messenger's `route.execute` processes a `notify.v1` envelope
- **AND** the target contact has the `owner` role
- **THEN** delivery proceeds immediately without checking standing rules

### Requirement: [TARGET-STATE] Notify Response Envelope
Successful delivery returns `notify_response.v1` with `status="ok"` and delivery metadata. Failed delivery returns `status="error"` with canonical error class and message.

#### Scenario: Successful delivery response
- **WHEN** the Messenger successfully delivers the message
- **THEN** the notify tool returns a response with `status="ok"` and `delivery.channel` and `delivery.delivery_id`

#### Scenario: Failed delivery response
- **WHEN** the Messenger fails to deliver
- **THEN** the notify tool returns a response with `status="error"`, `error.class`, and `error.message`

### Requirement: Origin Butler Identity
Every outbound interaction must include the originating butler's identity as `origin_butler` in the envelope. This is set automatically from the daemon's configuration.

#### Scenario: Origin butler set automatically
- **WHEN** the `health` butler calls `notify()`
- **THEN** the envelope's `origin_butler` field is `"health"`

### Requirement: [TARGET-STATE] Idempotency and Replay Tolerance
Because fanout is at-least-once, butlers must tolerate duplicate routed subrequests where request lineage matches.

#### Scenario: Duplicate notify tolerated
- **WHEN** the same `notify.v1` envelope is delivered twice with the same `request_context.request_id`
- **THEN** the Messenger applies deduplication or the butler tolerates the duplicate response

### Requirement: [TARGET-STATE] Notify Media Attachments
The `notify` tool SHALL accept an optional `delivery.attachments` list so butlers can deliver files and images alongside or instead of text. Each attachment references a blob already persisted in the S3-compatible blob store (`s3-blob-storage`) by `storage_ref`, and the Messenger uploads it to the target channel using channel-native media transport.

#### Scenario: Attachment delivered with message
- **WHEN** `notify(channel="telegram", message="Your report", attachments=[{type:"document", storage_ref:"s3://bucket/key.pdf", filename:"report.pdf", mime_type:"application/pdf"}])` is called
- **THEN** the `notify.v1` envelope includes `delivery.attachments` with each entry's `type`, `storage_ref`, `filename`, and `mime_type`
- **AND** the Messenger fetches each blob by `storage_ref` and uploads it to the channel as native media (Telegram document/photo, email MIME attachment)
- **AND** the text `message`, when present, accompanies the attachment as a caption or body

#### Scenario: Attachment-only delivery
- **WHEN** `notify(channel="email", attachments=[...])` is called with no `message`
- **THEN** delivery proceeds with the attachment(s) and an empty or default body

#### Scenario: Missing blob fails closed
- **WHEN** an attachment `storage_ref` cannot be resolved in the blob store at delivery time
- **THEN** the notify response returns `status="error"` with an error identifying the unresolved `storage_ref`
- **AND** no partial message is delivered without its referenced attachment

### Requirement: [TARGET-STATE] Draft Delivery Intent
A fifth delivery intent `draft` SHALL be supported. Instead of delivering a message, `intent="draft"` creates a reviewable draft in the target channel (e.g. Gmail Drafts) or presents the proposed message to the owner for explicit confirmation before any send. Drafts are non-destructive by design and never reach an external recipient without a subsequent approved send.

#### Scenario: Email draft created, not sent
- **WHEN** `notify(channel="email", intent="draft", recipient="alice@example.com", subject="Re: lunch", message="Sounds good")` is called
- **THEN** a draft is created in the owner's Gmail Drafts and no email is sent to the recipient
- **AND** the notify response returns `status="ok"` with a `draft_ref` identifying the created draft

#### Scenario: Draft never auto-sends
- **WHEN** a draft is created via `intent="draft"`
- **THEN** the message is NOT delivered to any external recipient until a separate, approval-gated send is invoked

### Requirement: [TARGET-STATE] Multi-Channel Delivery
The `notify` tool SHALL accept a list of channels in a single call so one message is delivered to multiple destinations (e.g. telegram and email) with per-channel formatting. A single approval covers all named channels. Delivery status is reported per channel; partial failure does not silently drop the message on other channels.

#### Scenario: Single call fans out to multiple channels
- **WHEN** `notify(channels=["telegram","email"], message="Trip confirmed", subject="Itinerary")` is called
- **THEN** the message is delivered to both telegram and email, each with channel-appropriate formatting (Markdown/Telegram-HTML for telegram, HTML/plain for email)
- **AND** the response reports per-channel outcome (`{telegram:"ok", email:"ok"}`)

#### Scenario: Partial delivery surfaced
- **WHEN** a multi-channel notify succeeds on email but fails on telegram
- **THEN** the response reports `{email:"ok", telegram:"error"}` rather than a single aggregate status
- **AND** the successful channel's delivery is not rolled back

#### Scenario: Single approval covers all channels
- **WHEN** a multi-channel notify to a non-owner requires approval
- **THEN** one pending action is created that names all target channels, and approving it permits delivery to all of them
