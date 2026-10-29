## Purpose

One accountable generalist lead fulfills each goal across domain-owned
capabilities, directly or with optional focused children at depth one. Every
reasoning session in the system is an invocation of an admitted goal. The
design is RFC 0038; authorization is specified in `capability-authorization`.

## ADDED Requirements

### Requirement: Whole-goal accountability
The system SHALL retain one accountable active lead for each admitted goal,
including its cross-domain constraints and final outcome. The lead SHALL be
able to complete a goal without creating children; independent goals SHALL NOT
be forced into one shared conversation or serialized solely by this rule.

ID: REQ-goal-orchestration-001
Source: about/heart-and-soul/vision.md, What Butlers Is; adopted 2026-10-14 doctrine amendment; RFC 0038 D1, D2 and D17
Scope: v1-mandatory

#### Scenario: Cross-domain constraints remain one goal
- **WHEN** an admitted goal combines travel, budget and health constraints
- **THEN** one lead retains all constraints and returns a reconciled outcome or explicitly identifies unresolved constraints instead of treating separate domain answers as completion

#### Scenario: Simple work needs no child
- **WHEN** the lead can fulfill a goal through its authorized capabilities
- **THEN** it may complete with zero children and a second reasoning session is not required solely because a capability has a different domain owner

### Requirement: Focused children have bounded governing briefs
Every child SHALL receive a dedicated task brief stating its objective, success
conditions, relevant context and provenance, applicable versioned governing
guidance, execution scope, and expected result. Generated instructions SHALL NOT
override governing policy or grant authority.

ID: REQ-goal-orchestration-002
Source: about/heart-and-soul/vision.md, Non-Negotiable Rules 5 and 6; RFC 0038 D9
Scope: v1-mandatory

#### Scenario: Cross-domain focus inherits applicable policies
- **WHEN** a child investigates a bounded question spanning multiple domains
- **THEN** its brief includes the applicable domain guidance and only authorized relevant context, without requiring a new permanent roster identity

#### Scenario: A prompt cannot enlarge authority
- **WHEN** a child's task prompt requests an operation beyond its grant
- **THEN** execution is refused regardless of the wording of the prompt or the lead's assertion that it is allowed

### Requirement: Delegation depth is enforced across every creation path
The trusted runtime SHALL derive goal lineage and enforce maximum depth one,
with a lead at depth 0 and children at depth 1. A child SHALL NOT create or
delegate to another reasoning agent through spawn, routing, scheduling, or
new-root admission paths. Admissions caused by a child's effects SHALL be
bounded as specified in `capability-authorization`. Follow-up work requested by
a child SHALL return to its lead, which may act directly or delegate a sibling
under the same goal.

ID: REQ-goal-orchestration-003
Source: about/heart-and-soul/vision.md, Non-Negotiable Rule 6; about/heart-and-soul/security.md, Goal and Delegation Authority; RFC 0038 D8
Scope: v1-mandatory

#### Scenario: Child attempts a grandchild
- **WHEN** a depth-1 child calls a delegation, routing, schedule-creation or admission tool, by name or through any capability, to start another reasoning session
- **THEN** the broker refuses the call because no such tool is in a child's grant, and no session starts even if the request asserts a fresh root or different parent

#### Scenario: Further investigation returns to the lead
- **WHEN** a child reports that additional focused work is needed
- **THEN** the lead may handle it or delegate another depth-1 child within the original goal's authority and remaining budget, and the original child cannot dispatch it itself

### Requirement: Capability ownership and call authority survive delegation
Lead and child operations SHALL use domain-owned MCP capabilities through the
control plane, with server-derived caller/goal/resource/operation authority and
applicable approval checked at invocation. Child authority SHALL be a subset of
the lead's delegable grant. Discovery, skill loading and dedicated prompts
SHALL NOT bypass these checks, credential custody, memory-write authority,
private-source restrictions, or domain invariants.

ID: REQ-goal-orchestration-004
Source: about/heart-and-soul/vision.md, Non-Negotiable Rule 3; RFC 0027, Governing Invariants 1, 3 and 6; RFC 0038 D4, D7 and D9
Scope: v1-mandatory

