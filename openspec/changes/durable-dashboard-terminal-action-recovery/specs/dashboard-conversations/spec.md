## MODIFIED Requirements

### Requirement: Conversation Pydantic Response Models

API response models for conversation endpoints SHALL preserve the existing
conversation and message fields and SHALL expose an optional durable terminal
action on the immutable dashboard user message that initiated a bug-report or
dead-letter lane. It SHALL also expose an optional durable `dashboard_turn` on
every immutable dashboard user message with a durable control record.
`terminal_action` SHALL be `null` for every other message.
When present, it SHALL contain `id` (owner-inspectable UUID), `kind`
(`bug_report` or `dead_letter`), `state`
(`pending_reconciliation`, `completed`, `failed`, `cancelled`, or `ambiguous`),
nullable safe `reference`, `updated_at`, and `ambiguity_reason_code` plus
`resolution_url` only when ambiguous. Its `effects` SHALL be an ordered list of
safe summaries, each containing `kind` (`qa_report`, `dead_letter_capture`, or
`conversation_reply`), owner-facing `state` (`pending_reconciliation`,
`completed`, `failed`, `cancelled`, or `ambiguous`), nullable safe `reference`,
and `updated_at`. It SHALL never contain raw relay, database, credential, or
exception text. When present, `dashboard_turn` SHALL contain raw
`ingress_state` (`pending`, `submitting`, `accepted`, `retryable_error`, or
`rejected`), nullable `target_kind` (`route`, `bug_report`, or `dead_letter`),
owner-facing `state` (`pending_ingress`, `pending_reconciliation`, `completed`,
`failed`, `cancelled`, `pending_cancellation`, `retryable_error`, `rejected`,
or `ambiguous`), nullable `cancel_requested_at`, nullable
`ingress_recovery_at`, nullable `stop_reconcile_deadline_at`, `updated_at`, and
a nullable sanitized `reason_code`; it SHALL never contain raw transport or
runtime error text. A terminal state SHALL take precedence over all ingress
fields: terminal `cancelled` SHALL project as `cancelled` even when the raw
ingress state remains `retryable_error` or `rejected`; another terminal state
SHALL project as its durable outcome. With no terminal state, non-null
`cancel_requested_at` SHALL project as `pending_cancellation`, SHALL include a
`stop_reconcile_deadline_at` no later than 15 minutes after the Stop intent, and
SHALL take precedence over `pending_ingress` or an ingress error. Only otherwise,
`pending` and `submitting` SHALL project as
`pending_ingress`; `retryable_error` and `rejected` SHALL project as their same-
named owner-facing states; and `accepted` without a claimed target SHALL project
as `pending_reconciliation` until a target or terminal outcome is durable.
`ingress_recovery_at` SHALL be a safe exact-message recovery boundary: it is 60
seconds after durable turn opening for a targetless `pending` turn, 60 seconds
after the durable ingress claim for `submitting`, immediately eligible for
`retryable_error`, and `null` for an accepted, cancelled, rejected, terminal, or
pending-cancellation turn.

ID: REQ-dashboard-conversations-002
Source: dashboard-conversations § Conversation Pydantic Response Models; heart-and-soul/vision.md § Non-Negotiable Rule 1; dashboard-terminal-action-recovery REQ-dashboard-terminal-action-recovery-005; design.md Decision 6
Scope: v1-mandatory

#### Scenario: ConversationSummary model

- **WHEN** a conversation list response is serialized
- **THEN** each entry SHALL include `id`, `butler_name`, `title`, `status`,
  `created_at`, `updated_at`, `message_count`, `routed_butler`, and
  `latest_assistant_reply_at`

#### Scenario: ConversationMessage model without a terminal action

- **WHEN** a message response is serialized for a message that did not initiate
  a dashboard bug-report or dead-letter action
- **THEN** it SHALL include `id`, `conversation_id`, `role`, `content`,
  `created_at`, `session_id`, `model_name`, `input_tokens`, `output_tokens`,
  `duration_ms`, `tool_calls`, `error`, `request_id`, and
  `terminal_action: null`
- **AND** `dashboard_turn` SHALL be `null` unless the message has a durable
  dashboard-turn control record

#### Scenario: Freshly opened turn projects a targetless ingress state

- **WHEN** a dashboard user message has just opened its durable turn and its
  control record has `ingress_state: "pending"` with no claimed target
- **THEN** its message response SHALL include `terminal_action: null` and
  `dashboard_turn = {ingress_state: "pending", target_kind: null,
  state: "pending_ingress", ...}`
- **AND** the response SHALL not imply that Switchboard, a domain butler, QA,
  or dead-letter capture has received the message

