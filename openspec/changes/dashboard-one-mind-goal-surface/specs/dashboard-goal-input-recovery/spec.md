## Purpose

This capability specifies the future D19 dashboard goal input recovery owner experience. It preserves the complete source-bound functional contracts below, remains subject to RFC 0038 and owner-controlled M6 integration, and conveys no current runtime or capability authority.

## ADDED Requirements

### Requirement: Goal input Conversation Pydantic Response Models

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
`rejected`), nullable `target_kind` (`goal`, `bug_report`, or `dead_letter`),
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
The future one-assistant input namespace above is a proposed dark interface, not a registered endpoint. Before goal admission, Stop and retry SHALL address the immutable input message; no goal_id is required or invented. Existing butler-route namespace and legacy routed_butler/session/conversation fields below remain historical compatibility evidence only. A modern dispatch receipt SHALL first attest durable accepted input with null goal_id; only a distinct later observation of actual durable goal binding may add the goal_id. It SHALL NOT invent a region, invocation or goal from a phase event, sticky route or message text. Retiring the historical response aliases requires the active recovery/conversation owner's exact model/client union; D19 does not waive its bounded compatibility migration. Every exact-message claim, 60-second recovery fence, 15-minute Stop ambiguity deadline, startup and at-most-60-second reconcile scan, durable precedence rule and immutable owner-resolution overlay in this full contract SHALL survive that namespace migration.

ID: REQ-dashboard-goal-input-recovery-001
Source: openspec/changes/durable-dashboard-terminal-action-recovery/specs/dashboard-conversations/spec.md; RFC 0038 D12/D14/D19; design.md
Scope: post-v1

#### Scenario: ConversationSummary model

- **WHEN** a conversation list response is serialized
- **THEN** each entry SHALL include `id`, `author_region`, `title`, `status`,
  `created_at`, `updated_at`, `message_count`, `goal_id`, and
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
  `dashboard_turn` with `ingress_state: "accepted"`, `target_kind: "goal"`,
  `state: "ambiguous"`, and a sanitized reason code
- **AND** a reload or reconnect SHALL return that same durable ambiguity rather
  than inferring a calm completion

#### Scenario: ConversationSearchResult model

- **WHEN** a search result is serialized
- **THEN** each entry SHALL include the ConversationSummary fields plus
  `snippet`, the matching message-content excerpt


### Requirement: Goal input Exact Message Ingress Recovery API

The canonical owner-only ingress-recovery API SHALL be `POST
/api/dashboard/inputs/{message_id}/retry-ingress`, where
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
The future one-assistant input namespace above is a proposed dark interface, not a registered endpoint. Before goal admission, Stop and retry SHALL address the immutable input message; no goal_id is required or invented. Existing butler-route namespace and legacy routed_butler/session/conversation fields below remain historical compatibility evidence only. A modern dispatch receipt SHALL first attest durable accepted input with null goal_id; only a distinct later observation of actual durable goal binding may add the goal_id. It SHALL NOT invent a region, invocation or goal from a phase event, sticky route or message text. Retiring the historical response aliases requires the active recovery/conversation owner's exact model/client union; D19 does not waive its bounded compatibility migration. Every exact-message claim, 60-second recovery fence, 15-minute Stop ambiguity deadline, startup and at-most-60-second reconcile scan, durable precedence rule and immutable owner-resolution overlay in this full contract SHALL survive that namespace migration.

ID: REQ-dashboard-goal-input-recovery-002
Source: openspec/changes/durable-dashboard-terminal-action-recovery/specs/dashboard-conversations/spec.md; RFC 0038 D12/D14/D19; design.md
Scope: post-v1

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


### Requirement: Goal input Bounded Targetless Ingress Stop Reconciliation

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
The future one-assistant input namespace above is a proposed dark interface, not a registered endpoint. Before goal admission, Stop and retry SHALL address the immutable input message; no goal_id is required or invented. Existing butler-route namespace and legacy routed_butler/session/conversation fields below remain historical compatibility evidence only. A modern dispatch receipt SHALL first attest durable accepted input with null goal_id; only a distinct later observation of actual durable goal binding may add the goal_id. It SHALL NOT invent a region, invocation or goal from a phase event, sticky route or message text. Retiring the historical response aliases requires the active recovery/conversation owner's exact model/client union; D19 does not waive its bounded compatibility migration. Every exact-message claim, 60-second recovery fence, 15-minute Stop ambiguity deadline, startup and at-most-60-second reconcile scan, durable precedence rule and immutable owner-resolution overlay in this full contract SHALL survive that namespace migration.

