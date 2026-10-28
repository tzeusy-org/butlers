## MODIFIED Requirements

### Requirement: Gate Wrapper Interception

The module MUST wrap configured MCP tools at FastMCP registration time so that gated tool calls are serialized into pending actions before the original handler executes. Unknown configured gated tools MUST be skipped during wrapping with warning logs. A pending action created for a goal invocation's call, whether parked by this wrapper or by the control-plane broker through this module, SHALL record the goal lineage of the call's verified broker assertion and never lineage supplied in tool arguments. A park-only effect SHALL never be approved by a standing rule and SHALL always wait for the owner's per-event approval tap. For other parked effects, this module SHALL match standing rules at park time within the exposure limits below. For a non-park-only call whose assertion is exposed, a standing rule SHALL auto-approve only a `communicate` call whose rule pins the recipient to the owner or is template-pinned (`REQ-capability-authorization-006`: every content-bearing argument fixed to an exact value or to an owner-authored template with only typed slots); every other approval-gated call from an exposed invocation SHALL be parked for per-event owner approval. A call to a tool whose catalog entry declares the approval mode `always_per_event` (`REQ-capability-authorization-017`) SHALL never match a standing rule, whether it arrives through a goal invocation's assertion or names no goal, and SHALL NOT take the owner-directed auto-approve path; it SHALL always be parked for the owner's per-event approval.

ID: REQ-module-approvals-005
Source: RFC 0038 D4, D6, D7 and D13; RFC 0021; bu-p5umi2.4 = A (always-per-event approval mode)
Scope: v1-mandatory

#### Scenario: Gated tool is called with no matching standing rule

- **WHEN** a gated tool is invoked through a verified broker assertion
- **THEN** the wrapper serializes the call into a PendingAction with status `pending` and a computed `expires_at`
- **AND** a structured `{"status":"pending_approval","action_id":"...","message":"...","risk_tier":"..."}` response is returned to the caller
- **AND** an `action_queued` audit event is recorded with path `pending`
- **AND** the pending action records the assertion's goal, invocation, depth and exposure, ignoring any lineage-shaped tool arguments

#### Scenario: Gated tool is called with a matching standing rule

- **WHEN** a gated non-park-only call is invoked without an exposed assertion and a standing rule matches the tool name and arguments
- **THEN** the action is persisted with status `approved` and `approval_rule_id` set to the matching rule
- **AND** for a call that names no goal, the original tool function is executed immediately via the shared executor
- **AND** for a goal invocation's call, the broker parks the call through this module, this module matches the rule at park time and approves the action, and the broker then forwards the stored call with an `approval_ref` naming that action after its per-call check that the goal is running (RFC 0038 D13); the wrapper executes the handler only for that forwarded call, never directly on its own rule match
- **AND** `action_queued` and `action_auto_approved` audit events are recorded
- **AND** the rule's `use_count` is incremented

#### Scenario: A matching standing rule cannot approve a park-only effect

- **WHEN** the broker parks an effect under a park-only grant and a standing rule matches, including an owner-addressed or template-pinned rule
- **THEN** the action remains `pending` for the owner's per-event approval tap and the original handler does not run
- **AND** no rule is recorded as its approval and the rule's `use_count` is unchanged

#### Scenario: Unknown gated tool configured

- **WHEN** a tool name in `gated_tools` config is not found in registered MCP tools
- **THEN** the gate wrapper skips wrapping for that tool with a warning log
- **AND** remaining valid gated tools are still wrapped

#### Scenario: An exposed call is not auto-approved by an unpinned rule

- **WHEN** an exposed invocation calls a gated `communicate` tool for a non-owner recipient
- **AND** the only matching standing rule neither pins the recipient to the owner nor is template-pinned (`REQ-capability-authorization-006`)
- **THEN** the action is parked as `pending` for per-event owner approval
- **AND** the rule's `use_count` is unchanged

#### Scenario: An exposed administer call ignores standing rules

- **WHEN** an exposed invocation calls a gated `administer` tool and a standing rule matches it
- **THEN** the action is parked as `pending` for per-event owner approval
- **AND** the original handler does not run

#### Scenario: An always-per-event goal call ignores a matching standing rule

- **WHEN** an unexposed invocation's verified assertion calls a gated tool that declares `always_per_event` and a standing rule matches the tool name and arguments
- **THEN** the action remains `pending` for the owner's per-event approval and the original handler does not run
- **AND** no rule is recorded as its approval and the rule's `use_count` is unchanged

#### Scenario: An always-per-event call that names no goal still parks

- **WHEN** a deterministic caller that names no goal calls a gated tool that declares `always_per_event`, whether a standing rule matches or its target resolves to the owner
- **THEN** the action is persisted as `pending` for the owner's per-event approval
- **AND** neither the standing-rule path nor the owner-directed auto-approve path executes the handler

