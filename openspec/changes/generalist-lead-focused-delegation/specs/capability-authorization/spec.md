## Purpose

Enforce what every goal invocation may discover and do, independently of the
model that runs it. Authority is derived from the goal's verified origin and
cause, narrowed by delegation and exposure, checked by the control-plane broker
on every call, and verified again by the owning domain before a model-reachable
handler runs. The design is RFC 0038.

## ADDED Requirements

### Requirement: Sessions run in a sandbox that reaches only the broker
The goal runner SHALL launch every lead and child invocation on a session host
separate from the domain and control-plane processes, each in its own
kernel-enforced filesystem and process domain, with network egress limited to
the control plane's session listener and the configured model providers. An
invocation SHALL NOT hold database credentials, credential-store access,
domain service addresses, runtime built-in shell, filesystem, web or
local-server tools, or provider-hosted tools. A runtime adapter that cannot
prove these restrictions for a runtime and model tuple SHALL be ineligible for
goal invocations.

ID: REQ-capability-authorization-001
Source: about/legends-and-lore/rfcs/0038-goal-runtime-and-capability-authorization.md, D1 and D3
Scope: proposed-v1

#### Scenario: A session tries to reach a domain directly
- **WHEN** an invocation process attempts a connection to a domain butler's MCP port, a control-plane service listener or PostgreSQL
- **THEN** the connection is blocked by the sandbox and no handler runs

#### Scenario: An adapter cannot disable built-in or provider-hosted tools
- **WHEN** a runtime and model tuple cannot prove that its shell, filesystem, web and provider-hosted tools are disabled
- **THEN** the runner refuses to launch goal invocations on that tuple

### Requirement: Every model-reachable capability declares authorization metadata
Every LLM-presentable tool SHALL declare its owner domain, effect class, output
trust, output sensitivity, resource parameters, safety arguments, approval
policy, idempotency mode and owner-authority flag. A tool without complete
metadata SHALL NOT be grantable, and CI SHALL fail when a registered
LLM-presentable tool lacks it.

ID: REQ-capability-authorization-002
Source: RFC 0038, D4
Scope: proposed-v1

#### Scenario: A new module tool omits its effect class
- **WHEN** a module registers an LLM-presentable tool without an effect class
- **THEN** the catalog check fails in CI and the broker never includes the tool in a grant

### Requirement: Authority ceilings derive from verified origin
Goal admission SHALL derive the goal's authority ceiling deterministically from
server-held origin context: owner, obligation, third party or staffer. Message
text, model output and sender-supplied envelope fields SHALL NOT change the
origin or the ceiling. A message SHALL be owner-origin only when owner-channel
authorization verifies its channel and sender. A third-party goal's ceiling
SHALL exclude `actuate` and `administer` capabilities and SHALL hold
`communicate` and `transact` capabilities, other than owner `notify`, only as
park-only grants that can produce a parked effect but never execute one.

ID: REQ-capability-authorization-003
Source: RFC 0038, D5; RFC 0017
Scope: proposed-v1

#### Scenario: An email claims to come from the owner
- **WHEN** an inbound email from an address that owner-channel authorization does not verify says it is from the owner and asks for a payment
- **THEN** the goal is admitted with origin `third_party`, and the payment can at most be parked for per-event owner approval

#### Scenario: A scheduled obligation runs
- **WHEN** a domain obligation declared in git becomes due
- **THEN** its goal receives exactly the capability set declared for that obligation

### Requirement: Grants bound discovery and every call
The broker SHALL list to an invocation only capabilities within its grant and
SHALL check every call against the grant's capabilities, resource scope,
park-only flags, approval policy and remaining budget before dispatch, in a
transaction that also requires the goal to be running and, for a lead, its
epoch to be current. Knowing a capability's name SHALL NOT make it callable.

ID: REQ-capability-authorization-004
Source: RFC 0038, D7
Scope: proposed-v1

#### Scenario: A model calls a capability outside its grant
- **WHEN** an invocation calls a correctly named capability that is not in its grant
- **THEN** the broker refuses the call without contacting the domain and records the refusal

#### Scenario: A resource argument leaves the granted scope
- **WHEN** a child granted reads for one account calls the same capability for a different account
- **THEN** the broker refuses the call

### Requirement: Delegation only narrows authority
A child grant SHALL be the intersection of the lead's request and the lead's
grant, excluding `administer` capabilities. The broker SHALL refuse a request
that is not a subset of the lead's grant. A child SHALL inherit the goal's
origin and exposure, and neither a child nor its lead SHALL be able to approve
the child's effects.

ID: REQ-capability-authorization-005
Source: RFC 0038, D9 and D13
Scope: proposed-v1