ID: REQ-dashboard-goal-input-recovery-003
Source: openspec/changes/durable-dashboard-terminal-action-recovery/specs/dashboard-conversations/spec.md; RFC 0038 D12/D14/D19; design.md
Scope: post-v1

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


### Requirement: Goal input Durable Current-Turn Dispatch Receipt

For an SSE request that owns an immutable dashboard `message_id`, the API MAY
emit `dispatch_accepted` only after its ingress is durably accepted: either
`bind_ingress` returned `accepted` for the submitted request or
`claim_ingress` reused an already-accepted request. Before emitting, the API
SHALL take a safe durable `dispatch_status` observation. Its first emitted
receipt SHALL be exactly `{"goal_id": null}`, regardless of whether that
observation is targetless or already has `target_kind: "goal"` with a non-empty
`target_goal_id`. Only a later, distinct safe status observation MAY emit exactly
one goal-id upgrade for that durable goal binding; the first observation SHALL NOT emit
both receipts. The API SHALL NOT derive a receipt target from `triage_decision`,
`triage_target`, a historical
`conversation.historical_route`, or another message.
Legacy streams without an immutable `message_id` SHALL emit no dispatch receipt.
The API SHALL also emit no receipt when the status observation is unavailable or
unsafe, when the turn is `cancelling`, cancelled, or ambiguous, or when its
target is a terminal action rather than a route. Those terminal/cancellation
facts retain their existing SSE error/outcome semantics; a dispatch receipt is
not a terminal-action receipt or confirmation of an assistant reply.
RFC 0007 Dashboard Conversation Endpoints
The future one-assistant input namespace above is a proposed dark interface, not a registered endpoint. Before goal admission, Stop and retry SHALL address the immutable input message; no goal_id is required or invented. Existing butler-route namespace and legacy routed_butler/session/conversation fields below remain historical compatibility evidence only. A modern dispatch receipt SHALL first attest durable accepted input with null goal_id; only a distinct later observation of actual durable goal binding may add the goal_id. It SHALL NOT invent a region, invocation or goal from a phase event, sticky route or message text. Retiring the historical response aliases requires the active recovery/conversation owner's exact model/client union; D19 does not waive its bounded compatibility migration. Every exact-message claim, 60-second recovery fence, 15-minute Stop ambiguity deadline, startup and at-most-60-second reconcile scan, durable precedence rule and immutable owner-resolution overlay in this full contract SHALL survive that namespace migration.

ID: REQ-dashboard-goal-input-recovery-004
Source: openspec/changes/durable-dashboard-terminal-action-recovery/specs/dashboard-conversations/spec.md; RFC 0038 D12/D14/D19; design.md
Scope: post-v1

#### Scenario: Safe accepted ingress yields a targetless receipt

- **WHEN** immutable ingress has bound as `accepted` and `dispatch_status`
  safely observes an active turn with no target kind
- **THEN** the stream SHALL emit one `dispatch_accepted` event with
  `goal_id: null`
- **AND** a pre-routing `triage_target` or historical conversation route SHALL
  not change that targetless receipt

#### Scenario: Existing durable route begins targetless then upgrades

- **WHEN** immutable ingress has been accepted and `dispatch_status` safely
  observes `target_kind: "goal"` with a non-empty `target_goal_id`
- **THEN** the first `dispatch_accepted` event SHALL be
  `{"goal_id": null}`
- **AND** only a later safe `dispatch_status` observation MAY emit one named
  event naming that exact durably bound goal
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


### Requirement: Goal input authoritative reply publication