### Requirement: Pending Actions Queue
The `pending_actions` table MUST provide a durable queue and audit log for approval-gated tool invocations. It stores `id`, `tool_name`, `tool_args` (JSONB), `status`, `requested_at`, and optional fields `agent_summary`, `session_id`, `expires_at`, `decided_by`, `decided_at`, `execution_result`, `approval_rule_id`, `why`, `evidence`, and producer-opt-in `deduplication_key`. An action requested by a goal invocation SHALL also record its goal lineage: the goal, goal origin, invocation, depth, exposure and effect reference of the verified broker assertion that requested it. An action created before the cutover SHALL record goal origin `legacy` and no goal, and its historic `session_id` stays readable; an action from a non-goal producer names no goal. Goal lineage SHALL be server-derived, SHALL NOT be settable from tool arguments, and is distinct from the producer `origin` value (`prepared`). When durable admission is enabled under the canonical additive rollout contract, every newly admitted `pending` row SHALL commit in the same transaction as one unique, schema-local approval delivery-intent root whose immutable logical action key and admission classification are safe for end-to-end notification recovery. The first three ordinary actions receive direct presentations; the fourth `cohort_anchor` joins and creates the cohort-owned digest; later ordinary `collapsed` actions record a terminal non-sendable local presentation and join that cohort. A digest-only `origin='prepared'` action uses the same durable representation with one standalone terminal `collapsed` action presentation, no cohort membership, no burst-count effect, and no provider or defer activation.

ID: REQ-module-approvals-001
Source: RFC-0021,RFC-0023; RFC 0038 D7 and D13
Scope: v1-mandatory

#### Scenario: Pending action rationale fields

- **WHEN** the `pending_actions` table is migrated or created fresh
- **THEN** nullable column `why TEXT` is available for human-readable rationale
- **AND** non-null column `evidence JSONB DEFAULT '[]'::jsonb` is available for cited evidence
- **AND** legacy rows without rationale data remain readable with `why = NULL` and `evidence = '[]'::jsonb`

#### Scenario: Semantic key serializes active equivalent actions

- **WHEN** concurrent producers park actions with the same non-null
  `deduplication_key`
- **THEN** a partial unique constraint MUST allow at most one row with that
  key in `pending`, `approved`, `rejected`, or `abandoned` status
- **AND** null historic keys MUST remain allowed, while an `expired` action
  MUST not block a newly surfaced action with the same key

#### Scenario: Atomic pending admission creates one intent
- **WHEN** a producer admits a new action with status `pending` and durable admission enabled
- **THEN** the pending row and one foreign-keyed intent with the action's immutable logical key plus its required direct presentation, cohort anchor/digest, or collapsed terminal presentation and membership commit together or both roll back
- **AND** an unavailable notification runtime does not permit a pending row without its intent

#### Scenario: Prepared action admission stays durable and silent
- **WHEN** a relationship or travel producer admits a digest-only action with `origin='prepared'`
- **THEN** the pending row, intent, and standalone terminal `collapsed` presentation commit atomically when admission is enabled
- **AND** it creates no cohort membership, due work, legacy emission, provider call, or defer successor
- **AND** default-off admission commits only the established prepared pending row

#### Scenario: List pending actions with status filter
- **WHEN** `list_pending_actions` is called with an optional status filter and limit
- **THEN** matching rows are returned ordered by `requested_at DESC`
- **AND** an invalid status value returns an error dict

#### Scenario: Show pending action detail
- **WHEN** `show_pending_action` is called with an action_id
- **THEN** the full PendingAction row is returned as a serialized dict
- **AND** its safe delivery projection is included without unsafe recovery detail
- **AND** an invalid UUID or missing action returns an error dict

#### Scenario: Count pending actions by status

- **WHEN** `pending_action_count` is called
- **THEN** a dict with `total` and `by_status` counts is returned
- **AND** delivery backlog metrics remain a separate safe aggregation rather than action payload data

#### Scenario: A parked goal effect carries its lineage
- **WHEN** a call from a goal invocation is parked
- **THEN** the pending row records the goal, goal origin, invocation, depth, exposure and effect reference of the call's verified assertion
- **AND** tool arguments cannot set or override any of them

#### Scenario: Pre-cutover actions are recorded as legacy
- **WHEN** the cutover release starts with `pending_actions` rows created before it
- **THEN** each of those rows carries goal origin `legacy` and no goal
- **AND** each remains executable by the approvals executor under the existing owning-daemon dispatch path

### Requirement: Status Transition Contract

The approval lifecycle MUST allow `pending -> approved|rejected|expired|cancelled`,
`approved -> executed|abandoned`, and no transition from
`rejected|expired|executed|abandoned|cancelled`. Invalid transitions raise
`InvalidTransitionError`. `pending -> cancelled` SHALL occur only for an action that names a goal, and only when the owner stops that goal or when a rollback of the cutover release stops every goal in flight (RFC 0038 "Rollback"); the action SHALL record which of the two cancelled it. No model, worker or other path SHALL cancel an action. Every transition out of `pending` SHALL atomically fence or cancel its nonterminal approval-delivery presentations without granting the notification worker domain-action mutation authority. Each authenticated dashboard defer remains a pending-state operation and appends exactly one bounded successor presentation generation through its own shared transaction path.

ID: REQ-module-approvals-002
Source: RFC-0021,RFC-0023; RFC 0038 D11, D13 and D14
Scope: v1-mandatory

#### Scenario: Approve a pending action

- **WHEN** `approve_action` is called with a valid action_id and authenticated
  human actor context
- **THEN** a compare-and-set UPDATE transitions status from `pending` to
  `approved`
- **AND** an `action_approved` audit event is recorded
- **AND** an available owning executor MAY then run the original tool function
- **AND** status advances to `executed` only after that execution persists its
  result and success audit event.