#### Scenario: A discovered capability is unauthorized
- **WHEN** a model knows a valid capability name but its invocation grant does not permit the requested operation or resource
- **THEN** the owning execution boundary rejects the action and discovery does not supply an alternate invocation path

#### Scenario: Explicit child writes retain domain gates
- **WHEN** a child with a delegated write grant requests a sensitive operation
- **THEN** the domain service applies the same approval and invariant checks as for a lead and neither agent can self-approve or bypass them

### Requirement: Depth does not substitute for finite work limits
The runtime SHALL enforce finite goal work and elapsed-time limits plus child
concurrency limits before admitting further work. Sequential siblings, retries,
lead replacements and escalations SHALL retain the same budget accounting
and SHALL NOT widen authority or reset consumed limits.

ID: REQ-goal-orchestration-005
Source: about/heart-and-soul/architecture.md, One lead, focused children, depth one; RFC 0038 D10 and D16
Scope: v1-mandatory

#### Scenario: Sequential children exhaust the goal budget
- **WHEN** a lead requests another depth-1 child after exhausting its goal budget
- **THEN** the runtime refuses admission and records an incomplete or budget-limited goal outcome rather than resetting the budget through a new child or root

#### Scenario: Recovery retains prior accounting
- **WHEN** a lead is replaced after a runtime failure
- **THEN** the replacement uses the durable goal identity and previously consumed budget under one fenced active ownership slot

#### Scenario: New-goal quotas preserve isolated owner capacity
- **WHEN** concurrent admissions count new goals against explicit versioned origin and source limits
- **THEN** they lock and charge the same captured fixed UTC window, persist that window and serialize the final slot
- **AND** non-owner origins cannot use owner capacity, while joins/refusals consume no new-goal slot
- **AND** missing/invalid/unreadable limits refuse without stale-cache fallback; no numeric launch default is implied
- **AND** accepted source dedupe returns the same goal without reserving twice, repeated attempts return their exact decision, and later retries of a retryable refusal retain the same durable source key

#### Scenario: An owner reply retains an existing goal's authority
- **WHEN** trusted infrastructure matches an incoming verified owner reply to an exact server-held delivery/effect, recipient channel/entity and live input-accepting prompt
- **THEN** admission locks the referenced goal before the effect, rechecks expiry/liveness under lock and attaches the unique incoming source to that same goal
- **AND** the join writes one immutable goal inbox entry, bound to its decision and prompt effect, as the reply's only delivery path to that goal
- **AND** origin, ceiling, cause and budget remain unchanged, exposure only increases, and no invocation or approval is created
- **AND** a terminal or expired prompt follows ordinary new-owner-goal admission retaining the delivery/effect reference without a caused-goal link
- **AND** unknown, foreign or mismatched bindings follow ordinary verified message admission without guessed continuity; concurrent Stop and join serialize on the goal


### Requirement: Results and effects have honest durable outcomes
Children SHALL return evidence references, uncertainty, unresolved constraints,
and recorded effect status. The lead SHALL reconcile those results before
claiming whole-goal success. Cancellation SHALL fence further child admission
and new effects across the goal. Recovery SHALL preserve committed-effect
receipts and SHALL NOT automatically replay effects whose outcomes are unknown.

ID: REQ-goal-orchestration-006
Source: about/heart-and-soul/architecture.md, The Core Loop; RFC 0027, Governing Invariant 7; RFC 0038 D11, D12 and D13
Scope: v1-mandatory

#### Scenario: Stop races a child effect
- **WHEN** the owner stops a goal while a child's action is pending or may already have committed
- **THEN** the runtime fences new work and reconciles any existing effect as committed, refused or unknown without claiming that Stop undid a committed action

#### Scenario: Children disagree or only partially complete
- **WHEN** children return conflicting evidence, incomplete work or an uncertain action result
- **THEN** the lead resolves the conflict using evidence or reports the unresolved state instead of treating a child's success text as proof of completion

### Requirement: Domain care survives reasoning-session completion
Domain schedules, event subscriptions, obligations, governing guidance and
owned data SHALL persist independently of lead and child lifetimes. Authorized
proactive events SHALL be able to initiate goals without a user message while
retaining existing delivery, deduplication and owner-attention policies.

ID: REQ-goal-orchestration-007
Source: about/heart-and-soul/vision.md, What Success Looks Like; RFCs 0009, 0011 and 0026; RFC 0038 D2 and D5
Scope: v1-mandatory