#### Scenario: Retryable ingress failure projects without a target lane

- **WHEN** durable ingress records `ingress_state: "retryable_error"` before
  any target lane is claimed
- **THEN** its message response SHALL include `terminal_action: null` and a
  targetless `dashboard_turn` with `state: "retryable_error"` and only a
  sanitized reason code
- **AND** it SHALL not represent the failure as a filed report, routed
  statement, completed turn, or confirmed cancellation

#### Scenario: Pending Stop takes precedence over submitting ingress

- **WHEN** a targetless turn has a non-null `cancel_requested_at`, no terminal
  state, and raw `ingress_state: "submitting"`
- **THEN** its message response SHALL include `terminal_action: null` and
  `dashboard_turn.state: "pending_cancellation"` with the durable cancellation
  timestamp and bounded reconciliation deadline
- **AND** a reload or reconnect SHALL not project that turn as ordinary pending
  ingress or claim that cancellation is confirmed

#### Scenario: Confirmed Stop overrides an ingress error

- **WHEN** a targetless turn has durable terminal state `cancelled` after a
  `retryable_error` or `rejected` ingress result
- **THEN** its message response SHALL project `dashboard_turn.state:
  "cancelled"` while preserving the raw ingress state for diagnostics
- **AND** it SHALL not present a retry, a failed submission, or a false routing
  result

#### Scenario: Rejected ingress projects a terminal targetless failure

- **WHEN** durable ingress records `ingress_state: "rejected"` before any
  target lane is claimed
- **THEN** its message response SHALL include `terminal_action: null` and a
  targetless `dashboard_turn` with `state: "rejected"` and only a sanitized
  reason code
- **AND** a reload or reconnect SHALL preserve that rejection rather than
  automatically replaying the message

#### Scenario: ConversationMessage model with a terminal action

- **WHEN** a user message initiated a dashboard terminal action
- **THEN** its message response SHALL include the exact `terminal_action` wire
  object and allowed state vocabulary defined by this requirement
- **AND** a reload or reconnect SHALL return the same durable action identity
  and state independently of the original SSE request

#### Scenario: Route-only turn has an unknown outcome

- **WHEN** a dashboard route could not prove whether target dispatch occurred
- **THEN** its initiating message SHALL return `terminal_action: null` and a
  `dashboard_turn` with `ingress_state: "accepted"`, `target_kind: "route"`,
  `state: "ambiguous"`, and a sanitized reason code
- **AND** a reload or reconnect SHALL return that same durable ambiguity rather
  than inferring a calm completion

#### Scenario: ConversationSearchResult model

- **WHEN** a search result is serialized
- **THEN** each entry SHALL include the ConversationSummary fields plus
  `snippet`, the matching message-content excerpt

### Requirement: Exact Message Ingress Recovery API

The canonical owner-only ingress-recovery API SHALL be `POST
/api/butlers/{name}/conversation-turns/{message_id}/retry-ingress`, where
`message_id` identifies the immutable dashboard user message. It SHALL never
insert another user message or automatically replay an outbound dispatch. It
SHALL reuse the durable message's original ingress workflow only after its
database claim fence authorizes `dispatch`; any `accepted`, `pending_ingress`,
`pending_cancellation`, terminal, or conflict result SHALL return without a new
outbound dispatch. A `retryable_error` turn may be retried through this endpoint
immediately; a targetless `pending` or `submitting` turn may be retried only at
or after its durable `ingress_recovery_at` boundary, initially 60 seconds after
turn opening or the ingress claim respectively. Every non-error response SHALL
be JSON, not an SSE stream, and SHALL contain `message_id`, `conversation_id`,
the current safe `dashboard_turn` projection, and exactly one semantic `outcome`
of `recovery_started`, `already_pending`, `already_accepted`,
`pending_cancellation`, `cancelled`, `already_finished`, `retryable_error`,
`rejected`, `ambiguous`, or `conflict`. `recovery_started` means only that this
exact message acquired the ingress fence and began the original ingress workflow;
it SHALL not claim that Switchboard or a downstream butler received the message.

ID: REQ-dashboard-conversations-006
Source: dashboard-turn cancellation migration core_193; dashboard-terminal-action-recovery REQ-dashboard-terminal-action-recovery-003; design.md Decision 6
Scope: v1-mandatory

#### Scenario: Stale submitting ingress is recovered with its original message

- **WHEN** an owner invokes the canonical recovery API at or after a
  `submitting` turn's `ingress_recovery_at`
- **THEN** the server SHALL reclaim only that `message_id` through the durable
  ingress claim fence before it invokes Switchboard
- **AND** it SHALL not create a second dashboard message or automatically retry
  before the owner invokes the endpoint