- **AND** the same transaction atomically cancels its sendable delivery presentations

#### Scenario: Approve with concurrent race
- **WHEN** two concurrent approve calls target the same pending action
- **THEN** the compare-and-set ensures only one succeeds with `WHERE status = 'pending'`
- **AND** the losing call receives a transition error with the current status and cannot revive a cancelled presentation

#### Scenario: Dashboard abandons a stalled approved action

- **WHEN** a dashboard actor supplies a non-blank reason for an action whose
  status is `approved` and whose `execution_result` is null
- **THEN** a compare-and-set update transitions that action to `abandoned`
- **AND** an immutable `action_abandoned` event stores the actor and reason in
  the same transaction
- **AND** no MCP, Telegram callback, automatic, bulk, or scheduled path can
  invoke abandonment
- **AND** an action outside that exact predicate remains unchanged.

#### Scenario: Reject a pending action

- **WHEN** `reject_action` is called with a valid action_id and authenticated
  human actor
- **THEN** status transitions from `pending` to `rejected` with `decided_by`
  set to `human:<actor_id> (reason: <escaped_reason>)`
- **AND** an `action_rejected` audit event is recorded.
- **AND** the same transaction atomically cancels delivery recovery; a started handoff may only append late evidence and never changes the rejected action

#### Scenario: Expire stale actions

- **WHEN** `expire_stale_actions` is called
- **THEN** all pending actions where `expires_at < now()` are transitioned to
  `expired`
- **AND** an `action_expired` audit event is recorded for each.
- **AND** the same transaction cancels each action’s sendable presentations and marks its cohort membership ineligible without cancelling a shared eligible digest
- **AND** a worker that merely observes an expired action cannot perform the domain expiry transition itself

#### Scenario: Send-start and decision race
- **WHEN** a worker and a terminal decision contend for the same pending action
- **THEN** the committed fenced handoff-start marker is the final cancellation-safe boundary under a fixed action-then-intent-presentation lock order
- **AND** whichever transaction loses cannot send or revive future recovery, while a decision after send-start stops only future recovery

#### Scenario: Dashboard defer schedules a guarded re-presentation
- **WHEN** an authenticated dashboard actor defers a still-pending action for `1 ≤ hours ≤ 168`
- **THEN** the same action/intent transaction extends expiry, supersedes an unstarted current presentation, and appends the next presentation generation for `now + hours`
- **AND** it keeps the logical action key, cannot route through generic notifications, and does not grant the worker ability to defer or schedule future presentations

#### Scenario: Already-executed action is replayed

- **WHEN** the executor is called for an action that is already `executed`
- **THEN** the stored `execution_result` is returned (idempotent replay)
- **AND** no second execution occurs.
- **AND** no second notification delivery occurs

#### Scenario: Abandon an approved unexecuted action

- **WHEN** an authenticated dashboard actor requests abandonment with a
  non-blank reason for an action whose status is `approved` and execution
  result is null
- **THEN** a compare-and-set UPDATE transitions it to `abandoned`
- **AND** an immutable `action_abandoned` event records the actor and exact
  reason in the same transaction
- **AND** the action cannot subsequently execute or return to an eligible
  recovery state.

#### Scenario: Invalid abandonment source state is rejected

- **WHEN** abandonment targets an action that is pending, rejected, expired,
  executed, abandoned, or has a non-null execution result
- **THEN** no action state or event is written
- **AND** the caller receives a transition error describing the durable current
  state.

#### Scenario: Retry and abandonment race

- **WHEN** retry dispatch and abandonment concurrently target the same approved
  action with a null execution result
- **THEN** the executor acquires a database row lock before any handler is
  invoked, and abandonment's compare-and-set waits for that lock
- **AND** only the winning terminal outcome is durably recorded
- **AND** the loser returns the current durable state without appending another
  terminal event.

#### Scenario: Goal Stop cancels its pending actions
- **WHEN** the owner stops a goal that has pending actions
- **THEN** each of those actions transitions from `pending` to `cancelled`
- **AND** an `action_cancelled` audit event is recorded for each
- **AND** the same transaction cancels each action's sendable delivery presentations

#### Scenario: Rollback cancels broker-parked actions
- **WHEN** the cutover release is rolled back while goal-naming actions are pending
- **THEN** each of those actions transitions from `pending` to `cancelled` with the rollback recorded as the reason
- **AND** an `action_cancelled` audit event is recorded for each and the owner sees them reported
- **AND** pending actions that name no goal are untouched

#### Scenario: A cancelled action cannot be approved
- **WHEN** `approve_action` targets a `cancelled` action
- **THEN** the caller receives a transition error with the current status
- **AND** nothing executes