An admitted goal's authoritative result publisher SHALL persist its one-assistant confirm, proposed-action, answer or safe failure into the server-bound immutable input's durable conversation. A completed terminal-action worker may publish its filed-case acknowledgement only after the corresponding durable QA receipt is complete. Publication SHALL use the actual D7/D13 recipient/effect grant and canonical call lineage; a model-supplied thread, actor, owner, recipient or approval field confers no authority. This is a kernel/result-publication boundary, not an ambient `conversation_reply` MCP registration on every region or lead.
The result publisher SHALL preserve message identity, exact content, canonical citations, server authorship, updated_at/count behavior and the stable terminal child-effect key. The poller watches that persisted message, never raw invocation completion. Normal owner reply publication is available for every actually admitted eligible goal, independent of legacy core_groups; unavailable grant or publisher refuses before unauthorized insertion/communication. Ordinary direct public owner service calls remain their distinct D7 control-plane authority and are not granted to a model.
The old conversation_reply registrations remain normative in the unchanged legacy source until the active conversation/recovery owner allocates their exact M6 retirement. Availability to eligible goals replaces ambient unit registration; it does not grant arbitrary recipients, historical thread takeover or model-facing owner decisions. Complete canonical citation normalization in dashboard-goal-message-rendering is required before insertion, including content-blind errors, legacy unlinked strings and no-trust inference.

ID: REQ-dashboard-goal-input-recovery-005
Source: durable-dashboard-terminal-action-recovery Conversation Reply Channel; RFC 0038 D7/D11/D13/D19; design.md
Scope: post-v1

#### Scenario: Conversation reply persists a normal confirm-loop message

- **WHEN** an admitted eligible goal publishes its normal result into its server-bound existing conversation without a terminal child-effect key
- **THEN** the publisher inserts one assistant public.dashboard_messages row with the exact result content, increments that conversation's message_count, refreshes updated_at and returns status ok with the actual message_id/conversation_id.

#### Scenario: Terminal action reply is retried

- **WHEN** the authorized terminal publisher presents the same conversation, exact content and stable child-effect key after a crash/retry
- **THEN** it returns the existing assistant message and actual IDs without a second insert or count increment.

#### Scenario: Terminal action reply rejects a changed payload for the same key

- **WHEN** an existing terminal child-effect key is presented with changed conversation or content
- **THEN** the publisher returns a structured conflict without insertion and preserves the original persisted text and receipt.

#### Scenario: Conversation reply rejects an unknown conversation

- **WHEN** authoritative publication cannot find its exact bound conversation
- **THEN** it inserts no message and returns a structured safe error rather than raising, claiming delivery or rerouting into a different thread.

#### Scenario: Conversation reply is available to every butler

- **WHEN** an actually admitted eligible goal needs an owner reply, regardless of its participating regions' legacy core-group registration
- **THEN** the server result publisher is available under that goal's actual recipient/effect grant, while no ambient model tool or arbitrary-thread grant is registered.

#### Scenario: conversation_reply persists the confirm-loop message

- **WHEN** the authoritative result publisher receives an eligible normal confirm for its existing server-bound conversation
- **THEN** it persists the exact assistant content, increments message_count and updates updated_at once, with the actual status ok/message_id/conversation_id result.

#### Scenario: conversation_reply rejects an unknown conversation_id

- **WHEN** the server-bound conversation identity is missing or invalid
- **THEN** no row is inserted and a content-blind structured error is returned; a model cannot correct the failure by selecting another owner's or historical conversation.

#### Scenario: conversation_reply is available to every butler

- **WHEN** a goal uses any admitted region and an owner reply is required
- **THEN** availability is determined by the actual admitted goal/publication grant, independent of legacy core_groups, without a classification/pinned-target unit ownership model.

#### Scenario: conversation_reply accepts an optional sources list for an answer-lane reply

- **WHEN** an authorized result includes non-empty consulted sources
- **THEN** the publisher applies the complete server-held canonical citation contract before insertion, retaining safe labels and persisted sources/citations; legacy strings remain unlinked and the safe success shape contains actual IDs only.

#### Scenario: conversation_reply is unaffected when sources is omitted