#### Scenario: Early recovery cannot duplicate an in-flight ingress

- **WHEN** an owner invokes the canonical recovery API before a `submitting`
  turn's 60-second recovery boundary
- **THEN** the server SHALL report the existing pending state without dispatching
  Switchboard again

#### Scenario: Recovery response hands durable state back to the client

- **WHEN** the canonical recovery API completes a non-error request
- **THEN** it SHALL return the exact immutable `message_id`, its
  `conversation_id`, one allowed semantic outcome, and the current safe
  `dashboard_turn` projection in JSON
- **AND** it SHALL not emit a second user message, an SSE token stream, or a
  downstream-delivery success claim

#### Scenario: Stop or ambiguity blocks ingress recovery

- **WHEN** a turn projects `pending_cancellation`, `cancelled`, or `ambiguous`
- **THEN** the canonical recovery API SHALL not dispatch or re-open ingress for
  that message

### Requirement: Bounded Targetless Ingress Stop Reconciliation

The Switchboard SHALL reconcile each targetless `pending_cancellation` turn at
startup and thereafter no less often than every 60 seconds. It SHALL inspect only
durable ingress, request, route, session, and terminal evidence; it SHALL NOT
reissue Switchboard ingress, route a domain butler, or create a terminal action
while reconciling an unconfirmed Stop. If the evidence proves cancellation or a
concrete terminal outcome, it SHALL persist and project that outcome. If the
outcome remains unprovable at `stop_reconcile_deadline_at`, it SHALL persist
`ambiguous` with the sanitized reason code `ingress_stop_outcome_unknown`. A
repeat canonical Stop request for that turn SHALL return `outcome: "ambiguous"`
with both compatibility booleans false, and no automatic redelivery or
`retry-ingress` recovery SHALL be permitted after ambiguity.

ID: REQ-dashboard-conversations-007
Source: dashboard-turn cancellation migration core_193; heart-and-soul/vision.md § Non-Negotiable Rule 1; dashboard-terminal-action-recovery REQ-dashboard-terminal-action-recovery-003; design.md Decisions 3 and 6
Scope: v1-mandatory

#### Scenario: Stop races outbound ingress and the process dies

- **WHEN** Stop persists targetless cancellation intent while ingress is
  `submitting` and the process dies before durable acceptance or failure is
  bound
- **THEN** startup reconciliation SHALL inspect the durable evidence without
  reissuing ingress or creating a target lane
- **AND** it SHALL persist the proven outcome or transition the turn to
  `ambiguous` by its bounded deadline

#### Scenario: Unprovable ingress Stop becomes durable ambiguity

- **WHEN** a targetless pending Stop reaches `stop_reconcile_deadline_at`
  without durable proof of cancellation or actual outcome
- **THEN** the dashboard turn SHALL transition to `ambiguous` with reason code
  `ingress_stop_outcome_unknown`
- **AND** reload, reconnect, a repeat Stop, and ingress recovery SHALL not
  present or cause a new delivery

### Requirement: Conversation Reply Channel

A routed butler session SHALL confirm its interpretation of a dashboard
statement or a durable terminal-action outcome by calling the
`conversation_reply` MCP tool, which persists an assistant-role message into
the originating conversation. The SSE poller SHALL watch that persisted message
rather than raw session completion. A terminal-lane reply SHALL carry its
stable child-effect idempotency key and SHALL not claim a report was filed
until the corresponding durable QA receipt is complete.

ID: REQ-dashboard-conversations-003
Source: dashboard-conversations § Conversation Reply Channel; dashboard-terminal-action-recovery REQ-dashboard-terminal-action-recovery-002; design.md Decision 4
Scope: v1-mandatory

#### Scenario: Conversation reply persists a normal confirm-loop message

- **WHEN** a routed session calls `conversation_reply` for an existing
  conversation without a terminal-action child-effect key
- **THEN** the system SHALL insert an assistant-role message, increment message
  count, refresh the conversation timestamp, and return the new message and
  conversation identities

#### Scenario: Terminal action reply is retried

- **WHEN** a terminal-lane worker calls `conversation_reply` with the same
  conversation and child-effect idempotency key after a crash or retry
- **THEN** the system SHALL return the existing assistant message without
  inserting a second reply or incrementing the message count again

#### Scenario: Conversation reply rejects an unknown conversation

- **WHEN** `conversation_reply` references an unknown conversation ID
- **THEN** it SHALL insert no message and return a structured error without
  raising to the caller

#### Scenario: Conversation reply is available to every butler

- **WHEN** any butler MCP server registers core tools
- **THEN** it SHALL register `conversation_reply` regardless of core-group
  configuration because any routable butler can own a dashboard conversation