### Requirement: Standing Rules CRUD and Matching
Standing approval rules MUST enable auto-approval of repeatable safe invocations. Each rule has `id`, `tool_name`, `arg_constraints` (JSONB), `description`, `created_at`, `active`, and optional `created_from`, `expires_at`, `max_uses`, `use_count`. A rule's `created_from` is historical action provenance rather than a deletion-blocking foreign key, so action retention does not mutate or delete a retained rule. No rule SHALL be created for a tool whose catalog entry declares the approval mode `always_per_event` (`REQ-capability-authorization-017`), and rule matching SHALL never consider such a tool.
Standing-rule creation and promotion SHALL require RFC 0038 D13's dashboard
step-up using the authenticated owner session and an independent passkey factor
on a different device from the Telegram phone. Ordinary approve/reject/edit/
dispatch SHALL remain one tap; this requirement SHALL NOT extend step-up to all
owner-only actions. A nonce SHALL bind owner/session, action kind/identity,
exact digest including recipients/arguments, current action/policy version and
expiry. Verification of those bindings, revocation and unconsumed state,
nonce consumption, the authorized transition and audit receipt SHALL commit
atomically. Missing/invalid/expired/revoked/wrong-owner/wrong-session/stale or
changed-digest confirmation SHALL refuse without mutation; rollback SHALL
consume nothing. Concurrent decisions SHALL have one winner, and retry of the
same completed action SHALL read back its outcome without a second rule or
effect. A Telegram request without confirmation SHALL offer the dashboard
without creating/promoting a rule. No callback token or presentation ACK SHALL
substitute for step-up or revive stopped/revoked authority.
Rule matching SHALL enforce RFC 0038 D4's exact slot supplier and sensitive
category boundaries: owner/device/code, or the rule's own recipient only for
the exact slot explicitly permitted on its creation card; no arbitrary third
party or unknown supplier. Non-owner communication after sensitive reads SHALL
name every applicable invocation category (`health`, `finance`, `location`,
`routine`, `relationship_notes`, `credentials`); unknown labels retain S1's
all-category restriction. Missing supplier/category evidence SHALL make the
rule ineligible and park only an otherwise-permitted call; independently
refused calls SHALL stay refused. These conditions SHALL NOT bypass exposure,
template pins, current grants, selections, park-only, always-per-event or
atomic bounded-use claims. A goal rule match SHALL still follow central PARK
and owning approval, and SHALL NOT execute the handler before the broker's
stored-call forwarding fence.


ID: REQ-module-approvals-003
Source: RFC-0021; bu-s11n0s.17 bounded standing-rule uses; RFC 0038 D4/D13; bu-p5umi2.4 = A (always-per-event approval mode); bu-k3wfma; bu-o3638f; bu-dhloi1.18
Scope: v1-mandatory

#### Scenario: Create a standing rule

- **WHEN** `create_approval_rule` is called with authenticated human actor, tool name, constraints, and description
- **AND** the owner confirms that exact current rule through the action-bound dashboard step-up
- **THEN** a new active rule is persisted
- **AND** a `rule_created` audit event is recorded
- **AND** high-risk tools require at least one `exact` or `pattern` constraint and bounded scope

#### Scenario: Create rule from pending action

- **WHEN** `create_rule_from_action` is called with an action_id
- **AND** the owner confirms the resulting exact current rule through the action-bound dashboard step-up
- **THEN** sensitivity classification generates suggested constraints (sensitive args get `exact`, others get `any`)
- **AND** optional `constraint_overrides` are merged on top
- **AND** the rule is persisted with `created_from` linking to the source action

#### Scenario: Rule matching precedence

- **WHEN** multiple standing rules match a tool invocation
- **THEN** the most specific rule wins using deterministic precedence: (1) higher constraint specificity, (2) bounded scope before unbounded, (3) newer rule before older, (4) lexical rule ID tie-breaker

#### Scenario: Rule constraint evaluation

- **WHEN** a rule's `arg_constraints` are checked against tool args
- **THEN** typed constraints (`exact`, `pattern`, `any`) are evaluated
- **AND** legacy formats (`"*"` wildcard, plain exact values) remain supported
- **AND** empty constraints `{}` match any invocation of the tool at argument matching only, without satisfying independent supplier/category, currentness, exposure or approval eligibility checks
- **AND** `pattern` type uses fnmatch-style glob matching

#### Scenario: Rule eligibility filtering

- **WHEN** rules are fetched for matching
- **THEN** only active rules are considered
- **AND** expired rules (`expires_at < now`) are excluded
- **AND** exhausted rules (`use_count >= max_uses`) are excluded

#### Scenario: Concurrent use of a bounded rule

- **WHEN** several gated invocations race for a rule that has fewer remaining uses than callers (`max_uses` minus `use_count`)
- **THEN** each use is claimed atomically with the insert of its `approved` action, so exactly the remaining uses are admitted
- **AND** the remaining invocations are parked as `pending` actions
- **AND** `use_count` never exceeds `max_uses`
- **AND** an inactive, expired, or exhausted rule claims nothing and parks the invocation

#### Scenario: A rule use is consumed at admission

- **WHEN** a rule-matched invocation is admitted
- **THEN** the rule's `use_count` is incremented by the admission claim, before the tool executes
- **AND** the executor does not increment `use_count` again
- **AND** a failed execution leaves the use consumed, because an approved action was issued
- **AND** the email recipient guard claims its rule use through the same helper and parks when the rule cannot be claimed

#### Scenario: Dashboard rule creation names its butler and is validated like the MCP tool

- **WHEN** `POST /api/approvals/rules` is called
- **AND** the authenticated owner supplies valid action-bound dashboard step-up for the exact current rule
- **THEN** `butler` is required (missing returns 422), an unknown butler returns 404, and a butler whose approvals pool is unavailable returns 503
- **AND** the rule is written into that butler's schema
- **AND** the same risk-tier validation as `create_approval_rule` runs first, so a high-risk tool without an `exact` or `pattern` constraint, or without `expires_at` or `max_uses`, returns 400 with the same message

