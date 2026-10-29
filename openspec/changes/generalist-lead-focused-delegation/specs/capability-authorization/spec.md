## Purpose

Enforce what every goal invocation may discover and do, independently of the
model that runs it. Authority is derived from the goal's verified origin and
cause, narrowed by delegation and exposure, checked by the control-plane broker
on every call, and verified again by the owning domain before a model-reachable
handler runs. The design is RFC 0038.

## ADDED Requirements

### Requirement: Invocation labels join without widening authority
The broker and every producer/store/deferred reader SHALL implement the complete S1 versioned I/D/C label and descriptor contract, including the exact six-category set, unknown fail-closed representation, same-transaction joins, predecessor recovery and writer/exposure-cause lineage. Labels SHALL NOT authenticate an origin, grant a capability or enable a typed-value/cited-label exception. The goal aggregate SHALL remain reporting evidence, not a replacement for the invoking chain. Existing always-per-event and owner-selection requirements SHALL remain unchanged.

ID: REQ-capability-authorization-019
Source: about/legends-and-lore/rfcs/0038-goal-runtime-and-capability-authorization.md, proposed shared switch interfaces S1; bu-dhloi1.19
Scope: v1-mandatory

#### Scenario: Concurrent reads retain both labels
- **WHEN** two authorized reads with different provenance/categories race in one invocation
- **THEN** the committed invocation/store label contains both joins before either result is consumed, with no lost category

#### Scenario: Clean siblings and recovered predecessors retain their actual inputs
- **WHEN** one running sibling reads untrusted text while a separate sibling has not, and a later lead replaces the exposed predecessor
- **THEN** the independent sibling does not inherit a reporting-only aggregate, but the replacement inherits predecessor/inbox/effect labels and cannot reset exposure

#### Scenario: Legacy and deferred stamps cannot become trusted by omission
- **WHEN** a legacy Boolean-exposed row or a deferred receipt has missing/unreadable labels
- **THEN** it retains at least its old restriction, resolves unknown provenance to world and unknown sensitivity to all six categories, and never silently becomes authoritative

#### Scenario: Typed shapes do not release held semantics
- **WHEN** an exposed invocation reads a typed date or a saved citation
- **THEN** first-switch D6 exposure remains, with no reminder-date or cited-label steering exemption

#### Scenario: A failed label write aborts its effect
- **WHEN** persistence of labels/lineage fails after authorization but before commit
- **THEN** the associated write/effect does not commit without its restriction and receipt

### Requirement: Tighten-only policy versions fence the next call
The broker SHALL apply S1's owner-only CAS policy schema and deterministic constraint order on every call, emitting typed term/version receipts. The API SHALL NOT be grantable to goals or accept model/source text as owner authority. The owner SHALL be able to tighten by `never` or `always_ask` for a capability,
region or effect class. These selectors SHALL resolve within S1's existing
capability/resource `scope`: exact catalog capability identity, catalog
`owner_domain` for a region, and catalog `effect_class` for a class, with
resource narrowing retained. Matching SHALL use the current catalog on every
call, including later capabilities in a selected region/class, rather than a
one-time expansion that silently loses restrictions. Unresolved scope SHALL
refuse, not become an empty permissive match. Existing S1 fields, monotonic
versions, expiry, owner-action identity and expected-version CAS SHALL remain
the single policy representation. `always_ask` SHALL hold only an otherwise
permitted exact effect; an otherwise refused call SHALL stay refused. No owner
term SHALL grant a capability, widen resources/selection, clear labels, bypass
park-only/always-per-event, or override another restriction. The structural
constraint slots SHALL NOT invent a mode, urgency or step-up grant. Owner cards
and `/status` SHALL show the actual narrowing stage/term/version using S6's
existing surface/privacy rules; shared-room output SHALL retain mode/health
redaction. A reason string or ACK SHALL NOT confer authority. Unknown health
SHALL NOT be described as healthy or bypass its existing admission hold.

ID: REQ-capability-authorization-020
Source: RFC 0038 D4, D7 and S1; bu-dhloi1.19; bu-u33sb7; bu-dhloi1.18
Scope: v1-mandatory

