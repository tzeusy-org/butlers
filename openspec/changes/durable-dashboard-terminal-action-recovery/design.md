## Context

PR #3624 gives dashboard turns durable identity, cancellation intent, and
route/runtime fencing.  Its terminal non-runtime lanes still make compound visible
effects after `dashboard_turn_claim_external_action()` returns: a bug report has a
QA relay and owner reply, while dead-lettering has a capture and owner reply. Any
one can succeed, fail, or crash after the claim but before
`dashboard_turn_mark_terminal()`. The existing turn row therefore protects against
duplicate immediate execution but cannot prove which individual effects happened
after process loss.

The design must preserve the owner-facing rule that a dashboard message never
pretends an effect was filed or cancelled when the system cannot establish that.

## Goals / Non-Goals

**Goals:**

- Give every claimed dashboard bug-report or dead-letter action a durable intent,
  receipt/ambiguity record, and bounded reconciliation owner.
- Prevent duplicate externally visible effects whenever the receiving contract
  supports idempotency or receipt lookup.
- Reach `completed`, `failed`, `cancelled`, or an explicit `ambiguous` result for
  every terminal action and targetless pending-Stop/route-uncertainty state this
  change owns; never leave one of those actions indefinitely in progress.
- Surface that durable result in the conversation API and chat UI, including the
  correct Stop semantics while recovery is running.
- Prove crash-boundary behavior for both terminal action kinds.

**Non-Goals:**

- Rework route-inbox leases, runtime cancellation, or the #3624 migration's
  route/session authority.
- Establish a durable answer/session-outcome contract after a domain route has
  been definitively acknowledged; that remains a separately approved scope.
- Guess that an unknown external relay succeeded, retry a potentially duplicate
  effect, or silently collapse ambiguity into failure/success.
- Add a generic question lane, first-token streaming, or a cross-channel reader.

**Pre-signoff HOLD:** PR #3624 must first land at an independently verified
current-base head. This delta must then rebase on its exact landing commit and
fully reconcile the replaced `dashboard-chat-ui` → `SSE Client Integration`
requirement, preserving or explicitly owner-approving supersession of every
landed Stop clause, including immutable pre-conversation `message_id`, accessible
pending intent, ingress/runtime fencing, terminal `SESSION_CANCELLED` SSE, and
truthful non-calm failure. PR #3618's active dashboard Stop/SSE delta must also
be rebased and reconciled with this change, or closed as superseded only after
the owner dispositions each distinct guarantee: its truthful
`dispatch_accepted` receipt and accessible routed-versus-targetless announcement,
accountable routed-butler link, and non-destructive list/history read recovery.
Retained guarantees MUST be transplanted into the surviving delta after #3618 no
longer actively modifies the same main requirement; an omission needs an explicit
owner rejection. The changes cannot define competing requirements for the same
owner-visible cancellation path or silently discard a truthful UI behavior.

## Decisions

### 1. Reserve one dashboard lane before any irreversible dispatch

The first dashboard classification action SHALL atomically reserve
`route_pending`, `bug_report`, or `dead_letter` for the immutable user message
before it dispatches a domain butler, relays QA, or captures a dead letter. A
definitive `accepted` route acknowledgement promotes `route_pending` to
immutable `route`. The
first reservation wins: a later conflicting tool call is refused with
`dashboard_lane_conflict`, keeps the first route/action authoritative, and
creates no second visible effect. Only a fenced `route_pending → dead_letter`
transition with definitive evidence that every route attempt was rejected before
dispatch and had no side effect may create a dead letter. A timeout or other
unknown route outcome becomes ambiguous; it is never retried or dead-lettered
automatically. A `route` reservation uses the existing dashboard-turn route
control record; only `bug_report` and `dead_letter` create the durable
terminal-action journal.

**Why this over replacing a route with a later bug call or treating timeout as
failure:** a routed domain session may already be an irreversible external
dispatch. Replacing its target or dead-lettering after an unknown route result
would create unprovable dual effects and contradict a singular action. Refusal or
explicit ambiguity is the only truthful behavior after the dispatch boundary.

### 2. Use a parent action journal with separately recoverable effects

Introduce one singular parent action record per dashboard message; its
`action_kind` and canonical payload/hash become immutable when the lane wins. Add
one child effect record per planned effect. A bug-report action has `qa_report`
and `conversation_reply` effects; a dead-letter action has `dead_letter_capture`
and `conversation_reply` effects. Every child carries its own idempotency key,
state, receipt, lease/fencing data, attempts, and ambiguity evidence. The parent
can become completed only when all planned effects are confirmed; recovery invokes
only a missing or safely retryable child effect.