#### Scenario: Revoke a standing rule

- **WHEN** `revoke_approval_rule` is called with authenticated human actor
- **THEN** the rule's `active` flag is set to `false`
- **AND** a `rule_revoked` audit event is recorded
- **AND** already-revoked rules return an error

#### Scenario: Suggest rule constraints

- **WHEN** `suggest_rule_constraints` is called for a pending action
- **THEN** the sensitivity classifier returns per-arg constraints without creating a rule
- **AND** sensitive args (heuristic or module-declared) suggest `exact` pinned to current value
- **AND** non-sensitive args suggest `any`

#### Scenario: A rule cannot target an always-per-event tool

- **WHEN** `create_approval_rule`, `create_rule_from_action` or `POST /api/approvals/rules` names a tool that declares `always_per_event`
- **THEN** the request is refused with a typed reason before any rule is persisted
- **AND** no `rule_created` audit event is recorded

#### Scenario: Missing or invalid step-up does not create a rule
- **WHEN** creation/promotion has no valid independent owner session/nonce, or the nonce is expired, revoked, wrong-owner, wrong-session, wrong-action or stale-version
- **THEN** no rule or mutation commits and Telegram receives only a dashboard handoff
- **AND** an ordinary current approve/reject/edit/dispatch action still uses its existing one-tap path

#### Scenario: Nonce consumption and rule mutation have one winner
- **WHEN** two surfaces race the same confirmed rule action or retry it after an ambiguous reply
- **THEN** one current-version transition, nonce consumption and audit receipt commit together, and completed-action readback cannot create a second rule/effect
- **AND** rollback leaves no consumed nonce or successful mutation, while a competing approve/reject decision cannot overwrite the winning action version

#### Scenario: Editing a confirmed action invalidates its old rule nonce
- **WHEN** the action, recipient, arguments or current version changes after a rule nonce is issued
- **THEN** the old nonce cannot create/promote the changed rule and fresh confirmation binds the exact new digest
- **AND** the edit itself retains ordinary one-tap semantics and cannot widen a grant or revive a stopped action

#### Scenario: Slot permission is principal and slot specific
- **WHEN** a recipient fills a typed slot explicitly permitted to that rule's own recipient on its owner creation card
- **THEN** that slot passes the supplier check only, without changing labels or any other gate
- **AND** a different third party, unknown supplier or unpermitted slot makes the rule ineligible and parks the call

#### Scenario: Category coverage never replaces the other rule gates
- **WHEN** a non-owner send follows reads labeled health and finance but the matching rule names health alone
- **THEN** the action parks and no bounded rule use is consumed
- **AND** a rule naming both still cannot approve a park-only/always-per-event call or evade template, selection, currentness or Stop checks

### Requirement: Shared Executor Path

All actual execution — auto-approved actions and manually approved actions dispatched by their owning butler — MUST go through `execute_approved_action()`. The executor calls the original tool function, normalizes non-dict return values to `{"value": ...}`, and only then persists a successful `execution_result`, `executed` status, and immutable success audit event. An auto-approval `use_count` increment is not made here: the use is claimed at admission. The terminal state and audit append are atomic when the runtime provides a transaction.

For an action that names a goal, execution SHALL happen only when the control-plane broker forwards the stored call, or the owner's edited version of it, with an assertion whose `approval_ref` names that action and whose argument digest equals the approved arguments, after the broker has re-checked that the goal is not stopped. `dispatch_approved_action` and every other domain-local path SHALL NOT execute such an action on its own. Actions that name no goal, including those with goal origin `legacy`, keep the owning-daemon dispatch path.

Direct non-Messenger producers covered by the executable-command contract MUST
persist a declared executable command: the owning daemon, registered original
tool name, and exact handler kwargs are validated before the row can be
parked. The owning daemon validates its declared command contracts against the
registered MCP surface during startup.

ID: REQ-module-approvals-004
Source: RFC 0038 D12 and D13; RFC 0021
Scope: v1-mandatory

#### Scenario: Tool execution succeeds

- **WHEN** the executor calls the original tool function successfully
- **THEN** the result is persisted as `{"success": true, "result": {...}, "executed_at": "..."}`
- **AND** status transitions from `approved` to `executed`
- **AND** an `action_execution_succeeded` audit event is recorded
- **AND** the successful result is not reported until the persisted result and audit transition complete

#### Scenario: Tool execution fails with exception

- **WHEN** the tool function raises an exception
- **THEN** the error is returned to the caller and an `action_execution_failed` audit event is recorded
- **AND** status remains `approved` with `execution_result = null`
- **AND** no automatic replay occurs; an operator may retry the approved action or reject it explicitly

#### Scenario: Manual approval without executor wired

- **WHEN** `approve_action` is called but no tool executor is wired
- **THEN** status remains `approved` with `execution_result = null`
- **AND** the action remains eligible for the owning-butler dispatch/retry seam

#### Scenario: Declared direct command dispatches through its owner

- **WHEN** an approved direct non-Messenger action that names no goal has a declared command and a null execution result
- **THEN** `dispatch_approved_action` invokes the owning daemon's registered original handler with exactly the persisted declared kwargs
- **AND** it does not re-enter the approval gate or use a different butler

#### Scenario: Approved dispatch recovery is deliberately narrow