#### Scenario: An obligation becomes due with no conversation running
- **WHEN** a standing authorized obligation becomes due after previous reasoning sessions have finished
- **THEN** its durable owner can initiate the appropriate goal with relevant context and the obligation is not lost because no agent was running

### Requirement: Goals are the only path to a reasoning session
Every reasoning session SHALL be a lead or child invocation of an admitted goal,
launched by the goal runner. Owner requests, third-party events, domain
obligations and staffer work SHALL all enter through goal admission. No domain
butler, staffer, module or scheduled job SHALL spawn a reasoning session by any
other path, and no model-classification routing step SHALL precede admission
except a dashboard fast answer's eligibility classification, which chooses only
between that fast answer and admitting the unchanged message as an owner goal.
The only model use outside goals SHALL be the engineering executors, tool-less
model functions and dashboard fast answers that RFC 0038 D18 names. None of
them SHALL receive capabilities; a dashboard fast answer's allowlisted reads
SHALL be made by deterministic code through the broker.

ID: REQ-goal-orchestration-008
Source: about/legends-and-lore/rfcs/0038-goal-runtime-and-capability-authorization.md, D1, D2, D18 and Hard cutover; later owner decision bu-u5euwb.21
Scope: v1-mandatory

#### Scenario: A domain schedule needs reasoning
- **WHEN** a domain's scheduled job requires model judgment
- **THEN** the domain submits an obligation admission and the reasoning runs as a goal lead, not as a session spawned by the domain daemon

#### Scenario: An owner message arrives
- **WHEN** a connector submits an owner message
- **THEN** admission creates a goal directly from the envelope without a model classification or route-dispatch step

#### Scenario: A dashboard fast answer declines before its first read
- **WHEN** the owner's dashboard question is ineligible, or its plan fails the schema, or its provider fails before the first read
- **THEN** the unchanged message is admitted as an owner goal
- **AND** nothing the fast answer produced enters that goal's admission, origin, ceiling or exposure

## Source References

- Non-Negotiable Rule 3 (cross-domain capability access is MCP-only through the control plane)
- Non-Negotiable Rule 4 (deterministic daemon; intelligence in ephemeral sessions)
- Non-Negotiable Rule 6 (governing documents; runtime-enforced depth and scope)
- `about/heart-and-soul/vision.md`, What Butlers Is; `about/heart-and-soul/architecture.md`, One lead, focused children, depth one
- RFC 0038 (goal runtime and capability authorization)
- RFCs 0009, 0011 and 0026 (situational context, proactive delivery, commitments)

## ADDED Requirements

### Requirement: Waiting goals wake through one fenced epoch intent
The ledger and runner SHALL implement S2's thread/plan/wait-envelope/inbox/epoch/wake-intent schemas and S4 evaluator interface. Conditional fire, wake consumption, labelled inbox and durable intent SHALL commit atomically; every wake SHALL narrow authority and retain finite horizon/budget. Waiting SHALL release capacity without erasing obligations or effects.
The lifecycle/admission owner SHALL implement S2.1's discriminated inbox/decision schema, kind-specific nullability and same-goal/source FKs, and atomic one-time consumed_at/consumed_epoch tuple with immutable legacy_consumed provenance. S2.2's generation rearm SHALL retain the same approved envelope, cumulative wake count/deadline, exclusive prior-fire cursor, same-or-narrower grants and accumulated labels. A genuinely changed envelope SHALL use its original approval rules; no reset, fake reply effect or duplicate re-fire is permitted.

ID: REQ-goal-orchestration-009
Source: about/legends-and-lore/rfcs/0038-goal-runtime-and-capability-authorization.md, proposed shared switch interfaces S2/S4; bu-dhloi1.19
Scope: v1-mandatory

#### Scenario: Already satisfied registration cannot lose a wake
- **WHEN** an envelope condition is true before its subscription is installed
- **THEN** registration evaluates the current snapshot and commits one unique successor intent

#### Scenario: Crash between fire and launch preserves one successor
- **WHEN** a worker crashes after the fire transaction, then another replays it
- **THEN** the same intent is claimed for one epoch with no duplicate launch or committed-effect replay

