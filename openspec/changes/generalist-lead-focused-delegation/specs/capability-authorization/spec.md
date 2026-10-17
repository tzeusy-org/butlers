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
Scope: v1-mandatory

#### Scenario: A session tries to reach a domain directly
- **WHEN** an invocation process attempts a connection to a domain butler's MCP port, a control-plane service listener or PostgreSQL
- **THEN** the connection is blocked by the sandbox and no handler runs

#### Scenario: An adapter cannot disable built-in or provider-hosted tools
- **WHEN** a runtime and model tuple cannot prove that its shell, filesystem, web and provider-hosted tools are disabled
- **THEN** the runner refuses to launch goal invocations on that tuple

### Requirement: Every model-reachable capability declares authorization metadata
Every LLM-presentable tool SHALL declare its owner domain, effect class, output
trust, output sensitivity, resource parameters, safety arguments, approval
policy, idempotency mode and owner-authority flag. Every tool whose call can
deliver content to, or invite, another party, whatever its effect class, SHALL
also declare the arguments that name those parties (recipients, attendees,
invitees, shares), or declare them undeterminable when its arguments cannot
name them. A tool without complete
metadata SHALL NOT be grantable, and CI SHALL fail when a registered
LLM-presentable tool lacks it. A capability whose output can include data that
a model-reachable `record` capability, a connector or a third party can write
SHALL NOT declare trusted output.

ID: REQ-capability-authorization-002
Source: RFC 0038, D4
Scope: v1-mandatory

#### Scenario: A read over writable notes is declared trusted
- **WHEN** a module declares trusted output for a read capability over a table that one of its `record` capabilities writes
- **THEN** the catalog check fails in CI

#### Scenario: A new module tool omits its effect class
- **WHEN** a module registers an LLM-presentable tool without an effect class
- **THEN** the catalog check fails in CI and the broker never includes the tool in a grant

#### Scenario: A calendar write omits its attendee arguments
- **WHEN** a module registers a calendar tool that can add attendees but declares no party arguments
- **THEN** the catalog check fails in CI

### Requirement: Authority ceilings derive from verified origin
Goal admission SHALL derive the goal's authority ceiling deterministically from
server-held origin context: owner, obligation, third party or staffer. Message
text, model output and sender-supplied envelope fields SHALL NOT change the
origin or the ceiling. A message SHALL be owner-origin only when owner-channel
authorization verifies its channel and sender. Device and telemetry feeds that
carry no message from a person SHALL wake reasoning only through a declared
subscription and SHALL be admitted with origin `obligation` under that
subscription's capability set; feed fields a person other than the owner can
author SHALL be untrusted output. Admission SHALL decide a goal's starting
exposure from server-held facts alone: a third-party goal SHALL start exposed;
an owner goal whose ingest envelope carries any connector-marked forwarded,
quoted or embedded span (`REQ-connector-base-spec-004`) SHALL start exposed,
with each span fenced as data in the goal input; a staffer goal whose inputs
include third-party content or error payloads, and every QA goal, SHALL start
exposed; every occurrence of a runtime-created obligation created under
exposure SHALL start exposed; and a domain-published admission input with no
goal lineage, such as an event-bus payload or a feed record, SHALL start its
goal exposed unless its publishing domain stamps it as derived only from
untainted owner-authority records, a missing or unreadable stamp counting as
exposed. Ingest envelopes take their starting exposure from the origin and
span rules above, and an obligation's declared prompt is trusted unless the
obligation was created under exposure. A third-party goal's ceiling
SHALL exclude `transact`, `actuate` and `administer` capabilities and SHALL
hold `communicate` capabilities, other than owner `notify`, only as park-only
grants that can produce a parked effect but never execute one.

ID: REQ-capability-authorization-003
Source: RFC 0038, D5 and D6; RFC 0017
Scope: v1-mandatory

#### Scenario: An email claims to come from the owner
- **WHEN** an inbound email from an address that owner-channel authorization does not verify says it is from the owner and asks for a reply to an outside address
- **THEN** the goal is admitted with origin `third_party`, and the reply can at most be parked for per-event owner approval