- **WHEN** `dispatch_approved_action` receives an `approved` action with `execution_result = null`
- **THEN** it invokes the owning daemon's registered original tool handler without re-entering the gate, only when the action names no goal
- **AND** a legacy `relationship` action named `entity_merge` is resolved as the registered `memory_entity_merge` callable without rewriting the stored row
- **AND** actions in any other status, or approved actions with a non-null result, are not replayed
- **AND** an approved action that names a goal is refused and left unchanged for the broker

#### Scenario: Historic malformed command remains unchanged

- **WHEN** a historic approved action has an unregistered name or incompatible stored arguments
- **THEN** dispatch records the failure and leaves the action approved with a null execution result
- **AND** it does not infer replacement arguments or rewrite the stored action provenance

#### Scenario: At-most-once execution with concurrency lock

- **WHEN** concurrent execution attempts target the same action
- **THEN** a per-action asyncio lock (WeakValueDictionary-based) serializes attempts within one daemon process
- **AND** a persisted `executed` result is replayed without invoking the tool again

#### Scenario: An approved goal action executes only through the broker

- **WHEN** an action that names a goal is approved, with or without owner edits
- **THEN** the broker forwards exactly the stored or owner-edited call with an assertion whose `approval_ref` names the action
- **AND** the owning daemon executes it once through `execute_approved_action` without re-entering the gate

#### Scenario: An approved action of a stopped goal does not execute

- **WHEN** the owner stops a goal after one of its actions was approved but before the broker dispatched it
- **THEN** the broker does not forward the action
- **AND** the action stays `approved` with `execution_result = null`, eligible only for owner abandonment

### Requirement: Immutable Audit Events

The `approval_events` table MUST be an append-only audit log. Events include
`event_type`, `action_id`, `rule_id`, `actor`, `reason`, `event_metadata`
(JSONB), and `occurred_at`. A database trigger prevents UPDATE and DELETE
operations. An event's `action_id` and `rule_id` are immutable historical
provenance rather than deletion-blocking foreign keys, so terminal-action and
inactive-rule retention do not mutate or delete the event. A newly inserted
non-null action or rule reference MUST still resolve to its live row when the
event is written.

Source: RFC 0038 D11 and D14
Scope: v1-mandatory

#### Scenario: Audit event creation for all state transitions

- **WHEN** any approval state transition occurs (queued, auto-approved,
  approved, rejected, expired, abandoned, cancelled, execution succeeded,
  execution failed, rule created, rule revoked)
- **THEN** an immutable event row is inserted with the corresponding
  `ApprovalEventType` value
- **AND** actor, action_id/rule_id, reason, and metadata are captured.

#### Scenario: Audit event types

- **WHEN** events are recorded
- **THEN** the following canonical event types are used: `action_queued`,
  `action_auto_approved`, `action_approved`, `action_rejected`,
  `action_expired`, `action_abandoned`, `action_cancelled`,
  `action_execution_succeeded`, `action_execution_failed`, `rule_created`,
  `rule_revoked`.

#### Scenario: Audit event immutability

- **WHEN** an UPDATE or DELETE is attempted on `approval_events`
- **THEN** the database trigger rejects the operation
- **AND** the row remains unchanged.

#### Scenario: Historical audit references preserve new-write validation

- **WHEN** a new event is inserted with a non-null `action_id` or `rule_id`
- **THEN** that identifier must reference a live pending action or approval
  rule when the event is written
- **AND** a retained event may continue to expose that identifier after its
  referenced terminal action or inactive rule is cleaned under the shorter
  retention policy.

### Requirement: Retention Policy

The module MUST support configurable retention windows for approvals data:
`pending_actions_retention_days` (default 90),
`approval_rules_retention_days` (default 180), and
`approval_events_retention_days` (default 365).

Source: RFC 0038 D11 and D14
Scope: v1-mandatory

#### Scenario: Cleanup old actions

- **WHEN** `cleanup_old_actions` runs
- **THEN** only terminal-status actions (`rejected`, `expired`, `executed`,
  `abandoned`, `cancelled`) older than the retention window are deleted
- **AND** `approved` actions remain retained and retryable, including old rows
  with a null `execution_result`
- **AND** related immutable action events remain unchanged with their historical
  `action_id` until their own event-retention window
- **AND** standing rules created from those terminal actions remain unchanged
  with their historical `created_from` until their separate rule-retention
  policy applies
- **AND** pending actions are never cleaned up automatically
- **AND** a dry-run mode returns counts without deleting.

#### Scenario: Cleanup old rules

- **WHEN** `cleanup_old_rules` runs
- **THEN** only inactive rules (`active=false`) older than the retention window
  are deleted
- **AND** immutable rule events remain unchanged with their historical `rule_id`
  until their separate 365-day event-retention window
- **AND** rerunning the cleanup after an eligible rule is deleted returns no
  additional rule deletion and never mutates or deletes the retained event.

#### Scenario: Cleanup old events requires privilege

- **WHEN** `cleanup_old_events` is called without `privileged=True`
- **THEN** a `PermissionError` is raised
- **AND** no events are deleted
- **WHEN** `cleanup_old_events` is called with `privileged=True`
- **THEN** events older than the retention window are deleted (bypasses
  immutability trigger).

### Requirement: MCP Tool Surface (17 Tools)

