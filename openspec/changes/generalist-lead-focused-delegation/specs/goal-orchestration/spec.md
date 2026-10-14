## Purpose

Reserve a future capability for one accountable generalist lead to fulfill a
whole goal across domain-owned capabilities with optional focused children.
These reserved target requirements do not supersede existing runtime contracts or
authorize implementation before the successor contract is approved.

## ADDED Requirements

### Requirement: Whole-goal accountability
The system SHALL retain one accountable active lead for each admitted goal,
including its cross-domain constraints and final outcome. The lead SHALL be
able to complete a goal without creating children; independent goals SHALL NOT
be forced into one shared conversation or serialized solely by this rule.

ID: REQ-goal-orchestration-001
Source: about/heart-and-soul/vision.md, What Butlers Is; adopted 2026-10-14 doctrine amendment
Scope: v1-reserved

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
Source: about/heart-and-soul/vision.md, Non-Negotiable Rules 5 and 6
Scope: v1-reserved

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
new-root admission paths. Follow-up work requested by a child SHALL return to
its lead for a separately admitted sibling or direct action under the same goal.

ID: REQ-goal-orchestration-003
Source: about/heart-and-soul/vision.md, Non-Negotiable Rule 6; about/heart-and-soul/security.md, Target Goal and Delegation Authority
Scope: v1-reserved

#### Scenario: Child attempts a grandchild through routing
- **WHEN** a depth-1 child asks a routing capability to start another reasoning session
- **THEN** admission rejects that request before a new session starts even if the request asserts a fresh root or different parent

#### Scenario: Further investigation returns to the lead
- **WHEN** a child reports that additional focused work is needed
- **THEN** the lead may handle it or admit another depth-1 child within the original goal's authority and remaining budget, and the original child cannot dispatch it itself

### Requirement: Capability ownership and call authority survive delegation
Lead and child operations SHALL use domain-owned MCP capabilities through the
control plane, with server-derived caller/goal/resource/operation authority and
applicable approval checked at invocation. Child authority SHALL be a subset of
the lead's delegable grant. Discovery, skill loading and dedicated prompts
SHALL NOT bypass these checks, credential custody, memory-write authority,
private-source restrictions, or domain invariants.

ID: REQ-goal-orchestration-004
Source: about/heart-and-soul/vision.md, Non-Negotiable Rule 3; RFC 0027, Governing Invariants 1, 3 and 6
Scope: v1-reserved

#### Scenario: A discovered capability is unauthorized
- **WHEN** a model knows a valid capability name but its invocation grant does not permit the requested operation or resource
- **THEN** the owning execution boundary rejects the action and discovery does not supply an alternate invocation path

#### Scenario: Explicit child writes retain domain gates
- **WHEN** a child with a delegated write grant requests a sensitive operation
- **THEN** the domain service applies the same approval and invariant checks as for a lead and neither agent can self-approve or bypass them

### Requirement: Depth does not substitute for finite work limits
The runtime SHALL enforce finite goal work and elapsed-time limits plus child
concurrency limits before admitting further work. Sequential siblings, retries,
lead replacements and resumed children SHALL retain the same budget accounting
and SHALL NOT widen authority or reset consumed limits.

ID: REQ-goal-orchestration-005
Source: about/heart-and-soul/architecture.md, One lead, focused children, depth one
Scope: v1-reserved

#### Scenario: Sequential children exhaust the goal budget
- **WHEN** a lead requests another depth-1 child after exhausting its goal budget
- **THEN** the runtime refuses admission and records an incomplete or budget-limited goal outcome rather than resetting the budget through a new child or root

#### Scenario: Recovery retains prior accounting
- **WHEN** a lead is replaced after a runtime failure
- **THEN** the replacement uses the durable goal identity and previously consumed budget under one fenced active ownership slot

### Requirement: Results and effects have honest durable outcomes
Children SHALL return evidence references, uncertainty, unresolved constraints,
and recorded effect status. The lead SHALL reconcile those results before
claiming whole-goal success. Cancellation SHALL fence further child admission
and new effects across the goal. Recovery SHALL preserve committed-effect
receipts and SHALL NOT automatically replay effects whose outcomes are unknown.

ID: REQ-goal-orchestration-006
Source: about/heart-and-soul/architecture.md, The Core Loop; RFC 0027, Governing Invariant 7
Scope: v1-reserved

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
Source: about/heart-and-soul/vision.md, What Success Looks Like; RFCs 0009, 0011 and 0026
Scope: v1-reserved

#### Scenario: An obligation becomes due with no conversation running
- **WHEN** a standing authorized obligation becomes due after previous reasoning sessions have finished
- **THEN** its durable owner can initiate the appropriate goal with relevant context and the obligation is not lost because no agent was running

### Requirement: Target adoption does not silently activate new authority
The system SHALL retain existing runtime and domain restrictions until an
explicitly approved successor RFC and baseline specification amendments have
been implemented and verified. This reserved capability SHALL NOT be treated
as proof of shipped behavior or permission to broaden existing sessions.

ID: REQ-goal-orchestration-008
Source: about/legends-and-lore/decisions/2026-10-14-doctrine-focused-delegation.md, Transition and precedence
Scope: v1-reserved

#### Scenario: Only the shape amendment exists
- **WHEN** the target shape or its proposed scenarios are recorded but successor enforcement has not been approved and implemented
- **THEN** existing per-butler MCP binding, routing and no-child-spawning restrictions remain in force
