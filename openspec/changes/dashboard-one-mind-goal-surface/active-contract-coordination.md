# Complete active and prospective owner coordination


The original 13-file / 37-requirement / 281-scenario population remains fully retained. It is a subset of the current 68 active changes and 45 intersecting full changes (plus explicit PR4485), totaling 443 full requirement bodies / 1,968 scenarios in the foreign program source account. All current sources remain held. The correction neither copies a foreign amendment into the repo nor adopts its pending future bodies.


bu-li92cu owns the complete collision disposition program. Owner decision `bu-u5euwb.21` = C names dashboard fast answers as a D18 kind (`bu-u5euwb.27`); `define-dashboard-fast-answer-runtime` is re-scoped to it (`bu-u5euwb.28`). Separate proposed paths do not confer semantic clearance. PR4524 at 3c824 remains a prospective three-file recipient/exact-call union, not a merged source authority; failed hosted run 38610786151 provides no PASS or retry permission. PR4526 logging/quarantine is a separate foreign owner proposal and grants no D19 source-admission or testing exception.


## openspec/changes/durable-dashboard-terminal-action-recovery/specs/dashboard-conversations/spec.md


The new input is admitted deterministically into a goal or its actual named terminal action. Classifier route lanes and provider resume execution retire at M6 under D3/D9; durable anchors, cancellation, reply idempotency and historical data remain. The full pre-admission and citation contracts below govern the successor; active terminal-recovery and continuity owners retain integration authority.

Before integration, the actual native owner and ROOT must adopt/archive the full current producer/consumer and all original scenarios, or allocate an exact atomic full-body amendment. Each clause remains held; none of the 13 active files is edited by this proposal. See the full 45-change program context and PR4485 amendment controlled by bu-li92cu.



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
nullable safe `reference`, `updated_at`, nullable sanitized `reason_code`,
nullable `ambiguity_reason_code`, `resolution_url` only when ambiguous, and
nullable `owner_resolution`. `owner_resolution`, when present, SHALL be the
owner-only immutable overlay `{id, assessment: "completed"|"failed", note,
recorded_at}`; it SHALL coexist with `state: "ambiguous"` and SHALL not be
represented as a receiver receipt or ordinary completed/failed action. Its
`effects` SHALL be an ordered list of safe summaries, each containing `kind`
(`qa_report`, `dead_letter_capture`, or `conversation_reply`), owner-facing
`state` (`pending_reconciliation`, `completed`, `failed`, `cancelled`, or
`ambiguous`), nullable safe `reference`, nullable sanitized `reason_code`, and
`updated_at`. It SHALL never contain raw relay, database, credential, or
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
Source: dashboard-conversations § Conversation Pydantic Response Models; heart-and-soul/vision.md § What Butlers Is Not (Not an experiment); dashboard-terminal-action-recovery REQ-dashboard-terminal-action-recovery-005; design.md Decision 6
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

#### Scenario: Ambiguous action carries a readable owner resolution overlay

- **WHEN** an owner has recorded a manual resolution for an ambiguous terminal
  action
- **THEN** the read model SHALL return `terminal_action.state: "ambiguous"` and
  the immutable safe `owner_resolution` object beside it
- **AND** it SHALL not serialize that assessment as a completed/failed receipt
  or offer automatic delivery recovery

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

## ADDED Requirements

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
The central `dashboard-owner-auth` boundary SHALL admit a valid configured
`X-API-Key` or a valid server-managed owner session before protected body reads,
domain-pool acquisition, caches or handlers. Passkey verification issues a session;
it is not a new per-route credential. Cookie-backed unsafe actions additionally
require independent synchronizer CSRF and exact Origin validation. Unavailable
authoritative auth state returns safe `503`; missing, expired, revoked or invalid
caller authority returns `401`. An absent API key alone is not unavailability when
healthy keyless session authority exists. Domain checks remain mandatory after
central authentication; auth-store reads necessary for verification are distinct
from forbidden pre-authentication domain access.

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
with no automatic redelivery, and no `retry-ingress` recovery SHALL be permitted
after ambiguity.

ID: REQ-dashboard-conversations-007
Source: dashboard-turn cancellation migration core_193; heart-and-soul/vision.md § What Butlers Is Not (Not an experiment); dashboard-terminal-action-recovery REQ-dashboard-terminal-action-recovery-003; design.md Decisions 3 and 6
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

### Requirement: Durable Current-Turn Dispatch Receipt

For an SSE request that owns an immutable dashboard `message_id`, the API MAY
emit `dispatch_accepted` only after its ingress is durably accepted: either
`bind_ingress` returned `accepted` for the submitted request or
`claim_ingress` reused an already-accepted request. Before emitting, the API
SHALL take a safe durable `dispatch_status` observation. Its first emitted
receipt SHALL be exactly `{"routed_butler": null}`, regardless of whether that
observation is targetless or already has `target_kind: "route"` with a non-empty
`target_butler`. Only a later, distinct safe status observation MAY emit exactly
one named upgrade for that durable route; the first observation SHALL NOT emit
both receipts. The API SHALL NOT derive a receipt target from `triage_decision`,
`triage_target`, a historical
`conversation.routed_butler`, or another message.

Legacy streams without an immutable `message_id` SHALL emit no dispatch receipt.
The API SHALL also emit no receipt when the status observation is unavailable or
unsafe, when the turn is `cancelling`, cancelled, or ambiguous, or when its
target is a terminal action rather than a route. Those terminal/cancellation
facts retain their existing SSE error/outcome semantics; a dispatch receipt is
not a terminal-action receipt or confirmation of an assistant reply.

ID: REQ-dashboard-conversations-008
Source: dashboard-turn cancellation migration core_193; design.md Decision 6;
RFC 0007 Dashboard Conversation Endpoints
Scope: v1-mandatory

#### Scenario: Safe accepted ingress yields a targetless receipt

- **WHEN** immutable ingress has bound as `accepted` and `dispatch_status`
  safely observes an active turn with no target kind
- **THEN** the stream SHALL emit one `dispatch_accepted` event with
  `routed_butler: null`
- **AND** a pre-routing `triage_target` or historical conversation route SHALL
  not change that targetless receipt

#### Scenario: Existing durable route begins targetless then upgrades

- **WHEN** immutable ingress has been accepted and `dispatch_status` safely
  observes `target_kind: "route"` with a non-empty `target_butler`
- **THEN** the first `dispatch_accepted` event SHALL be
  `{"routed_butler": null}`
- **AND** only a later safe `dispatch_status` observation MAY emit one named
  event naming that exact durable target
- **AND** the first observation SHALL not emit both events

#### Scenario: Targetless receipt upgrades only after a durable route claim

- **WHEN** a stream has emitted a targetless receipt and a later safe
  `dispatch_status` observation first exposes a non-empty durable route target
- **THEN** the stream SHALL emit one named `dispatch_accepted` upgrade
- **AND** it SHALL not emit a second named upgrade for that turn, including on a
  third identical durable-route poll

#### Scenario: Unsafe or non-message stream emits no receipt

- **WHEN** a request has no immutable `message_id`, the durable status cannot
  be observed safely, ingress is cancelling/cancelled/ambiguous, or the durable
  target is a terminal action
- **THEN** the stream SHALL not emit `dispatch_accepted`
- **AND** it SHALL preserve the applicable existing Stop or error outcome rather
  than substituting route/progress language

## MODIFIED Requirements

### Requirement: Conversation Reply Channel

A routed butler session SHALL confirm its interpretation of a dashboard
statement (or acknowledge a filed bug report, or answer a question) by
calling the `conversation_reply` MCP tool, which persists an assistant-role
message directly into the conversation it was routed from. The SSE poller
MUST watch for this message rather than the routed session's raw completion
(see the SSE Response Streaming requirement).

For a durable terminal-action outcome, the persisted reply SHALL carry its stable child-effect idempotency key and SHALL not claim a report was filed until the corresponding durable QA receipt is complete.

ID: REQ-dashboard-conversations-003
Source: dashboard-conversations § Conversation Reply Channel; dashboard-terminal-action-recovery REQ-dashboard-terminal-action-recovery-002; design.md Decision 4
Scope: v1-mandatory

#### Scenario: Conversation reply persists a normal confirm-loop message

- **WHEN** a routed session calls `conversation_reply(conversation_id, message)`
  for an existing conversation without a terminal-action child-effect key
- **THEN** the system SHALL insert an assistant-role `public.dashboard_messages`
  row with `content = message`, increment the conversation `message_count`, and
  refresh `updated_at`
- **AND** the tool SHALL return `{"status": "ok", "message_id": "...",
  "conversation_id": "..."}`

#### Scenario: Terminal action reply is retried

- **WHEN** a terminal-lane worker calls
  `conversation_reply(conversation_id, message, child_effect_idempotency_key)`
  with the same conversation, message, and child-effect idempotency key after a
  crash or retry
- **THEN** the system SHALL return the existing assistant message as
  `{"status": "ok", "message_id": "...", "conversation_id": "..."}`
  without inserting a second reply or incrementing the message count again

#### Scenario: Terminal action reply rejects a changed payload for the same key

- **WHEN** a terminal-lane worker calls `conversation_reply` with an existing
  child-effect idempotency key but a different conversation or message payload
- **THEN** the system SHALL insert no message and return a structured conflict
- **AND** it SHALL preserve the original reply text and receipt rather than
  silently changing owner-visible content

#### Scenario: Conversation reply rejects an unknown conversation

- **WHEN** `conversation_reply` references an unknown conversation ID
- **THEN** it SHALL insert no message and return `{"status": "error",
  "error": "..."}` without raising so the caller can correct its mistake

#### Scenario: Conversation reply is available to every butler

- **WHEN** any butler MCP server registers core tools
- **THEN** it SHALL register `conversation_reply` regardless of core-group
  configuration because any routable butler can own a dashboard conversation

#### Scenario: conversation_reply persists the confirm-loop message

- **WHEN** a routed butler session calls `conversation_reply(conversation_id, message)` for a `conversation_id` that references an existing conversation
- **THEN** an assistant-role row is inserted into `public.dashboard_messages` with `content = message`
- **AND** the conversation's `message_count` is incremented and `updated_at` is refreshed
- **AND** the tool returns `{"status": "ok", "message_id": "...", "conversation_id": "..."}`

#### Scenario: conversation_reply rejects an unknown conversation_id

- **WHEN** `conversation_reply` is called with a `conversation_id` that does not reference an existing conversation
- **THEN** no message row is inserted
- **AND** the tool returns `{"status": "error", "error": "..."}` (never raises) so the calling model can see and correct its own mistake

#### Scenario: conversation_reply is available to every butler

- **WHEN** any butler's MCP server registers its core tools
- **THEN** `conversation_reply` SHALL be registered regardless of `core_groups` configuration — any butler can be the classification or pinned-target destination of a dashboard conversation, so the tool cannot be scoped to a subset of butlers

#### Scenario: conversation_reply accepts an optional sources list for an answer-lane reply

- **WHEN** a routed butler session calls `conversation_reply(conversation_id, message, sources=[...])` with a non-empty list of strings
- **THEN** the inserted message row's `sources` column SHALL persist the given list
- **AND** the tool's success response SHALL be unaffected in shape otherwise

#### Scenario: conversation_reply is unaffected when sources is omitted

- **WHEN** `conversation_reply` is called without a `sources` argument (the existing confirm-loop, action-proposal, and bug-report call sites)
- **THEN** the inserted message row's `sources` column SHALL be NULL
- **AND** behavior SHALL be identical to before `sources` existed

#### Scenario: conversation_reply rejects empty or blank source names

- **WHEN** `conversation_reply` is called with `sources=[]` or with any blank source name
- **THEN** no message row is inserted
- **AND** the tool returns `{"status": "error", "error": "..."}` guiding the caller to either name what it consulted or omit `sources` entirely and give an honest decline instead of fabricating a citation

## ADDED Requirements

### Requirement: Dashboard Turn Cancellation API

The canonical owner Stop API SHALL be `POST
/api/butlers/{name}/conversation-turns/{message_id}/cancel`, where `message_id`
identifies the immutable dashboard user turn. Before the client detaches its SSE
watch, the server SHALL persist and linearize the Stop against the exact durable
turn and, when present, its terminal action. A semantic cancellation response
SHALL include `message_id`, nullable `conversation_id` and `session_id`, and a
required `outcome` of `cancelled`, `already_finished`,
`pending_reconciliation`, or `ambiguous`. It SHALL NOT carry the legacy
`cancelled` or `already_finished` booleans. A control-plane failure that cannot
establish a durable semantic outcome SHALL be surfaced as a request failure, not
fabricated as one of those four outcomes.

The repository-owned conversation-scoped cancel endpoint and boolean response
are a bounded migration source, not a compatibility contract. The implementation
change SHALL introduce this canonical endpoint; migrate
`frontend/src/api/client.ts`, `FloatingChatWidget`, `ChatPanel`, their types,
tests, and dashboard API inventory to the exact `message_id` plus `outcome`
contract; prove no repository-owned caller remains; and delete the old endpoint,
response model/type, client alias, and boolean assertions before the change is
archived. No external consumer is currently verified. Any exception SHALL
require an owner-approved amendment that names the verified consumer,
accountable owner, and dated sunset; without it, deletion in the same change is
mandatory.

ID: REQ-dashboard-conversations-004
Source: dashboard-chat-ui § SSE Client Integration; dashboard-terminal-action-recovery REQ-dashboard-terminal-action-recovery-004; design.md Decision 6
Scope: v1-mandatory

#### Scenario: Stop wins before a terminal effect starts

- **WHEN** the canonical message-scoped API returns `outcome: "cancelled"`
- **THEN** it SHALL prove that the exact turn/action cannot start a runtime or
  child effect after its Stop linearization point

#### Scenario: Stop races with a terminal effect

- **WHEN** Stop reaches an action after a child effect has recorded
  `attempt_started` but before the effect outcome is proven
- **THEN** the API SHALL persist the action-level Stop intent and return
  `outcome: "pending_reconciliation"` or `outcome: "ambiguous"`
- **AND** it SHALL NOT claim cancellation

#### Scenario: Stop wins after a primary effect but before its acknowledgement

- **WHEN** a primary terminal child has a durable completed receipt and Stop
  wins the conditional fence against a still-planned required acknowledgement
- **THEN** the API SHALL return `outcome: "pending_reconciliation"` until the
  action's failed `stopped_after_partial_effect` projection is durable
- **AND** it SHALL never return `outcome: "cancelled"` for that partial effect
  outcome

#### Scenario: Stop races targetless ingress

- **WHEN** Stop persists against a targetless `submitting` ingress whose outbound
  result cannot yet be proven
- **THEN** the API SHALL return `outcome: "pending_reconciliation"` with the
  exact message identity and the message read model SHALL project
  `pending_cancellation`
- **AND** after bounded targetless-ingress reconciliation proves no outcome, a
  repeat Stop SHALL return `outcome: "ambiguous"` rather than a cancellation
  claim

#### Scenario: Stop reaches a completed turn

- **WHEN** the exact immutable turn has already reached its durable terminal
  runtime/action outcome before Stop is linearized
- **THEN** the API SHALL return `outcome: "already_finished"`

#### Scenario: Repository-owned compatibility surface is retired

- **WHEN** the implementation has migrated the dashboard API client, both chat
  surfaces, their types/tests, and the API inventory to the canonical endpoint
- **THEN** the same change SHALL delete the conversation-scoped endpoint,
  boolean response model/type, client alias, and compatibility assertions
- **AND** repository search and focused tests SHALL prove that no in-repo caller
  or process-local conversation cancellation path remains

### Requirement: Dashboard Turn Outcome Projection

The conversation message read model SHALL project the durable dashboard-turn
state independently of `terminal_action`, from targetless ingress through a
claimed route or terminal action, so pending, retryable, rejected, and ambiguous
outcomes survive the request/SSE lifecycle. A route-only `ambiguous` turn SHALL
be owner-visible, but it SHALL not expose a manual relay retry or claim that a
domain butler received the statement.

ID: REQ-dashboard-conversations-005
Source: butler-switchboard § Dashboard Chat-Widget Classification Lanes; heart-and-soul/vision.md § What Butlers Is Not (Not an experiment); design.md Decision 1
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


## openspec/changes/durable-dashboard-terminal-action-recovery/specs/dashboard-chat-ui/spec.md


A single assistant goal/thread surface replaces the two unit chat postures. Public unit identity and current-unit switching retire; server-held invocation/region provenance remains inspectable. Sanitized partial rendering, safe citations and persistence are specified in dashboard-goal-message-rendering. The current chat remains until an allocated migration implements every case.

Before integration, the actual native owner and ROOT must adopt/archive the full current producer/consumer and all original scenarios, or allocate an exact atomic full-body amendment. Each clause remains held; none of the 13 active files is edited by this proposal. See the full 45-change program context and PR4485 amendment controlled by bu-li92cu.



## MODIFIED Requirements

### Requirement: SSE Client Integration

The frontend SHALL connect to the SSE streaming endpoints for real-time
response delivery and SHALL reconcile any dashboard terminal action with the
durable conversation read model rather than treating its original stream as the
authority.

ID: REQ-dashboard-chat-ui-001
Source: dashboard-chat-ui § SSE Client Integration; dashboard-conversations REQ-dashboard-conversations-004; dashboard-terminal-action-recovery REQ-dashboard-terminal-action-recovery-004; design.md Decision 6
Scope: v1-mandatory

#### Scenario: SSE connection for new conversation

- **WHEN** `POST /api/butlers/{name}/conversations` is called
- **THEN** the frontend SHALL read the SSE stream using Fetch `ReadableStream`
- **AND** `conversation_created` creates local conversation state, `token`
  appends content, `message_complete` finalizes model, token, duration, and
  tool-call metadata, `error` displays in the thread, and `done` closes the
  stream and re-enables input

#### Scenario: SSE connection for follow-up

- **WHEN** `POST /api/butlers/{name}/conversations/{id}/messages` is called
- **THEN** the frontend SHALL apply the same SSE handling without a
  `conversation_created` event

#### Scenario: Stream cancellation is a real server-side stop

- **WHEN** the owner clicks Stop during streaming
- **THEN** the frontend SHALL call `POST
  /api/butlers/{name}/conversation-turns/{message_id}/cancel`, where
  `message_id` is the client-created immutable user-turn identifier and SHALL
  work before a new conversation has delivered its `conversation_id` over SSE
- **AND** the Stop button SHALL enter a pending (`Stopping…`) state that prevents
  a second request and exposes its state to assistive technology
- **AND** the backend SHALL record cancellation intent against that durable turn,
  preventing a later Switchboard ingress claim, classifier, target route,
  recovery, or runtime invocation from starting work for it
- **AND** when the exact immutable turn owns one or more in-flight routed
  runtimes, the backend SHALL resolve every exact registered runtime session and
  kill the corresponding subprocesses through `cancel_session`; it SHALL not
  merely detach a watcher or persist intent alone
- **AND** when the turn instead owns a terminal action, the backend SHALL persist
  and linearize terminal-action Stop intent against that action
- **AND** only after the API returns `outcome: "cancelled"` or emits the terminal
  `SESSION_CANCELLED` SSE outcome from the durable turn record may the frontend
  abort its own SSE watch (`AbortController`) and render the partial assistant
  message with `Cancelled by owner`, distinct from the generic `Interrupted`
  indicator used for unrelated client-side aborts
- **AND** it SHALL re-enable the input

#### Scenario: Stop requires terminal-action reconciliation

- **WHEN** the owner clicks Stop and the server returns
  `outcome: "pending_reconciliation"` or `outcome: "ambiguous"`
- **THEN** the frontend SHALL render that exact durable state and preserve the
  Stop/recovery context in the thread
- **AND** it SHALL NOT render `Cancelled by owner`, `Interrupted`, or a filed
  claim
- **AND** it SHALL invalidate and refetch the current conversation message query
  using the response `message_id`/`conversation_id`, then follow its bounded
  pending-action refresh policy instead of relying on the original SSE stream
- **AND** when the refetched targetless turn has an unconfirmed durable Stop
  intent, it SHALL render `dashboard_turn.state: "pending_cancellation"` rather
  than ordinary pending ingress

#### Scenario: Stop clicks an already-finished turn

- **WHEN** the owner clicks Stop and the server returns
  `outcome: "already_finished"`
- **THEN** the frontend SHALL stop watching without claiming that it stopped
  the already-arrived or arriving reply
- **AND** it SHALL refetch the current durable message record before rendering a
  terminal outcome

#### Scenario: Cancel request fails before a durable outcome

- **WHEN** the cancel request itself fails without a durable action outcome, an
  already-ended runtime is still settling, or an irreversible action was already
  committed without a cancellation result
- **THEN** the frontend SHALL surface the returned safe explanation or durable
  outcome inline; it SHALL re-enable the Stop control only when the server has
  not persisted a pending, terminal, or ambiguous outcome
- **AND** it SHALL not render `Cancelled by owner`, `Interrupted`, or any other
  terminal-state indicator implying that the runtime or action actually stopped

#### Scenario: Dispatch receipt is an accessible current-turn-only status

- **WHEN** the stream emits `dispatch_accepted` for the immutable message owned
  by the active chat turn
- **THEN** the UI SHALL render exactly one polite, atomic textual pending status:
  `Received by Switchboard; waiting for a reply.` for `routed_butler: null`, or
  `Routed to <name>; waiting for a reply.` for a named durable route
- **AND** the first receipt SHALL be rendered as the targetless status even
  when its safe durable observation already has a route; only the later named
  upgrade may change that status and add the current-turn link
- **AND** before any receipt it SHALL render `Sending to Switchboard.`, preserve
  its animated typing dots as decorative `aria-hidden` elements, and expose the
  pending text through one `role="status"` live region
- **AND** it SHALL link `/butlers/{encodeURIComponent(name)}` only for the
  named receipt on that current stream; targetless receipts and absent receipts
  SHALL keep the ordinary Butler label with no current-route link