The module MUST register exactly 17 stable MCP tools when enabled (8 queue tools + 6 rule tools + 3 autonomy suggestion tools). The owner-decision tools `approve_action`, `reject_action`, `dispatch_approved_action`, `create_approval_rule`, `create_rule_from_action`, `revoke_approval_rule`, `confirm_promotion_suggestion` and `dismiss_promotion_suggestion` SHALL NOT be LLM-presentable and SHALL never be granted to a goal invocation; owner decisions reach them only through the authenticated dashboard and signed Telegram callbacks.

Source: RFC 0038 D4; RFC 0021
Scope: v1-mandatory

#### Scenario: Queue management tools (8)

- **WHEN** the approvals module is registered
- **THEN** the following 8 queue tools are available: `list_pending_actions`, `show_pending_action`, `approve_action`, `dispatch_approved_action`, `reject_action`, `pending_action_count`, `expire_stale_actions`, `list_executed_actions`
- **AND** `dispatch_approved_action` executes an eligible already-approved action through its owning daemon's original registered tool handler, without re-entering the approval gate
- **AND** an already-executed replay returns its stored result without invoking the tool again

#### Scenario: Rule management tools (6)

- **WHEN** the approvals module is registered
- **THEN** the following 6 rule tools are available: `create_approval_rule`, `create_rule_from_action`, `list_approval_rules`, `show_approval_rule`, `revoke_approval_rule`, `suggest_rule_constraints`

#### Scenario: Autonomy suggestion tools (3)

- **WHEN** the approvals module is registered
- **THEN** the following 3 suggestion tools are available: `list_promotion_suggestions`, `confirm_promotion_suggestion`, `dismiss_promotion_suggestion`

#### Scenario: List executed actions with filters

- **WHEN** `list_executed_actions` is called with optional `tool_name`, `rule_id`, `since`, `limit` filters
- **THEN** only executed actions matching the filters are returned ordered by `decided_at DESC`

#### Scenario: Decision tools are never grantable

- **WHEN** a goal invocation's grant is computed, or the invocation searches its catalog for a tool that approves, rejects, dispatches or creates a rule
- **THEN** none of the owner-decision tools is listed or callable
- **AND** a parked action stays pending until the owner decides through the dashboard or a signed Telegram callback

### Requirement: Defense-in-Depth at the Delivery Layer

The approval gate operates at two layers. Both MUST enforce gating independently.

**Layer 1 - MCP tool wrapping** (gate.py): Intercepts gated tool calls at the MCP
boundary before the tool handler runs. This is the primary gate for direct tool
invocations.

**Layer 2 (inline delivery gate)** (`core_tools/_notifications.py` and
`core_tools/_routing.py`): Outbound delivery paths call module methods directly
(e.g., `_send_email()`, `_send_message()`), bypassing MCP tool wrappers entirely.
An inline approval gate MUST re-enforce role-based gating at this layer for every
outbound channel. `notify()` (`_notifications.py`) enforces the gate on all channels:
the email channel via `check_email_recipient` and every non-email channel via the
channel-general `check_recipient` guard. Messenger `route.execute` synchronous
delivery (`_routing.py`) likewise gates email via `check_email_recipient` and
Telegram, WhatsApp, and future non-email channels via `check_recipient`.

Both layers MUST apply one channel-uniform owner predicate: the normalized
candidate identifier set must resolve through active literal facts to exactly
one live, non-merged, non-deleted entity with the `owner` role. Primary and
secondary identifiers are equivalent for outbound authorization. External,
unknown, ambiguous, inactive, merged, deleted, malformed, and lookup-error
identifiers MUST NOT receive the owner bypass.

That synchronous delivery is Messenger's service-plane delivery endpoint (today
`route.execute` on Messenger), which survives the deletion of `route.execute`'s
session-dispatch role and is called only by Switchboard `deliver()`. For goal
invocations the broker's non-owner communication rule
(`REQ-capability-authorization-016`) applies before either layer, and neither
layer SHALL rely on it. Layer 2 SHALL gate every envelope it processes whoever
sent it, including deterministic service-plane senders. A reaction SHALL skip
the inline gate only when the chat it reaches resolves uniquely to the owner; a
reaction into any other chat is non-owner communication and SHALL be gated like
a send.

Source: RFC 0038 D4 and D7; `REQ-capability-authorization-016`
Scope: v1-mandatory

#### Scenario: route.execute enforces approval gate for email delivery

- **WHEN** the Messenger's `route.execute` handler processes a `notify.v1` envelope with `channel="email"`
- **THEN** it MUST resolve the normalized email candidate set through active literal `relationship.entity_facts`
- **AND** if exactly one live owner entity matches, it MUST deliver without a rule regardless of identifier primacy
- **AND** otherwise it MUST check standing approval rules
- **AND** if no standing rule matches, nothing is sent: the delivery MUST follow the pending-approval path, and the caller receives a descriptive result naming the pending approval
- **AND** if the target is an owner, delivery proceeds without rule check

#### Scenario: route.execute enforces approval gate for telegram delivery