## ADDED Requirements

### Requirement: Dashboard Turn Cancellation API

The canonical owner Stop API SHALL be `POST
/api/butlers/{name}/conversation-turns/{message_id}/cancel`, where `message_id`
identifies the immutable dashboard user turn. Before the client detaches its SSE
watch, the server SHALL persist and linearize the Stop against the exact durable
turn and, when present, its terminal action. A semantic cancellation response
SHALL include `message_id`, nullable `conversation_id` and `session_id`, and a
required `outcome` of `cancelled`, `already_finished`,
`pending_reconciliation`, or `ambiguous`. The existing `cancelled` and
`already_finished` booleans SHALL remain additive compatibility fields: outcomes
map to `(true, false)`, `(false, true)`, `(false, false)`, and `(false, false)`
respectively. A control-plane failure that cannot establish a durable semantic
outcome SHALL be surfaced as a request failure, not fabricated as one of those
four outcomes. The legacy conversation-scoped cancel endpoint MAY remain only as
a compatibility adapter: it SHALL forward to the canonical endpoint when it can
resolve the exact active `message_id`, and it SHALL never guess a different turn.
New dashboard clients SHALL call the canonical message-scoped endpoint.

ID: REQ-dashboard-conversations-004
Source: dashboard-chat-ui § SSE Client Integration; dashboard-terminal-action-recovery REQ-dashboard-terminal-action-recovery-004; design.md Decision 6
Scope: v1-mandatory

#### Scenario: Stop wins before a terminal effect starts

- **WHEN** the canonical message-scoped API returns `outcome: "cancelled"`
- **THEN** it SHALL prove that the exact turn/action cannot start a runtime or
  child effect after its Stop linearization point
- **AND** it SHALL set `cancelled: true` and `already_finished: false`

#### Scenario: Stop races with a terminal effect

- **WHEN** Stop reaches an action after a child effect has recorded
  `attempt_started` but before the effect outcome is proven
- **THEN** the API SHALL persist the action-level Stop intent and return
  `outcome: "pending_reconciliation"` or `outcome: "ambiguous"`
- **AND** it SHALL set both compatibility booleans to `false` and SHALL NOT
  claim cancellation

#### Scenario: Stop races targetless ingress

- **WHEN** Stop persists against a targetless `submitting` ingress whose outbound
  result cannot yet be proven
- **THEN** the API SHALL return `outcome: "pending_reconciliation"` with both
  compatibility booleans false and the message read model SHALL project
  `pending_cancellation`
- **AND** after bounded targetless-ingress reconciliation proves no outcome, a
  repeat Stop SHALL return `outcome: "ambiguous"` rather than a cancellation
  claim

#### Scenario: Stop reaches a completed turn

- **WHEN** the exact immutable turn has already reached its durable terminal
  runtime/action outcome before Stop is linearized
- **THEN** the API SHALL return `outcome: "already_finished"`,
  `cancelled: false`, and `already_finished: true`

#### Scenario: Legacy caller provides an exact message identity

- **WHEN** a legacy conversation-scoped caller supplies or the adapter resolves
  the active immutable `message_id`
- **THEN** the adapter SHALL preserve the canonical outcome and compatibility
  boolean mapping for that exact message
- **AND** it SHALL not use a process-local conversation record to cancel a
  different dashboard turn

### Requirement: Dashboard Turn Outcome Projection

The conversation message read model SHALL project the durable dashboard-turn
state independently of `terminal_action`, from targetless ingress through a
claimed route or terminal action, so pending, retryable, rejected, and ambiguous
outcomes survive the request/SSE lifecycle. A route-only `ambiguous` turn SHALL
be owner-visible, but it SHALL not expose a manual relay retry or claim that a
domain butler received the statement.

ID: REQ-dashboard-conversations-005
Source: butler-switchboard § Dashboard Chat-Widget Classification Lanes; heart-and-soul/vision.md § Non-Negotiable Rule 1; design.md Decision 1
Scope: v1-mandatory

#### Scenario: Route outcome changes after the initial request

- **WHEN** a `route_pending` reservation becomes completed, failed, cancelled,
  or ambiguous after the initial dashboard request
- **THEN** the initiating message's `dashboard_turn` SHALL reflect the durable
  ingress state, target kind, state, cancellation/recovery timestamps, and
  sanitized reason code on the next message query

#### Scenario: Route ambiguity is read after reconnect

- **WHEN** an owner reloads or reconnects to a conversation whose route turn is
  ambiguous
- **THEN** the message query SHALL return the route ambiguity without relying on
  a process-local active-turn map or the original SSE stream