#### Scenario: The owner forwards a phishing email
- **WHEN** the owner forwards a third-party email to the assistant through a verified owner channel and asks what to do about it
- **THEN** the goal is admitted with origin `owner` but starts exposed, with the forwarded content fenced as data
- **AND** a send it requests to anyone other than the owner is parked for per-event approval, and it cannot write a steering record

#### Scenario: A scheduled obligation runs
- **WHEN** a domain obligation declared in git becomes due
- **THEN** its goal receives exactly the capability set declared for that obligation

#### Scenario: A device state change wakes a declared subscription
- **WHEN** a Home Assistant state change matches a subscription declared in git whose capability set includes `actuate`
- **THEN** the goal is admitted with origin `obligation` and exactly that capability set, and its actuation follows the RFC 0028 risk map
- **AND** a device name or note in the event that someone other than the owner could set is untrusted output, so reading it exposes the invocation

#### Scenario: Staffer admission uses a closed Git-owned contract
- **WHEN** trusted infrastructure admits a staffer using its versioned Git-owned declaration
- **THEN** the declaration identifies staffer, owning domain, contract and pinned catalog digests, exact capability IDs and resource sets, and execute or park-only restriction
- **AND** missing, stale or unreadable declarations/catalogs, wildcards, duplicate/unknown IDs, owner-decision, custody or transact entries refuse admission
- **AND** intersection only narrows resources and preserves park-only; no prompt confers authority


### Requirement: Grants bound discovery and every call
The broker SHALL list to an invocation only capabilities within its grant and
SHALL check every call against the grant's capabilities, resource scope,
park-only flags, approval policy and remaining budget before dispatch, in a
transaction that also requires the goal to be running and, for a lead, its
epoch to be current. Knowing a capability's name SHALL NOT make it callable.

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
origin and exposure, and neither a child nor its lead SHALL be able to approve
the child's effects.

ID: REQ-capability-authorization-005
Source: RFC 0038, D9 and D13
Scope: v1-mandatory

#### Scenario: A lead requests more for a child than it holds
- **WHEN** a lead without a send capability asks to delegate a child holding that send capability
- **THEN** the broker refuses the delegation and no child is launched

### Requirement: Exposure and taint gate consequential effects
The broker SHALL mark an invocation exposed when its goal is exposed at the
invocation's start, when it receives output from a capability whose output
trust is untrusted, when it receives stamped output containing an item without
owner authority or carrying taint, or when it receives a result from an exposed
child. Exposure SHALL NOT clear, and an exposed invocation SHALL mark its goal
exposed. Domains SHALL taint records written under an exposed assertion. While
exposed, every `communicate` effect SHALL require per-event owner approval,
except `notify` to the owner with link previews disabled and URLs rendered
inert, and a standing rule whose recipient is the owner or that is
template-pinned; `administer` and consequential or protected `actuate` effects
SHALL require per-event owner approval; and `record` writes with owner
authority SHALL be refused. Approvals of effects requested while exposed SHALL
NOT count toward standing-rule promotion. Exposure SHALL NOT relax the
non-owner communication rule (`REQ-capability-authorization-016`).

A standing rule SHALL count as template-pinned only when its argument
constraints fix every content-bearing argument (body, subject, title,
description, caption, attachment and any other argument whose value reaches
the recipient as text or content) to an exact value or to an owner-authored
template whose slots accept only typed values such as dates, amounts and
identifiers, never free text. A rule that leaves any content-bearing argument
unconstrained, or whose template has a free-text slot, SHALL NOT exempt an
exposed call.

ID: REQ-capability-authorization-006
Source: RFC 0038, D4, D6 and D13; RFC 0021
Scope: v1-mandatory

#### Scenario: An injected instruction asks the lead to forward mail
- **WHEN** an owner goal reads an email whose body instructs the assistant to forward the inbox to an outside address, and the lead then calls the send capability
- **THEN** the send is parked for per-event owner approval showing the exact recipient and content, and nothing is sent without that approval

