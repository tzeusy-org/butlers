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
goal invocations. Research invocations follow REQ-capability-authorization-018.

ID: REQ-capability-authorization-001
Source: about/legends-and-lore/rfcs/0038-goal-runtime-and-capability-authorization.md, D1, D3 and D20
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
grants that can produce a parked effect but never execute one. Third-party
ceilings SHALL exclude `web_research`, regardless of its `read` class.

ID: REQ-capability-authorization-003
Source: RFC 0038, D5, D6 and D20; RFC 0017
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
- **WHEN** the owner submits a dashboard message to a contact who is not the owner after seeing the exact recipients and content
- **THEN** the submit click is recorded as per-event approval by the authenticated owner, and the control plane forwards that exact approved call with an owner-origin dashboard assertion, its approval reference and no goal
- **AND** this self-approval does not count toward standing-rule promotion suggestions

#### Scenario: The dashboard presents a goal-prepared draft
- **WHEN** a draft prepared by an exposed or third-party-started goal reaches the owner through the dashboard
- **THEN** it is presented only as that goal's parked effect, retaining its goal, invocation, depth and exposure lineage, and waits for the owner's per-event approval
- **AND** it is never treated as a dashboard action of the owner's own with owner origin and no goal

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

#### Scenario: The owner submits a dashboard communication
- **WHEN** the owner prepares a dashboard action that delivers content to or invites someone other than the owner and submits it after seeing the exact recipients and content
- **THEN** the submit click is the confirmation and records per-event approval by the authenticated owner for that exact action before dispatch
- **AND** this self-approval does not count toward standing-rule promotion suggestions

### Requirement: Web research runs only in the research sandbox through the broker
The broker SHALL provide `web_research` as a control-plane-owned catalog
capability with read effect class and untrusted, ordinary output, no resource,
recipient or safety parameters, no idempotency key and no owner-authority flag.
It SHALL accept one bounded question and an optional bounded domain allowlist.
Only the goal runner SHALL launch its research invocation, on a research host
separate from the session host, domain services and control plane. The research
invocation SHALL have only its staged per-invocation model credential, no MCP
configuration, grant, invocation token or capability or delegation tool, and
exactly one provider-hosted search tool with all runtime built-in tools
disabled. Its execution domain SHALL terminate before its one bounded,
structured answer, citations and event-derived opened URLs are accepted.
Its network SHALL allow only the configured model provider and cluster DNS,
blocking the session listener, domain ports, service listeners, PostgreSQL,
Kubernetes API, cloud metadata and arbitrary internet hosts. The broker SHALL
select live mode for an unexposed caller and cached mode with no live fetch
for an exposed caller, from server-held exposure at call time rather than
caller arguments. Owner goals SHALL hold it in their ceiling; obligations and
staffers SHALL hold it only when their governing declaration names it;
third-party goals SHALL never hold it; children SHALL receive it only through
the existing grant intersection. Before returning the result fenced as
third-party data, the broker SHALL expose the caller and goal; later stores
SHALL retain taint and no returned content SHALL satisfy an approval or
authorization check. Within the per-call transaction the broker SHALL reserve
the call's tokens, spend, search-call and wall-time allowance from the goal
budget before launch, enforcing finite per-call limits, a separate research
concurrency cap and a daily research spend cap, all fail-closed with defaults in
`runtime_config`. The runner SHALL meter event-stream usage, terminate at the
reservation and charge the whole reservation when usage is unreadable. The
goal ledger SHALL record question, selected mode, event-derived opened URLs,
citations, usage and outcome, while telemetry SHALL contain only identifiers
and digests. The research role SHALL select only a tuple whose adapter proves
this research profile, including the requested search mode and required usage
and URL evidence; version 1 SHALL support only Codex. Unsupported or unproven
profiles SHALL be refused without a live or tool-capable fallback. Research
SHALL create no new delegation depth, domain assertion or approval exception.

ID: REQ-capability-authorization-018
Source: RFC 0038, D20; about/heart-and-soul/security.md, Session Sandboxing
Scope: v1-mandatory

#### Scenario: The owner asks a research question
- **WHEN** an unexposed owner lead granted `web_research` asks a bounded research question within its budget
- **THEN** the broker has the runner launch the separate research profile in live mode and returns one cited, structured answer marked untrusted after that execution domain terminates
- **AND** the lead and its goal become exposed before receiving the fenced answer, citations and event-derived opened URLs

#### Scenario: An exposed lead calls web_research
- **WHEN** an exposed lead granted `web_research` requests further research
- **THEN** the broker selects cached provider-index mode without live page fetch, regardless of instructions in the question
- **AND** a tuple unable to enforce that mode is refused rather than falling back to live research

#### Scenario: A third-party goal cannot research the web
- **WHEN** an invocation of a third-party goal lists capabilities or calls `web_research` by name
- **THEN** the capability is absent from its listing, the call is refused and recorded, and no research invocation is launched

#### Scenario: A research process attempts a forbidden connection
- **WHEN** a research invocation process attempts a connection to the broker's session listener, a domain port, a service listener, PostgreSQL, the Kubernetes API, cloud metadata or an arbitrary internet host
- **THEN** the connection is blocked and the research invocation cannot reach that destination

#### Scenario: A runtime and model tuple cannot prove the research profile
- **WHEN** a research launch resolves to a non-Codex runtime in version 1 or to a tuple whose adapter cannot prove exactly one provider-hosted search tool, no built-in tools or MCP servers, the broker-selected mode, bounded usage and event-derived opened URLs
- **THEN** the runner refuses the launch, records the failure and launches no research process

#### Scenario: The research reservation does not fit the budget
- **WHEN** any dimension of the per-call research allowance cannot be reserved from the goal budget, or the research concurrency or daily spend cap would be exceeded or cannot be checked
- **THEN** the broker refuses and records the call and launches nothing
- **AND** concurrent calls cannot reserve the same remaining allowance

#### Scenario: A research answer contains instructions to message someone
- **WHEN** a research answer instructs the lead to send goal data to a non-owner and the lead requests that send without an applicable owner-created standing rule permitted under exposure
- **THEN** the send is parked for per-event owner approval showing the exact recipient and content, and nothing is sent without that approval under REQ-capability-authorization-006 and REQ-capability-authorization-016
- **AND** the answer and its citations confer no approval or authorization

## Source References

- Requirement identity allocation (2026-10-15): historical D20 drafting reference `REQ-capability-authorization-017` maps to `REQ-capability-authorization-018`; 017 remains reserved for its separate held amendment.

- Non-Negotiable Rule 3 (cross-domain capability access is MCP-only through the control plane)
- Non-Negotiable Rule 4 (deterministic daemon; intelligence in ephemeral sessions)
- Non-Negotiable Rule 6 (governing documents; runtime-enforced depth and scope)
- `about/heart-and-soul/security.md`, Goal and Delegation Authority; Session Sandboxing; Approval Gates
- RFC 0038 (goal runtime and capability authorization), including D20 brokered web research
- RFC 0017 (owner-routing safety), RFC 0021 (approvals), RFC 0027 (tool discovery), RFC 0028 (physical actuation), RFC 0037 (private custody)