- **AND** it SHALL never use a historical `conversation.routed_butler` or a
  pre-routing triage value as a fallback link or pending status target

#### Scenario: Receipt preserves durable Stop semantics

- **WHEN** a turn is cancelling, cancelled, ambiguous, or owns a terminal action
- **THEN** the UI SHALL retain the exact existing Stop/error/result presentation
  and SHALL not synthesize receipt progress or route language
- **AND** during cancelling or confirmed Stop it SHALL suppress the pending
  receipt live region, decorative dots, and `Routed to` link so the authoritative
  Stop status is the only live status

#### Scenario: Stop click on an already-finished turn is a benign no-op

- **WHEN** the user clicks "Stop" but the turn already completed on the
  routed butler (`already_finished: true` in the cancel response)
- **THEN** the frontend stops watching the stream without rendering
  "Cancelled by owner" or any other claim that it stopped something —
  the (already-arrived or arriving) reply is unaffected

#### Scenario: A failed cancel attempt is never rendered as calm

- **WHEN** the cancel request itself fails (e.g. the routed butler is
  unreachable), an already-ended runtime is still settling, or an irreversible
  action was already committed, so the server cannot confirm cancellation
- **THEN** the frontend surfaces the returned explanation inline in the thread
  and gives the owner an actionable, truthful Stop state
- **AND** it SHALL NOT render "Cancelled by owner", "Interrupted", or any
  other terminal-state indicator implying the session actually stopped

#### Scenario: A same-message ingress is still in progress

- **WHEN** the SSE stream reports `INGEST_IN_PROGRESS` because another caller
  owns or is settling the same immutable dashboard turn
- **THEN** the frontend retains that logical message and presents a
  "Check again" or history-refresh affordance
- **AND** it SHALL NOT offer Retry or issue a new ingestion request for that
  `message_id`
- **AND** `SESSION_CANCELLED` remains a confirmed terminal Stop outcome, while
  `TURN_OUTCOME_UNKNOWN` suppresses automatic replay and surfaces the
  uncertainty honestly
### Requirement: Conversation React Query Hooks

TanStack Query hooks SHALL manage conversation data fetching and caching,
including bounded refresh of a terminal action or dashboard turn while its
durable state is still pending ingress or reconciliation.

ID: REQ-dashboard-chat-ui-002
Source: dashboard-chat-ui § Conversation React Query Hooks; dashboard-terminal-action-recovery REQ-dashboard-terminal-action-recovery-003; design.md Decision 6
Scope: v1-mandatory

#### Scenario: useConversations hook

- **WHEN** `useConversations(butlerName, status)` is called
- **THEN** it SHALL return a paginated list using key
  `["conversations", butlerName, "list", params]` and a 10-second stale time

#### Scenario: useConversationMessages hook

- **WHEN** `useConversationMessages(butlerName, conversationId)` is called
- **THEN** it SHALL return the message list using key
  `["conversation-messages", butlerName, conversationId]` with `staleTime: 0`
  so it always refetches on conversation switch

#### Scenario: Pending dashboard outcome is refreshed

- **WHEN** the current conversation contains a
  `terminal_action.state = "pending_reconciliation"` or
  `dashboard_turn.state = "pending_ingress"` or
  `dashboard_turn.state = "pending_reconciliation"` or
  `dashboard_turn.state = "pending_cancellation"`
- **THEN** the messages query SHALL refetch no slower than every 10 seconds
- **AND** for `pending_ingress`, it SHALL stop passive refresh at the durable
  `ingress_recovery_at` boundary and render the owner-initiated exact-message
  recovery affordance instead of dispatching automatically
- **AND** for `pending_cancellation`, it SHALL continue bounded refresh only
  until durable reconciliation projects a concrete outcome or `ambiguous`; the
  server SHALL resolve the state no later than one reconciliation cadence after
  `stop_reconcile_deadline_at`
- **AND** it SHALL stop that pending-outcome refresh after the action or turn
  reaches completed, failed, cancelled, retryable_error, rejected, or ambiguous
  state

#### Scenario: Mutation or reconciliation changes a conversation

- **WHEN** a message is sent, a conversation is created, or a terminal-action
  reconciliation changes its durable state
- **THEN** a sent message or created conversation SHALL invalidate
  `["conversations", butlerName]` to refresh the list
- **AND** `message_complete` SHALL invalidate
  `["conversation-messages", butlerName, conversationId]`
- **AND** a terminal-action reconciliation SHALL invalidate the relevant
  conversation-list and exact message queries

#### Scenario: Exact-message recovery hands control back to the durable query

- **WHEN** the owner invokes `POST
  /api/butlers/{name}/conversation-turns/{message_id}/retry-ingress`
- **THEN** the UI SHALL disable a duplicate recovery click until the JSON
  response arrives, then invalidate and refetch that response's exact
  `conversation_id` and `message_id`
- **AND** it SHALL resume the appropriate bounded durable-status refresh only
  when the returned/refetched state is pending; it SHALL render a terminal,
  rejected, retryable, cancellation, or ambiguous result from the read model
  without opening a second SSE stream or inserting another local user message

#### Scenario: List and search read errors preserve cached rows

- **WHEN** a conversation-list or search query errors while it still has cached
  rows
- **THEN** the UI SHALL show those cached rows together with a retryable
  `role="alert"` that calls that query's own `refetch`
- **AND** it SHALL not replace the rows with an empty-state claim

#### Scenario: History read recovery preserves the active thread only

- **WHEN** the active conversation history query fails or refreshes while it
  retains same-thread messages
- **THEN** the UI SHALL preserve the draft, selection, and same-thread cached or
  optimistic messages beside a retryable query-refetch alert
- **AND** after the owner selects a different loading or failed conversation it
  SHALL not render optimistic messages owned by the previous conversation or a
  false empty-history state

#### Scenario: Mutation invalidation

- **WHEN** a new message is sent or a conversation is created
- **THEN** the `["conversations", butlerName]` query is invalidated to refresh the list
- **AND** the `["conversation-messages", butlerName, conversationId]` query is invalidated after `message_complete`
## ADDED Requirements

### Requirement: Durable Dashboard Outcome Presentation

The dashboard chat UI SHALL render terminal-action and dashboard-turn state from
the durable conversation read model and SHALL never present a filed, routed, or
cancelled claim that the journal cannot prove. An ambiguous terminal action SHALL
expose its sanitized reason and owner-only resolution link, but SHALL not offer
an automatic duplicate retry.

ID: REQ-dashboard-chat-ui-003
Source: heart-and-soul/vision.md § What Butlers Is Not (Not an experiment); dashboard-terminal-action-recovery REQ-dashboard-terminal-action-recovery-005; design.md Decisions 5-6
Scope: v1-mandatory

#### Scenario: Action has a durable outcome

- **WHEN** a user message has a terminal action in its conversation read model
- **THEN** the UI SHALL render its exact durable state and safe reference
- **AND** it SHALL expose manual resolution only for an ambiguous action

#### Scenario: A primary effect completed but acknowledgement failed

- **WHEN** `qa_report` is completed and `conversation_reply` is failed for a
  bug-report action
- **THEN** the UI SHALL render `Report filed; acknowledgement failed` and the
  safe report reference
- **AND** it SHALL not reduce that effect-level truth to a generic `Report
  failed` parent label

#### Scenario: A dead-letter capture completed but acknowledgement failed

- **WHEN** `dead_letter_capture` is completed and `conversation_reply` is failed
  for a dead-letter action
- **THEN** the UI SHALL render `Saved for manual review; acknowledgement failed`
  and the safe capture reference
- **AND** it SHALL not imply that the capture was lost

#### Scenario: Stop suppresses an unstarted acknowledgement after a primary effect

- **WHEN** a primary terminal effect is completed and its required
  `conversation_reply` effect is `cancelled` with
  `reason_code: "suppressed_by_stop"`
- **THEN** the UI SHALL render the completed primary safe reference and a
  truthful `Stopped after partial effect; acknowledgement was suppressed` state
- **AND** it SHALL not render `Cancelled`, `Report failed`, or an unqualified
  filed/saved success while the parent is pending reconciliation or after it
  becomes failed with `reason_code: "stopped_after_partial_effect"`

#### Scenario: Action is ambiguous

- **WHEN** a terminal action is ambiguous
- **THEN** the UI SHALL show the sanitized ambiguity reason and resolution link
- **AND** it SHALL not offer an automatic effect retry

#### Scenario: Owner assessment remains an ambiguity overlay

- **WHEN** an ambiguous terminal action has an `owner_resolution` overlay
- **THEN** the UI SHALL show the owner assessment, bounded sanitized note, and
  recorded time beside the still-ambiguous action
- **AND** it SHALL not relabel the action as a receipt-backed completion/failure
  or offer another resolution submission after the immutable overlay exists

#### Scenario: Route-only outcome is ambiguous

- **WHEN** a message has `terminal_action: null` and
  `dashboard_turn = {target_kind: "route", state: "ambiguous", ...}`
- **THEN** the UI SHALL render `Route outcome could not be confirmed; no second
  delivery was attempted` with the sanitized reason code
- **AND** it SHALL not claim that the routed butler received the statement or
  offer an automatic route, bug-report, or dead-letter retry

#### Scenario: Fresh ingress is visibly pending without a false target

- **WHEN** a message has `terminal_action: null` and
  `dashboard_turn = {ingress_state: "pending", target_kind: null,
  state: "pending_ingress", ...}`
- **THEN** the UI SHALL render an in-progress submission state and continue its
  bounded durable-status refresh
- **AND** it SHALL not show a routed, filed, saved, or cancelled result

#### Scenario: Retryable ingress failure is visibly distinct

- **WHEN** a message has `terminal_action: null` and a targetless
  `dashboard_turn.state = "retryable_error"`
- **THEN** the UI SHALL render a sanitized retryable submission failure distinct
  from a rejected message
- **AND** it SHALL not claim the message was routed, filed, saved, or cancelled;
  its recovery affordance SHALL call the exact-message ingress-recovery API and
  rely on its ingress fence

#### Scenario: Rejected ingress is not automatically replayed

- **WHEN** a message has `terminal_action: null` and a targetless
  `dashboard_turn.state = "rejected"`
- **THEN** the UI SHALL render the sanitized rejection state rather than an
  in-progress, routed, filed, saved, or cancelled result
- **AND** it SHALL not automatically replay the message

#### Scenario: Stop during ingress survives reload as pending cancellation

- **WHEN** a reloaded message has `terminal_action: null` and
  `dashboard_turn = {ingress_state: "submitting", target_kind: null,
  state: "pending_cancellation", cancel_requested_at: ..., ...}`
- **THEN** the UI SHALL render the unconfirmed Stop context and continue bounded
  durable-status refresh
- **AND** it SHALL not show ordinary submission, a recovery affordance, or
  `Cancelled by owner`

#### Scenario: Unresolved ingress Stop becomes ambiguity

- **WHEN** a reloaded message's targetless pending Stop reaches its bounded
  reconciliation deadline without a provable outcome
- **THEN** the UI SHALL render the durable ambiguous state and sanitized
  `ingress_stop_outcome_unknown` reason
- **AND** it SHALL not offer ingress recovery or claim that the Stop succeeded

#### Scenario: Stale ingress requires an explicit exact-message recovery

- **WHEN** a pending-ingress message reaches its durable
  `ingress_recovery_at` boundary after a crash or stalled submission
- **THEN** the UI SHALL stop passive polling and offer recovery for that exact
  immutable message only
- **AND** it SHALL not automatically re-dispatch Switchboard or create a second
  user message


## openspec/changes/restore-butler-control-plane-liveness/specs/system-overview-page/spec.md


System remains the infrastructure home. A Region is not a process and its presence does not prove liveness; region detail links to actual infrastructure facts without a guessed mapping.

Before integration, the actual native owner and ROOT must adopt/archive the full current producer/consumer and all original scenarios, or allocate an exact atomic full-body amendment. Each clause remains held; none of the 13 active files is edited by this proposal. See the full 45-change program context and PR4485 amendment controlled by bu-li92cu.



## MODIFIED Requirements

### Requirement: Per-Butler Heartbeat Facts

The `/api/system/butlers/heartbeat` endpoint SHALL return the last successfully verified healthy receiver-observation
timestamp and session activity summary for each registered butler. It SHALL also
expose observation, administrative policy, route compatibility, and effective eligibility
separately. The legacy timestamp field name is retained for existing clients but its value
is the receiver's DB-server time of the last successful healthy verification, not a daemon-authored heartbeat or the latest failed probe. A separate `last_probe_at` SHALL carry the last attempted receiver probe time. Clients SHALL classify effective status from `observation_state` and `effective_eligibility`, never heartbeat age alone.

ID: REQ-system-overview-page-007
Source: RFC 0007 §System Ownership Page; docs/plans/2026-09-23-liveness-control-plane-reliability-packet.md §5.1; [Observed] src/butlers/api/routers/system.py
Scope: v1-mandatory

#### Scenario: Heartbeat endpoint returns per-butler status

- **WHEN** `GET /api/system/butlers/heartbeat` is called
- **THEN** the response body contains:
  - `butlers: ButlerHeartbeat[]` -- one entry per registered butler, ordered by
    butler name ascending
- **AND** each `ButlerHeartbeat` entry contains:
  - `name: string` -- butler name (e.g., `"general"`, `"health"`)
  - `last_heartbeat_at: string | null` -- ISO 8601 UTC timestamp of the most recent
    successfully verified healthy receiver observation in the switchboard registry,
    or `null` if the butler has never passed verification
  - `last_probe_at: string | null` -- ISO 8601 UTC timestamp of the latest receiver
    probe attempt, successful or failed; it does not imply health
  - `last_session_at: string | null` -- ISO 8601 UTC timestamp of the most recent
    completed session for this butler, derived from `{schema}.sessions WHERE
    completed_at IS NOT NULL ORDER BY completed_at DESC LIMIT 1`. The `IS NOT NULL`
    filter is required because active sessions have `completed_at = NULL` and
    PostgreSQL sorts NULLs last by default in DESC -- omitting the filter risks
    returning an active (incomplete) session as the "last" session.
  - `active_session_count: number` -- count of sessions where `completed_at IS NULL`
    in `{schema}.sessions` at query time. Note: the sessions table has no `status`
    column; active sessions are identified by `completed_at IS NULL` (see
    `src/butlers/core/sessions.py` `sessions_active` implementation)
  - `heartbeat_age_seconds: number | null` -- seconds since `last_heartbeat_at`,
    or `null`; it does not override a newer failed probe or non-active policy
  - `observation_state`, `administrative_policy`, `route_compatibility`, and
    `effective_eligibility` -- separate content-blind control-plane facts
- **AND** the response wraps in the standard `ApiResponse<HeartbeatFacts>` envelope

#### Scenario: Heartbeat data is read from the registry, not from live MCP calls

- **WHEN** the heartbeat endpoint assembles its response
- **THEN** it reads liveness data from the switchboard's liveness registry table
  (the same source the butler list page uses)
- **AND** it does NOT issue live MCP `status` tool calls to any butler
- **AND** if a butler's liveness entry is missing from the registry (never started or
  deregistered), `last_heartbeat_at`, `last_probe_at`, and `heartbeat_age_seconds` are `null`

#### Scenario: Session facts are read via the dashboard API's existing DB fan-out

- **WHEN** the heartbeat endpoint reads per-butler session data
- **THEN** it uses the `DatabaseManager` fan-out pattern that the dashboard API already
  uses for cross-butler queries (not new ad-hoc SQL per butler)
- **AND** if a butler's schema is unreachable, that butler's `last_session_at` and
  `active_session_count` are `null` and 0 respectively, and the entry is still
  included in the response with an `error: "schema_unreachable"` flag

#### Scenario: Administrative quarantine is not a heartbeat age
- **WHEN** a daemon is freshly observed but paused or quarantined by the owner
- **THEN** the endpoint reports fresh observation and the separate non-active policy
- **AND** clients do not infer policy from `heartbeat_age_seconds`

#### Scenario: Fresh failed probe cannot look green
- **WHEN** a receiver probe fails after an earlier successful healthy observation
- **THEN** `last_probe_at` advances to the failed attempt while `last_heartbeat_at` and its age remain based on the earlier success
- **AND** `observation_state` and `effective_eligibility` report the failure and the System UI cannot classify the row healthy from a recent `last_probe_at` or legacy age alone


## openspec/changes/restore-butler-control-plane-liveness/specs/dashboard-butler-management/spec.md


Region resident view owns domain records/obligations; Operator is read-only. Unit Sessions, Trigger, MCP debugging, routing log and delegation controls retire. Existing authenticated authoring APIs are not silently revoked or moved into Operator. System/Settings retains actual infrastructure controls; domain edits require the original service authority. Facts/Rules/Episodes and contact replace-navigation remain byte-for-byte.

Before integration, the actual native owner and ROOT must adopt/archive the full current producer/consumer and all original scenarios, or allocate an exact atomic full-body amendment. Each clause remains held; none of the 13 active files is edited by this proposal. See the full 45-change program context and PR4485 amendment controlled by bu-li92cu.



## MODIFIED Requirements

### Requirement: Butler List Page

The `/butlers` page SHALL render as a status board: a page-level header strip
showing fleet health at a glance, a unified 4-column grid of butler cells
sorted by recent activity, and a footer KPI band summarising fleet state.

The page SHALL use `<Page archetype="status-board">` as its outer shell. The
archetype shell owns the header strip, the cell grid slot, the footer band
slot, and the page-level loading and error states. Pages MUST NOT reinvent
those surfaces.

Implementation source constraints:

- **No new `ButlerSummary` fields.** Every cell field MUST be composed from
  existing data surfaces. `ButlerSummary` is defined in
  `src/butlers/api/models/__init__.py:101-120` and exposes `name`, `status`,
  `port`, `type`, `description`, and `sessions_24h`.
  The list router constructs summaries in
  `src/butlers/api/routers/butlers.py:124-131`.
  Note: `active_session_count` is NOT a `ButlerSummary` field; it comes from
  `useButlerHeartbeats` (the `ButlerHeartbeat.active_session_count` field,
  `frontend/src/api/types.ts:3626`).
- Cell data MUST be composed exclusively from these five existing data surfaces:
  1. `useButlers` -- butler list (`ButlerSummary` fields)
  2. `useRegistry` -- eligibility state (`RegistryEntry.eligibility_state`)
     via `frontend/src/hooks/use-general.ts:24-30`,
     `frontend/src/api/client.ts:1137-1140`,
     `frontend/src/api/types.ts:1055-1063`
  3. `useButlerHeartbeats` -- last-seen / heartbeat age / active session count
     via `frontend/src/hooks/use-system.ts:71-78`
  4. `useSpendSummary('today').by_butler` -- per-butler spend today via
     `frontend/src/hooks/use-spend.ts:31-47`
  5. Sessions for the last 24h -- fetched via `useQuery(getSessions({ since: <ISO> }))`
     (no new endpoint; the existing sessions endpoint filtered by a rolling ISO
     timestamp, bucketed client-side for the activity stripe)
  - Per-butler load% denominator (`max_concurrent`) comes from the existing
    `GET /api/butlers/{name}/runtime-config` endpoint.
- The cell identity mark MUST use the existing `ButlerMark` component from
  `frontend/src/components/ui/ButlerMark.tsx`.
- The list MUST render API-provided butler and staffer rows only. No butler
  names or types may be hardcoded in the grid render path.

**Doctrine citations** (`about/heart-and-soul/design-language.md`):

- Non-negotiable 2: "The `Page` is a primitive." The status-board page uses
  `<Page archetype="status-board">` and does not reinvent its chrome.
- Non-negotiable 1: "One token system or none." No raw `oklch(...)` values,
  hex literals, or ad-hoc inline styles in cell JSX. All colors use design
  tokens.
- Non-negotiable 4: "Time is a typed primitive." The clock display in the
  header strip and all `last` timestamps in cells MUST render via `<Time>`.
- Non-negotiable 6: "No em-dashes in prose." Cell copy, chip labels, KPI
  labels, and empty-state text MUST NOT contain em-dashes.

#### Scenario: Header strip

- **WHEN** the butler list page is not showing an initial request failure with
  no cached board rows
- **THEN** a header strip SHALL be displayed containing:
  - An eyebrow label (e.g., "Fleet status")
  - An `h1` reading "The staff, at a glance" styled `text-2xl font-bold tracking-tight`
  - A healthy/total pill (count of healthy butlers over total registered count,
    where healthy = total minus `offline`, `quarantined`, `overdue`, and
    activity-derived `unknown` counts, derived from `StatusBoardAggregates`)
  - A clock and date display rendered via `<Time mode="clock-24h-mono">` that
    updates every minute (aligned to minute boundaries via a 60-second interval)
- **AND** the `unknown` count SHALL be derived from canonical `BoardRow.activity`
  values, not from registry eligibility availability

#### Scenario: Unified cell grid sorted by activity

- **WHEN** butler and staffer list rows are loaded from the API
- **THEN** all butlers and staffers SHALL be rendered in a single 4-column grid of
  butler cells without any grouping by type
- **AND** cells SHALL be sorted by `sessions_24h` descending; ties are broken by
  name ascending
- **AND** no butler or staffer SHALL be hidden from the grid; unavailable registry
  rows SHALL render a dim `--` activity verb without removing the cell

Note: the previous Butler List Page requirement asserted "the page preserves
the existing butlers and staffers grouping." That constraint is removed by
this change. The butler/staffer distinction is preserved in the footer KPI
band composition addendum and visually in each cell's `ButlerMark` component.

#### Scenario: Butler cell composition

