## Purpose

Enforce what every goal invocation may discover and do, independently of the
model that runs it. Authority is derived from the goal's verified origin,
narrowed by delegation and exposure, checked by the Switchboard broker on every
call, and verified again by the owning domain before a model-reachable handler
runs. The design is RFC 0038.

## ADDED Requirements

### Requirement: Sessions run in a sandbox that reaches only the broker
The goal runner SHALL launch every lead and child invocation in its own
execution domain whose network egress is limited to the broker session endpoint
and the configured model provider. An invocation SHALL NOT hold database
credentials, credential-store access, domain service addresses, or runtime
built-in shell, filesystem, web or local-server tools. A runtime adapter that
cannot prove these restrictions for a runtime and model tuple SHALL be
ineligible for goal invocations.

ID: REQ-capability-authorization-001
Source: about/legends-and-lore/rfcs/0038-goal-runtime-and-capability-authorization.md, D3
Scope: v1-mandatory

#### Scenario: A session tries to reach a domain directly
- **WHEN** an invocation process attempts a connection to a domain butler's MCP port or to PostgreSQL
- **THEN** the connection is blocked by the sandbox and no domain handler runs

#### Scenario: An adapter cannot disable built-in tools
- **WHEN** a runtime and model tuple cannot prove that its shell, filesystem and web tools are disabled
- **THEN** the runner refuses to launch goal invocations on that tuple

### Requirement: Every model-reachable capability declares authorization metadata
Every LLM-presentable tool SHALL declare its owner domain, effect class, output
trust, output sensitivity, resource parameters, safety arguments, approval
policy, idempotency mode and owner-authority flag. A tool without complete
metadata SHALL NOT be grantable, and CI SHALL fail when a registered
LLM-presentable tool lacks it.

ID: REQ-capability-authorization-002
Source: RFC 0038, D4
Scope: v1-mandatory

#### Scenario: A new module tool omits its effect class
- **WHEN** a module registers an LLM-presentable tool without an effect class
- **THEN** the catalog check fails in CI and the broker never includes the tool in a grant

### Requirement: Authority ceilings derive from verified origin
Goal admission SHALL derive the goal's authority ceiling deterministically from
server-held origin context: owner, obligation, third-party or staffer. Message
text, model output and envelope fields supplied by a sender SHALL NOT change the
origin or the ceiling. A third-party goal's ceiling SHALL exclude
`communicate`, `transact`, `actuate` and `administer` capabilities.

ID: REQ-capability-authorization-003
Source: RFC 0038, D5
Scope: v1-mandatory

#### Scenario: An email claims to come from the owner
- **WHEN** an inbound email from a non-owner address says it is from the owner and asks for a payment
- **THEN** the goal is admitted with origin `third_party` and its grant contains no `transact` capability

#### Scenario: A scheduled obligation runs
- **WHEN** a domain obligation becomes due
- **THEN** its goal receives exactly the capability set declared for that obligation in git

### Requirement: Grants bound discovery and every call
The broker SHALL list to an invocation only capabilities within its grant and
SHALL check every call against the grant's capabilities, resource scope,
approval policy and remaining budget before dispatch. Knowing a capability's
name SHALL NOT make it callable.

ID: REQ-capability-authorization-004
Source: RFC 0038, D7
Scope: v1-mandatory

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
origin and the lead's exposure, and SHALL NOT be able to approve, or have its
lead approve, its own effects.

ID: REQ-capability-authorization-005
Source: RFC 0038, D9 and D13
Scope: v1-mandatory

#### Scenario: A lead requests more for a child than it holds
- **WHEN** a lead without `transact` asks to delegate a child with a `transact` capability
- **THEN** the broker refuses the delegation and no child is launched

### Requirement: Exposure to untrusted content gates consequential effects
The broker SHALL mark an invocation exposed when its goal starts exposed, when
it receives output from a capability whose output trust is untrusted, or when it
receives a result from an exposed child. Exposure SHALL NOT clear within an
invocation. While exposed, `communicate` effects SHALL require per-event owner
approval except owner-directed `notify` and standing rules that pin every
safety argument; `transact`, `administer` and consequential or protected
`actuate` effects SHALL require per-event owner approval; and `record` writes
with owner authority SHALL be refused.

ID: REQ-capability-authorization-006
Source: RFC 0038, D6
Scope: v1-mandatory