**Why this over columns on `dashboard_turns` or one journal row:** a turn row
cannot represent attempts, a receipt, a recovery lease, and a proof gap without
conflating control state with effect history. A single receipt cannot distinguish
"QA report persisted, reply missing" from the inverse. The parent/child model
preserves exactly that boundary and also allows future terminal action kinds
without another state-machine rewrite.

### 3. Persist intent before each effect and reconcile through a fenced worker

The lane writes the parent intent and planned child effects transactionally with
the terminal-action claim. The Switchboard daemon owns a supervised reconciler:
it runs at startup and then on a persisted cadence no greater than 60 seconds,
claims a 60-second fenced lease, and heartbeats at least every 20 seconds. Before
each irreversible call, it writes a fenced `attempt_started` record and rechecks
the action-level Stop intent. Stop and `attempt_started` use reciprocal
conditional fences: Stop changes each still-`planned` child to `cancelled` with
`suppressed_by_stop`, while the worker can enter `attempt_started` only if Stop
is absent under the same current action/lease generation. The call may happen
only after that attempt transition commits. The receiver must enforce the stable
effect idempotency key or support receipt lookup; lease expiry alone never
authorizes a second call. On restart or lease expiry, the worker first
queries/derives a receipt; it retries only when the receiver proves the earlier
attempt had no effect. Otherwise it marks that child ambiguous.

Every action receives a persisted retry budget of five attempts and a
`reconcile_deadline_at` no later than 15 minutes after intent. Reaching either
bound without proof transitions the unresolved child and parent to `ambiguous`.
Those values are visible operational defaults rather than an unbounded loop.

**Why this over synchronous retries in the request path:** a process crash makes
the synchronous caller unknowable.  A durable worker separates delivery from the
owner's HTTP/SSE lifetime and makes retry policy inspectable.

### 4. Give QA, dead-letter capture, and replies distinct proof contracts

For a dashboard QA report, Switchboard uses a dedicated authenticated MCP service
principal. Its cached QA client loads a bearer credential through the existing
credential store. A QA FastMCP auth provider validates the credential and
exposes an access-token principal whose subject/client is the Switchboard router
and whose audience is the QA staffer. QA derives that identity from request
context; it never treats caller-supplied `source_butler` or dashboard identity
arguments as authorization. Anonymous callers, a wrong subject or audience, and
a spoofed source are rejected before any write or receipt lookup.

After authorization, `report_finding` receives the stable parent/effect identity
and durably persists a QA inbox receipt keyed by that identity before reporting
the effect as accepted. The receipt starts `pending`; the existing
`butler_reports` source fences a `pending -> claimed -> acknowledged` lifecycle
and creates/links exactly one patrol-owned canonical finding. The durable
acceptance response contains no `finding_id` until acknowledgement. Receipt
lookup distinguishes durable `found` from proven `not_found` (the only state
that permits same-key redelivery) and lookup `unavailable` (bounded ambiguity,
never a redelivery). Dashboard delivery is rejected before an inbox write when
the source is disabled; an already accepted inbox remains inspectable and waits
for a future enabled, fenced claim. A normal non-dashboard `report_finding` call
retains its existing volatile-buffer behavior. Dead-letter capture receives a
durable unique action reference, and `conversation_reply` receives a
child-effect idempotency key plus a database uniqueness boundary so recovery can
create only a missing reply.

**Why this over trusting the current QA acknowledgement:** the current
`report_finding` acknowledgement only enqueues an in-memory source buffer. It is
not proof of a durable report after restart.

### 5. Define one explicit ambiguity outcome and an owner resolution path

If the QA relay or dead-letter capture cannot supply an idempotency key or receipt
query that distinguishes "not sent" from "possibly sent", reconciliation records
`ambiguous` with sanitized evidence and a durable action ID. It does not make a
second visible submission. The dashboard exposes an owner-only action-inspection
resource and an explicit one-time manual assessment operation (`completed` or
`failed` with a required bounded sanitized note). The action read model carries
the immutable overlay `{id, assessment, note, recorded_at}` while the parent and
turn stay `ambiguous`; a normalized repeat returns the same event, and a changed
repeat conflicts. The operation cannot invoke a relay, resume recovery, or
convert unproven child state into a receipt-backed completion. The turn and chat
UI show ambiguity as unresolved work, not as a filed report, failed report, or
confirmed cancellation.