- **WHEN** a butler cell is rendered
- **THEN** the cell SHALL display:
  - `ButlerMark` component representing the butler's identity
  - The butler's name, capitalized
  - A role tagline sourced from `ButlerSummary.description`
  - An activity chip showing the derived activity verb (see Activity Verb
    Derivation scenario)
  - A KPI quartet: sessions in the last 24h (`sessions_24h`), spend today
    (from `useSpendSummary('today').by_butler`), load% (derived client-side),
    and last active (last heartbeat timestamp from `useButlerHeartbeats`,
    rendered via `<Time>`)
  - A 24h activity stripe pinned to the bottom of the cell, derived from
    the sessions query (`getSessions({ since: <ISO> })`) bucketed client-side
  - A hover affordance (open arrow or equivalent) linking to the butler detail
    page

#### Scenario: Activity stripe is its own nested door (bu-27dxl.8.3)

- **WHEN** a butler cell renders
- **THEN** the 24h activity stripe region (label + bars/skeleton/error) is a
  nested `<button>`, keyboard-accessible and semantically valid HTML (never a
  nested anchor), with an accessible name naming the butler and the activity
  destination
- **AND** activating it routes to `/butlers/<name>?tab=activity`, independent
  of the root tile's own destination, and does not also trigger the root
  tile's navigation (the click does not propagate to the root)
- **AND** the root tile's own Enter/Space keyboard activation continues to
  route to `/butlers/<name>` (Overview), unaffected by the nested control
- **AND** this door remains present and reachable even while the stripe's
  underlying data is loading or has errored -- a sparse/incomplete stripe
  stays navigable, it just makes no claim of completeness in what it visually
  shows

#### Scenario: Activity verb derivation

- **WHEN** a butler's board row is assembled by `GET /api/butlers/board`
- **THEN** the activity verb (`BoardRow.activity`) and chip color
  (`BoardRow.cell_tone`) SHALL be derived SERVER-SIDE by the canonical
  `_derive_board_activity` function (`src/butlers/api/routers/butlers.py`) --
  this endpoint is the single source of the liveness verdict for every
  butler-status surface, replacing the former client-side per-cell derivation
  (bu-86c4c.17) -- using this first-match-wins priority order:
  1. `status = down` (the raw MCP probe result): verb `offline`, tone `red`
  2. `eligibility = quarantined`: verb `quarantined`, tone `red`
  3. `heartbeat_unavailable` is true, OR the registry `last_seen_at` is
     clock-skewed more than 5 minutes into the future (`clock_skewed`): verb
     `unknown`, tone `neutral` (an untrustworthy heartbeat is not a confidently
     healthy one, the bu-y1am9 clock-skew fold)
  4. `active_session_count > 0`: verb `running`, tone `green`
  5. `cadence_status = overdue` (silent longer than twice the butler's own cron
     cadence per `_CADENCE_OVERDUE_FACTOR`, or longer than 5 minutes when no
     cadence is known): verb `overdue`, tone `amber`
  6. otherwise: verb `idle`, tone `neutral`
- **AND** the complete verb set is `running` / `idle` / `overdue` / `offline` /
  `quarantined` / `unknown`, and the complete tone set is `green` / `amber` /
  `red` / `neutral` (there is no `dim` tone).
- **AND** `eligibility` may also be `unavailable` when the butler registry source
  errored (`_fetch_board_row` sets it when `registry_source_error` is true or no
  registry row exists); `unavailable` is NOT `quarantined`, so it does not
  trigger rule 2, and the row's verb is derived from the remaining heartbeat /
  session / cadence signals.
- **AND** the raw `_probe_butler` status (`BoardRow.status`) is only `ok` or
  `down`; the richer `overdue` / `unknown` verbs are produced by the derivation
  above, not by the probe, and no `degraded` or `waiting` status value is
  produced.
- **AND** the mockup verbs `patrol`, `consolidating`, and `ingesting` MUST NOT
  be used. These verbs imply butler-specific semantic knowledge not carried by
  the board row, and are explicitly rejected.

#### Scenario: Canonical cadence labels

- **WHEN** the board derives a human-facing cadence label from a butler's
  shortest enabled cron interval
- **THEN** exactly one hour, one day, and seven days SHALL be labeled `hourly`,
  `daily`, and `weekly` respectively
- **AND** any other positive interval, including two hours, SHALL be labeled
  `custom`
- **AND** a butler with no enabled schedule SHALL retain a null cadence label
- **AND** the raw `cadence_seconds` and cadence-overdue calculation SHALL remain
  authoritative and unchanged

#### Scenario: Load percentage

- **WHEN** the KPI quartet's load field is rendered
- **THEN** load% SHALL be derived client-side as
  `active_session_count / max_concurrent * 100`
- **AND** `max_concurrent` comes from the per-butler `runtime-config`
  (`GET /api/butlers/{name}/runtime-config`), which is the existing runtime
  config endpoint
- **AND** when `max_concurrent` is unknown or zero, the load field SHALL render as
  `--` rather than a percentage

#### Scenario: Eligibility state rail

- **WHEN** a butler cell is rendered with registry data available
- **THEN** a left-edge state rail on the cell SHALL be colored by eligibility:
  - `active` eligibility: emerald rail
  - `stale` eligibility: amber rail and informational chip; a successful receiver health check clears this state
  - `quarantined` eligibility: red rail, chip is clickable to restore
  - No matching registry entry or unavailable registry response: dim rail
- **AND** clicking a `quarantined` chip SHALL schedule the existing
  `setEligibility(name, "active")` mutation (`frontend/src/hooks/use-general.ts:36-53`)
  a fixed undo window (5s) out behind an "Undo" toast action, rather than
  firing it instantly (restore-with-reason-and-undo, JARVIS audit move 6,
  bu-86c4c.15) -- clicking Undo before the window elapses cancels the
  mutation entirely; letting the window elapse fires it exactly as before
- **AND** the cell SHALL NOT be hidden for any eligibility state, including
  unavailable
- **AND** a `stale` chip SHALL NOT invoke the administrative policy mutation, because setting policy to `active` cannot produce a healthy receiver observation
- **AND** the board query SHALL reconcile receiver liveness at the 30-second cadence even when the fleet event socket is healthy, so both active-to-stale and stale-to-active changes become visible without a session event

#### Scenario: Footer KPI band

- **WHEN** the butler list page is not showing an initial request failure with
  no cached board rows
- **THEN** a footer KPI band SHALL be displayed below the cell grid containing:
  - Active butler count (with emerald status-tone dot, shown only when count > 0)
  - Offline butler count (with red status-tone dot, shown only when count > 0)
  - Quarantined butler count (with red status-tone dot, shown only when count > 0)
  - Fleet sessions in the last 24h
  - Fleet spend today (from `useSpendSummary('today')`)
  - Fleet average load% (mean of all per-butler load% values where
    `max_concurrent` is known)
  - A composition addendum showing "Nb butlers, Ns staffers" where Nb and Ns
    are the counts of butlers and staffers respectively in the API response

#### Scenario: Loading state

- **WHEN** any butler list data request is in flight on initial load
- **THEN** the page-level skeleton SHALL render:
  - A header strip skeleton line
  - A 2x4 grid of cell skeletons (8 placeholder cells)
  - A footer band skeleton
- **AND** this skeleton SHALL be owned by the status-board archetype shell, not by
  the page component directly

#### Scenario: Error resilience with stale data

- **WHEN** a refresh request fails but prior butler data exists in cache
- **THEN** the stale butler cells SHALL remain visible in the grid
- **AND** an error banner SHALL be displayed explaining that the shown data is from
  the last successful fetch
- **AND** the header strip and footer KPI band SHALL remain visible with the cached
  board context

#### Scenario: Initial request failure does not fabricate board context

- **WHEN** the initial butler list request fails and no cached board rows exist
- **THEN** the Page error surface and retry control SHALL render
- **AND** the header strip and footer KPI band SHALL NOT render around the error

#### Scenario: Empty state

- **WHEN** the API returns zero butler list rows
- **THEN** an empty-state message SHALL be displayed: "No butlers found" with guidance
  to check daemon status

#### Scenario: Auto-refresh polling

- **WHEN** the butler list page is mounted
- **THEN** the following polling cadences SHALL be maintained:
  - Butler list (`useButlers`): every 30 seconds
  - Registry and heartbeats (`useRegistry`, `useButlerHeartbeats`): every 30
    seconds
  - Cost summary (`useSpendSummary`): every 60 seconds
  - Header strip clock: updates every minute via `<Time mode="clock-24h-mono">`,
    which aligns to the next minute boundary then fires a 60-second interval


## openspec/changes/specify-roster-identity-owner-operations-overlay/specs/dashboard-butler-management/spec.md


Region resident view owns domain records/obligations; Operator is read-only. Unit Sessions, Trigger, MCP debugging, routing log and delegation controls retire. Existing authenticated authoring APIs are not silently revoked or moved into Operator. System/Settings retains actual infrastructure controls; domain edits require the original service authority. Facts/Rules/Episodes and contact replace-navigation remain byte-for-byte.

Before integration, the actual native owner and ROOT must adopt/archive the full current producer/consumer and all original scenarios, or allocate an exact atomic full-body amendment. Each clause remains held; none of the 13 active files is edited by this proposal. See the full 45-change program context and PR4485 amendment controlled by bu-li92cu.



## MODIFIED Requirements

### Requirement: System Prompt Versioning API

The dashboard SHALL expose owner-only read, compare-and-swap update, and version-history operations
for a butler's owner-operations overlay at the existing prompt route paths. The surface SHALL
distinguish immutable git-roster identity from mutable overlay content and SHALL never label an
overlay or legacy full-replacement row as the complete current system prompt.
All prompt routes SHALL pass fail-closed dashboard owner control before body buffering, database-pool
acquisition, roster-content reads, or protected-state observation. Unavailable authoritative owner-auth state SHALL
return 503; a missing or mismatched credential SHALL return 401. `authenticated_principal()` SHALL
provide attribution only after admission. Unknown roster names SHALL return 404 before prompt-pool
acquisition.
The current response SHALL report resolved roster identity content and digest; overlay content,
active state, version, source `owner_operations`, trust `trusted_owner_input`, timestamp, and
server-derived actor; the selected composition mode and mode version; the ordered layer names; and
`semantic_enforcement=false`. The update body
SHALL contain exactly `overlay`, `active`, and `expected_version`. Overlay text SHALL be at most
65,536 UTF-8 bytes and stored byte-for-byte. NUL, invalid UTF-8, reserved overlay delimiters, an
active blank overlay, a negative/non-integer version, or an unknown field SHALL return a fixed 422
without echoing content. The API SHALL permit a disabled state to carry an empty overlay.
Valid updates SHALL serialize per agent in one transaction. An exact state match SHALL be a 200
no-op at the current version. A divergent update with matching `expected_version` SHALL append one
version and atomic metadata-only audit evidence. A divergent stale update SHALL return 409 with the
current version and no prompt text. Persistence or audit failure SHALL commit neither. Read,
history, errors, audit, logs, metrics, and traces outside the owner response SHALL be content-blind.
A committed divergent overlay update SHALL emit `butler.prompt_overlay_set` with target equal to the
canonical agent name and metadata limited to `version`, `active`, `byte_count`, `digest`, and
`roster_digest`. An exact no-op SHALL emit no audit event. The owner-only
`PUT /api/butlers/{name}/prompt/mode` route SHALL accept exactly `mode` (`roster_overlay` or
rollback-only `legacy_full_replacement`), `expected_mode_version`, and
`acknowledge_identity_replacement`. It SHALL use the same pre-body owner control, dedicated pool,
per-agent serialization, compare-and-swap conflict, content-blind privacy, and atomic audit rules as
overlay PUT. Selecting legacy mode SHALL require an open approved rollback window, an existing
legacy head, and `acknowledge_identity_replacement=true`. A committed transition SHALL emit
`butler.prompt_mode_changed` with target equal to the canonical agent name and metadata limited to
`mode_version`, `from_mode`, `to_mode`, `overlay_version`, and `roster_digest`. An exact no-op SHALL
emit no audit event.
Rollback-window authority SHALL come only from server-side deployment configuration
`BUTLERS_PROMPT_LEGACY_ROLLBACK_UNTIL`, parsed as an RFC 3339 UTC timestamp. A missing, malformed,
or expired value SHALL mean closed. No request field, prompt/mode row, MCP tool, runtime session, or
generic API route SHALL open or extend the window. The migration-seeded
`precutover_legacy_hold` SHALL be a distinct read-only compatibility state: it SHALL NOT require or
claim owner selection, SHALL NOT be accepted as a mode PUT target, and SHALL NOT be re-enterable
after an agent leaves it.
The central `dashboard-owner-auth` boundary SHALL admit a valid configured
`X-API-Key` or a valid server-managed owner session before protected body reads,
domain-pool acquisition, caches or handlers. Passkey verification issues a session;
it is not a new per-route credential. Cookie-backed unsafe actions additionally
require independent synchronizer CSRF and exact Origin validation. Unavailable
authoritative auth state returns safe `503`; missing, expired, revoked or invalid
caller authority returns `401`. An absent API key alone is not unavailability when
healthy keyless session authority exists. Domain checks remain mandatory after
central authentication; auth-store reads necessary for verification are distinct
from forbidden pre-authentication domain access.

ID: REQ-dashboard-butler-management-001
Source: specify-roster-identity-owner-operations-overlay design D4 and D7; heart-and-soul/security.md Dashboard and API Authentication
Scope: v1-mandatory

#### Scenario: Read current prompt
- **WHEN** an authenticated owner calls `GET /api/butlers/{name}/prompt` for a known roster agent
- **THEN** the response separates resolved roster identity from the current owner-operations overlay
- **AND** it reports source/trust/version metadata, roster digest, closed layer order, and `semantic_enforcement=false`
- **AND** it reports the selected composition mode and mode version, labeling legacy mode as identity-replacing
- **AND** it sets `Cache-Control: no-store`

#### Scenario: Update prompt snapshots history
- **WHEN** an authenticated owner submits a valid divergent overlay state with the current `expected_version`
- **THEN** `PUT /api/butlers/{name}/prompt` appends exactly one `owner_operations_overlay` version
- **AND** `butler.prompt_overlay_set` with the specified target and metadata commits in the same transaction
- **AND** the response reports the new version without presenting the overlay as roster identity

#### Scenario: Prompt history list
- **WHEN** an authenticated owner calls `GET /api/butlers/{name}/prompt/history?limit=20`
- **THEN** the response is ordered by version descending and uses bounded pagination
- **AND** each row reports its exact provenance kind, including `legacy_full_replacement` for migrated historical rows
- **AND** no legacy row is relabeled as an owner-operations overlay

#### Scenario: Owner control is unconfigured
- **WHEN** authoritative owner-auth state is unavailable and any prompt route is called
- **THEN** the API returns 503 before body buffering, pool acquisition, roster reads, or protected-state access
- **AND** no audit row claims an authenticated action

#### Scenario: Missing or wrong owner credential is denied
- **WHEN** authoritative auth state is healthy but the caller lacks valid central key-or-session authority
- **THEN** the API returns 401 before body buffering, pool acquisition, roster reads, or protected-state access
- **AND** the error contains no roster, overlay, digest, or history content

#### Scenario: Unknown roster agent is rejected before prompt access
- **WHEN** an authenticated owner names an agent absent from the current roster
- **THEN** the API returns 404 before acquiring the dedicated prompt pool
- **AND** no row is read or appended for that name

#### Scenario: Invalid overlay is rejected content-blind
- **WHEN** an update violates the body shape, size, active-content, encoding, or reserved-delimiter rules
- **THEN** the API returns one fixed 422 category without echoing submitted content
- **AND** no version or audit row is written

#### Scenario: Exact retry is a no-op
- **WHEN** a valid submitted overlay and active state equal the current state
- **THEN** the API returns 200 with the unchanged version even if `expected_version` is stale
- **AND** it performs no prompt-history or audit write

#### Scenario: Concurrent divergent writers produce one winner
- **WHEN** two authenticated updates submit different valid states from the same observed version
- **THEN** per-agent serialization permits at most one changed state to commit
- **AND** the other returns 409 with the current version and no prompt text

#### Scenario: Concurrent identical writers converge
- **WHEN** two authenticated updates submit the same valid state concurrently
- **THEN** exactly one version and audit record commit
- **AND** the other request returns the resulting version as a 200 no-op

#### Scenario: Persistence and audit are atomic
- **WHEN** overlay persistence or audit append fails before commit
- **THEN** neither row commits and no response claims success
- **AND** the prior active state and version remain unchanged

#### Scenario: Owner changes composition mode with compare-and-swap
- **WHEN** an authenticated owner submits a valid divergent mode with the current `expected_mode_version`
- **THEN** the mode route appends exactly one mode-history version
- **AND** `butler.prompt_mode_changed` with the specified target and metadata commits in the same transaction
- **AND** no prompt text enters the mode response or audit event

#### Scenario: Concurrent mode writers produce one winner
- **WHEN** two authenticated requests submit different modes from the same observed mode version
- **THEN** per-agent serialization permits at most one transition to commit
- **AND** the other returns 409 with the current mode version and no prompt text

#### Scenario: Legacy rollback requires explicit acknowledgement
- **WHEN** an authenticated owner selects `legacy_full_replacement` during the approved rollback window
- **THEN** the change succeeds only when a legacy head exists and `acknowledge_identity_replacement=true`
- **AND** a missing acknowledgement, closed window, or absent legacy head returns a fixed content-blind conflict without a write

#### Scenario: Rollback window fails closed
- **WHEN** `BUTLERS_PROMPT_LEGACY_ROLLBACK_UNTIL` is absent, malformed, or not later than the current UTC time
- **THEN** a request for `legacy_full_replacement` returns the fixed content-blind conflict without a write
- **AND** no request or stored row can override that closed result

#### Scenario: Owner can leave expired rollback mode
- **WHEN** rollback configuration is closed while mode history still selects `legacy_full_replacement`
- **THEN** the owner projection reports the expired blocked state without prompt text in error metadata
- **AND** an authenticated compare-and-swap transition to `roster_overlay` remains available
- **AND** selecting or re-entering `legacy_full_replacement` remains denied

#### Scenario: Pre-cutover hold is not an owner rollback target
- **WHEN** an existing agent is still in migration-seeded `precutover_legacy_hold`
- **THEN** the owner projection labels it as unreviewed pre-cutover compatibility state
- **AND** the mode route accepts a transition out to `roster_overlay` but rejects `precutover_legacy_hold` as an input mode
- **AND** no audit evidence claims the owner selected the seeded hold

#### Scenario: Dashboard discloses the semantic limit
- **WHEN** the owner views or edits an overlay
- **THEN** the interface persistently explains that roster identity remains structurally present but natural-language conflicts are not mechanically prevented
- **AND** activation requires explicit owner confirmation of that warning

#### Scenario: Non-owner evidence is content-blind
- **WHEN** any prompt route succeeds, is denied, conflicts, or fails
- **THEN** audit, logs, metrics, traces, and error bodies outside the authenticated owner data response contain only allowlisted metadata
- **AND** roster identity and overlay text are absent


## openspec/changes/harden-runtime-auth-and-breaker-attention/specs/dashboard-model-settings/spec.md


Unit trigger complexity and per-unit runtime override controls cannot configure an invocation after D3/D7. Future goal detail displays actual selected model/tier/role from the invocation receipt. Global settings survive independently; removing legacy override APIs/rows is a separate exact model-owner amendment, not a dashboard permission.

Before integration, the actual native owner and ROOT must adopt/archive the full current producer/consumer and all original scenarios, or allocate an exact atomic full-body amendment. Each clause remains held; none of the 13 active files is edited by this proposal. See the full 45-change program context and PR4485 amendment controlled by bu-li92cu.



## MODIFIED Requirements

### Requirement: Catalog Verify-All API

The dashboard SHALL expose `POST /api/settings/models/verify-all` to re-verify every enabled model in parallel. The verification core is shared (`butlers.api.routers.model_settings.run_verify_all_models`) between this manual endpoint and the hourly automated sweep (see Hourly Automated Verification Sweep) so the two can never disagree about what "verified" means or how the result is persisted. The core requests each probe through the signed Switchboard runtime-probe control client; the dashboard process neither constructs a runtime adapter nor writes model verification evidence. Switchboard owns runtime construction and completed-probe persistence.
The central `dashboard-owner-auth` boundary SHALL admit a valid configured
`X-API-Key` or a valid server-managed owner session before protected body reads,
domain-pool acquisition, caches or handlers. Passkey verification issues a session;
it is not a new per-route credential. Cookie-backed unsafe actions additionally
require independent synchronizer CSRF and exact Origin validation. Unavailable
authoritative auth state returns safe `503`; missing, expired, revoked or invalid
caller authority returns `401`. An absent API key alone is not unavailability when
healthy keyless session authority exists. Domain checks remain mandatory after
central authentication; auth-store reads necessary for verification are distinct
from forbidden pre-authentication domain access.

ID: REQ-dashboard-model-settings-003
Source: dashboard-owner-auth successor design D1-D9; existing dashboard-model-settings behavior preserved except explicit owner-auth supersession
Scope: v1-mandatory

#### Scenario: Verify-all parallel execution
- **WHEN** `POST /api/settings/models/verify-all` is accepted
- **THEN** the system requests a signed Switchboard runtime probe for each enabled model concurrently with a bounded concurrency of 8
- **AND** only a completed probe causes Switchboard to persist `last_verified_at`, `last_verified_latency_ms`, `last_verified_ok`, and `last_verified_error`
- **AND** a control-plane unavailable result preserves the prior verification evidence and is reported separately from a completed failed probe
- **AND** the response data contains exactly `accepted`, `total`, `ok`, `failed`, and `unavailable`
- **AND** the call is rate-limited to once per minute system-wide; subsequent calls within the minute return `429 Too Many Requests`
- **AND** `audit.append("models.verify_all", actor="owner")` is invoked once per accepted run.

#### Scenario: Verify-all persists completed model probes