#### Scenario: Stored third-party text exposes a later goal
- **WHEN** an unexposed owner goal reads back a memory item that an earlier exposed goal stored from an email
- **THEN** the reading invocation becomes exposed before it can request a consequential effect

#### Scenario: Exposed sessions cannot write steering memory
- **WHEN** an exposed invocation tries to store a rule or owner-profile fact
- **THEN** the broker refuses the write

#### Scenario: A rule that pins the body but not the subject does not exempt an exposed send
- **WHEN** an exposed invocation sends an email to a third party under a standing rule that fixes the body by template but leaves the subject unconstrained
- **THEN** the send is parked for per-event owner approval

#### Scenario: A template with a free-text slot does not exempt an exposed send
- **WHEN** an exposed invocation sends a message under a standing rule whose owner-authored template has a slot that accepts free text
- **THEN** the send is parked for per-event owner approval

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
only by the control-plane process. A dashboard call to a model-reachable tool
SHALL reach the domain only through the control plane's service listener,
authenticated by the signed dashboard control client, and SHALL be forwarded
with an assertion whose origin is `owner`, principal `dashboard` and goal
null; such calls SHALL follow the two owner rules
(`REQ-capability-authorization-015`, `REQ-capability-authorization-016`).

ID: REQ-capability-authorization-007
Source: RFC 0038, D1 and D7
Scope: v1-mandatory

#### Scenario: A forged or altered call reaches a domain
- **WHEN** a domain receives a model-reachable tool call with no assertion, an expired assertion, or arguments whose digest differs from the assertion
- **THEN** the domain refuses the call before its handler runs

#### Scenario: An assertion is replayed or redirected
- **WHEN** a valid assertion is presented a second time, or to a domain other than its audience
- **THEN** the domain refuses the call

#### Scenario: The dashboard messages a contact
- **WHEN** the owner uses the dashboard to send a message to a contact who is not the owner
- **THEN** the control plane forwards the call with an owner-origin dashboard assertion and no goal, and the message is parked for owner approval unless an owner-created standing rule matches

### Requirement: Invocation tokens are single-invocation and revocable
Each invocation SHALL authenticate to the broker with a random token bound to
that invocation, stored only as a hash. The token SHALL stop working when the
invocation ends or the goal stops. A lead's token SHALL also stop working when
the goal's lead epoch advances; a child's token SHALL NOT.

ID: REQ-capability-authorization-008
Source: RFC 0038, D7 and D14
Scope: v1-mandatory

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
Scope: v1-mandatory

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
Scope: v1-mandatory

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
Scope: v1-mandatory

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
Scope: v1-mandatory

#### Scenario: The owner asks why an action happened
- **WHEN** the owner inspects a committed effect
- **THEN** the ledger shows the goal, its origin and cause, the invocation and depth that requested it, the grant that allowed it and the approval if any

### Requirement: Caused admissions and runtime obligations cannot widen authority
An admission caused by an effect, or by a domain event an effect produced, SHALL
record the causing goal and effect, inherit the causing goal's exposure, receive
no more than the intersection of the declared subscription's capability set and
the causing goal's origin ceiling, and be refused beyond the configured causal
chain length. An obligation created at runtime SHALL have
a capability set no larger than its creator's grant at creation, and if created
under exposure SHALL start every occurrence exposed.

ID: REQ-capability-authorization-013
Source: RFC 0038, D5
Scope: v1-mandatory

#### Scenario: A child's recorded receipt triggers a finance reaction
- **WHEN** an exposed child records an expense receipt whose domain event wakes a declared finance subscription that needs reasoning
- **THEN** the finance goal is admitted with the receipt's goal as its cause, starts exposed, and holds only the subscription's declared capabilities

#### Scenario: A third-party goal's effect wakes a privileged subscription
- **WHEN** a third-party goal records a fact that wakes a subscription whose declared set includes `actuate`
- **THEN** the caused goal holds no `actuate` capability, because its ceiling is limited by the third-party origin

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
Scope: v1-mandatory

#### Scenario: A lead looks for the approval tool
- **WHEN** a lead searches its catalog for a tool that approves parked actions
- **THEN** no such tool is listed or callable, and the parked effect stays pending until the owner decides through an owner channel