**Why this over at-least-once delivery:** duplicate QA cases and duplicate
dead-letter records are less trustworthy than an explicit owner decision when
exactly-once cannot be proved.

### 6. Extend the existing message read model with ingress and terminal-action status

`ConversationMessage` gains an optional `dashboard_turn` object on every
immutable dashboard user message with a durable control record:
`{ingress_state, target_kind|null, state, cancel_requested_at,
ingress_recovery_at, stop_reconcile_deadline_at, updated_at, reason_code}`.
`ingress_state` preserves the existing durable ingress vocabulary (`pending`,
`submitting`, `accepted`, `retryable_error`, or `rejected`); the owner-facing
`state` maps fresh ingress to `pending_ingress`, a targetless accepted turn to
`pending_reconciliation`, and preserves retryable/rejected states without
inventing a target. A durable terminal state takes precedence over raw ingress
(including `cancelled` after an ingress error); otherwise a durable Stop intent
takes precedence as `pending_cancellation`, so reload never makes an
unconfirmed Stop look like ordinary submission. Every targetless pending Stop
has a durable deadline no later than 15 minutes after intent. A Switchboard
startup/60-second reconciler inspects durable ingress/request/session evidence
without reissuing ingress; it persists a concrete proven outcome or
`ambiguous`/`ingress_stop_outcome_unknown` by that deadline. It gains an optional terminal-
action object only once a `bug_report` or `dead_letter` lane is claimed:
`{id, kind, state, effects[], reference, reason_code, updated_at,
ambiguity_reason_code, resolution_url, owner_resolution}`. Every effect has a
safe state/reference/reason summary; `owner_resolution` is nullable and never a
receipt. Terminal-action `state` is exactly `pending_reconciliation`,
`completed`, `failed`, `cancelled`, or `ambiguous`; raw exception/database details
never enter either object. `ingress_recovery_at` is 60 seconds after turn opening
for targetless `pending`, immediately eligible for `retryable_error`, and 60
seconds after the durable ingress claim for `submitting`; it is null once a turn
is accepted, terminal, or has a pending Stop. While a dashboard turn is `pending_ingress`,
`pending_reconciliation`, or `pending_cancellation`, the existing conversation
query polls no slower than every 10 seconds. At `ingress_recovery_at`, the UI
stops passive polling and offers owner-initiated `POST
/api/butlers/{name}/conversation-turns/{message_id}/retry-ingress`; that API uses
the existing exact-message claim fence and never inserts another user message or
automatically dispatches Switchboard. It returns JSON with the exact message and
conversation identities, a semantic recovery outcome, and current durable turn
projection; the UI invalidates/refetches that message and resumes only the
appropriate bounded polling rather than opening a second SSE stream. Reconnect/
reload reads the same durable object. The widget renders the durable result from
that model.

The cancel endpoint returns a durable outcome of `cancelled`, `already_finished`,
`pending_reconciliation`, or `ambiguous`. It persists action-level Stop intent
even after terminal-action intent exists. A Stop that wins before a child writes
`attempt_started` atomically cancels the action and turn only if no other child
has started or completed. If a primary effect is complete and Stop suppresses an
unstarted acknowledgement, the Stop response remains `pending_reconciliation`
until the parent durably becomes failed with `stopped_after_partial_effect`; it
must never claim `cancelled`. A Stop after an effect begins remains pending or
ambiguous until the journal proves the parent result. It must not claim that it
stopped an action whose result is unknown.

The canonical response is outcome-only. The current repository-owned frontend
still calls the conversation-scoped endpoint and reads `cancelled` /
`already_finished`; those are migration inputs, not a compatibility commitment.
The implementation change introduces the message-scoped endpoint, migrates
`frontend/src/api/client.ts`, `FloatingChatWidget`, `ChatPanel`, their types and
tests, verifies the API inventory and repository have no remaining callers, and
then deletes the old endpoint, response model/type, and client alias before the
change archives. No external consumer is currently verified. Any future
exception requires an owner-approved amendment naming the consumer, accountable
owner, and dated sunset; without that evidence, deletion is mandatory.

**Why this over a separate turn-status endpoint or transient SSE-only status:** the
effect is caused by one persisted user message and must survive reload, handoff,
and a late reply.  Extending the already-authoritative message history gives the
smallest durable read path without a second polling lifecycle.

### 7. Stage reconciliation through an owner-controlled observe-safe mode