#### Scenario: Never and always ask do not grant
- **WHEN** an owner term says never or always_ask for a call
- **THEN** never refuses and always_ask parks only an otherwise permitted exact call; neither bypasses origin/grant/exposure, selection or always-per-event rules

#### Scenario: Tightening wins the next effect fence
- **WHEN** a policy CAS update races an invocation between calls
- **THEN** the next call sees the committed version and records every constraining term while past committed effects remain honest

#### Scenario: Unreadable or expired policy cannot widen a grant
- **WHEN** policy state is unavailable, or a finite term expires
- **THEN** unavailable state refuses; expiry removes only that term and does not revive revoked authority

#### Scenario: Policy retry is not a second mutation
- **WHEN** the owner retries the same expected-version change or a stale client submits it
- **THEN** the same action has one receipt, while a conflicting version refuses without overwrite

#### Scenario: Scope selectors survive catalog publication
- **WHEN** never or always_ask selects a capability, region or effect class and a later catalog is published
- **THEN** the same term/version constrains all matching current capabilities and narrowed resources, including new region/class members, without granting any call
- **AND** unresolved selector/catalog state refuses rather than silently dropping the restriction

#### Scenario: Receipts identify the holding intersection without payloads
- **WHEN** a call is evaluated against catalog/origin/grant, exposure/category/slot, owner law, mode and health/admission restrictions
- **THEN** it is checked in that order, and the typed receipt names its holding/refusing stage and constraining terms with versions without message content or secret arguments
- **AND** a missing/stale invocation binding, unknown origin or unreadable stage is never reported as authorized

#### Scenario: A tightening races the final effect fence
- **WHEN** a committed policy update and a call contend at the existing goal/effect transaction fence
- **THEN** the call either commits under the old version before the tightening or is held/refused under the new version, and a retry cannot restore an older version
- **AND** already committed effects are not described as undone

#### Scenario: Private and shared status keep the same authority
- **WHEN** an owner card or `/status` explains a policy, mode or health hold on a private or shared-room surface
- **THEN** it shows the actual narrowing stage and term/version under the existing surface privacy rules, retaining mode/health redaction in shared rooms
- **AND** neither its text nor an ACK clears a restriction or treats unknown health as healthy

### Requirement: Declared gateway interfaces cannot widen workload egress
Gateway consumers SHALL implement S7's workload/profile identity, destination, redirect, static-key custody, denial and bounded-recovery interfaces. Static-key eligibility SHALL be registered and gateway-owned; it SHALL NOT grant a model, caller or unregistered analysis workload credentials, hosts or a runtime widening path.
S7's authenticated internal-peer route SHALL require exact source/destination principal, discovery identity, protocol and port with default deny. External private/IP/metadata/LAN/tailnet/redirect/rebinding refusal applies to the external gateway route and SHALL NOT become a broad private-range exemption or deny a separately authorized exact internal peer. No peer or host is authorized merely by this interface.

ID: REQ-capability-authorization-021
Source: about/legends-and-lore/rfcs/0038-goal-runtime-and-capability-authorization.md, proposed shared switch interfaces S7; bu-dhloi1.19
Scope: v1-mandatory

#### Scenario: A host or redirect escapes the declared destination
- **WHEN** an external gateway request targets an undeclared host, IP literal, private/metadata/LAN/tailnet destination, mismatched SNI or redirect
- **THEN** the request is refused with a content-free receipt and no redirected connection or credential forwarding

#### Scenario: Unavailable gateway preserves owned work
- **WHEN** the gateway is down while a connector has an uncommitted cursor
- **THEN** external egress stays denied, the cursor is retained and only finite backoff/retry is scheduled

#### Scenario: Static key eligibility is absent
- **WHEN** a caller names a key/header or endpoint lacking its registered provider and custody proof
- **THEN** no credential is supplied and there is no caller-key, other-workload or arbitrary-URL fallback

#### Scenario: Rollback does not restore an open path
- **WHEN** a profile or gateway release must be rolled back
- **THEN** affected workloads remain held/disabled unless their reviewed previous restrictive profile is proven, including additive policy union