#### Scenario: Owner answer and bounded follow-up have distinct approval
- **WHEN** a goal waits for an exact owner answer, or asks approval for an effect plus envelope
- **THEN** answer-only waiting adds no approval; the latter binds exact digests and only bounded follow-ups, never an exposed extension

#### Scenario: Stop wins a competing wake
- **WHEN** Stop and a condition fire race for the goal fence
- **THEN** a fire committed first is retained honestly; a losing or later wake cannot launch or execute

#### Scenario: Changed plans and expired horizons cannot silently continue
- **WHEN** a plan premise changes or an envelope exhausts its deadline/wake count
- **THEN** a new immutable plan version is recorded; no envelope is enlarged and unresolved work ends partial with preserved receipts

#### Scenario: A timer or transition wakes before any reply exists
- **WHEN** a trusted condition fires for a valid waiting goal that has never emitted a reply effect
- **THEN** one condition_fire inbox row uses its real event/envelope/generation and condition_joined decision, with delivery_ref/reply_effect_id NULL; its intent/epoch/fire/count commit together

#### Scenario: A running owner input needs no fabricated delivery
- **WHEN** a verified same-thread non-reply arrives inside the join window before any outgoing reply
- **THEN** one owner_non_reply input joins with original transport source identity and NULL reply bindings; caller-asserted owner or a waiting non-reply cannot use this path

#### Scenario: Inbox input bindings cannot cross goals
- **WHEN** a new row names another goal's decision, reply effect, fire generation or consumed epoch
- **THEN** the matching composite FK/kind constraint and restricted writer refuse it without an inbox or launch

#### Scenario: Consumption is one atomic fenced tuple
- **WHEN** two epochs race to consume one unconsumed input, or one update changes only half the tuple
- **THEN** one actual running epoch may record consumed_at plus consumed_epoch once; the partial or losing mutation refuses and immutable input fields remain

#### Scenario: A second bounded wake reuses only its original approval
- **WHEN** an approved envelope with remaining wakes returns to waiting under unchanged plan/source/effect bounds before its original deadline
- **THEN** rearm creates exactly generation+1 and new owned instances; the next eligible event increments the same cumulative counter once without resetting approval/deadline/grants

#### Scenario: Already true or replayed events cannot manufacture a second wake
- **WHEN** rearm observes the old true level, old owner input, settled effect, transition or fixed timer tick again
- **THEN** none is a new eligible cause after the prior-fire cursor; the fixed at tick is at most once per envelope

#### Scenario: An event during the running to waiting gap is not lost
- **WHEN** a new eligible trusted event arrives after the first fire but before rearm snapshots its high-water
- **THEN** the new generation replays the complete interval after the prior fire before advancing; snapshot/subscription timing loses no accepted event

#### Scenario: Rearm retry and Stop preserve one winner
- **WHEN** rearm/fire/launch races a duplicate request, crash, expiry or Stop
- **THEN** the shared goal/generation fence retains one committed winner and cumulative count; a losing or post-Stop launch cannot proceed, and exhaustion yields partial receipts without a reset

### Requirement: Ledger conversion preserves every existing state and effect
The migration and lifecycle owner SHALL apply the complete S2 mapping from actual switchboard sw_047 → sw_050 → sw_051, preserving immutable attempt/source-to-canonical decision bindings, composite FK/backfill, runtime RLS, terminal recorded_without_goal, atomic constraint replacement and durable-row downgrade refusals, as well as all identities, statuses, parked/unknown effects, joined labels, inbox consumption, origin/ceiling/budgets and receipts. Conversion SHALL retain source status/provenance and refuse inconsistent legacy bindings. It SHALL NOT create a second control ledger or destructive rollback.
Conversion SHALL implement the bounded S2.1 trigger/check/FK replacement atomically, retaining every original reply binding and source row. Only already-consumed historical rows may carry legacy_consumed with an unknown epoch; they SHALL never be redelivered. Sw050 attempt binding, sw051 record-only semantics, RLS and durable-row downgrade refusals remain unchanged.

ID: REQ-goal-orchestration-010
Source: about/legends-and-lore/rfcs/0038-goal-runtime-and-capability-authorization.md, proposed shared switch interfaces S2; bu-dhloi1.19
Scope: v1-mandatory