The Switchboard owns a persisted, owner-only operational setting named
`terminal_action_reconciler.mode` with exactly `observe` and `active` values.
The deployment default is `observe`. In `observe`, the journal writer and
request-path child-effect receipt boundaries are active, but the reconciler only
claims/inspects rows, performs receipt lookup, records staleness metrics, and
marks a bounded unprovable action ambiguous; it SHALL NOT invoke a missing child
effect or perform an automatic retry. In `active`, it may invoke a missing child
only after the receipt/idempotency proof required by Decision 3.

Only an owner-authorized settings change may promote `observe` to `active`, after
the compose-backed kill/restart canary and pending/stale metrics are reviewed.
Rollback changes the setting back to `observe`: it stops new automatic effect
invocations, preserves parent/effect rows and leases as evidence, and leaves
pending rows visible for owner inspection rather than deleting, resetting, or
silently reclassifying them.

**Why this over an implicit feature flag or a disabled worker:** a persisted,
audited owner control makes the rollout state visible across daemon restarts. An
observe mode still proves receipt/read-model wiring without creating a second
external delivery attempt; a disabled/untracked worker would hide the very
pending actions the system must make truthful.

## Risks / Trade-offs

- **QA relay lacks a durable receipt today** → add the dashboard-specific QA
  authenticated receipt/inbox/lookup contract before allowing a filed outcome;
  use `ambiguous`, not automatic retry, until it exists.
- **Dashboard mode could be spoofed through tool arguments** → authorize the
  validated Switchboard service principal at the QA MCP boundary and reject
  anonymous, wrong-audience, wrong-subject, or source-spoofed calls before data
  access.
- **Compatibility aliases can become permanent cruft** → migrate every
  repository-owned caller and delete the conversation endpoint, booleans,
  response model/type, and client alias in the same implementation change.
- **Dead-letter capture is local but unkeyed** → add a durable uniqueness boundary
  tied to the dashboard action/request before enabling replay.
- **Worker duplication after a crash** → use lease generation/fencing on every
  journal write, receiver-enforced idempotency, and make terminal transitions
  monotonic.
- **Old in-progress rows have no historical proof** → backfill them to
  `ambiguous` by default; reconcile automatically only if a new durable receipt
  lookup proves the exact action state.
- **UI presents noisy operational detail** → render concise owner language with a
  case/reference and an inspectable action record, not raw exception text.

## Migration Plan

1. Resolve and independently verify #3624 so the dashboard-turn authority
   exists: repair the processing-lease handoff race before any slow anchor I/O
   can outlive ownership, reconcile the dashboard no-replay/ambiguous rule and
   canonical cancel endpoint with the normative RFC/API inventory, add the
   post-acceptance-retry and cross-client Stop regressions, then obtain
   current-base exact-head or validated merge-result evidence.
2. Add parent/effect journal tables, state/lease constraints, indexes, QA
   receipt/discovery-inbox storage, and unique action boundaries in migrations;
   deploy these before any worker begins claiming.
3. Deploy authenticated Switchboard-to-QA service-principal wiring, the
   restart-safe QA discovery source and receipt lookup, lane writers, and the
   supervised reconciler with `terminal_action_reconciler.mode=observe` (the
   default). In observe mode it
   records/queries evidence and marks bounded unknown work ambiguous but performs
   no automatic child-effect retry; promote only through the owner setting after
   the canary and metrics gate.
4. Backfill every pre-existing `external_action_in_progress` turn to an
   explicit `ambiguous` record by default. Only a newly available durable receipt
   lookup may prove an exact historical action state; never retain an unjournaled
   stale claim or replay legacy work blindly.
5. Add API/UI read and manual-resolution support, run a compose-backed
   kill/restart canary, and alert on stale/ambiguous action counts before declaring
   the maturity gate passed.
6. In the same implementation change, migrate the repository-owned dashboard
   callers to the canonical message-scoped outcome response, prove no remaining
   callers through tests, inventory, and repository search, then delete all
   conversation-scoped and boolean compatibility surfaces before archive.
7. Amend RFC 0015 and the QA and Switchboard manifestos with the authenticated
   durable-inbox exception, ownership, permissions, recovery, modes, and SLAs.

Rollback changes the reconciler to `observe` while retaining the journal for
forensic truth. It must not delete journal rows, reset leases/effects, or return
ambiguous actions to an untracked in-progress state.

## Open Questions

The implementation must select existing or new migration-level storage names for
the QA receipt/discovery inbox and dead-letter uniqueness boundary, but the
behavioral contracts above are fixed: no validated Switchboard service principal
means no dashboard-mode access; no restart-safe QA handoff means no accepted
dashboard report; and no durable receipt means ambiguity, never a claimed filing.