- **WHEN** an eligible confirm, proposal, bug acknowledgement, refusal or error result omits sources
- **THEN** persisted sources and citations are null, and no grounded/verified label or altered normal publication behavior is invented.

#### Scenario: conversation_reply rejects empty or blank source names

- **WHEN** supplied sources are explicitly empty, blank, over item/total budget or leave no valid citation
- **THEN** no message is inserted and the publisher returns a content-blind structured error requesting usable consulted sources or their honest omission, without echoing labels, targets or private text.
### Requirement: Goal input Dashboard Turn Cancellation API

The canonical owner Stop API SHALL be `POST
/api/dashboard/inputs/{message_id}/cancel`, where `message_id`
identifies the immutable dashboard user turn. Before the client detaches its SSE
watch, the server SHALL persist and linearize the Stop against the exact durable
turn and, when present, its terminal action. A semantic cancellation response
SHALL include `message_id`, nullable `conversation_id`, `goal_id` and `invocation_id`, and a
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
The future one-assistant input namespace above is a proposed dark interface, not a registered endpoint. Before goal admission, Stop and retry SHALL address the immutable input message; no goal_id is required or invented. Existing butler-route namespace and legacy routed_butler/session/conversation fields below remain historical compatibility evidence only. A modern dispatch receipt SHALL first attest durable accepted input with null goal_id; only a distinct later observation of actual durable goal binding may add the goal_id. It SHALL NOT invent a region, invocation or goal from a phase event, sticky route or message text. Retiring the historical response aliases requires the active recovery/conversation owner's exact model/client union; D19 does not waive its bounded compatibility migration. Every exact-message claim, 60-second recovery fence, 15-minute Stop ambiguity deadline, startup and at-most-60-second reconcile scan, durable precedence rule and immutable owner-resolution overlay in this full contract SHALL survive that namespace migration.

ID: REQ-dashboard-goal-input-recovery-006
Source: openspec/changes/durable-dashboard-terminal-action-recovery/specs/dashboard-conversations/spec.md; RFC 0038 D12/D14/D19; design.md
Scope: post-v1

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


### Requirement: Goal input Dashboard Turn Outcome Projection

The conversation message read model SHALL project the durable dashboard-turn
state independently of `terminal_action`, from targetless ingress through a
claimed goal admission or terminal action, so pending, retryable, rejected, and ambiguous
outcomes survive the request/SSE lifecycle. A route-only `ambiguous` turn SHALL
be owner-visible, but it SHALL not expose a manual relay retry or claim that a
domain butler received the statement.
The future one-assistant input namespace above is a proposed dark interface, not a registered endpoint. Before goal admission, Stop and retry SHALL address the immutable input message; no goal_id is required or invented. Existing butler-route namespace and legacy routed_butler/session/conversation fields below remain historical compatibility evidence only. A modern dispatch receipt SHALL first attest durable accepted input with null goal_id; only a distinct later observation of actual durable goal binding may add the goal_id. It SHALL NOT invent a region, invocation or goal from a phase event, sticky route or message text. Retiring the historical response aliases requires the active recovery/conversation owner's exact model/client union; D19 does not waive its bounded compatibility migration. Every exact-message claim, 60-second recovery fence, 15-minute Stop ambiguity deadline, startup and at-most-60-second reconcile scan, durable precedence rule and immutable owner-resolution overlay in this full contract SHALL survive that namespace migration.

ID: REQ-dashboard-goal-input-recovery-007
Source: openspec/changes/durable-dashboard-terminal-action-recovery/specs/dashboard-conversations/spec.md; RFC 0038 D12/D14/D19; design.md
Scope: post-v1

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

## Source References

- Non-Negotiable Rules 1, 3, 4 and 6 in about/heart-and-soul/vision.md.
- RFC 0038 D19 and owner decision 11; further exact decisions and current source boundaries remain the per-requirement Source fields.
- Requirement-level source: openspec/changes/durable-dashboard-terminal-action-recovery/specs/dashboard-conversations/spec.md; RFC 0038 D12/D14/D19; design.md
- Requirement-level source: durable-dashboard-terminal-action-recovery Conversation Reply Channel; RFC 0038 D7/D11/D13/D19; design.md