- **WHEN** `POST /api/settings/models/verify-all` is called for an enabled
  model whose signed runtime probe completes
- **THEN** Switchboard persists `last_verified_at`, `last_verified_latency_ms`,
  `last_verified_ok`, and `last_verified_error` for that model
- **AND** `last_verified_error` is cleared on success and retains the existing
  safe failure classification on a completed failed probe
- **AND** the call remains rate-limited to once per minute system-wide and
  appends one `models.verify_all` audit record for the accepted run

#### Scenario: Unavailable Codex authority is unavailable without poisoning verification evidence

- **WHEN** the signed Switchboard runtime-probe coordinator cannot establish
  the required runtime authority for an enabled Codex entry
- **THEN** it SHALL report that probe as unavailable without producing a model
  completion
- **AND** it SHALL not write `last_verified_at`, `last_verified_latency_ms`,
  `last_verified_ok`, or `last_verified_error`, preserving the prior catalog
  evidence and routing eligibility
- **AND** the accepted result increments `unavailable`, does not increment
  `failed`, and records only a categorical authority-unavailable audit note
  without provider diagnostic text
- **AND** remaining eligible entries continue through the same signed
  Switchboard runtime-probe path

### Requirement: Hourly Automated Verification Sweep

The dashboard-api process SHALL run an hourly background sweep
(`butlers.jobs.model_verify.run_model_verify_loop`, started from the FastAPI
lifespan alongside the other periodic jobs). Each interval tick SHALL call the
same verification core as the manual endpoint through the registered `scheduler`
caller and signed Switchboard runtime-probe control client, so verification is
attempted roughly once per interval even when no operator visits the Models tab.
A control-plane-unavailable result preserves prior verification evidence. The
sweep does not construct a runtime adapter or persist verification evidence
inside the dashboard process.

#### Scenario: Hourly sweep runs independently of the manual rate limit
- **WHEN** the sweep's interval (default `DEFAULT_MODEL_VERIFY_INTERVAL_S = 3600`,
  overridable via `MODEL_VERIFY_INTERVAL_S`) elapses
- **THEN** the sweep calls `run_verify_all_models(pool, audit_actor="model_verify_sweep", caller="scheduler")`
  directly as the registered scheduler caller, bypassing the manual endpoint's
  once-per-minute HTTP rate limit (that limit is an HTTP-surface concern
  specific to the operator-facing route)
- **AND** `audit.append("models.verify_all", actor="model_verify_sweep")` is invoked,
  distinguishing an automated run from an owner-initiated one in `public.audit_log`

#### Scenario: Sweep sleeps first and tolerates a bad tick
- **WHEN** the dashboard-api process starts
- **THEN** the sweep loop sleeps for one interval before its first run (mirrors
  `run_secrets_lifecycle_loop`), so it never starts a runtime-probe request
  during a process boot or a test that exercises the full API lifespan
- **AND** a single sweep's failure is logged and swallowed; the loop continues on its
  next interval rather than dying
- **AND** when no shared credential pool is configured, the sweep is a no-op tick (logged
  at WARNING) rather than raising

#### Scenario: Hourly sweep preserves authority-unavailable catalog entries

- **WHEN** the signed runtime-probe coordinator cannot run an enabled Codex
  entry because its required authority is unavailable
- **THEN** the sweep returns that safe `unavailable` count rather than recording a
  false model failure or excluding the entry from routing

## ADDED Requirements

### Requirement: Catalog Test Uses a Runtime Probe, Not a Dashboard-Local Adapter

The Models API's per-entry test and scheduled verification SHALL invoke a
deterministic Switchboard-owned runtime-probe coordinator. The coordinator
SHALL use the same runtime home, authoritative CLI-auth source, adapter
construction, canonical model identifier, runtime-specific execution mapping,
generated runtime configuration, and runtime arguments as a new daemon
invocation, but SHALL expose no domain MCP tools and SHALL not create routed
dispatch provenance. It SHALL be reached only through an authenticated
dashboard-to-Switchboard control-plane command that is not registered in the
generic MCP/LLM tool surface. Dashboard-triggered Test and Verify requests
SHALL first pass the fail-closed `require_dashboard_owner_control` dependency:
the central `dashboard-owner-auth` configured-key-or-owner-session contract
is required, including CSRF and exact Origin for cookie mutations. Only
unavailable authoritative auth state is configuration unavailability; an absent
key does not disable a healthy enrolled passkey session. The dashboard server SHALL then call Switchboard through a
dedicated `runtime_probe_control` client using a separately scoped system
signed capability produced by `RUNTIME_PROBE_CONTROL_SIGNING_KEY` from a
dedicated Dashboard-only deployment-secret mount, never from `CredentialStore`
or the generic Secrets API. Switchboard SHALL accept only Ed25519 JWS
capabilities with protected `alg=EdDSA` and protected configured key ID, using
its non-secret verification key; it SHALL reject token-selected `none`,
symmetric, and other algorithms. It SHALL require the fixed
`switchboard.runtime_probe_control.v1` audience, matching catalog entry ID,
registered caller class, five-second-skew-bounded `iat`/`exp` interval of at
most one minute, and unused 256-bit nonce on its dedicated internal-control
endpoint. It SHALL atomically commit a SHA-256 nonce-digest receipt before
catalog resolution, runtime launch, or verification persistence, retain it
through at least `exp + 5s`, and never retain a raw nonce or signature. The
private signing key SHALL not be available to the
all-butlers daemon, model sessions, generic MCP clients, the normal MCP client
manager, logs, or any generic Secrets API response. The command accepts only a
catalog entry ID, enforces bounded timeout, per-entry de-duplication, and a
bounded global concurrency cap, and accepts no credential material, prompt,
model override, or runtime arguments from the dashboard. A successful probe
updates verification evidence only; it does not close an open breaker.
The production signing-key mount SHALL be activated only after every Dashboard
runtime-CLI child path is removed or forced through the exclusive
per-invocation identity and kernel-containment launcher required by
core-credentials REQ-core-credentials-002. The cutover of
Test, verify-all, and scheduled verification SHALL remove every
dashboard-local runtime-adapter probe path. The signed client SHALL remain
unavailable and sign nothing during canonical full-stack startup until
Switchboard's private `GET /_control/runtime-probe/v1/readiness?kid=<kid>`
returns the exact `200/ready` response for the matching verifier key ID; its
exact unavailable behavior and no-action/no-disclosure limits are those in
core-credentials REQ-core-credentials-002. Rollback while the mount is active
SHALL retain the child sandbox and make model-verification callers unavailable
rather than restoring a local adapter probe.
The dedicated client SHALL use `POST /_control/runtime-probe/v1` with the
compact capability only in `Authorization: Bearer`. Its exact protected header,
claim names/types, nonce encoding, time validation, and key-selection rules are
those in core-credentials REQ-core-credentials-002. The control response SHALL
preserve safe typed HTTP/status pairs `200/completed`, `401/unauthorized`,
`409/replay`, `429/busy`, `503/unavailable`, and `504/timeout`. Runtime execution SHALL
have a 30-second deadline, global concurrency eight, and per-catalog-entry
concurrency one. Dashboard API mapping SHALL preserve these distinctions rather
than reporting a provider failure or successful test.

ID: REQ-dashboard-model-settings-001
Source: heart-and-soul/security.md; craft-and-care/security-and-secrets.md; core-credentials REQ-core-credentials-002; dashboard-model-settings Catalog Verify-All API and Hourly Automated Verification Sweep; model-catalog REQ-model-catalog-001; design.md Decisions 2 and 6
Scope: v1-mandatory

#### Scenario: Test checks the routed runtime environment

- **WHEN** an operator selects `Test` for a catalog entry or the scheduled
  verification sweep runs
- **THEN** the request is executed by the runtime-probe coordinator using the
  same shared runtime environment, canonical-to-execution mapping, generated
  runtime configuration, and catalog arguments as new daemon work
- **AND** the returned evidence is labelled as a runtime probe rather than a
  routed session result

#### Scenario: Probe control command is not a generic routable tool

- **WHEN** a model session, ordinary MCP client, or unauthenticated caller
  enumerates or invokes Switchboard tools
- **THEN** it cannot discover or invoke the runtime-probe control command
- **AND** only the Dashboard/Scheduler control client with a valid scoped,
  unexpired signed capability can request a bounded probe by catalog entry ID

#### Scenario: Dashboard-triggered probe requires owner control before Switchboard work

- **WHEN** authoritative owner-auth state is unavailable, or a dashboard caller
  lacks valid central key-or-session authority for Test or Verify
- **THEN** the API returns its safe unavailable or `401` response before it
  contacts Switchboard
- **AND** it launches no runtime and writes no verification evidence

#### Scenario: Direct control-plane callers require the scoped signed capability

- **WHEN** a caller reaches the private runtime-probe command without the
  valid fixed-algorithm signature, fixed audience, matching caller
  class/catalog ID, accepted bounded time claims, unused nonce, and configured
  verification key
- **THEN** Switchboard rejects it before catalog lookup, runtime launch, or
  verification persistence
- **AND** a generic MCP client cannot substitute its normal connection for
  that capability or replay a previously accepted request

#### Scenario: Scheduled verification is a registered trusted control caller

- **WHEN** the scheduled verification sweep requests a runtime probe
- **THEN** it uses the dedicated control client and scoped capability as an
  explicitly registered scheduler caller
- **AND** it does not bypass the command through generic MCP-tool access

#### Scenario: Production signer mount cannot reach an unsandboxed runtime child

- **WHEN** the Dashboard deployment activates its private signing-key mount
- **THEN** Test, verify-all, and scheduled verification use only the dedicated
  signed client and no dashboard-local model-verification adapter can spawn
- **AND** every other Dashboard runtime-CLI child is forced through the
  exclusive per-invocation identity and kernel-containment sandbox, is
  behaviorally denied access to the signer and peer invocations, and has no
  surviving descendant before staged output can be consumed
- **AND** the client signs nothing until Switchboard verifier readiness matches
  its configured signer key ID to a currently issuable current or retiring
  verifier entry
- **AND** a rollback retains the sandbox and disables those callers, or removes
  the mount before legacy code can start

#### Scenario: Probe success does not close a breaker

- **WHEN** a breaker-open entry's runtime probe succeeds
- **THEN** the API persists its verification result without inserting
  `model_dispatch_attempts.success`
- **AND** the list response and Models page continue to show the entry as
  breaker-open until a later routed success is recorded

#### Scenario: Probe coordinator unavailability is honest

- **WHEN** the runtime-probe coordinator is unavailable or cannot establish
  the authoritative runtime environment, exceeds its rate/concurrency limit,
  or reaches its bounded timeout
- **THEN** the test response reports the coordinator as unavailable or degraded
  without overwriting the last successful verification evidence
- **AND** the UI does not show a successful model test or a generic provider
  failure for that condition

### Requirement: Model Breaker Attention Episode Visibility and Reissue

The Models list and per-entry detail API SHALL expose the latest relevant
model-breaker attention episode's sanitized lifecycle state, timestamps, and
safe reason independently from verification and breaker facts. The Models page
SHALL make an `uncertain` episode's one permitted manual reissue deliberate:
it presents a confirmation-gated `Send a new alert` control, disables it while
the request is pending or a successor exists, and immediately reports the new
episode result. Every Models endpoint that exposes attention episode data or
permits reissue SHALL use a fail-closed `require_dashboard_owner_control`
server dependency. That dependency SHALL require central `dashboard-owner-auth`
configured-key-or-owner-session authority, treating either valid method as the
single dashboard-owner principal, with CSRF and exact Origin on cookie-backed
reissue. It SHALL not inherit the dashboard's optional general API-auth behavior. No UI visibility rule is an
authorization substitute. No other attention state offers an automatic resend
control.

ID: REQ-dashboard-model-settings-002
Source: heart-and-soul/vision.md Rule 1; RFC 0005; runtime-attention-outbox REQ-runtime-attention-outbox-003; design.md Decision 6
Scope: v1-mandatory

#### Scenario: Independent operational facts are visible

- **WHEN** the Models page renders a catalog entry with verification evidence,
  an open breaker, and an attention episode
- **THEN** it renders those as distinct labelled facts with canonical status
  indicators and safe timestamps/reasons
- **AND** a runtime-probe success explicitly states that it does not clear the
  breaker

#### Scenario: Manual reissue is server-enforced and idempotent

- **WHEN** an operator confirms `Send a new alert` for an `uncertain` episode
- **THEN** the API creates or returns exactly one successor episode for that
  original episode and returns both safe episode identities and states
- **AND** concurrent or retried submissions cannot create additional
  successors for the same original episode

#### Scenario: Owner-control configuration and credentials are required before observation or delivery

- **WHEN** authoritative owner-auth state is unavailable and a
  caller requests attention detail or `Send a new alert`
- **THEN** the API returns a safe `503` owner-control-unavailable response
  without exposing the episode, creating a successor, or invoking delivery
- **WHEN** auth state is healthy but a caller lacks a valid configured key or
  owner session (including a malformed supplied header despite a valid cookie)
- **THEN** the API returns `401` without exposing the episode, creating a
  successor, or invoking delivery
- **WHEN** a caller supplies the matching configured `X-API-Key` or a valid owner session (with CSRF and exact Origin for cookie-backed reissue)
- **THEN** it is the authenticated dashboard-owner principal eligible for the
  existing observation/reissue state checks

#### Scenario: Non-uncertain episode cannot be resent from the Models page

- **WHEN** a caller requests a manual reissue for a pending, sending, sent,
  failed, or already-reissued episode
- **THEN** the API rejects it without creating an episode or external delivery
- **AND** the UI keeps the control absent or disabled with an accessible reason

#### Scenario: Attention observation degradation stays truthful

- **WHEN** the API cannot read the model's attention-episode source
- **THEN** the response marks that observation unavailable rather than
  returning no episode as a proven no-alert state
- **AND** existing verification and breaker fields retain their independently
  available values


## openspec/changes/harden-runtime-auth-and-breaker-attention/specs/dashboard-spend-dashboard/spec.md


Goal/invocation attribution is additive stored lineage; null historical unit/session provenance stays historical. Fleet-halt visibility remains an actual control-plane fact, not a region stopped-state inference. Budget exhaustion and settled spend are different receipts.

Before integration, the actual native owner and ROOT must adopt/archive the full current producer/consumer and all original scenarios, or allocate an exact atomic full-body amendment. Each clause remains held; none of the 13 active files is edited by this proposal. See the full 45-change program context and PR4485 amendment controlled by bu-li92cu.



## MODIFIED Requirements

### Requirement: Fleet-Halt Visibility

The dashboard SHALL surface the monthly spend ceiling's enforcement action —
dispatches being denied fleet-wide — as a loud, explicit state on the Spend
page, not silence. The ceiling is enforced by `check_monthly_ceiling` / the
spawner, which writes an `outcome='quota_skip'` row to
`public.model_dispatch_attempts` with `failure_reason` starting `Monthly spend
ceiling reached` for every denied dispatch.

ID: REQ-dashboard-spend-dashboard-001
Source: dashboard-spend-dashboard Fleet-Halt Visibility; runtime-attention-outbox REQ-runtime-attention-outbox-001; design.md Decisions 3-5
Scope: v1-mandatory

#### Scenario: A red fleet-halt banner renders while the ceiling is breached

- **WHEN** `GET /api/dispatch/attempts?outcome=quota_skip&reason_prefix=Monthly+spend+ceiling+reached`
  (scoped to the current calendar month) returns one or more rows
- **THEN** the Spend page renders a red state reading `Monthly ceiling reached —
  N dispatches denied since <timestamp>`, where N is the total matching count
  for the current month and `<timestamp>` is the earliest matching row's `ts`
- **AND** the banner additionally shows a denied-today count (rows since the
  start of the current owner-tz day)
- **AND** the banner does not render when no such rows exist for the current
  month

#### Scenario: An attempts drawer lists recent denials with session doors

- **WHEN** the fleet-halt banner is active
- **THEN** an expandable drawer lists the most recent denied attempts (butler,
  timestamp, failure reason)
- **AND** each row whose `session_id` is non-null links to that session's
  detail page (`/sessions/:id`)
- **AND** rows with no `session_id` render without a session door instead of a
  dead or broken link

#### Scenario: The owner is notified exactly once per breach window

- **WHEN** the monthly ceiling transitions from not-breached to breached — the
  first current-calendar-month `quota_skip` dispatch denial whose
  `failure_reason` starts `Monthly spend ceiling reached`
- **THEN** the authorized producer operation in the spawner transaction appends
  exactly one `fleet_halt` attention episode keyed to that calendar-month breach
  window and carrying only the safe denied-dispatch count and Spend drawer door
- **AND** that episode is the sole route by which the owner is notified — the
  spawner reaches Switchboard's outbox delivery and nothing else, so it neither
  pages the owner directly nor relies on an audit-log debounce marker
- **AND** every later denial in that same breach window appends neither another
  episode nor another page

#### Scenario: Switchboard delivery is failure-isolated from a ceiling denial

- **WHEN** the fleet-halt episode is pending, sent, failed, or uncertain
- **THEN** Switchboard owns its delivery lifecycle under the
  `runtime-attention-outbox` requirements
- **AND** an episode claim, Messenger failure, or bookkeeping failure never
  blocks or delays the spawner's ceiling-deny decision

#### Scenario: Degraded attempts source never renders as "no denials"

- **WHEN** `GET /api/dispatch/attempts` fails (network error, non-2xx)
- **THEN** the Spend page SHALL render a degraded-source note for the fleet-halt
  state instead of silently omitting the banner

#### Scenario: Fleet-halt attention observation requires owner control

- **WHEN** the Spend page requests `GET /api/spend/runtime-attention`
- **THEN** the API SHALL require the fail-closed dashboard owner-control
  dependency before reading durable attention state
- **AND** unavailable authoritative auth state returns `503`, while absent or
  invalid central configured-key-or-owner-session authority returns `401`
- **AND** a healthy keyless owner session is sufficient without `X-API-Key`
- **AND** a source read failure returns `available: false` rather than an
  available empty observation


## openspec/changes/reconcile-dashboard-conversation-contracts/specs/dashboard-conversations/spec.md


The new input is admitted deterministically into a goal or its actual named terminal action. Classifier route lanes and provider resume execution retire at M6 under D3/D9; durable anchors, cancellation, reply idempotency and historical data remain. The full pre-admission and citation contracts below govern the successor; active terminal-recovery and continuity owners retain integration authority.

Before integration, the actual native owner and ROOT must adopt/archive the full current producer/consumer and all original scenarios, or allocate an exact atomic full-body amendment. Each clause remains held; none of the 13 active files is edited by this proposal. See the full 45-change program context and PR4485 amendment controlled by bu-li92cu.



## MODIFIED Requirements

### Requirement: Dashboard Ingestion Envelope Construction

Dashboard conversations SHALL construct `ingest.v1` envelopes that flow through
the standard Switchboard ingestion pipeline, submitted to the Switchboard's
`ingest` MCP tool. RFC 0003 §"ingest.v1 Envelope Format" SHALL recognize
`dashboard` / `internal` as the canonical pair for this direct owner-dashboard
ingress; it is not connector provenance. The dashboard API, not a connector
startup probe, SHALL assign `dashboard:web:{conversation_id}` as the endpoint
identity.

ID: REQ-dashboard-conversations-001
Source: RFC 0003 § ingest.v1 Envelope Format; dashboard-conversations § Dashboard Ingestion Envelope Construction; design.md Decision 1
Scope: v1-mandatory

#### Scenario: Envelope structure for dashboard messages

- **WHEN** a dashboard message is submitted for ingestion
- **THEN** the envelope SHALL have:
  - `schema_version`: `"ingest.v1"`
  - `source.channel`: `"dashboard"`
  - `source.provider`: `"internal"`
  - `source.endpoint_identity`: `"dashboard:web:{conversation_id}"`
  - `event.external_event_id`: `"{message_id}"`, where `message_id` is
    client-generated for a new user message and reused for a retry of that
    message
  - `event.external_thread_id`: `"{conversation_id}"`
  - `event.observed_at`: current timestamp
  - `sender.identity`: `"dashboard:operator"`
  - `payload.normalized_text`: the user's message content (with conversation
    context for follow-ups)
  - `payload.raw`: `{"source": "dashboard", "conversation_id": "...",
    "message_id": "...", "message": "...", "page_context": {...}}` —
    `page_context` is present only when the client supplied one
  - `control.policy_tier`: `"interactive"`
  - `control.ingestion_tier`: `"full"`
  - `control.pinned_target`: present per the routing-pin scenarios below

#### Scenario: Dashboard messages bypass discretion

- **WHEN** a dashboard message is ingested by the Switchboard
- **THEN** the `"dashboard"` channel SHALL NOT be subject to discretion
  evaluation (operator messages are always intentional)

#### Scenario: Per-butler conversation envelope carries a routing pin

- **WHEN** a message is submitted via `POST /api/butlers/{name}/conversations`
  (or a follow-up on an existing per-butler conversation) and `{name}` is a
  routable domain butler (not the Switchboard staffer)
- **THEN** the constructed envelope SHALL set `control.pinned_target` to
  `{name}`
- **AND** the Switchboard SHALL route the message to `{name}` deterministically,
  without LLM classification choosing a different target

#### Scenario: Switchboard-addressed conversations are unpinned until routed

- **WHEN** a message is submitted via `POST
  /api/butlers/switchboard/conversations` (the dashboard chat widget's
  classification-routed conversation) and the conversation has no
  `routed_butler` yet
- **THEN** the constructed envelope SHALL NOT set `control.pinned_target` — the
  Switchboard staffer is never a registered, routable target
- **AND** the message proceeds through Switchboard's ordinary classify -> route
  pipeline

#### Scenario: Classification-routed follow-up is sticky

- **WHEN** a follow-up message is submitted via `POST
  /api/butlers/switchboard/conversations/{conversation_id}/messages` and the
  conversation already has a `routed_butler` set (from an earlier successful
  `route_to` decision)