### Requirement: Transact capabilities are disabled
The broker SHALL NOT grant or list a `transact` capability to any goal or
invocation, including as a park-only grant, whatever the goal's origin, cause
or exposure. Enabling `transact` SHALL require an owner decision recorded as an
amendment to RFC 0038.

ID: REQ-capability-authorization-015
Source: RFC 0038, D4, D5, D9 and owner decision 8
Scope: v1-mandatory

#### Scenario: The owner asks the assistant to pay a bill
- **WHEN** an owner goal needs a payment that only a `transact` capability could make
- **THEN** no `transact` capability is listed to any invocation, a call naming one is refused and recorded, and nothing is dispatched
- **AND** the lead reports what the owner would need to do

#### Scenario: A lead asks for a child that can book
- **WHEN** a lead requests a child grant that includes a `transact` capability
- **THEN** the broker refuses the delegation and no child is launched

### Requirement: Communication with anyone other than the owner requires owner approval
For every call to a capability that declares party arguments, whatever its
effect class, the broker SHALL resolve each named party with the `core-notify`
owner-resolution rule: a party is the owner only when its identifier resolves
uniquely to the live, unmerged, undeleted owner entity. A call that names no
party SHALL be owner communication only when the capability declares the owner
as its default recipient, as `notify` does. A call with any other party,
including an unknown, ambiguous or unresolvable one, or to a capability whose
parties are declared undeterminable, SHALL be non-owner communication. The
broker SHALL dispatch non-owner communication only under per-event owner
approval or a matching standing rule the owner created, and no origin, grant,
exposure state, lead or child SHALL bypass this. The owning domain's own
approval gate SHALL continue to apply, and Messenger's delivery gate SHALL
apply the same rule to deterministic service-plane senders.

ID: REQ-capability-authorization-016
Source: RFC 0038, D4, D6, D7, D13 and owner decision 9; `core-notify` Role-Based Approval Gating for Notify; RFC 0021
Scope: v1-mandatory

#### Scenario: An unexposed owner goal emails a friend
- **WHEN** an owner goal that has read no third-party content calls a send capability addressed to a known contact who is not the owner, and no standing rule matches
- **THEN** the message is parked for per-event owner approval and nothing is sent until the owner approves

#### Scenario: A standing rule covers a recurring message
- **WHEN** an unexposed goal sends a message that matches a standing rule the owner created for that recipient
- **THEN** the message is sent under that rule and the effect records the rule as its approval

#### Scenario: The recipient is ambiguous
- **WHEN** a send capability's recipient identifier resolves to more than one entity, to no entity, or cannot be looked up
- **THEN** the broker treats the call as non-owner communication and parks it for owner approval

#### Scenario: A calendar event invites someone other than the owner
- **WHEN** an unexposed owner goal creates or updates a calendar event whose attendees include anyone other than the owner, and no standing rule matches
- **THEN** the write is parked for per-event owner approval showing the attendees, and no invitation or update notification is sent until the owner approves

#### Scenario: A reply whose recipients the arguments cannot name
- **WHEN** a goal calls a reply capability declared undeterminable, with no party in its arguments
- **THEN** the broker treats the call as non-owner communication and parks it for owner approval

#### Scenario: A message to the owner
- **WHEN** an unexposed goal sends a message whose recipient resolves uniquely to the owner
- **THEN** the message is exempt from the non-owner rule; the tool's approval policy still applies

## Source References

- Non-Negotiable Rule 3 (cross-domain capability access is MCP-only through the control plane)
- Non-Negotiable Rule 4 (deterministic daemon; intelligence in ephemeral sessions)
- Non-Negotiable Rule 6 (governing documents; runtime-enforced depth and scope)
- `about/heart-and-soul/security.md`, Goal and Delegation Authority; Session Sandboxing; Approval Gates
- RFC 0038 (goal runtime and capability authorization)
- RFC 0017 (owner-routing safety), RFC 0021 (approvals), RFC 0027 (tool discovery), RFC 0028 (physical actuation), RFC 0037 (private custody)