#### Scenario: Every current status has a safe disposition
- **WHEN** the migration encounters admitted/running/settling/awaiting_approval/recovering or any terminal state
- **THEN** it applies the explicit S2 map, keeps terminal outcomes identical and preserves all invocation/effect statuses

#### Scenario: Missing legacy approval evidence is held
- **WHEN** an awaiting_approval record cannot be bound to its original effect
- **THEN** it remains waiting with unavailable evidence, without invented approval or completion

#### Scenario: Shadow parity precedes enforcement
- **WHEN** conversion/backfill has run but a producer/reader count, FK, receipt or role check disagrees
- **THEN** activation is refused and old compatible readers remain; no table or effect is discarded

#### Scenario: A rollback retains newly written restrictions
- **WHEN** the new consumer must be disabled after recording labels or wake receipts
- **THEN** new admissions/wakes stop, all evidence remains and no unknown dispatch or cancelled decision is replayed

#### Scenario: Already consumed legacy input keeps its real history
- **WHEN** conversion encounters a legacy reply with non-null consumed_at but no reconstructable epoch
- **THEN** it preserves the exact timestamp/identities, marks immutable legacy_consumed and NULL epoch, and forbids replay or invented historical epoch

#### Scenario: Unconsumed legacy reply uses the new tuple once
- **WHEN** conversion encounters an unconsumed reply with valid original delivery/effect FKs
- **THEN** the original bindings remain and its first fenced consumption atomically records real consumed_at/epoch; backfill does not claim it consumed

#### Scenario: A new-kind constraint or trigger install fails
- **WHEN** migration cannot validate a kind/FK or complete the guarded trigger replacement
- **THEN** the transaction rolls back with old protection/rows intact; new input admission stays dark and rollback cannot cast a new input into a fake reply

### Requirement: Owner thread inputs obey running and waiting bindings
Admission SHALL use S2's sole owner-thread identity, configured 2–10 minute running join window, exact waiting reply/button binding, labelled inbox and Stop-aware between-call delivery. Joining SHALL NOT widen ceiling, reset budget or imply approval.

ID: REQ-goal-orchestration-011
Source: about/legends-and-lore/rfcs/0038-goal-runtime-and-capability-authorization.md, proposed shared switch interfaces S2; bu-dhloi1.19
Scope: v1-mandatory

#### Scenario: One running chat receives a fresh non-reply
- **WHEN** exactly one matching owner goal is running inside the configured window
- **THEN** the input joins through one deduplicated inbox receipt and displays its thread chip plus /new

#### Scenario: Ambiguous or waiting chat cannot guess a target
- **WHEN** multiple goals match, the running window elapsed, or a waiting goal receives an unbound message
- **THEN** no latest-thread guess is used; only an exact reply/button joins waiting work and other input follows separate admission

#### Scenario: Stop words separate old and new work
- **WHEN** the owner sends a stop word and a new ask while a tool call is completing
- **THEN** old work is fenced and the new ask is separately admitted; between-call delivery does not create an unfenced turn

### Requirement: First-switch acceptance stays separate from M7
The programme SHALL use the complete first-switch/M7 and release-boundary matrices in shared-contract-coverage.md. Conservative representation, named successors and first-switch evidence SHALL NOT assert M7 readiness, release a held rule, execute a switch or close M6 with absent source owners. Existing per-event approval semantics SHALL remain binding before the richer M7 surface.

ID: REQ-goal-orchestration-012
Source: about/legends-and-lore/rfcs/0038-goal-runtime-and-capability-authorization.md, proposed shared switch interfaces S8; bu-dhloi1.19
Scope: v1-mandatory

#### Scenario: A source contract is reviewed
- **WHEN** the draft has authoring checks and a normative review
- **THEN** reserved exact-text adoption, source implementation, M6 candidate evidence and operational approval remain distinct unfinished boundaries

#### Scenario: M6 prepares a candidate
- **WHEN** first-switch successor evidence is incomplete but M7 work is available
- **THEN** M6 retains its selected first-switch requirements and does not substitute M7 or archive/release old contracts

#### Scenario: A held typed or cited rule is encountered
- **WHEN** a worker can represent typed values or citations
- **THEN** representation confers no steering/authority relaxation and the held decision remains for its named M7 gate