- **WHEN** the Messenger's `route.execute` handler processes a `notify.v1` envelope with `channel="telegram"` and `intent` of `"send"` or `"reply"`
- **THEN** it MUST resolve the normalized Telegram candidate set through active literal `relationship.entity_facts`
- **AND** if exactly one live owner entity matches, it MUST deliver without a rule regardless of identifier primacy
- **AND** otherwise it MUST check standing approval rules
- **AND** if no standing rule matches, nothing is sent: the delivery MUST follow the pending-approval path, and the caller receives a descriptive result naming the pending approval
- **AND** if the target is an owner, delivery proceeds without rule check

#### Scenario: route.execute enforces approval gate for WhatsApp delivery

- **WHEN** the Messenger's `route.execute` handler processes a `notify.v1` envelope with `channel="whatsapp"` and `intent` of `"send"` or `"reply"`
- **THEN** it MUST resolve the normalized WhatsApp candidate set through active literal `relationship.entity_facts`
- **AND** if exactly one live owner entity matches, it MUST deliver without a rule regardless of identifier primacy
- **AND** otherwise it MUST check standing approval rules
- **AND** if no standing rule matches, nothing is sent: the delivery MUST follow the pending-approval path, and the caller receives a descriptive result naming the pending approval
- **AND** if the target is an owner, delivery proceeds without rule check

#### Scenario: route.execute skips gate for emoji reactions

- **WHEN** the Messenger's `route.execute` handler processes a `notify.v1` envelope with `channel="telegram"` and `intent="react"` whose chat resolves uniquely to the owner
- **THEN** the inline approval gate is NOT applied (reactions in the owner's own chat are low-risk, non-content operations)

#### Scenario: A reaction into a non-owner chat is gated

- **WHEN** the Messenger's `route.execute` handler processes a `notify.v1` envelope with `intent="react"` whose chat does not resolve uniquely to the owner
- **THEN** it MUST follow the standing-rule or pending-approval path exactly as a send
- **AND** no reaction is posted until the owner approves or an owner-created standing rule matches

#### Scenario: A deterministic sender receives no bypass at the delivery layer

- **WHEN** a deterministic service-plane sender asks Switchboard `deliver()` to deliver a `notify.v1` envelope whose target does not resolve uniquely to the owner
- **THEN** the inline gate MUST apply the standing-rule or pending-approval path
- **AND** the sender's identity grants no bypass

#### Scenario: All channels have parity

- **WHEN** a new outbound channel is added to the Messenger butler
- **THEN** the `route.execute` handler MUST include an inline approval gate using the same unique-active-live-owner predicate as email, Telegram, and WhatsApp
- **AND** it MUST treat primary and secondary owner identifiers equivalently
- **AND** it MUST fail closed for every unsafe association category defined above
- **AND** the absence of an inline gate for any outbound channel is a spec violation

### Requirement: Post-Approval Tracker Hook

After a pending action is manually approved by a human actor (status transitions from `pending` to `approved`), the approvals module MUST invoke the autonomy tracker to record the approval and check for promotion threshold crossings, unless the action's goal lineage records that the requesting invocation was exposed. The approval of an exposed action SHALL NOT be recorded toward promotion.

Source: RFC 0038 D6 and D13; RFC 0021
Scope: v1-mandatory

#### Scenario: Tracker invoked after manual approval

- **WHEN** `approve_action` successfully transitions an action without exposed lineage to `approved`
- **THEN** the autonomy tracker's `record_approval` function MUST be called with the action's `tool_name`, `tool_args`, `action_id`, `requested_at`, and `decided_at`
- **AND** tracker invocation MUST NOT block or delay the approval response to the caller

#### Scenario: Tracker failure does not block approval

- **WHEN** the autonomy tracker raises an exception during `record_approval`
- **THEN** the approval MUST still succeed
- **AND** the tracker error MUST be logged at WARNING level
- **AND** no approval data SHALL be lost

#### Scenario: Exposed approval does not count toward promotion

- **WHEN** `approve_action` approves an action whose goal lineage records an exposed invocation
- **THEN** `record_approval` MUST NOT be called for it
- **AND** no promotion suggestion can be derived from that approval

### Requirement: Server-Derived Approved Execution Lineage

While the shared executor invokes an approved tool, it MUST bind trusted
execution lineage containing the pending action id, the action's stored goal
lineage (goal, goal origin, invocation, depth and exposure; goal origin `legacy`
with its historic session id; or no goal for a non-goal producer), and its
persisted `decided_by` actor. For an action that names a goal, the bound lineage
MUST equal both the stored lineage and the lineage of the broker assertion that
carries its `approval_ref`. Tool arguments MUST NOT be
able to supply or override this lineage. Authority MUST be bound to the exact
executor task, tool name, and canonical tool arguments, and the binding MUST be
reset after the invocation, including when the handler fails. An asynchronous
child task that inherits context storage MUST receive no approval authority.

Source: RFC 0038 D7 and D13; RFC 0028
Scope: v1-mandatory

#### Scenario: Approved physical action receives trusted lineage

- **WHEN** an approved action is dispatched through `execute_approved_action`
- **THEN** the original handler can read its action id, goal lineage, and decision actor from executor-owned context
- **AND** wrong-tool, wrong-argument, concurrent child-task, and subsequent calls cannot observe that action's lineage

#### Scenario: Mismatched assertion lineage refuses execution

- **WHEN** the broker forwards an approved action that names a goal and the assertion lineage differs from the action's stored goal lineage
- **THEN** the executor refuses to run the handler
- **AND** the action stays `approved` with `execution_result = null`