#### Scenario: A lead requests more for a child than it holds
- **WHEN** a lead without `transact` asks to delegate a child with a `transact` capability
- **THEN** the broker refuses the delegation and no child is launched

### Requirement: Exposure and taint gate consequential effects
The broker SHALL mark an invocation exposed when its goal is exposed at the
invocation's start, when it receives output from a capability whose output
trust is untrusted, when it receives stamped output containing an item without
owner authority or carrying taint, or when it receives a result from an exposed
child. Exposure SHALL NOT clear, and an exposed invocation SHALL mark its goal
exposed. Domains SHALL taint records written under an exposed assertion. While
exposed, `communicate` effects SHALL require per-event owner approval except
owner-directed `notify` with link previews disabled and URLs inert, and
standing rules whose recipient is the owner or whose body is template-pinned;
`transact`, `administer` and consequential or protected `actuate` effects SHALL
require per-event owner approval; and `record` writes with owner authority
SHALL be refused. Approvals of effects requested while exposed SHALL NOT count
toward standing-rule promotion.

ID: REQ-capability-authorization-006
Source: RFC 0038, D4 and D6; RFC 0021
Scope: proposed-v1

#### Scenario: An injected instruction asks the lead to forward mail
- **WHEN** an owner goal reads an email whose body instructs the assistant to forward the inbox to an outside address, and the lead then calls the send capability
- **THEN** the send is parked for per-event owner approval showing the exact recipient and content, and nothing is sent without that approval

#### Scenario: Stored third-party text exposes a later goal
- **WHEN** an unexposed owner goal reads back a memory item that an earlier exposed goal stored from an email
- **THEN** the reading invocation becomes exposed before it can request a consequential effect

#### Scenario: Exposed sessions cannot write steering memory
- **WHEN** an exposed invocation tries to store a rule or owner-profile fact
- **THEN** the broker refuses the write

### Requirement: Domains execute model-reachable tools only with a valid broker assertion
The broker SHALL forward each call with a signed assertion naming the owning
domain as audience and binding the goal, invocation, depth, origin, exposure,
capability, a digest of the canonical JSON of the raw arguments, a unique call
identifier, the effect identifier, the approval reference, a key identifier and
an expiry. Before argument coercion and before a model-reachable handler runs,
a domain SHALL verify the signature, its own name as audience, the expiry, the
capability name, the argument digest, and that the call identifier has not
been used before, and SHALL expose the verified lineage to the handler as its
only source of session and actor context. The signing key SHALL be readable
only by the control-plane process.

ID: REQ-capability-authorization-007
Source: RFC 0038, D1 and D7
Scope: proposed-v1

#### Scenario: A forged or altered call reaches a domain
- **WHEN** a domain receives a model-reachable tool call with no assertion, an expired assertion, or arguments whose digest differs from the assertion
- **THEN** the domain refuses the call before its handler runs

#### Scenario: An assertion is replayed or redirected
- **WHEN** a valid assertion is presented a second time, or to a domain other than its audience
- **THEN** the domain refuses the call

### Requirement: Invocation tokens are single-invocation and revocable
Each invocation SHALL authenticate to the broker with a random token bound to
that invocation, stored only as a hash. The token SHALL stop working when the
invocation ends or the goal stops. A lead's token SHALL also stop working when
the goal's lead epoch advances; a child's token SHALL NOT.

ID: REQ-capability-authorization-008
Source: RFC 0038, D7 and D14
Scope: proposed-v1

#### Scenario: A replaced lead's token is used
- **WHEN** a call arrives with the token of a lead whose epoch was superseded by recovery or escalation
- **THEN** the broker refuses the call

#### Scenario: A child outlives its lead
- **WHEN** a lead is replaced by recovery while one of its children is still running
- **THEN** the child's token keeps working and the child's result is available to the new lead

### Requirement: Effects are recorded before dispatch and never replayed blindly
The broker SHALL write an effect record before dispatching any non-read call,
settle it to committed, failed, refused or unknown from the domain receipt, and
SHALL NOT re-dispatch an effect on its own except when the domain provably did
not receive it. Within a goal, a call matching the capability and argument
digest of a committed or unknown effect SHALL be refused with that effect's
reference unless the owner approves it afresh.

ID: REQ-capability-authorization-009
Source: RFC 0038, D12; RFC 0028
Scope: proposed-v1

#### Scenario: A recovered lead repeats an uncertain send
- **WHEN** a replacement lead calls a send capability with the same arguments as an effect left unknown by the previous lead
- **THEN** the broker refuses the call and returns the unknown effect's reference