#### Scenario: A declared internal peer retains its exact allowed leg
- **WHEN** an existing reviewed profile authorizes both authenticated principals and the exact internal control-plane or Dolt service protocol/port
- **THEN** only that identity-bound internal connection is allowed despite its private address; all ordinary service authority remains

#### Scenario: An internal address alone grants nothing
- **WHEN** a caller names an undeclared peer, wrong port/protocol, mismatched service identity or spoofed source header
- **THEN** the internal route denies it without fallback or CIDR-wide allowance

#### Scenario: External redirection cannot become internal routing
- **WHEN** an external public hostname redirects/rebinds to a private peer or metadata/LAN/tailnet/IP endpoint
- **THEN** the gateway refuses before redirected connection/credential forwarding even if another profile separately authorizes that peer internally

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
trust, output sensitivity, resource parameters (each marked selection-bound or
not; `REQ-capability-authorization-018`), safety arguments, approval
policy (including its approval mode, `default` or `always_per_event`;
`REQ-capability-authorization-017`), idempotency mode and owner-authority flag.
Every tool whose call can
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

#### Scenario: Recovery and owner steering keep their actual effect
- **WHEN** the catalog describes `correct`, an owner identity/profile/channel steering write, or `remind`
- **THEN** `correct` is `administer` with owner authority, owner steering writes declare owner authority, and `remind` is `administer` because it creates a scheduling obligation
- **AND** ordinary attributed fact recording remains `record`; no handler behavior changes

#### Scenario: A Drive write inherits shared-folder parties
- **WHEN** the catalog describes an existing Drive file, folder or move write that can inherit shared-parent recipients
- **THEN** it declares `communicate` and undeterminable inherited recipients, without inventing a sharing handler or treating unknown parties as the owner

#### Scenario: The Home Assistant gateway and attachments retain conservative declarations
- **WHEN** the catalog describes `ha_call_service` and `attachment_view`
- **THEN** the gateway declares `target` as its resource parameter and `target` and `data` as safety-critical recipient-bearing parameters
- **AND** the attachment reader declares untrusted, sensitive output; arguments outside resource and recipient selectors remain content-bearing by default; an fnmatch pattern on a content argument counts as free text

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
epoch to be current. For a selection-bound resource parameter, the same
transaction SHALL also require that the owner selection record the scope derives
from is still active (`REQ-capability-authorization-018`). Knowing a
capability's name SHALL NOT make it callable.

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
exposed call. Slot supplier authority SHALL be checked independently of type:
only owner, device or deterministic code may supply it, except that the
owner's rule-creation card may explicitly permit that rule's own recipient to
fill that exact slot. Unknown/mismatched principals and arbitrary third-party
suppliers SHALL make the rule ineligible, not be laundered through a typed
value. After reading sensitive data, a standing-rule send to a non-owner SHALL
name every applicable invocation category: `health`, `finance`, `location`,
`routine`, `relationship_notes`, `credentials`. Unknown labels SHALL retain
S1's all-category restriction. Missing coverage SHALL park an otherwise
permitted call for per-event approval; an independently refused call SHALL
stay refused. These conditions SHALL NOT release D6's held typed-value/cited-label
semantics or replace recipient/template pins, selection, current grant,
park-only or always-per-event constraints.


ID: REQ-capability-authorization-006
Source: RFC 0038, D4, D6 and D13; RFC 0021; bu-o3638f; bu-dhloi1.18
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

#### Scenario: A typed slot retains its supplier boundary
- **WHEN** a rule's typed slot is filled by its declared owner/device/code supplier or by its own recipient explicitly permitted for that slot on the creation card
- **THEN** that slot is eligible subject to every other rule and label restriction
- **AND** an arbitrary third party, unknown supplier or recipient filling a different slot makes the rule ineligible and the call parks