#### Scenario: An injected instruction asks the lead to forward mail
- **WHEN** an owner goal reads an email whose body instructs the assistant to forward the inbox to an outside address, and the lead then calls the send capability
- **THEN** the send is parked for per-event owner approval showing the exact recipient and content, and nothing is sent without that approval

#### Scenario: Exposed sessions cannot write steering memory
- **WHEN** an exposed invocation tries to store a rule or owner-profile fact
- **THEN** the broker refuses the write

### Requirement: Domains execute model-reachable tools only with a valid broker assertion
The broker SHALL forward each call with a short-lived signed assertion binding
the goal, invocation, depth, origin, exposure, capability, argument digest,
effect identifier and approval reference. A domain SHALL run a model-reachable
handler only after verifying the signature, expiry, capability name and
argument digest, and SHALL expose the verified lineage to the handler as its
only source of session and actor context. The signing key SHALL be readable
only by the broker process.

ID: REQ-capability-authorization-007
Source: RFC 0038, D7
Scope: v1-mandatory

#### Scenario: A forged or altered call reaches a domain
- **WHEN** a domain receives a model-reachable tool call with no assertion, an expired assertion, or arguments whose digest differs from the assertion
- **THEN** the domain refuses the call before its handler runs

### Requirement: Invocation tokens are single-invocation and revocable
Each invocation SHALL authenticate to the broker with a random token bound to
that invocation and its goal's lead epoch, stored only as a hash. The token
SHALL stop working when the invocation ends, the goal stops, or the lead epoch
advances.

ID: REQ-capability-authorization-008
Source: RFC 0038, D7 and D14
Scope: v1-mandatory

#### Scenario: A replaced lead's token is used
- **WHEN** a call arrives with the token of a lead whose epoch was superseded by recovery
- **THEN** the broker refuses the call

### Requirement: Effects are recorded before dispatch and never replayed blindly
The broker SHALL write an effect record before dispatching any non-read call,
settle it to committed, failed, refused or unknown from the domain receipt, and
SHALL NOT re-dispatch an effect on its own except when the domain provably did
not receive it. Within a goal, a call matching the capability and argument
digest of a committed or unknown effect SHALL be refused with that effect's
reference unless the owner approves it afresh.

ID: REQ-capability-authorization-009
Source: RFC 0038, D12; RFC 0028
Scope: v1-mandatory

#### Scenario: A recovered lead repeats an uncertain send
- **WHEN** a replacement lead calls a send capability with the same arguments as an effect left unknown by the previous lead
- **THEN** the broker refuses the call and returns the unknown effect's reference

### Requirement: Approved effects execute exactly as approved
When a call needs approval, the broker SHALL park the exact capability,
arguments and lineage through the approvals module and, on approval, SHALL
execute that stored call rather than a call re-issued by a model. A stopped
goal SHALL cancel its parked approvals.

ID: REQ-capability-authorization-010
Source: RFC 0038, D13; RFC 0021
Scope: v1-mandatory

#### Scenario: The model re-issues an approved call with different text
- **WHEN** the owner approves a parked message and the lead later calls the send capability with edited text
- **THEN** the approved stored message is sent once and the edited call is evaluated as a new effect under the current rules

### Requirement: Stop fences the whole goal at once
An owner Stop SHALL, in one transaction, mark the goal stopped and revoke every
invocation token of that goal. The runner SHALL then terminate every invocation
process domain, and dispatched effects SHALL settle to their receipts or
unknown without being reported as undone.

ID: REQ-capability-authorization-011
Source: RFC 0038, D14
Scope: v1-mandatory

#### Scenario: Stop arrives while two children are working
- **WHEN** the owner stops a goal with a running lead and two running children
- **THEN** every subsequent call from any of the three is refused and their processes are terminated

### Requirement: Goal records are reconstructable without payloads
The goal ledger SHALL record admission decision, origin, ceiling digest,
grants, delegation tree, budget consumption, effects with receipts, approvals
and outcome. Telemetry SHALL carry goal, invocation, depth and epoch
identifiers and SHALL NOT carry tool arguments or results.

ID: REQ-capability-authorization-012
Source: RFC 0038, D17; RFC 0005
Scope: v1-mandatory

#### Scenario: The owner asks why an action happened
- **WHEN** the owner inspects a committed effect
- **THEN** the ledger shows the goal, its origin, the invocation and depth that requested it, the grant that allowed it and the approval if any