### Requirement: Approved effects execute exactly as approved
When a call needs approval or its grant is park-only, the broker SHALL park the
exact capability, arguments and lineage through the approvals module. On
approval it SHALL execute that stored call, or the owner's edit of it, rather
than a call re-issued by a model, and SHALL re-check before dispatch that the
goal is not stopped. A stopped goal SHALL cancel its parked effects; a goal
whose lead has finished SHALL wait for its parked effects to resolve.

ID: REQ-capability-authorization-010
Source: RFC 0038, D11 and D13; RFC 0021
Scope: proposed-v1

#### Scenario: The model re-issues an approved call with different text
- **WHEN** the owner approves a parked message and the lead later calls the send capability with edited text
- **THEN** the approved stored message is sent once and the edited call is evaluated as a new effect under the current rules

#### Scenario: Approval arrives after Stop
- **WHEN** the owner stops a goal and an approval for one of its parked effects arrives afterwards
- **THEN** the effect is not dispatched

### Requirement: Stop fences the whole goal at once
An owner Stop SHALL lock the goal, mark it stopped and revoke every invocation
token of that goal in one transaction. The runner SHALL then terminate every
invocation process domain, and dispatched effects SHALL settle to their
receipts or unknown without being reported as undone.

ID: REQ-capability-authorization-011
Source: RFC 0038, D14
Scope: proposed-v1

#### Scenario: Stop arrives while two children are working
- **WHEN** the owner stops a goal with a running lead and two running children
- **THEN** every subsequent call or delegation from any of the three is refused and their processes are terminated

### Requirement: Goal records are reconstructable without payloads
The goal ledger SHALL record admission decision, origin, cause, exposure,
ceiling digest, grants, delegation tree, budget consumption, effects with
receipts, approvals and outcome. Telemetry SHALL carry goal, invocation, depth
and epoch identifiers and SHALL NOT carry tool arguments or results.

ID: REQ-capability-authorization-012
Source: RFC 0038, D17; RFC 0005
Scope: proposed-v1

#### Scenario: The owner asks why an action happened
- **WHEN** the owner inspects a committed effect
- **THEN** the ledger shows the goal, its origin and cause, the invocation and depth that requested it, the grant that allowed it and the approval if any

### Requirement: Caused admissions and runtime obligations cannot widen authority
An admission caused by an effect, or by a domain event an effect produced, SHALL
record the causing goal and effect, inherit the causing goal's exposure, receive
no more than the declared subscription's capability set, and be refused beyond
the configured causal chain length. An obligation created at runtime SHALL have
a capability set no larger than its creator's grant at creation, and if created
under exposure SHALL start every occurrence exposed.

ID: REQ-capability-authorization-013
Source: RFC 0038, D5
Scope: proposed-v1

#### Scenario: A child's booking triggers a finance reaction
- **WHEN** an exposed child commits a booking whose domain event wakes a declared finance subscription that needs reasoning
- **THEN** the finance goal is admitted with the booking goal as its cause, starts exposed, and holds only the subscription's declared capabilities

#### Scenario: Reactions chain past the limit
- **WHEN** a caused goal's effect would admit a further goal beyond the causal chain limit
- **THEN** admission refuses it and records the refusal

#### Scenario: An exposed lead creates a recurring obligation
- **WHEN** an exposed lead's obligation-creation effect is approved
- **THEN** the obligation's capability set is no larger than that lead's grant and each occurrence starts exposed

### Requirement: Owner decisions and private custody are unreachable from goals
Tools that approve, reject, edit or dispatch parked actions, create or promote
standing rules, or return private-custody content SHALL NOT be LLM-presentable
or grantable to any invocation.

ID: REQ-capability-authorization-014
Source: RFC 0038, D4; RFC 0021; RFC 0037
Scope: proposed-v1

#### Scenario: A lead looks for the approval tool
- **WHEN** a lead searches its catalog for a tool that approves parked actions
- **THEN** no such tool is listed or callable, and the parked effect stays pending until the owner decides through an owner channel

## Source References

- Non-Negotiable Rule 3 (cross-domain capability access is MCP-only through the control plane)
- Non-Negotiable Rule 4 (deterministic daemon; intelligence in ephemeral sessions)
- Non-Negotiable Rule 6 (governing documents; runtime-enforced depth and scope)
- `about/heart-and-soul/security.md`, Goal and Delegation Authority; Session Sandboxing; Approval Gates
- RFC 0038 (goal runtime and capability authorization)
- RFC 0017 (owner-routing safety), RFC 0021 (approvals), RFC 0027 (tool discovery), RFC 0028 (physical actuation), RFC 0037 (private custody)