#### Scenario: Every sensitive category must be named
- **WHEN** an invocation reads health and finance data and sends to a non-owner under a rule naming health alone
- **THEN** the rule cannot approve the call and it parks for per-event approval
- **AND** naming both categories would only satisfy category coverage, not bypass template, slot, grant, selection or always-per-event checks

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
standing rules, create, change or revoke owner selection records
(`REQ-capability-authorization-018`), or return private-custody content SHALL
NOT be LLM-presentable or grantable to any invocation. Ordinary owner
approve/reject/edit/dispatch SHALL remain one tap after the existing current
exact-action checks. Dashboard step-up SHALL be limited to standing-rule
creation/promotion, Vouch, Contain and mode loosening, using RFC 0038 D13's
independent owner factor and atomic action-bound nonce; owner-only status
SHALL NOT extend that list. Held action implementations and factor custody
SHALL remain unchanged by this contract.

ID: REQ-capability-authorization-014
Source: RFC 0038, D4; RFC 0021; RFC 0037
Scope: v1-mandatory

#### Scenario: A lead looks for the approval tool
- **WHEN** a lead searches its catalog for a tool that approves parked actions
- **THEN** no such tool is listed or callable, and the parked effect stays pending until the owner decides through an owner channel

#### Scenario: A lead looks for a tool that selects sources
- **WHEN** a lead searches its catalog for a tool that selects sources for a subject or creates, changes or revokes an owner selection record
- **THEN** no such tool is listed or callable, and only the owner's action on the owner surface can write or revoke a selection

#### Scenario: Ordinary owner approval is still one tap
- **WHEN** the authenticated owner approves, rejects, edits or dispatches an otherwise permitted current exact action
- **THEN** the existing one-tap route applies without blanket owner-only step-up, while Stop, exact digest and current authorization remain checked

#### Scenario: A listed authority change needs independent step-up
- **WHEN** an owner requests standing-rule creation/promotion, Vouch, Contain or mode loosening without its bound dashboard confirmation
- **THEN** the transition refuses with a non-mutating dashboard handoff; Telegram identity, callback ACK or model priority cannot supply the independent factor

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

### Requirement: Catalog-declared always-per-event approval mode
A capability's catalog entry MAY declare the approval mode `always_per_event`.
The declaration SHALL travel with the tool's catalog descriptor, and no butler
configuration, owner policy or standing rule SHALL be able to remove or loosen
it. The catalog check SHALL fail in CI when a tool declares the mode but its
owning butler has no approvals gate for that tool, and such a tool SHALL NOT be
grantable.

The broker SHALL apply a capability that declares the mode as park-only in every
grant that includes it, whatever the goal's origin, cause or exposure and
whether the invocation is a lead or a child. Every call SHALL park through the
owning domain's approvals module for the owner's per-event approval. No standing
rule SHALL approve such a call, including a rule whose recipient is the owner or
that is template-pinned, and no rule SHALL be creatable for the capability. The
mode adds no capability to any origin's ceiling: it only removes automatic
approval paths.