- **THEN** the constructed envelope SHALL set `control.pinned_target` to
  `routed_butler`, bypassing classification entirely
- **AND** a conversation whose `routed_butler` is still NULL (not yet routed, or
  a bug-lane report with no domain-butler target) continues through
  classification as in the "unpinned until routed" scenario above

#### Scenario: Optional page context is preserved

- **WHEN** a dashboard message is submitted with a `page_context` object
  (`route`, `query_params`, optional `entity_ref`) on the request body
- **THEN** the envelope's `payload.raw.page_context` SHALL carry that object
  unchanged, grounding the statement for the routed butler
- **AND** when no `page_context` is provided, `payload.raw` SHALL NOT contain a
  `page_context` key

#### Scenario: Sticky follow-up pinning for classification-routed conversations

- **WHEN** a follow-up message is submitted via `POST /api/butlers/switchboard/conversations/{conversation_id}/messages` and the conversation already has a `routed_butler` set (from an earlier successful `route_to` decision)
- **THEN** the constructed envelope SHALL set `control.pinned_target` to `routed_butler`, bypassing classification entirely
- **AND** a conversation whose `routed_butler` is still NULL (not yet routed, or a bug-lane report with no domain-butler target) continues through classification as in the "unpinned until routed" scenario above

#### Scenario: Optional page context on dashboard messages

- **WHEN** a dashboard message is submitted with a `page_context` object (`route`, `query_params`, optional `entity_ref`, optional `visible_resource` {`kind`, `id`, `filters`, `window`}, optional `visible_summary`) on the request body
- **THEN** the API SHALL strip any query-param key containing a secret-ish marker (`token`, `key`, `secret`, `password`, `authorization`) before persisting or forwarding it, regardless of what the client sent
- **AND** the API SHALL reject a `visible_resource.kind` outside the closed registry vocabulary
- **AND** a payload exceeding the size budget SHALL be truncated (dropping `visible_resource.filters`, then `query_params`, then trimming `visible_summary`, in that order) with `truncated=true` set, never silently dropped or rejected outright
- **AND** the persisted user message row SHALL store the (possibly redacted/truncated) `page_context` plus a `captured_at` timestamp
- **AND** the envelope's `payload.raw.page_context` SHALL carry that object unchanged, grounding the statement for the routed butler
- **AND** when no `page_context` is provided, `payload.raw` SHALL NOT contain a `page_context` key

#### Scenario: A retry reuses the originally-captured page context