Only these SHALL count as the per-event approval: the owner's approval of the
parked call (or of the owner's edit of it) through an owner channel; the
owner's own submit on the owner surface after seeing the exact call
(`REQ-capability-authorization-016`); and one "Approve all" tap on a decision
set that shows every item's exact call and recipients with per-item untick.
A standing rule SHALL NOT match a decision set, and an approved wait envelope
SHALL NOT pre-approve a later call to the capability. Approvals of these calls
SHALL NOT count toward standing-rule promotion.

ID: REQ-capability-authorization-017
Source: RFC 0038, D4, D5 and D13; bu-p5umi2.4 = A (always-per-event approval mode); bu-qlx055 = A (decision sets and wait envelopes); RFC 0038 owner item 3 (calendar invitation answers)
Scope: v1-mandatory

#### Scenario: A standing rule matches an unexposed owner goal's call
- **WHEN** an unexposed owner goal calls a capability that declares `always_per_event` and an active standing rule matches its tool and arguments
- **THEN** the call parks as `pending` for the owner's per-event approval and the handler does not run
- **AND** no rule is recorded as its approval and the rule's `use_count` is unchanged

#### Scenario: An exposed goal's call ignores owner-addressed and template-pinned rules
- **WHEN** an exposed invocation calls a capability that declares `always_per_event` under a standing rule that pins the recipient to the owner or is template-pinned
- **THEN** the call parks for the owner's per-event approval

#### Scenario: Third-party, obligation and child calls park
- **WHEN** a third-party goal, an obligation goal whose declared capability set includes the capability, or a child whose lead holds it calls a capability that declares `always_per_event`
- **THEN** each call parks for the owner's per-event approval, the obligation's declaration pre-approves nothing, and neither the lead nor the child can approve the child's call

#### Scenario: The owner submits the action on the owner surface
- **WHEN** the owner submits an action backed by a capability that declares `always_per_event` after seeing the exact call
- **THEN** the submit is the recorded per-event approval for that exact call
- **AND** it does not count toward standing-rule promotion suggestions

#### Scenario: Approve all on a decision set
- **WHEN** the owner taps "Approve all" on a decision set whose card shows the exact call and recipients of an always-per-event item, with per-item untick
- **THEN** that tap is the per-event approval for the item
- **AND** no standing rule matches the set or any of its always-per-event items

#### Scenario: A wait envelope never pre-approves the capability
- **WHEN** the owner approved an effect card carrying a wait envelope and the goal later calls a capability that declares `always_per_event` inside that envelope
- **THEN** the call parks for its own per-event approval

#### Scenario: A rule cannot be created for the capability
- **WHEN** the owner asks to create a standing rule, or to create a rule from a pending action, for a tool that declares `always_per_event`
- **THEN** the request is refused with a typed reason and no rule is persisted

#### Scenario: A tool declares the mode without an approvals gate
- **WHEN** a module declares `always_per_event` for a tool that its owning butler's approvals configuration does not gate
- **THEN** the catalog check fails in CI and the broker never includes the tool in a grant

#### Scenario: Butler configuration cannot remove the mode
- **WHEN** a butler's approvals configuration sets expiry or risk tier for a tool that declares `always_per_event`
- **THEN** those settings apply, the mode is unchanged, and no configuration key unsets it

#### Scenario: Calendar invitation answers declare the mode
- **WHEN** the catalog describes `calendar_respond`
- **THEN** it declares `always_per_event`, so every invitation answer needs the owner's per-event approval and no standing rule applies, as today

### Requirement: Owner selection records bind selection-bound resource scopes
A capability's catalog entry MAY declare some of its resource parameters
selection-bound. The control plane SHALL hold owner selection records. Each
record SHALL carry an id; its subject (the owning domain and a subject
reference); the capability it covers; an exact, bounded set of resource
identifiers; the authenticated owner action that wrote it (actor, owner
surface, request id and time); and its state, `active` or `revoked`, with the
revoke time and actor. A record SHALL be immutable except for a one-way revoke.
Only an authenticated owner action on the owner surface, through a
control-plane endpoint, SHALL write or revoke a record. Goals, invocations,
standing rules, approvals of parked effects, service-plane relays, domains and
connectors SHALL NOT write or revoke one. Two submits of one owner action with
the same request id SHALL resolve to one record. Only the broker and the owner
surface SHALL read records, and only the broker SHALL turn a record into a
resource scope.

A selection SHALL reach a goal only through an owner-origin admission from the
owner-surface action that wrote or referenced it, and admission SHALL record
the selection's id from server-held context. Third-party, obligation, staffer
and caused admissions SHALL NOT carry a selection. A selection id or resource
list in message text, model output, tool arguments, an ingest envelope, a
standing-rule slot or a caused admission's payload SHALL be ignored.

When the broker mints a grant that includes a capability with selection-bound
parameters, the grant's resource scope for those parameters SHALL be exactly
the identifiers of the active selection record, for that capability, that the
goal's admission references. With no such record the scope SHALL be empty, and
the capability SHALL be neither listed nor callable. A child's scope SHALL be
the intersection of its request and the lead's selection-bound scope
(`REQ-capability-authorization-005`). On every call, in the per-call
transaction (`REQ-capability-authorization-004`), the broker SHALL also require
that the record is still active; a call after a revoke commits SHALL be refused
and recorded. Reads already returned are not recalled.

A selection is not an effect: no approval, "Approve all" tap or standing rule
SHALL create, extend or substitute for one. Selection-bound calls keep their
effect class, so a selection-bound read stays a read: it is allowed under
exposure, its output trust and sensitivity apply unchanged, and a later
standing-rule send to someone other than the owner still needs to name any
sensitive category the invocation read (bu-o3638f = A). A capability with no
selection-bound parameters is unaffected.

ID: REQ-capability-authorization-018
Source: RFC 0038, D4, D5, D7 and D9; bu-p5umi2.4 = A (OQ1: a control-plane selection record written only from the owner dashboard); bu-qlx055 = A (decision sets); bu-o3638f = A (standing-rule slots and sensitive categories)
Scope: v1-mandatory

#### Scenario: The owner selects two of three sources
- **WHEN** the owner selects sources A and C for a subject on the owner surface, and the owner goal admitted from that action calls the selection-bound capability for C
- **THEN** the grant's resource scope for that capability is exactly A and C, and the call proceeds
- **AND** a call for B, a known identifier the owner did not select, is refused and recorded without contacting the domain

#### Scenario: No selection keeps the capability unavailable
- **WHEN** a goal's admission references no active selection record for a capability with selection-bound parameters
- **THEN** the capability is not listed to any of the goal's invocations, and a call naming it is refused and recorded
- **AND** an owner checklist's Finance and Travel sources therefore stay unavailable, as the adopted owner-checklist default requires

#### Scenario: Forged selection references are ignored
- **WHEN** a selection id or a list of resource identifiers appears in an owner message, model output, tool arguments, an ingest envelope, a standing-rule slot or a caused admission's payload
- **THEN** admission and the broker ignore it, and a grant's selection-bound scope derives only from a record the admission references from server-held context

#### Scenario: Non-owner and caused admissions carry no selection
- **WHEN** a third-party, obligation, staffer or caused goal needs a capability with selection-bound parameters
- **THEN** its admission carries no selection, and the capability is neither listed nor callable to it

#### Scenario: The owner revokes a selection mid-goal
- **WHEN** the owner revokes an active selection on the owner surface while a goal that references it is running
- **THEN** the next call naming any of its identifiers is refused in the per-call transaction and recorded
- **AND** reads already returned are not recalled, and Stop and lead-epoch rules are unchanged

#### Scenario: A revoke races a call
- **WHEN** a revoke of a selection and a call within its scope commit concurrently
- **THEN** the call either commits before the revoke and proceeds, or is refused after it, and no call is forwarded against a revoked record

#### Scenario: A child only narrows a selection-bound scope
- **WHEN** a lead whose selection-bound scope is A and C delegates a child, requesting C alone, or B and C
- **THEN** the request for C alone gives the child exactly C, and the request naming B is refused, so no child can add a resource

#### Scenario: Approve all and standing rules cannot select sources
- **WHEN** the owner taps "Approve all" on a decision set, or a standing rule matches a goal's effect
- **THEN** neither creates, extends or substitutes for a selection record, and the selection-bound scope is unchanged

#### Scenario: A selection-bound read keeps the sensitive-category rule
- **WHEN** an exposed owner goal reads a selected source whose output is in a sensitive category, and then sends to someone other than the owner under a standing rule that does not name that category
- **THEN** the read is allowed with its declared output trust and sensitivity
- **AND** the standing rule does not fire, and the send parks for per-event owner approval

#### Scenario: The owner submits the same selection twice
- **WHEN** the owner surface submits one selection action twice with the same request id
- **THEN** one selection record exists, and both submits resolve to it

#### Scenario: A capability without selection-bound parameters is unaffected
- **WHEN** a goal calls a capability whose catalog entry declares no selection-bound parameter
- **THEN** its grant and resource scope behave as they would with no selection records at all

## Source References

- Non-Negotiable Rule 3 (cross-domain capability access is MCP-only through the control plane)
- Non-Negotiable Rule 4 (deterministic daemon; intelligence in ephemeral sessions)
- Non-Negotiable Rule 6 (governing documents; runtime-enforced depth and scope)
- `about/heart-and-soul/security.md`, Goal and Delegation Authority; Session Sandboxing; Approval Gates
- RFC 0038 (goal runtime and capability authorization)
- RFC 0017 (owner-routing safety), RFC 0021 (approvals), RFC 0027 (tool discovery), RFC 0028 (physical actuation), RFC 0037 (private custody)