- **WHEN** a dashboard message is retried with the same client-generated `message_id` (`message_create_idempotent`'s conflict path)
- **THEN** the API SHALL forward the `page_context` stored on the original write, not a `page_context` on the retry request body, into the ingest envelope
- **AND** no new capture SHALL occur for the retried message


## openspec/changes/unify-dashboard-navigation-intent/specs/dashboard-shell/spec.md


unaffected

Before integration, the actual native owner and ROOT must adopt/archive the full current producer/consumer and all original scenarios, or allocate an exact atomic full-body amendment. Each clause remains held; none of the 13 active files is edited by this proposal. See the full 45-change program context and PR4485 amendment controlled by bu-li92cu.



## ADDED Requirements

### Requirement: Unified Navigation Intent Warmup

The dashboard shell SHALL expose one navigation-intent primitive for route-aware navigation and
disclosure controls. For the current destination, one intent cycle SHALL warm every resource that
the shell capability authority maps: the lazy route chunk and, when declared, the destination's
query data. Both projections SHALL be coordinated by one pending timer per mounted primitive
instance.

Pointer intent SHALL require an uninterrupted 120 ms dwell. Keyboard focus and activation by click,
Enter, or Space where the control's existing accessibility contract exposes Space activation SHALL
warm immediately. Activation handlers MUST start warmup before router navigation or an imperative
activation callback begins.

An intent cycle SHALL be bound to one mounted control and one target. It starts with the first
pointer enter, focus, imperative immediate-warm signal, or activation after the prior cycle ends.
Focus and the immediately following click, Enter, or supported Space activation SHALL join the
current cycle. The cycle ends after activation dispatch completes, when neither pointer nor focus
remains, when the target changes, or when the primitive unmounts. A remount starts with fresh cycle
state. Each resource SHALL start at most once within a cycle; ending a cycle SHALL permit a later
distinct cycle to request warmup again without aborting or evicting work already started.

Navigation warmup MUST remain speculative, side-effect-free, and failure-contained. It MUST use the
destination's existing authenticated read, exact query key, stale time, and TanStack Query cache
policy; it MUST NOT add an authorization path, persistent cache, mutation, provider effect, URL
state, telemetry payload, or event-bus payload. Route chunks SHALL use the browser module cache.

#### Scenario: Deliberate pointer intent warms every mapped resource

- **WHEN** a route-aware control targets `/sessions/abc` with both a route-chunk loader and query
  warmup mapping
- **AND** pointer presence remains uninterrupted for 120 ms
- **THEN** the primitive starts the mapped route-chunk loader
- **AND** it submits the mapped query through the current `QueryClient` with the destination's exact
  query key, query function, and stale time
- **AND** both resources start from the same intent cycle

#### Scenario: Pointer sweep ends before intent is established

- **WHEN** pointer enter is followed by pointer leave or pointer cancellation before 120 ms
- **THEN** the pending intent cycle is cancelled
- **AND** neither the chunk loader nor query prefetch starts

#### Scenario: Keyboard focus warms immediately

- **WHEN** a route-aware control receives keyboard focus for a mapped destination
- **THEN** every available mapped resource starts without waiting for the pointer delay
- **AND** the control remains keyboard-operable under its existing accessibility contract

#### Scenario: Click, Enter, or supported Space warms before activation

- **WHEN** the owner activates a route-aware control by click, Enter, or Space where that control's
  existing accessibility contract exposes Space activation while a pointer timer may be pending
- **THEN** the primitive cancels the pending timer and starts every available mapped resource once
  for that intent cycle
- **AND** warmup starts before router navigation or the caller's imperative activation callback

#### Scenario: Only a route chunk is mapped

- **WHEN** intent is established for a destination with a route-chunk loader and no query warmup
  mapping
- **THEN** the route chunk starts
- **AND** the absent query mapping causes no error, placeholder query, or alternate request

#### Scenario: Only query data is available

- **WHEN** intent is established for a destination with query warmup but no available route-chunk
  loader
- **THEN** the query warmup is submitted through the current `QueryClient`
- **AND** the absent chunk mapping does not suppress or delay the query warmup

#### Scenario: Query provider is unavailable

- **WHEN** intent is established for a destination whose chunk is mapped but no current
  `QueryClient` is available
- **THEN** the chunk warmup may proceed
- **AND** query warmup is a contained no-op without a render error or substitute cache

#### Scenario: Destination has no warmup mapping

- **WHEN** the target is null, invalid, or unmapped by the shell capability authority
- **THEN** navigation intent is a no-op
- **AND** it issues no resource request and does not alter navigation behavior

#### Scenario: Speculative resource rejection is contained

- **WHEN** the mapped chunk loader, query prefetch, or both reject
- **THEN** no rejection escapes the navigation-intent boundary
- **AND** failure of one resource does not cancel the other
- **AND** navigation is neither blocked nor replaced by a speculative toast or error state
- **AND** actual destination loading retains authority over its normal authorization, loading,
  empty, and error presentation

#### Scenario: Pending work is cancelled when its cycle ends

- **WHEN** a pointer timer is pending and, before 120 ms, the target changes, the primitive
  unmounts, or all intent presence ends because pointer leave or cancellation occurs with no focus
  or blur occurs with no pointer presence
- **THEN** the pending cycle is cancelled
- **AND** no resource for the stale or abandoned target starts
- **AND** a replacement target requires a new intent signal

#### Scenario: Focus and activation share one cycle without suppressing a later cycle

- **WHEN** a mounted control receives focus and is then activated by click, Enter, or supported Space
  for the same destination
- **THEN** focus and activation belong to the same intent cycle
- **AND** the pointer timer, when present, is cancelled before immediate warmup
- **AND** each mapped resource starts at most once for that intent cycle
- **AND** completion of activation ends the cycle
- **AND** blur followed by refocus when no pointer remains, pointer leave followed by re-entry when
  no focus remains, or a later explicit activation after completion starts a distinct cycle that
  may request each mapped resource again
- **AND** later cycles and other controls continue to use TanStack Query freshness and in-flight
  deduplication and the browser module cache rather than a second navigation-intent cache

#### Scenario: Warmed query preserves trust and cache boundaries

- **WHEN** navigation intent submits a destination query before navigation
- **THEN** it uses only the same authenticated, side-effect-free read and exact query identity the
  destination normally consumes
- **AND** the response may enter only the existing in-memory TanStack Query cache under its normal
  stale-time and garbage-collection policy
- **AND** warmup does not mint or refresh credentials, broaden authorization, persist a second copy,
  mutate server state, invoke a provider effect, or place response content in URL state, telemetry,
  logs, or event-bus payloads

#### Scenario: Predecessor hooks are retired atomically

- **WHEN** the unified primitive is implemented
- **THEN** `RowLink`, `DisclosureRow`, `SessionTable`, `Sidebar`, and `EntityFinder` migrate in the
  same implementation change
- **AND** `usePrefetchOnIntent` and `useRouteChunkPrefetchOnIntent`, their duplicate timing
  constants, and obsolete focused tests are removed without temporary compatibility aliases
- **AND** one consolidated behavior suite verifies the primitive while caller tests verify wiring
  and warm-before-activation ordering, including focus followed by Space for `DisclosureRow` and the
  nested-interactive `RowLink` fallback

#### Scenario: Implementation rollback requires no data repair

- **WHEN** the future unified-hook implementation is reverted before adoption
- **THEN** predecessor hook wiring can be restored atomically
- **AND** no server schema, query key, persisted cache format, credential, or provider state needs
  migration or repair


## openspec/changes/education-mind-map-lifecycle-integrity/specs/dashboard-education-api/spec.md


The future producer is an education obligation goal, with stable request and actual invocation correlation; it cannot fabricate legacy session IDs. The complete goal receipt contract retains every delivered/no_record/unproven and failure case. Current trigger/session producer remains until the education owner allocates its exact migration.

Before integration, the actual native owner and ROOT must adopt/archive the full current producer/consumer and all original scenarios, or allocate an exact atomic full-body amendment. Each clause remains held; none of the 13 active files is edited by this proposal. See the full 45-change program context and PR4485 amendment controlled by bu-li92cu.



## MODIFIED Requirements

### Requirement: Mind map status update endpoint

The system SHALL expose `PUT /api/education/mind-maps/{mind_map_id}/status`
accepting a JSON body `{"status": "<new_status>"}` where `new_status` is one of
`active`, `completed`, `abandoned`.

`draft` SHALL NOT be an accepted request value: a mind map enters `draft` only
through its creation path, and the dashboard MUST NOT be able to manufacture
one. Read responses SHALL nonetheless be able to carry `status = 'draft'`,
because draft maps are real rows the dashboard lists and can abandon.

The endpoint SHALL call the existing
`mind_map_update_status(pool, mind_map_id, status)` tool function. The endpoint
SHALL return 404 if the mind map does not exist. The endpoint SHALL return 422
if the status value is not one of the three allowed request values.

The endpoint SHALL return 409 when the requested transition is rejected by the
mind map lifecycle rules rather than by request validation — in particular when
the target status is `active` and the mind map has zero nodes, and when the
transition itself is not permitted (for example `draft` → `completed`). The
409 body SHALL state which rule rejected the transition, so the dashboard can
explain the refusal rather than reporting a generic failure. The endpoint MUST
NOT translate a lifecycle rejection into a 200 with an unchanged status.

On success, the endpoint SHALL return the updated mind map object (without
nodes/edges).

#### Scenario: Abandon an active mind map

- **WHEN** a PUT request is made to `/api/education/mind-maps/{id}/status` with body `{"status": "abandoned"}`
- **AND** the mind map exists with status `active`
- **THEN** the response status SHALL be 200
- **AND** the returned mind map object SHALL have `status` equal to `abandoned`

#### Scenario: Abandon a draft mind map

- **WHEN** a PUT request is made to `/api/education/mind-maps/{id}/status` with body `{"status": "abandoned"}`
- **AND** the mind map exists with status `draft` and zero nodes
- **THEN** the response status SHALL be 200
- **AND** the returned mind map object SHALL have `status` equal to `abandoned`

#### Scenario: Re-activate an abandoned mind map

- **WHEN** a PUT request is made to `/api/education/mind-maps/{id}/status` with body `{"status": "active"}`
- **AND** the mind map exists with status `abandoned` and at least one node
- **THEN** the response status SHALL be 200
- **AND** the returned mind map object SHALL have `status` equal to `active`

#### Scenario: Activating a zero-node mind map is refused

- **WHEN** a PUT request is made to `/api/education/mind-maps/{id}/status` with body `{"status": "active"}`
- **AND** the mind map exists with zero nodes
- **THEN** the response status SHALL be 409
- **AND** the response body SHALL state that a curriculum with no concepts cannot be activated
- **AND** the mind map's stored `status` SHALL be unchanged

#### Scenario: Draft is rejected as a request value

- **WHEN** a PUT request is made to `/api/education/mind-maps/{id}/status` with body `{"status": "draft"}`
- **THEN** the response status SHALL be 422

#### Scenario: Invalid status value

- **WHEN** a PUT request is made to `/api/education/mind-maps/{id}/status` with body `{"status": "paused"}`
- **THEN** the response status SHALL be 422

#### Scenario: Mind map not found

- **WHEN** a PUT request is made to `/api/education/mind-maps/{nonexistent-id}/status` with body `{"status": "abandoned"}`
- **THEN** the response status SHALL be 404

---

### Requirement: Curriculum request submission endpoint

The system SHALL expose `POST /api/education/curriculum-requests` accepting a JSON body `{"topic": "<topic>", "goal": "<optional_goal>"}`.

The `topic` field SHALL be required and non-empty (max 200 characters). The `goal` field SHALL be optional (max 500 characters).

Before any detached work begins, the endpoint SHALL insert an immutable receipt row into `education.curriculum_requests` with status `accepted`, the submitted topic and goal, and a generated `id`. That `id` is the request's permanent receipt.

The endpoint SHALL NOT persist a `pending_curriculum_request` KV key. The one-pending-at-a-time guard SHALL be the partial unique index `uq_curriculum_requests_one_open`, which permits at most one receipt in a non-terminal status (`accepted`, `running`). When the insert is refused by that index, the endpoint SHALL return 409 Conflict.

When the receipt store cannot be read or written because the education migration chain is absent, the endpoint SHALL return 503 rather than accepting a request it cannot evidence.

On success, the endpoint SHALL return 202 Accepted with body `{"status": "accepted", "topic": "<topic>", "request_id": "<uuid>"}`. `202` SHALL mean *accepted and recorded* only; it SHALL NOT be treated by any caller as evidence that a curriculum was created or that the owner was contacted.

#### Scenario: Submit a new curriculum request

- **WHEN** a POST request is made to `/api/education/curriculum-requests` with body `{"topic": "Python", "goal": "Learn web development with Flask"}`
- **AND** no non-terminal receipt exists
- **THEN** the response status SHALL be 202
- **AND** the response body SHALL contain `{"status": "accepted", "topic": "Python"}` and a `request_id`
- **AND** a receipt row SHALL exist in `education.curriculum_requests` with status `accepted`, the topic and goal, and no outcome evidence

#### Scenario: Receipt precedes detached work

- **WHEN** a curriculum request is accepted
- **THEN** the receipt row SHALL be persisted before the detached curriculum task is created

#### Scenario: Submit request without goal

- **WHEN** a POST request is made with body `{"topic": "Linear Algebra"}`
- **AND** no non-terminal receipt exists
- **THEN** the response status SHALL be 202
- **AND** the receipt row SHALL have `goal` set to null

#### Scenario: Duplicate request while one is in flight

- **WHEN** a POST request is made to `/api/education/curriculum-requests`
- **AND** a receipt already exists in status `accepted` or `running`
- **THEN** the response status SHALL be 409
- **AND** the response body SHALL indicate a curriculum request is already pending

#### Scenario: Receipt store unavailable

- **WHEN** a POST request is made and `education.curriculum_requests` does not exist
- **THEN** the response status SHALL be 503

#### Scenario: Empty topic

- **WHEN** a POST request is made with body `{"topic": ""}`
- **THEN** the response status SHALL be 422

#### Scenario: Topic exceeds length limit

- **WHEN** a POST request is made with a `topic` longer than 200 characters
- **THEN** the response status SHALL be 422


## openspec/changes/education-mind-map-lifecycle-integrity/specs/dashboard-education-ui/spec.md


These panels live in the education Region. Actual map/request doors and historical session evidence remain. New invocation/goal doors require stored linkage. No unit Trigger control remains in the future UI, and no completed or notified claim comes from an accepted request.

Before integration, the actual native owner and ROOT must adopt/archive the full current producer/consumer and all original scenarios, or allocate an exact atomic full-body amendment. Each clause remains held; none of the 13 active files is edited by this proposal. See the full 45-change program context and PR4485 amendment controlled by bu-li92cu.



## ADDED Requirements

### Requirement: Age-aware empty-curriculum copy

When the Curriculum tab renders a mind map that has zero nodes, the copy it
shows SHALL be derived from the map's `status` and the age of its `created_at`,
so that the page tells the owner what is actually true at that moment.

The evergreen string "This curriculum has no concepts yet — the butler is
still building it" SHALL NOT be rendered in any state. It asserts progress it
cannot observe, and it reads identically at ten seconds and at thirty-four
days.

The tiers, evaluated against `now - created_at`:

| Status | Age | Copy |
| --- | --- | --- |
| `draft` | under 30 minutes | "Setting up this curriculum — the butler is mapping out the concepts." |
| `draft` | 30 minutes to under 24 hours | "Still setting up — requested {relative age}. This is taking longer than usual." |
| `draft` | 24 hours or more | "Setup stalled — requested {relative age} and no concepts have been added yet." |
| `abandoned` | any | "This curriculum was abandoned before any concepts were mapped." |
| `completed` | any | "This curriculum was marked complete without any concepts." |
| `active` | any | "This curriculum is marked active but has no concepts. That should not be possible — please report it." |

The first boundary is 30 minutes because that is the default maximum lifetime
of the session that builds the curriculum (`session_timeout_s` defaults to
1800 at `src/butlers/api/routers/model_settings.py:81`). Below it, a working
session explains the empty map, so the calm copy is true. Above it, no session
that started with the request can still be running under default
configuration, so copy promising an imminent finish would be asserting
something the page cannot know — the same fault as the evergreen string, only
smaller.

`{relative age}` SHALL be a human-readable elapsed duration derived from
`created_at` (for example "3 hours ago", "34 days ago"). The copy SHALL be
computed at render time, not cached from a server-side status field, so it
stays accurate without a refetch and does not depend on the weekly staleness
sweep having run.

In the stalled tier the graph area SHALL additionally present the map's
"Abandon" action inline, so the owner can clear a stalled curriculum from the
place where they discover it.

The `active` row exists as a fault indicator, not as a supported state: the
mind map content invariant forbids an `active` zero-node map. If the client
receives one, it SHALL surface the fault rather than dressing it as progress.

#### Scenario: Freshly requested curriculum shows setup copy

- **WHEN** the Curriculum tab renders a `draft` mind map with 0 nodes created 2 minutes ago
- **THEN** the graph area SHALL display "Setting up this curriculum — the butler is mapping out the concepts."
- **AND** it SHALL NOT display any "still building" copy

#### Scenario: Slow setup shows elapsed time

- **WHEN** the Curriculum tab renders a `draft` mind map with 0 nodes created 3 hours ago
- **THEN** the graph area SHALL display the "Still setting up" copy including a relative age of "3 hours ago"
- **AND** the copy SHALL NOT promise that setup will finish shortly

#### Scenario: A map younger than the session lifetime reads as in progress

- **WHEN** the Curriculum tab renders a `draft` mind map with 0 nodes created 20 minutes ago
- **THEN** the graph area SHALL display "Setting up this curriculum — the butler is mapping out the concepts."
- **AND** it SHALL NOT display the "Still setting up" copy, because a session started with the request may still be running

#### Scenario: Stalled setup is named as stalled

- **WHEN** the Curriculum tab renders a `draft` mind map with 0 nodes created 34 days ago
- **THEN** the graph area SHALL display "Setup stalled — requested 34 days ago and no concepts have been added yet."
- **AND** the "Abandon" action SHALL be presented inline in the graph area

#### Scenario: Boundary at 24 hours moves from slow to stalled

- **WHEN** a `draft` mind map with 0 nodes has `created_at` exactly 24 hours in the past
- **THEN** the stalled copy SHALL be shown, not the "Still setting up" copy

#### Scenario: Abandoned empty curriculum reads as abandoned

- **WHEN** the Curriculum tab renders an `abandoned` mind map with 0 nodes
- **THEN** the graph area SHALL display "This curriculum was abandoned before any concepts were mapped."

#### Scenario: Active zero-node map is surfaced as a fault

- **WHEN** the Curriculum tab receives a mind map with `status = 'active'` and 0 nodes
- **THEN** the graph area SHALL display the integrity-fault copy naming the state as impossible
- **AND** it SHALL NOT display setup, "still building", or any other progress copy

#### Scenario: Copy updates without a refetch as the map ages

- **WHEN** a `draft` mind map with 0 nodes crosses the 30-minute boundary while the page is open
- **THEN** the rendered copy SHALL move to the next tier on the next render, because it is computed from `created_at` at render time

---

### Requirement: Per-map review fetch failure surfacing

The Reviews surfaces fan out one pending-reviews request per mind map. A
failure of any one of those requests SHALL be surfaced to the owner, named,
and SHALL NOT be folded into the calm empty state.

Specifically:

- The result of each per-map query SHALL be tracked individually. A query that
  errored SHALL NOT be coerced to an empty list of reviews.
- The "No reviews scheduled" empty state SHALL be rendered only when every
  per-map query succeeded and all of them returned zero reviews. If any query
  failed, the empty state MUST NOT be shown, because the surface cannot know
  whether reviews exist.
- A failed per-map query SHALL be reported with the `SourceDegradedNote`
  vocabulary (`frontend/src/components/ui/query-boundary.tsx`), naming the
  affected mind map by title and the reason, per
  `docs/api_and_protocols/response-conventions.md` ("Gate any verdict/all-clear
  renderer on the relevant flag(s) ... never suppress it").
- Reviews from the maps that did succeed SHALL still render. A partial failure
  degrades the surface; it does not blank it.
- The same rules apply to the `/education` page Reviews tab and to the
  education butler-detail Reviews tab, since both perform the same fan-out.

A review the owner never sees because its fetch failed silently is worse than
a banner they do see. Fabricated calm is the specific failure mode this
requirement exists to prevent.

#### Scenario: One map's review fetch fails among several

- **WHEN** the Reviews tab fans out over 3 active mind maps and the request for map "Rust" fails
- **AND** the other two maps return reviews
- **THEN** the reviews from the two succeeding maps SHALL render
- **AND** a `SourceDegradedNote` SHALL name "Rust" and the failure reason
- **AND** the "No reviews scheduled" empty state SHALL NOT be rendered

#### Scenario: All review fetches fail

- **WHEN** every per-map pending-reviews request fails
- **THEN** the tab SHALL render a degraded note naming the failing maps
- **AND** the tab SHALL NOT render "No reviews scheduled — keep learning and reviews will appear here."

#### Scenario: A failing map with genuinely zero reviews is still not calm

- **WHEN** the Reviews tab fans out over 2 maps, one returns zero reviews and the other's request fails
- **THEN** the empty state SHALL NOT be rendered, because the surface cannot distinguish "no reviews" from "not known"
- **AND** a degraded note SHALL name the failing map

#### Scenario: Empty state requires every query to have succeeded

- **WHEN** all per-map pending-reviews requests succeed and every one returns zero reviews
- **THEN** the tab SHALL render "No reviews scheduled — keep learning and reviews will appear here."
- **AND** no degraded note SHALL be rendered

#### Scenario: Butler-detail Reviews tab surfaces the same failure

- **WHEN** the education butler-detail Reviews tab fans out over active mind maps and one map's request fails
- **THEN** its pending reviews timeline SHALL render a degraded note naming the failing map
- **AND** its KPI row SHALL NOT present counts as complete while a source is degraded

#### Scenario: Recovery clears the degraded note

- **WHEN** a previously failing per-map request succeeds on a subsequent refetch
- **THEN** the degraded note for that map SHALL be removed
- **AND** that map's reviews SHALL appear in the timeline

---

## MODIFIED Requirements

### Requirement: Education page layout with tab panels

The education page SHALL display a page header with title "Education" and a description line. Below the header, the page SHALL render three tab panels: **Curriculum**, **Reviews**, and **Analytics**.

The page SHALL maintain a "selected mind map" state. When the page loads, it SHALL fetch the list of mind maps whose status is `active` or `draft` and auto-select one, preferring the first `active` map and falling back to the first `draft` map when there is no active one. A mind map selector (dropdown) SHALL be visible above the tab panels, allowing the user to switch between mind maps. Draft maps SHALL be listed with a "Setting up" badge distinguishing them from active ones.

Draft maps SHALL NOT be filtered out of the selector. A map the owner cannot see is a map the owner cannot abandon, which is how the phantom curriculum survived unnoticed; visibility is what makes a stalled draft actionable.

The page SHALL coordinate node selection through one page-level `{mindMapId, nodeId}` handler shared by the Curriculum graph, Reviews timeline, and Analytics struggling-node callout. A selection for a different mind map SHALL update the selected map and node together. One shared node-detail panel SHALL render only when the selected map and node resolve together, SHALL remain visible when the user changes tabs, and SHALL not show a placeholder or details from another map when the node is unavailable. Closing the panel SHALL clear only the node selection, preserving the selected map and active tab.

The Curriculum tab SHALL be the default active tab.

#### Scenario: Page loads with active mind maps

- **WHEN** the user navigates to `/education`
- **AND** there are 3 active mind maps
- **THEN** the mind map selector SHALL list all 3 mind maps by title
- **AND** the first mind map SHALL be auto-selected
- **AND** the Curriculum tab SHALL be active

#### Scenario: Page loads with no mind maps

- **WHEN** the user navigates to `/education`
- **AND** there are no mind maps
- **THEN** the page SHALL display an empty state with a prompt to request a new curriculum
- **AND** the tab panels SHALL NOT be rendered

#### Scenario: Draft maps appear in the selector

- **WHEN** the user navigates to `/education`
- **AND** there are 2 active mind maps and 1 draft mind map
- **THEN** the selector SHALL list all 3
- **AND** the draft map SHALL carry a "Setting up" badge
- **AND** an active map SHALL be auto-selected in preference to the draft

#### Scenario: A draft map is selectable when it is the only map

- **WHEN** the user navigates to `/education`
- **AND** the only mind map is a `draft` with zero nodes
- **THEN** the draft map SHALL be auto-selected
- **AND** the tab panels SHALL be rendered
- **AND** the Curriculum tab SHALL show the age-aware empty-curriculum copy for that map

#### Scenario: Switching between tabs preserves selected mind map

- **WHEN** the user selects mind map "Python" from the dropdown
- **AND** switches from the Curriculum tab to the Analytics tab
- **THEN** the Analytics tab SHALL display data for the "Python" mind map

#### Scenario: Unavailable selected node does not render mismatched details

- **WHEN** a selected node no longer belongs to the selected mind map
- **THEN** the shared node-detail panel SHALL NOT render a placeholder or details for another node

---

### Requirement: Mind map graph visualization in Curriculum tab

The Curriculum tab SHALL render the selected mind map as an interactive directed acyclic graph (DAG) using XYFlow with dagre top-to-bottom layout, with the following guarantees:
- Each node SHALL display the concept label and a mastery score badge. Nodes SHALL be color-coded by `mastery_status`:
  - `mastered`: emerald (`#10b981`)
  - `reviewing`: blue (`#3b82f6`)
  - `learning`: amber (`#f59e0b`)
  - `diagnosed`: slate (`#64748b`)
  - `unseen`: gray (`#d1d5db`)
- Edges of type `prerequisite` SHALL render as solid arrows. Edges of type `related` SHALL render as dashed lines.
- Frontier nodes (from the `/frontier` endpoint) SHALL have a pulsing ring indicator to highlight them as next teachable concepts.
- Clicking a node SHALL select it through the shared page-level handler and reveal the shared detail panel showing: node label, description, mastery score, mastery status, next review date (if scheduled), effort estimate, the spaced-repetition internals `ease_factor` and `repetitions`, and a link to view quiz history for that node.
- The detail panel SHALL additionally render the node's pedagogy annotations when its `metadata`
carries them: `concept_type` as a tag beside the mastery status, and each `source_refs` entry as a
row bearing a leading provenance label in plain words, the entry's `location`, and its optional
`note`. A node whose metadata carries neither annotation SHALL render exactly as before.
- Provenance SHALL be resolved against the source registry, by requesting
`GET /api/education/sources?source_ids=...` with the `source_id`s present on the node's
`source_refs` (never the full registry), and rendered in exactly one of four states:
  - **Referenced** when the entry records `provenance: "referenced"` (or names a source and records
  no provenance) AND the registry resolves its `source_id`. Only this state SHALL render the
  resolved source title or a link to the registered source's URL.
  - **Model-recalled** when the entry records `provenance: "model-recalled"` or carries a null
  `source_id`, regardless of whether the registry resolves the source, together with text stating
  that the butler did not read the source and the location is unverified.
  - **Source no longer registered** when the registry resolves and does not contain the entry's
  `source_id`. The panel SHALL show no title and no citation link, and SHALL surface the
  unresolved `source_id` so the owner can act on it.
  - **Not checked against the registry** when the registry request is loading or failed. The panel
  SHALL NOT report the entry as registered or as unregistered, because it has no basis for either.
- An entry with an unrecognized `provenance` value SHALL be rendered as model-recalled: a malformed
annotation is never promoted to a citation.

When the selected mind map has zero nodes, the graph area SHALL render the copy defined by "Age-aware empty-curriculum copy" rather than a fixed string.

ID: REQ-dashboard-education-ui-001
Source: source-grounded-education design.md; REQ-education-source-grounding-002; REQ-dashboard-education-api-001
Scope: v1-mandatory

#### Scenario: Render a mind map with mixed mastery statuses

- **WHEN** the Curriculum tab loads for a mind map with 10 nodes
- **AND** 3 are mastered, 2 reviewing, 2 learning, 1 diagnosed, 2 unseen
- **THEN** the graph SHALL render 10 nodes with the correct color for each status
- **AND** prerequisite edges SHALL be solid arrows
- **AND** the layout SHALL flow top-to-bottom (root concepts at top)

#### Scenario: Frontier nodes highlighted

- **WHEN** the graph renders
- **AND** the frontier endpoint returns 2 nodes
- **THEN** those 2 nodes SHALL have a pulsing ring indicator

#### Scenario: Node click opens detail panel

- **WHEN** the user clicks a node labeled "List Comprehensions"
- **THEN** a detail panel SHALL appear showing the node's label, description, mastery score, mastery status, and next review date

#### Scenario: Empty mind map (no nodes)

- **WHEN** the Curriculum tab loads for a mind map with 0 nodes
- **THEN** the graph area SHALL display the copy selected by "Age-aware empty-curriculum copy" for that map's `status` and `created_at` age
- **AND** the graph area SHALL NOT display "This curriculum has no concepts yet — the butler is still building it"

---

#### Scenario: Referenced source annotation on node detail

- **WHEN** a node's `metadata.source_refs` carries an entry with `provenance: "referenced"` whose
  `source_id` is present in the source registry
- **THEN** the detail panel SHALL show the label "Referenced", the registered source's title, and
  the entry's location
- **AND** a link to the registered source's URL SHALL be offered when the record has one

#### Scenario: Model-recalled location against a registered source

- **WHEN** a node's `metadata.source_refs` carries an entry with `provenance: "model-recalled"`
  whose `source_id` is present in the source registry
- **THEN** the detail panel SHALL label the entry "Model-recalled" and state that the location is
  unverified
- **AND** SHALL NOT offer a link to the registered source

#### Scenario: Dangling source reference after the source is removed

- **WHEN** a node's `metadata.source_refs` names a `source_id` the registry does not contain
- **THEN** the detail panel SHALL label the entry "Source no longer registered"
- **AND** SHALL show no source title and no citation link
- **AND** SHALL show the unresolved `source_id`

#### Scenario: Source registry unavailable

- **WHEN** the source registry request is still loading or has failed
- **AND** a node's `metadata.source_refs` names a `source_id`
- **THEN** the detail panel SHALL label the entry as not checked against the registry
- **AND** SHALL NOT label it registered or unregistered, and SHALL show no source title

#### Scenario: Concept type tag on node detail

- **WHEN** a node's `metadata.concept_type` is one of `factual`, `procedural`, `conceptual`, or
  `creative`
- **THEN** the detail panel SHALL render it as a tag beside the mastery status
- **AND** a node with no `concept_type`, or an unrecognized one, SHALL render no such tag

---

### Requirement: Curriculum management actions

Below the mind map graph, the Curriculum tab SHALL display management actions for the selected mind map:
- A status badge showing the current mind map status (draft/active/completed/abandoned), rendered as "Setting up" for `draft`
- An "Abandon" button (visible when status is `active` or `draft`) that calls `PUT /mind-maps/{id}/status` with `{"status": "abandoned"}`
- A "Re-activate" button (visible when status is `abandoned`) that calls `PUT /mind-maps/{id}/status` with `{"status": "active"}`. The button SHALL be disabled for an abandoned map with zero nodes, with an explanation that there are no concepts to return to, since the server refuses that transition with 409.

There SHALL be no control that activates a mind map with zero nodes. If a 409 lifecycle refusal is nonetheless received from `PUT /mind-maps/{id}/status`, the dashboard SHALL display the reason from the response body rather than a generic error.

Above the mind map selector, a "Request curriculum" button SHALL open a dialog with fields for topic (required) and goal (optional). Submitting the dialog SHALL call `POST /curriculum-requests`. On 202 success, the dialog SHALL close and the UI SHALL announce that the request was **accepted**, not that it was set up or that the owner will be contacted. On 409 conflict, the dialog SHALL display an error that a request is already pending.

After a successful status change, the mind map list query cache SHALL be invalidated to reflect the new status.

#### Scenario: Abandon an active curriculum

- **WHEN** the user views an active mind map
- **AND** clicks "Abandon"
- **THEN** a confirmation dialog SHALL appear
- **AND** confirming SHALL call `PUT /mind-maps/{id}/status` with `abandoned`
- **AND** the status badge SHALL update to "abandoned"

#### Scenario: Abandon a stalled draft curriculum

- **WHEN** the user views a `draft` mind map with zero nodes
- **THEN** the status badge SHALL read "Setting up"
- **AND** an "Abandon" button SHALL be available
- **AND** confirming it SHALL call `PUT /mind-maps/{id}/status` with `abandoned`

#### Scenario: Re-activate is unavailable for an empty abandoned map

- **WHEN** the user views an `abandoned` mind map with zero nodes
- **THEN** the "Re-activate" button SHALL be disabled
- **AND** the disabled state SHALL explain that the curriculum has no concepts to return to

#### Scenario: Request a new curriculum

- **WHEN** the user clicks "Request curriculum"
- **AND** enters topic "Rust" and goal "Systems programming basics"
- **AND** submits the form
- **THEN** the system SHALL call `POST /curriculum-requests`
- **AND** on 202 response, the toast SHALL claim acceptance only, and SHALL NOT claim the curriculum was created or that the butler will message the owner
- **AND** the dialog SHALL close
- **AND** the returned `request_id` SHALL become the tracked receipt for the outcome

#### Scenario: Duplicate curriculum request blocked

- **WHEN** the user submits a curriculum request
- **AND** the server returns 409
- **THEN** the dialog SHALL display "A curriculum request is already pending — please wait for the butler to process it"

---

### Requirement: Spaced repetition review timeline in Reviews tab

The Reviews tab SHALL display pending and upcoming spaced repetition reviews as a grouped timeline list with sections: **Overdue**, **Today**, **This Week**, **Later**.

Each review entry SHALL display: node label, parent mind map title, mastery score badge, and the scheduled review date/time.

Each review entry SHALL be a native keyboard-accessible button that emits its owning `{mindMapId, nodeId}` selection to the page-level handler.

The Overdue and Today sections SHALL be visually distinct (e.g., Overdue has a red left border, Today has an amber left border).

Reviews SHALL be fetched by iterating all active mind maps and calling the pending reviews endpoint for each. Draft maps SHALL be excluded from this fan-out: they have no nodes and therefore no reviews. The pending reviews query SHALL refetch every 15 seconds.

When there are no pending reviews across any mind map **and every per-map query succeeded**, the Reviews tab SHALL display "No reviews scheduled — keep learning and reviews will appear here." When any per-map query failed, the tab SHALL instead follow "Per-map review fetch failure surfacing": the empty state MUST NOT stand in for an unknown result.

#### Scenario: Reviews grouped by time period

- **WHEN** the Reviews tab loads
- **AND** there are 2 overdue nodes, 1 due today, and 3 due this week
- **THEN** the Overdue section SHALL list 2 entries with red left border
- **AND** the Today section SHALL list 1 entry with amber left border
- **AND** the This Week section SHALL list 3 entries

#### Scenario: No pending reviews

- **WHEN** the Reviews tab loads
- **AND** no nodes have `next_review_at` in the past or near future
- **AND** every per-map pending-reviews query succeeded
- **THEN** the tab SHALL display the empty state message

#### Scenario: Reviews span multiple mind maps

- **WHEN** the user has 2 active mind maps each with pending reviews
- **THEN** reviews from both mind maps SHALL appear in the timeline
- **AND** each entry SHALL show its parent mind map title

#### Scenario: Cross-map review selection preserves Reviews context

- **WHEN** map "Python" is selected and the Reviews tab is active
- **AND** the user activates a review for a node in map "Rust"
- **THEN** the selected map and node SHALL change to the "Rust" review together
- **AND** the shared node-detail panel SHALL show the selected "Rust" node
- **AND** the Reviews tab SHALL remain active

#### Scenario: Draft maps are not fanned out over

- **WHEN** the owner has 1 active mind map and 1 draft mind map
- **THEN** the Reviews tab SHALL issue a pending-reviews request only for the active map

---

### Requirement: Education butler-detail Reviews tab

In addition to the standalone `/education` page, the butler detail page for the education butler SHALL provide a "Reviews" tab (`ButlerEducationReviewsTab`) that aggregates learning state across all active mind maps into a single multi-panel dashboard.

The tab SHALL display:
1. A KPI summary row: total review cards, mastered count, overdue count, and average mastery score.
2. A mind maps progress panel: per-map mastery percentage with a progress bar and mastered/total node count.
3. A pending reviews timeline grouped by Overdue / Today / This Week / Later, with colored left borders per group.
4. A frontier nodes panel: the next concepts to learn, each with a mastery badge.
5. A 7-day retention trend chart with the latest mastery percentage as a headline value and a hover tooltip.

Polling intervals SHALL scale with the number of active mind maps so that total request volume stays bounded as the map count grows (base interval 15 seconds for reviews, 30 seconds for mastery and frontier data).

Every panel above is built from a per-map fan-out and SHALL obey "Per-map review fetch failure surfacing": a failed per-map query SHALL be named rather than absorbed, and no panel SHALL present an all-clear or a complete-looking total while one of its sources is degraded.

#### Scenario: Reviews tab aggregates across maps

- **WHEN** the education butler detail page Reviews tab loads with multiple active mind maps
- **THEN** the KPI row, mind maps progress panel, pending reviews timeline, frontier panel, and retention trend chart SHALL each render with data aggregated across those maps
- **AND** the pending reviews SHALL be grouped into Overdue, Today, This Week, and Later sections

#### Scenario: Degraded source is named on the butler-detail tab

- **WHEN** the Reviews tab loads and the pending-reviews request for one mind map fails
- **THEN** a degraded note SHALL name that mind map
- **AND** the KPI row SHALL NOT present its counts as a complete total


## openspec/changes/recover-ingestion-target-deliveries/specs/dashboard-ingestion-dispatch-console/spec.md


Region and goal trace doors are additive only for recorded relationships. Historical unit targets remain evidence; routing log console controls retire without inventing safe replay or a new receiver claim.

Before integration, the actual native owner and ROOT must adopt/archive the full current producer/consumer and all original scenarios, or allocate an exact atomic full-body amendment. Each clause remains held; none of the 13 active files is edited by this proposal. See the full 45-change program context and PR4485 amendment controlled by bu-li92cu.



## MODIFIED Requirements

### Requirement: Timeline Ledger

The `/ingestion` Timeline SHALL render external events as a ledger stream.
- It SHALL include:
  - header band with eyebrow, live freshness/status pill, range-aware headline,
    one-sentence serif summary, and event/session/cost KPIs;
  - sticky toolbar with range picker, search, saved views (with a
    filters-diverged indicator and a re-apply/update path), status filter chips
    (the badge vocabulary exactly), and an "add channel" control alongside any
    active channel chips;
  - bulk-action bar when rows are selected, including a select-all-visible
    action (capped at the bulk replay batch limit) and, on a replay-unsafe
    (409) rejection, a one-click action to deselect exactly the ineligible
    events;
  - hour-group headers with an honest event/error/replay count sourced from the
    histogram endpoint (correct even when only some pages of that hour have
    loaded) and a status-stacked, clickable, keyboard-operable per-minute
    activity strip;
  - ledger rows with time (leftmost column, mono `HH:mm:ss` via the shared Time
    primitive), a click-to-filter channel glyph, sender summary with an inline
    filter/error reason, quiet dot-and-word status, a per-butler dispatch-ticks
    cell, cost, and an expand control; a demoted selection checkbox (hidden by
    default, revealed on hover/focus or once selection mode is active); token
    totals live in the expanded drawer, not the row;
  - in-place expanded drawer with step ledger, raw payload, replay history,
    request metadata, session index, and copy/open actions;
  - footer rollup band for the active filter window.

ID: REQ-dashboard-ingestion-dispatch-console-001
Source: heart-and-soul/vision.md Rule 3; design.md Decision 5
Scope: v1-mandatory

#### Scenario: Every ledger row can expand into full request detail

- **WHEN** the owner clicks or keyboard-activates (focus + Enter) any event
  row, regardless of its status — including `filtered` and `error` rows
- **THEN** an in-place drawer opens below that row
- **AND** the drawer includes a step-ledger tab for every session associated
  with the event
- **AND** each session block exposes status, session id, model, duration, cost,
  token totals, and step rows
- **AND** the drawer includes raw-payload and replay-history tabs
- **AND** the right rail exposes request metadata and a session index
- **AND** for `filtered` or `error` rows with no sessions, the drawer states
  the honest reason (skip-triage rule, filter reason, or dispatch failure)
  instead of a bare "no sessions" message

#### Scenario: Row status never renders as a filled pill

- **WHEN** the owner views the ledger
- **THEN** each row's status renders as a small dot plus a mono status word
  (state color as foreground/border only)
- **AND** no row renders a background-filled status badge
- **AND** the status word matches the badge vocabulary exactly: `ingested`,
  `skipped`, `filtered`, `error`, `failed`, `replay pending`,
  `replay complete`, `replay failed`
- **AND** `filtered` rows are visually de-emphasized (reduced opacity) rather
  than distinguished by a gray pill
- **AND** `filtered`/`error`/`failed` rows show their
  `filter_reason`/`error_detail` inline next to the sender, truncated with a
  title tooltip, instead of only on hover of the status control
- **AND** `failed` (a routing failure recorded after the event was already
  ingested — see `ingestion_event_mark_failed`) renders with the same
  destructive-red treatment as `error`; its recovery action SHALL remain
  disabled when no durable eligible target-delivery work can be queued and
  SHALL never relabel the row `ingested` merely because it was invoked

#### Scenario: Ledger row shows a dispatch-ticks summary without opening the drawer

- **WHEN** the owner views a ledger row for an event with one or more butler
  sessions
- **THEN** the row's dispatch column renders one tick per session (from the
  list-provided, API-capped session summary), each tick's width proportional
  to that session's duration, with a minimum width and a total width bounded
  to the column
- **AND** a failed session's tick renders in the destructive color; other
  ticks render as a neutral foreground color (butler hue is not used on the
  tick fill, consistent with "butler hues only on letter marks"; the butler
  name appears in the tick's hover/focus tooltip instead)
- **AND** a trailing mono session count appears once more than one session
  fired
- **AND** the dispatch column as a whole is keyboard-focusable and activating
  it (click or Enter) opens the row's drawer at the sessions tab, without
  toggling any other row control
- **AND** an event with no sessions renders a muted em-dash instead of an
  interactive cell
- **AND** rendering the cell issues no additional network request beyond the
  events list response

#### Scenario: Raw payload access is audited

- **WHEN** the owner opens or downloads an event raw payload
- **THEN** the backend records an audit entry for that payload access
- **AND** the UI shows loading, unavailable, and permission/error states
  without exposing stale or partial PII as successful content

#### Scenario: Hour strip renders status-stacked activity and reads honestly

- **WHEN** the owner views an hour-group header
- **THEN** the header's event/error/replay counts come from
  `GET /api/ingestion/events/histogram` for that hour and its active
  filters, and are correct even when only some pages of that hour have
  loaded into the ledger
- **AND** the event and error counts include `failed` and `replay_failed`
  events (terminal failures recorded after ingestion or replay) — each counts
  as both an event and an error, the same as `error`, so it never silently
  vanishes from the honest hourly total
- **AND** the per-minute strip renders each minute as a status-stacked bar:
  ingested at a low foreground alpha, filtered/skipped at a lower foreground
  alpha, error/failed/replay failed together in the destructive color, replay
  pending in neutral, and replay complete in green
- **AND** a minute where every event errored, failed, or replay-failed renders
  as solid destructive color
- **AND** the strip exposes an `aria-label` summarizing the hour's activity
  instead of being hidden from assistive technology

#### Scenario: Hour strip minutes are keyboard-operable and route to the ledger or a scoped view

- **WHEN** the owner activates a minute in the strip (click or keyboard)
- **THEN** if a loaded ledger row falls within that minute, the ledger
  scrolls that row into view
- **AND** otherwise the ledger's window narrows to that exact minute,
  reflected in the URL like every other filter
- **AND** every minute is reachable via keyboard focus with a visible focus
  state
- **AND** hovering or focusing a minute shows its time and per-status counts

#### Scenario: Timeline URL opens an event drawer

- **WHEN** the owner loads `/ingestion?event=<event-id>`
- **THEN** the matching ledger row scrolls into view when present
- **AND** that row opens its drawer
- **AND** closing the drawer removes the `event` query parameter

## ADDED Requirements

### Requirement: Target delivery recovery is legible and actionable

The Timeline SHALL expose the server-derived state of each non-dashboard ingestion-to-domain target delivery and offer recovery only when a durable eligible intent can be queued. It SHALL show waiting, accepted, ambiguous, and terminal failure distinctly, with an accessible explanation and a stable response to repeated actions.

ID: REQ-dashboard-ingestion-dispatch-console-002
Source: heart-and-soul/vision.md Rule 3; design.md Decision 5
Scope: v1-mandatory

#### Scenario: Partial fan-out has no false all-clear

- **WHEN** an event has one accepted target and another waiting, ambiguous, or terminally failed target
- **THEN** the row and drawer SHALL show the non-complete aggregate and per-target states
- **AND** the accepted target SHALL not display a retry action

#### Scenario: Ingested source may still have pending target work

- **WHEN** an `ingested` source row has one unresolved classified target
- **THEN** the Timeline SHALL show its non-complete delivery summary alongside the coarse source status
- **AND** the owner SHALL not have to infer delivery from the `ingested` word alone

#### Scenario: Recovery control follows server authority

- **WHEN** the owner activates recovery for an eligible failed target
- **THEN** the control SHALL promptly show pending feedback and SHALL resolve to the server's queued identity or an actionable refusal
- **AND** a repeat activation SHALL not queue a second intent or hide the first result

#### Scenario: Ambiguous and legacy failures explain their limits

- **WHEN** a target is ambiguous or a legacy failed event lacks durable recovery evidence
- **THEN** the control SHALL be disabled or absent with a concise, keyboard and screen-reader accessible reason
- **AND** the row SHALL remain visibly unresolved rather than presenting a successful replay

#### Scenario: Evidence read fails

- **WHEN** delivery state cannot be loaded while the ledger row remains cached
- **THEN** the drawer SHALL show unavailable recovery evidence and preserve the prior row without a false all-clear


## openspec/changes/define-dashboard-fast-answer-runtime/specs/dashboard-fast-answer/spec.md


unaffected

Before integration, the actual native owner and ROOT must adopt/archive the full current producer/consumer and all original scenarios, or allocate an exact atomic full-body amendment. Each clause remains held; none of the 13 active files is edited by this proposal. See the full 45-change program context and PR4485 amendment controlled by bu-li92cu.



## ADDED Requirements

### Requirement: Side-Effect-Free Dashboard Fast-Answer Admission

The Switchboard SHALL admit direct fast-answer execution only for a dashboard Lane D system-plane
question whose deterministic catalog selection names Concierge and whose complete read plan resolves
to at most three names in the exact `FAST_ANSWER_CONCIERGE_TOOLS_V1` allowlist that are live on
Concierge while its `dashboard_read` module is enabled. The accepted Concierge, module-dashboard-read,
and RFC 0030 contracts SHALL supply the read-only guarantee; `ToolMeta`, discovery, and visibility
metadata SHALL NOT supply effect authority. Structured admission
SHALL classify lane, scope, selected owner, and read plan without executing any terminal tool or
other effect. Every non-eligible, invalid, unsupported, or pre-effect unavailable result SHALL use
the existing Spawner continuation or its existing fail-closed outcome. Domain questions SHALL retain
the accepted `answer_question(scope="domain")` to `route.execute` path.

ID: REQ-dashboard-fast-answer-001
Source: add-dashboard-question-lane § Dashboard Chat-Widget Classification Lanes and Dashboard
Message Intent Lanes; RFC 0030 § Data Flow; design.md Decisions 1 and 5
Scope: proposed-v1

#### Scenario: Eligible system question enters the fast path without a CLI spawn

- **WHEN** a dashboard turn has an immutable message identity, structured admission returns Lane D
  with `scope="system"`, catalog selection names Concierge, both provider phases resolve to exact
  cheap API catalog entries, and every planned tool is allowlisted and live on enabled Concierge
- **THEN** the turn MAY execute the dashboard fast-answer phase
- **AND** the admitted plan SHALL contain at most three read calls
- **AND** its successful path uses no CLI subprocess or Spawner classification session
- **AND** admission itself invokes no tool, route, bug report, dead letter, conversation reply, or
  other terminal action

#### Scenario: Domain question keeps the accepted route.execute spine

- **WHEN** structured admission returns Lane D with `scope="domain"`
- **THEN** the fast-answer phase SHALL NOT invoke a Concierge tool or phrase an answer
- **AND** the question SHALL continue through the existing
  `answer_question(scope="domain")` to `route.execute` contract

#### Scenario: Non-answer lanes never enter direct execution

- **WHEN** admission identifies a statement, action request, bug report, or ambiguous turn
- **THEN** no fast-answer read, phrasing call, reply, write-capable tool, or direct terminal tool SHALL
  execute
- **AND** the turn SHALL preserve its existing Spawner routing, QA, clarification, dead-letter, and
  approval-gated behavior as applicable

#### Scenario: Invalid or unsupported admission falls back before effects

- **WHEN** an exact-cheap API candidate is absent for either phase, direct runtime is unsupported,
  catalog evidence is unavailable, classification fails, or structured output is invalid after its
  existing bounded schema retry
- **THEN** no fast-answer tool or reply SHALL have executed
- **AND** the pipeline SHALL use the existing Spawner classifier or its existing fail-closed outcome
- **AND** catalog unavailability SHALL NOT be converted into no match or `cannot_answer`

#### Scenario: Ineligible read plan is rejected before invocation

- **WHEN** a proposed plan contains a name outside the exact V1 allowlist, an unregistered,
  disabled-module, non-Concierge, direct-handler, or otherwise unauthorized tool, or more than three
  calls
- **THEN** admission SHALL reject the whole plan before any planned tool runs
- **AND** tool visibility, catalog provenance, or a model-produced name SHALL NOT grant authority

#### Scenario: Fallback never restarts work after a read begins

- **WHEN** the first registered Concierge MCP read has begun for an admitted fast answer
- **THEN** failure SHALL NOT restart the whole turn through the CLI classifier, repeat the admitted
  read plan, or choose General
- **AND** completion, failure, cancellation, or ambiguity SHALL settle under the same durable
  fast-answer runtime identity

### Requirement: Durable Cross-Process Fast-Answer Stop

One Switchboard-owned durable `fast_answer` runtime identity SHALL span catalog resolution,
structured classification, registered Concierge reads, answer phrasing, and reply settlement. The
runtime SHALL be registered against the immutable dashboard message before catalog resolution or the
first provider invocation, SHALL claim the existing pre-invoke fence once, and SHALL be addressable
through Switchboard's registered `cancel_session` MCP boundary. It SHALL carry a boot-scoped owner
instance, monotonically fenced lease generation, heartbeat, 60-second lease, nullable immutable
reconciliation anchor, and nullable reconciliation deadline. The owner SHALL heartbeat at least
every 20 seconds, and successful renewal SHALL set lease expiry from the durable database clock.
Registration age SHALL NOT limit healthy execution. On expired-lease takeover, the reconciler SHALL
capture that predecessor generation's last durable lease expiry as immutable `L` and set immutable
`D = L + 15 minutes`. A supervised Switchboard reconciler SHALL scan at startup and at most every 60
seconds. Confirmed cancellation SHALL require every active invoke/reply claim to be released and a
durable cancellation acknowledgement. Unprovable runtime or reply outcomes SHALL become durable
ambiguity with reason `fast_answer_runtime_outcome_unknown` on the first available sweep at or after
`D` and SHALL NOT be replayed.

ID: REQ-dashboard-fast-answer-002
Source: dashboard-conversations § Durable Dashboard Turn Control; dashboard-chat-ui § SSE Client
Integration; durable-dashboard-terminal-action-recovery REQ-dashboard-conversations-004 and
REQ-dashboard-conversations-005; design.md Decisions 2, 3, and 6
Scope: proposed-v1

#### Scenario: Runtime registration precedes catalog and provider work

- **WHEN** a dashboard turn attempts structured fast-answer admission
- **THEN** the Switchboard SHALL create one session UUID, durably register it for the exact message
  as phase `fast_answer`, register its live cancellable task, and claim the existing invoke fence
  before catalog resolution or the first provider call
- **AND** registration or claim observing prior Stop SHALL prevent every provider, MCP read,
  phrasing, and reply call

#### Scenario: Active owner heartbeats a fenced lease

- **WHEN** a fast runtime owns active catalog, provider, MCP-read, or reply work
- **THEN** it SHALL refresh its durable 60-second lease at least every 20 seconds using the same
  `owner_instance_id` and `lease_generation`
- **AND** each accepted heartbeat SHALL set `lease_expires_at = database_now + 60 seconds`
- **AND** every phase advance, invoke release, reply claim/receipt, and terminal transition SHALL
  present that generation or fail closed

#### Scenario: Healthy runtime may renew beyond registration minute 15

- **WHEN** a healthy fast runtime continues to heartbeat before each lease expires through minute 15
  after registration and later
- **THEN** it SHALL remain active and eligible to complete under its separate provider execution
  budget
- **AND** registration age SHALL NOT create a reconciliation anchor, cancel the runtime, authorize a
  takeover, or mark the turn ambiguous
- **AND** the real periodic reconciler entrypoint SHALL leave that current live generation unchanged

#### Scenario: Expired lease starts observation-only reconciliation

- **WHEN** the startup or periodic reconciler finds a fast runtime whose lease expired and whose
  outcome is not terminal
- **THEN** it SHALL first inspect durable runtime, Stop, and deterministic reply-receipt evidence
- **AND** it MAY conditionally claim a new reconciliation generation only while the old lease remains
  expired
- **AND** a successful claim SHALL atomically copy the exact predecessor generation's last durable
  `lease_expires_at` into immutable `reconcile_anchor_expires_at = L` and set immutable
  `reconcile_deadline_at = D = L + 15 minutes`
- **AND** lease expiry or a changed process instance alone SHALL NOT prove that the predecessor died,
  stopped, failed, or completed

#### Scenario: Expiry around a sweep has an explicit scan bound

- **WHEN** a lease expires immediately before a healthy periodic sweep while the durable store is
  available
- **THEN** that sweep MAY claim the expired generation and capture `L`
- **WHEN** a lease expires immediately after a healthy periodic sweep under the same conditions
- **THEN** the next sweep SHALL inspect it no later than `L + 60 seconds`
- **AND** these scan positions SHALL not change `D = L + 15 minutes`

#### Scenario: Reconciliation generation fences a partitioned predecessor

- **WHEN** a reconciler claims the generation after an expired lease while the predecessor process
  is merely partitioned and later regains database access
- **THEN** the predecessor's stale heartbeat, phase, reply, release, and completion writes SHALL be
  rejected
- **AND** the predecessor SHALL cancel its local work rather than persist a late result
- **AND** the reconciler SHALL NOT describe the predecessor as dead or cancelled by inference

#### Scenario: Reconciler settles from receipts or bounded ambiguity

- **WHEN** reconciliation finds a proven reply receipt, cancellation acknowledgement, or
  deterministic failure receipt
- **THEN** it SHALL preserve that receipt and project completed, cancelled, or failed respectively
- **AND** lease expiry, registration age, takeover, or deadline SHALL NOT overwrite that proven
  outcome with ambiguity
- **WHEN** no conclusive receipt exists at the first successful sweep at or after immutable `D`
- **THEN** it SHALL mark the turn `ambiguous` exactly once with reason
  `fast_answer_runtime_outcome_unknown`
- **AND** before the deadline it SHALL retain `pending_reconciliation` or, after Stop,
  `pending_cancellation`
- **AND** it SHALL never invoke a provider, repeat a Concierge read, persist a reply, or replay the
  runtime

#### Scenario: Available supervision gives the true wall-clock bound

- **WHEN** the Switchboard reconciliation supervisor runs and the durable store accepts every needed
  scan/claim/read/write transaction continuously from `L` through `D + 60 seconds`
- **THEN** an unproven outcome SHALL become ambiguous no earlier than `D` and no later than
  `D + 60 seconds`
- **AND** measured from the last accepted heartbeat, the maximum consists of at most 60 seconds of
  remaining lease, 15 minutes from lease expiry, and 60 seconds of scan latency
- **AND** this bound SHALL NOT be treated as a provider or workflow execution timeout

#### Scenario: Repeated sweeps and restarts cannot extend reconciliation

- **WHEN** multiple sweeps or replacement Switchboard reconcilers revisit the same fenced unresolved
  generation
- **THEN** they SHALL reuse the stored `L` and `D` and SHALL NOT recompute either from claim time,
  sweep time, startup time, or the current clock
- **AND** they SHALL produce no duplicate terminal outcome, provider call, read, reply, or replay

#### Scenario: Heartbeat and reconciliation claim have one durable winner

- **WHEN** an owner heartbeat and a reconciliation claim race around the same owner instance,
  generation, and lease expiry
- **THEN** reciprocal conditional writes SHALL select one winner
- **AND** a heartbeat that commits before expiry SHALL extend the lease and make the stale claim fail
- **AND** a reconciliation claim that commits for the expired generation SHALL advance the generation
  and make the predecessor heartbeat and every later predecessor write fail

#### Scenario: Durable-store outage suspends only the timing promise

- **WHEN** the durable store is unavailable around lease expiry or immutable `D`
- **THEN** the system SHALL preserve the last readable pending/unavailable state and SHALL NOT
  fabricate timely completion, confirmed cancellation, failure, or ambiguity
- **AND** after storage recovers, while the supervisor runs and the store remains available, the next
  successful startup/periodic sweep no later than 60 seconds after availability returns SHALL capture
  or reuse the original `L`, inspect receipts first, and apply the already-expired `D` when applicable
- **AND** outage/recovery SHALL NOT move `L` or `D`, renew stale ownership, or authorize replay

#### Scenario: Stop during classification cancels the shared runtime

- **WHEN** message-scoped Stop is persisted while structured classification is active
- **THEN** the dashboard API SHALL address the durable session through Switchboard's registered
  `cancel_session` MCP tool
- **AND** the classification task SHALL unwind without beginning a Concierge read or reply
- **AND** cancellation SHALL not be confirmed until its invoke claim is released and the durable
  acknowledgement is recorded

#### Scenario: Stop during or between reads fences remaining work

- **WHEN** Stop arrives during a Concierge MCP read or after a read settles but before phrasing
- **THEN** cancellation SHALL propagate through the active MCP request when one exists
- **AND** every later read, phrasing call, and reply claim SHALL observe the durable Stop and remain
  unstarted
- **AND** an uncertain read transport outcome SHALL not be called cancelled until it settles or is
  classified as ambiguous

#### Scenario: Stop fences phrasing and reply persistence

- **WHEN** Stop arrives during phrasing or after phrasing completes but before reply persistence wins
  its durable claim
- **THEN** the active phrasing task SHALL be cancelled or the next fence SHALL suppress it
- **AND** no assistant reply SHALL be persisted after Stop wins the reply claim
- **AND** a late provider completion SHALL be unable to cross the lost fence

#### Scenario: Repeated and concurrent Stop are idempotent

- **WHEN** two or more Stop requests address the same fast-answer message before, during, or after
  cancellation settlement
- **THEN** they SHALL converge on one durable cancellation intent and one terminal outcome
- **AND** they SHALL create no second runtime cancellation, reply, route, or replay

#### Scenario: Stop after completed reply is already finished

- **WHEN** the idempotent reply receipt and durable runtime completion won before Stop linearized
- **THEN** Stop SHALL preserve the completed answer and return the canonical already-finished meaning
- **AND** it SHALL NOT rewrite the turn as cancelled

#### Scenario: Crash or transport uncertainty is never replayed

- **WHEN** Switchboard or an MCP transport dies while the fast runtime has an active invoke or reply
  claim and durable evidence cannot prove whether it completed
- **THEN** recovery SHALL preserve any proven receipt or durably classify the turn as ambiguous
- **AND** it SHALL NOT reconstruct prompts, rerun classification, repeat reads, rephrase, or reissue
  reply persistence from process-local absence

#### Scenario: Restart test drives the operational reconciler

- **WHEN** an integration test persists an active fast runtime with a live lease, crashes its owning
  Switchboard process just before registration minute 15, starts a replacement Switchboard, and
  advances an injected database clock through the last lease expiry `L`, both sides of the periodic
  scan boundary, immutable `D`, and `D + 60 seconds`
- **THEN** the replacement's real startup/periodic reconciler entrypoint SHALL claim a new generation
  and persist `ambiguous` with reason `fast_answer_runtime_outcome_unknown`
- **AND** a companion path SHALL heartbeat a healthy runtime beyond registration minute 15 without
  takeover or ambiguity
- **AND** restart/repeated-sweep, both heartbeat/claim winners, store-outage/recovery, and receipt-
  precedence cases SHALL retain the same `L` and `D`
- **AND** the test SHALL observe zero provider, Concierge-read, reply, and replay calls
- **AND** directly calling a transition helper SHALL NOT satisfy this scenario

#### Scenario: Every settled path releases the runtime

- **WHEN** fast execution completes, fails deterministically, or confirms cancellation
- **THEN** it SHALL release every invoke/reply claim, complete the durable runtime outcome, and remove
  the process-local cancellable registration
- **AND** an incomplete cleanup caused by process death SHALL be reconciled under the no-replay crash
  scenario rather than silently stamped complete

### Requirement: Typed Catalog Evidence and Selected-Owner Rule

Catalog-assisted dashboard ownership SHALL return `matched`, `no_match`, or `unavailable` rather
than one nullable tuple. A match SHALL contain at most three ordered, wholly valid candidates after
server-held sensitivity filtering. Each candidate SHALL carry bounded provenance and RRF ranking
evidence. Any malformed envelope or candidate SHALL poison the whole result to `unavailable`; no
record SHALL be discarded to strengthen selection. The selected owner SHALL follow the deterministic
same-owner rule below; raw RRF score SHALL NOT be named, normalized, or interpreted as calibrated
confidence.

ID: REQ-dashboard-fast-answer-003
Source: memory-discovery-catalog § Cross-butler search via catalog and Sensitivity filtering;
cross-butler-delegation § Domain Resolution Via Shared Catalog; design.md Decision 4
Scope: proposed-v1

#### Scenario: Matched outcome carries bounded provenance

- **WHEN** an authoritative held-sensitivity-filtered catalog search requested `limit=3` and returns
  one to three candidates in a wholly valid envelope
- **THEN** `matched` SHALL return every candidate in catalog order without discarding or reordering a
  record
- **AND** each candidate SHALL contain only `catalog_id`, resolved owner, `source_schema`,
  `source_table`, `source_id`, `ranking_method="rrf"`, `rrf_score`, `semantic_rank`, and
  `keyword_rank`
- **AND** canonical memory content and stored sensitivity SHALL not be included

#### Scenario: One candidate selects its valid owner

- **WHEN** `matched` contains exactly one well-formed finite-score candidate whose resolved owner is
  currently eligible
- **THEN** `selected_owner` SHALL equal that candidate's owner
- **AND** its score SHALL remain ranking evidence rather than a confidence percentage

#### Scenario: Multiple candidates require same-owner agreement

- **WHEN** `matched` contains two or three wholly valid candidates
- **THEN** `selected_owner` SHALL equal the first candidate's owner only when the first two candidates
  name the same owner and every candidate tied for highest score names that owner
- **AND** a cross-owner top-two result, cross-owner top-score tie, or well-formed but ineligible owner
  SHALL leave `selected_owner` null
- **AND** numeric score margin SHALL never override owner disagreement

#### Scenario: Any malformed candidate poisons the whole result

- **WHEN** a returned catalog envelope contains more than three candidates or any candidate lacks a
  resolved owner, required provenance, semantic/keyword rank, or finite RRF score, whether malformed
  evidence appears alone, before a valid candidate, or after a valid candidate
- **THEN** the whole outcome SHALL be `unavailable` with a bounded categorical reason
- **AND** it SHALL NOT return `matched`, select an owner, discard the malformed record, promote a
  remaining record, or return `no_match`

#### Scenario: Successful empty differs from unavailable

- **WHEN** the hook, server-held authority source, embedding/search dependencies, and catalog query
  all succeed and the search returns zero candidates
- **THEN** the outcome SHALL be `no_match`
- **WHEN** the hook is absent, held authority cannot be loaded, a dependency/query fails, or returned
  evidence is malformed
- **THEN** the outcome SHALL be `unavailable` with a bounded categorical reason
- **AND** neither outcome SHALL expose raw errors or withheld metadata

#### Scenario: No match declines only after question classification

- **WHEN** catalog outcome is `no_match` and side-effect-free admission later proves Lane D
- **THEN** the existing `cannot_answer` path MAY persist its honest decline and dead-letter evidence
- **AND** zero `invoke_structured` calls SHALL be made for ownership or another target
- **WHEN** the lane is not yet proven to be Lane D
- **THEN** `no_match` SHALL NOT terminate, dead-letter, or reroute the turn

#### Scenario: Selection rule is verified before enablement

- **WHEN** implementation proposes to enable selected-owner pre-resolution
- **THEN** a fixed labeled seeded catalog corpus SHALL record its digest, result limit, rule version,
  selected coverage, wrong-owner count, per-owner breakdown, and confusion matrix
- **AND** any wrong-owner selected hit SHALL block enablement
- **AND** broadening the same-owner rule SHALL require a new approved proposal rather than a threshold
  change hidden in implementation

### Requirement: Target-Owned Registered Concierge Reads

Every fast-answer data access SHALL invoke an already registered Concierge `dashboard_read` handler
through the registered Switchboard-to-Concierge MCP boundary. Executable authority SHALL be the
intersection of Concierge's enabled module state, its live target registration, and the exact
checked-in `FAST_ANSWER_CONCIERGE_TOOLS_V1` set below. The accepted `butler-concierge`,
`module-dashboard-read`, and RFC 0030 contracts SHALL supply the read-only guarantee. `ToolMeta`,
tool discovery, visibility metadata, and naming convention SHALL NOT supply effect authority.
Existing schema validation, module checks, database role, call-time authorization, middleware, RFC
0030 views, and source-envelope rules SHALL remain in force. Switchboard SHALL NOT import or call
Concierge handlers directly, query another schema, or use catalog provenance as canonical read
authority.

`FAST_ANSWER_CONCIERGE_TOOLS_V1` SHALL be a checked-in grant map whose entries all declare
`target="concierge"`, `module="dashboard_read"`, and `effect="read"`. It SHALL contain exactly these
names:

- `dashboard_read_fleet_status`
- `dashboard_read_butler_detail`
- `dashboard_read_sessions_recent`
- `dashboard_read_session_detail`
- `dashboard_read_sessions_aggregate`
- `dashboard_read_sessions_trigger_breakdown`
- `dashboard_read_fleet_errors_recent`
- `dashboard_read_fleet_search`
- `dashboard_read_timeline_recent`
- `dashboard_read_butler_activity`
- `dashboard_read_spend_summary`
- `dashboard_read_spend_daily`
- `dashboard_read_spend_top_sessions`
- `dashboard_read_spend_breakdown_by_butler`
- `dashboard_read_spend_breakdown_by_model`
- `dashboard_read_insight_delivery_state`

Broadening this set SHALL require a new owner-approved amendment. Disabling a module or narrowing the
set for security SHALL remain fail-closed.

ID: REQ-dashboard-fast-answer-004
Source: Non-Negotiable Rule 3; RFC 0030 §§ Exception Scope, Data Flow, and Guardrails;
butler-concierge § Read-Only Tool Surface, System-Plane Scope Boundary, Every Result Carries a Source
Envelope, and Cross-Schema Reads Only Through Sanctioned Views; design.md Decision 5
Scope: proposed-v1

#### Scenario: Registered Concierge read executes through MCP

- **WHEN** an admitted plan names a V1-allowlisted handler that is live on Concierge while its
  `dashboard_read` module is enabled
- **THEN** Switchboard SHALL invoke it through the registered target MCP boundary
- **AND** the target's normal input validation, module-state, schema-role, call-time, transport, and
  middleware checks SHALL run

#### Scenario: Read plan has an exact maximum

- **WHEN** structured admission proposes more than three read calls
- **THEN** the entire plan SHALL be rejected before any read starts
- **AND** an admitted plan SHALL execute each listed call at most once under the shared runtime

#### Scenario: Existing contracts rather than ToolMeta provide read authority

- **WHEN** the fast-answer executor evaluates a proposed Concierge call
- **THEN** it SHALL derive executable authority from the exact V1 set, live target registration, and
  enabled module state
- **AND** it SHALL rely on the accepted Concierge/module/RFC contracts for the read-only guarantee
- **AND** the absence of a `DashboardReadModule.tool_metadata()` effect declaration,
  argument-sensitivity metadata, or presentation metadata SHALL neither reject every allowlisted
  happy path nor grant any additional tool

#### Scenario: Malformed or contradictory V1 grant map fails closed

- **WHEN** the checked-in V1 grant map has a missing, duplicate, malformed, or contradictory
  name/target/module/effect entry
- **THEN** fast admission SHALL be disabled before classification or any read
- **AND** the turn SHALL use the existing Spawner continuation

#### Scenario: Presentation metadata cannot grant a call

- **WHEN** discovery, visibility, catalog, or model output names a tool that is not currently
  registered and allowed by Concierge
- **THEN** the call SHALL be rejected before invocation
- **AND** no generic invoke gateway, direct `.fn()` call, imported handler, or copied schema SHALL be
  used as a fallback

#### Scenario: Missing or contradictory authority rejects the plan

- **WHEN** the owning module is disabled, a planned name is absent from V1 or live target
  registration, or an allowlisted implementation contradicts the accepted read-only module/RFC
  contracts
- **THEN** the whole proposed read plan SHALL be rejected before any planned tool runs
- **AND** no approval policy or write-tool wrapper SHALL be used to make it eligible

#### Scenario: Broadening the live module does not broaden fast authority

- **WHEN** Concierge registers a new `dashboard_read_*` handler that is absent from
  `FAST_ANSWER_CONCIERGE_TOOLS_V1`
- **THEN** the new handler SHALL remain ineligible for fast answers
- **AND** only a new owner-approved amendment may add it to the V1 authority set

#### Scenario: Source attribution is server-derived

- **WHEN** one or more Concierge reads succeed and an answer is phrased
- **THEN** every result SHALL retain its target-produced `source.kind`, `source.ref`, and
  `source.as_of` envelope
- **AND** the persisted reply's source list SHALL be derived by server code from those validated
  envelopes rather than model prose
- **AND** missing or invalid source provenance SHALL prevent a grounded success claim

#### Scenario: Domain data cannot use the system-plane exception

- **WHEN** a question concerns the owner's finances, health, relationships, calendar, or another
  domain butler's canonical data
- **THEN** Concierge and the fast-answer path SHALL decline eligibility regardless of wording or
  catalog similarity
- **AND** the question SHALL remain on its target-owned domain `route.execute` path

### Requirement: Lane-Aware Dashboard Reply Observation

Each immutable dashboard turn SHALL have a monotonic durable `intent_lane` independent of
`target_kind`, with values `answer` and `non_answer` and nullable legacy/unknown state. A validated
answer classification SHALL persist `answer` before its first read or domain-answer dispatch; a
definitive statement, action, or bug classification SHALL persist `non_answer` before dispatch.
The SSE reply observer SHALL use 45 seconds for `answer` and 300 seconds for `non_answer`, unknown,
and legacy/null, measured from the existing observation start. Observation timeout SHALL close only
the SSE stream and SHALL NOT cancel or fail execution, suppress a late reply, or grant replay.

ID: REQ-dashboard-fast-answer-005
Source: dashboard-conversations § SSE Response Streaming and Durable Dashboard Turn Control;
durable-dashboard-terminal-action-recovery REQ-dashboard-conversations-005; design.md Decision 7
Scope: proposed-v1

#### Scenario: Answer lane uses a 45-second observation window

- **WHEN** a safe durable observation shows `intent_lane="answer"`
- **THEN** the SSE reply deadline SHALL be 45 seconds from the original reply-observation start
- **AND** learning the lane later SHALL not restart or extend that deadline

#### Scenario: Non-answer and legacy turns retain 300 seconds

- **WHEN** a safe durable observation shows `intent_lane="non_answer"`, or the lane is null, unknown,
  or from a legacy row
- **THEN** the SSE reply deadline SHALL remain 300 seconds from the original observation start
- **AND** `target_kind` SHALL not be reinterpreted as an intent lane

#### Scenario: Intent lane is immutable once known

- **WHEN** the definitive classification for an immutable dashboard message persists `answer` or
  `non_answer`
- **THEN** later routing, reply, Stop, retry observation, reconnect, or recovery SHALL not change it
- **AND** conflicting persistence SHALL fail closed rather than shorten or extend the observation
  window unpredictably

#### Scenario: Timeout text names the observation

- **WHEN** an answer or non-answer/unknown SSE observer reaches its applicable deadline without a
  persisted in-thread reply
- **THEN** `SESSION_TIMEOUT` SHALL name the durable lane and state that the stream was waiting for an
  in-thread reply
- **AND** it SHALL not claim that the model, provider, runtime, route, or tool timed out or was
  cancelled

#### Scenario: Observation timeout preserves runtime and late reply

- **WHEN** SSE emits its lane-aware timeout and `done`
- **THEN** it SHALL close only that observer, leave the conversation open, and perform no Stop,
  terminal-state, retry, replay, or failure mutation
- **AND** a reply persisted after the stream closes SHALL remain visible through normal history and
  unread refresh
- **AND** message-scoped Stop SHALL remain available while the runtime is still active

### Requirement: Fast-Answer Reply, Model Attribution, and Latency Evidence

An admitted fast answer SHALL use one shared durable runtime, one normal classification provider
call (plus only the existing pre-read schema-invalid retry), bounded registered Concierge MCP reads,
exactly one answer-phrasing provider call, and one deterministic message-derived idempotent reply
claim. Before any provider or read, classification and phrasing SHALL each resolve an exact `cheap`,
catalog-backed `runtime_type="api"` candidate with `allow_tier_fallthrough=false`, distinct purpose
attribution, and its catalog execution timeout. Static fallback, non-cheap fallthrough, and provider
failover SHALL be ineligible for fast execution. Required performance evidence SHALL be the original
hermetic fixed-latency-stub benchmark; live-provider evidence SHALL be optional and separately
authorized.

ID: REQ-dashboard-fast-answer-006
Source: dashboard-conversations § Message Data Model and Conversation Reply Channel;
docs/runtime/model-routing.md § Resolution Flow in the Spawner; about/craft-and-care/performance-
discipline.md; design.md Decisions 6, 8, and 9
Scope: proposed-v1

#### Scenario: Exact cheap catalog models are required before reads

- **WHEN** the fast runtime resolves classification and phrasing candidates
- **THEN** it SHALL request `cheap` with `allow_tier_fallthrough=false` for both phase-specific intents
- **AND** both selections SHALL be catalog-backed API entries with effective tier `cheap`
- **AND** no exact candidate, quota denial, non-API selection, non-cheap fallthrough, or static
  fallback SHALL make the fast path ineligible before any provider or Concierge read
- **AND** the turn SHALL continue through the existing Spawner without treating that continuation as
  fast-answer evidence

#### Scenario: Successful fast answer has bounded calls and one reply

- **WHEN** an eligible fast answer succeeds without a schema retry or provider failure
- **THEN** it SHALL make one structured classification provider call, zero CLI spawns, only the
  zero-to-three registered reads in its admitted plan, one phrasing provider call, and one reply
  attempt
- **AND** exactly one assistant reply SHALL complete under the deterministic reply identity

#### Scenario: Admission schema retry has an exact bound

- **WHEN** the exact-cheap admission candidate returns a schema-invalid result on its first call
- **THEN** it MAY make exactly one retry against the same catalog candidate before any read
- **AND** both attempts SHALL be attributed to `dashboard_fast_answer_classification` under the same
  fast runtime
- **AND** a second invalid result SHALL release the fast runtime and use the existing Spawner

#### Scenario: Provider failure does not fail over inside the fast path

- **WHEN** the admission provider/runtime fails before any read
- **THEN** the fast runtime SHALL record that single failed attempt, release, and use the existing
  Spawner continuation
- **AND** it SHALL NOT invoke another catalog candidate inside the fast path
- **WHEN** the one phrasing call fails after reads begin
- **THEN** it SHALL record an honest fast-runtime failure and SHALL NOT retry, provider-failover,
  restart classification, repeat reads, or persist a fabricated answer

#### Scenario: Reply identity makes repeats idempotent

- **WHEN** the same immutable message and identical reply payload reach reply persistence again
- **THEN** the existing reply receipt SHALL be returned without inserting a second message
- **WHEN** the same reply identity carries changed conversation, text, or sources
- **THEN** persistence SHALL conflict and preserve the original reply

#### Scenario: Uncertain reply is reconciled without replay

- **WHEN** reply transport or process failure leaves its write outcome unknown
- **THEN** the runtime SHALL use durable receipt evidence to preserve a proven reply or mark the turn
  pending reconciliation/ambiguous
- **AND** absence of process-local state SHALL never trigger a second reply attempt

#### Scenario: Provider phases carry distinct model purposes

- **WHEN** the classification and phrasing provider candidates and calls are resolved and recorded
- **THEN** both SHALL record requested tier `cheap`, effective tier `cheap`, resolution source
  `catalog`, runtime type `api`, model, catalog entry, and provider execution timeout
- **AND** classification SHALL record purpose `dashboard_fast_answer_classification`
- **AND** phrasing SHALL record purpose `dashboard_fast_answer_phrasing`
- **AND** both SHALL correlate to the shared runtime and dashboard request without recording prompt,
  tool argument/result, or answer content in attribution telemetry

#### Scenario: SSE observation does not replace provider timeout

- **WHEN** a fast answer has a 45-second SSE observation deadline
- **THEN** each provider call SHALL retain the execution budget resolved from the model catalog
- **AND** neither deadline SHALL be reported or persisted as the other

#### Scenario: Hermetic benchmark preserves the original latency gate

- **WHEN** the slow fast-answer benchmark runs 20 representative system-plane questions against a
  seeded database and a fixed-latency stub adapter
- **THEN** it SHALL record declared stub latency, sample count, per-run wall time, p50, p95, provider
  calls, registered tool calls, and CLI spawns
- **AND** the valid-schema successful corpus SHALL make exactly 40 provider calls total, one
  classification and one phrasing call per question
- **AND** registered tool calls SHALL equal the sum of the admitted plans with zero-to-three per
  question, p95 SHALL be less than 3 seconds, and CLI spawns SHALL be zero
- **AND** the benchmark SHALL remain marked slow, MAY be skipped in normal CI, and SHALL record its
  result in the future implementation PR body
- **AND** the result SHALL be labeled hermetic orchestration evidence, not live-provider latency

#### Scenario: Live latency evidence is optional and separate

- **WHEN** a live-provider benchmark is separately authorized and run
- **THEN** its evidence SHALL separately name date, sample size, runtime/model/catalog/timeout tuple,
  p50, p95, and failures
- **AND** no live run is required by this capability
- **AND** no stub result SHALL be presented as live proof

## Source References

- Non-Negotiable Rule 3 (MCP-only inter-butler communication)
- Non-Negotiable Rule 4 (deterministic infrastructure; reasoning in ephemeral LLM work)
- RFC 0001 (daemon lifecycle and runtime sessions)
- RFC 0002 (MCP tool surface and modules)
- RFC 0003 (Switchboard routing and ingestion)
- RFC 0007 (dashboard and API surface)
- RFC 0030 (system-plane read exception)
- `openspec/specs/dashboard-conversations/spec.md`
- `openspec/specs/dashboard-chat-ui/spec.md`
- `openspec/specs/butler-concierge/spec.md`
- `openspec/specs/memory-discovery-catalog/spec.md`
- `openspec/specs/cross-butler-delegation/spec.md`
- `openspec/changes/add-dashboard-question-lane/`
- `openspec/changes/durable-dashboard-terminal-action-recovery/`

## Dated owner-note and public-source coordination supplement (2026-10-15)

All earlier full source bodies and C1-C3/current/future populations above remain unchanged; their dated counts and public statuses are historical snapshots. This supplement adds exact future obligations under existing D19 requirement IDs, not a new backend capability or an adoption of foreign source.

| Source owner / proposal | Exact D19 boundary and new cases | Retained condition / disposition |
| --- | --- | --- |
| bu-idcqzy Hold design and D19 owner note | mind-overview001: Engage a global Hold; Hold and stop all has two receipts; Hold status persists across owner destinations; Release and optional until retain deferral truth; Unknown or unavailable Hold is never an all-clear; Repeated Hold commands preserve owner audit. mind-overview002: Hold controls and banner are accessible. | Design proceeds now: explicit no design-blocking edge. Service listener accepts only signed dashboard client; no model/session/catalog/grant path. Backend RFC approval/delivery and final D19/M6 gates remain actual prerequisites for runtime, not claimed by a mock. |
| bu-idcqzy plus existing D14 runner owner | goals006: Stop-all retains each goal outcome. goals007: Global control receipts link to exact goals. goals008: Hold and research projections use their owning contracts. | Existing per-goal Stop transaction and terminal-skip/idempotence/committed-unknown-cancelled settlements; engagement and Stop-all are separate receipts. Literal endpoints/DTOs stay with source owner. No current backend or goal state is changed. |
| Current per-butler quarantine Pause; bu-idcqzy excludes regional holds | regions004: Regional Pause has no silent global successor. | Explicit proposed future retirement, current Pause unchanged before approved cutover; Overview global Hold and goal Stop are named separate destinations, never redirected pending mutations. Final D19 owner review remains. |
| bu-6qaju2.4 broker audit, bu-0rbswe read model | goals003: Inspect a recorded research call; Research population and unavailable sources; Stopped and old-epoch research remains historical; Research read preserves payload custody. goals007: Research navigation uses stored lineage. message-rendering001: Research evidence is data and navigation only. | Owner-authenticated, stable paginated question/recorded mode/opened URLs/citations/status/lineage/digest audit. Original digest-only ledger is the bounded display input. No product delivery, research launch, mode choice, grant, restored epoch or telemetry payload is implied. |
| D20 bu-6qaju2.1 / public PR4540 atbd206499915652db201ede910c69b2e6993596e5 | Research future read display is conditional on actual owner-approved source and backend. | Finished-PR verbatim owner approval remains pending. Answer-text retention/reuse, third-party cached ceiling, mode_reason vocabulary, citation handles/live exposed exceptions/pinned probes remain unsettled; D19 selects none. Notify/Drive Docs restrictions remain separate from normalized owner audit navigation. |
| bu-0ynlk.19 / public PR4548 at1a0c3cfdd696983f28452c39b88a4dfbf1b63921 | Proposed Sidebar REQ-dashboard-chat-ui-017 with rename/archive, full predecessor scenarios. | Pending owner adoption; no new canonical authority, backend/UI delivery or D19 source transfer. Existing draft/queued/selection/history guarantees remain governed by their actual sources. |
| bu-0ynlk.18 / public draft PR4549 at0fe6e68416ba341a37deaf92b2458918e9dfe93e | Proposed Global018/RecentRecall019: scoped local recent same-prefix hint, explicit identity/New precedence and retention boundaries. | Pending owner adoption; D19 does not adopt its window/route/query/storage policy, copy source or grant implementation. Thread/goal continuity stays separate from prohibited provider resume. |
| Public PR4513 at245946a74485b6c792de30e276c0aa029b5e6afe | openspec/config.yaml independent context and proposal rules; D19 key-only rules.spec→rules.specs. | Preserve exact disjoint hunk union without importing its schema/workflow source. Existing context/rule values/source references remain byte-identical in D19 except the single key. |

The new semantic population extends existing D19 IDs only. Historical capability-authorization017 references in Hold and D20’s proposed018 belong to those owning amendments; this D19 change allocates neither number and cannot resolve their source custody by copying it. The complete public1024 census and later known4549 source delta are held externally; foreign private WIP was not read. Native/public snapshots identify coordination, not owner adoption or a full current-main semantic union. Exact resulting-source gates/review still bind all original seven AC and (a)-(h).
