## ADDED Requirements

### Requirement: [TARGET-STATE] Owner checklist revisions have honest clause states

General SHALL prepare only against an exact, owner-supplied and confirmed
checklist revision. A model-segmented pasted list MUST remain a draft until
the owner confirms its ordered clauses. Each revision MUST retain the owner's
ordered active clauses and a separately visible manifest of any explicit
owner-confirmed exclusion without rewriting an earlier revision. The owner
MUST be able to inspect every active clause as exactly one of `missing`,
`needs_review`, `unavailable`, or `covered`; excluded clauses retain their
prior identity/text and confirmation provenance but have no coverage state.
No model proposal, ambiguous match, unreadable source, or silent waiver may
become `covered`. The system
MUST NOT present the supplied list as an official or exhaustive requirements
list. A packet SHALL refuse more than 200 active-plus-excluded clauses, 500
selected evidence links, or 10 accepted links for one clause instead of
silently truncating.

Checklist drafting, revision and match proposal are General `record`
capabilities. Their stored text SHALL carry the invoking goal's taint stamp, so
a draft or proposal built from captured third-party content stays tainted. Owner
confirmation makes a revision the preparation target but does not clear taint:
a revision confirmed through a goal's approved call keeps that goal's taint
stamp. Only an owner-authored revision is untainted: clauses the owner enters on the
owner surface, or edits starting from an owner-authored revision, written
through an owner-origin dashboard call (REQ-capability-authorization-007).
Editing a goal-drafted revision is an RFC 0021 edit of that goal's stored call
and keeps its lineage and taint. Both confirmation paths
exist: a goal-drafted revision is confirmed through a parked effect, and an
owner-authored revision is confirmed by the owner's own submit, which is the
server-recorded per-event approval. Confirming a revision is an owner decision
under the confirmation requirement below, never a model action.

ID: REQ-owner-checklist-preparation-001
Source: heart-and-soul/vision.md Rules 1 and 6; RFC 0038 D2, D4 and D6; bu-ijzgu6 owner-adopted text (PR #4217)
Scope: v1-mandatory

#### Scenario: Owner-supplied revision is the only preparation target
- **WHEN** the owner confirms the exact ordered clauses of a supplied checklist
- **THEN** General MUST create an immutable revision with its exact clause order and digest
- **AND** the response MUST say that official requirements have not been discovered or verified

#### Scenario: First checklist intake does not require an existing revision
- **WHEN** the verified owner supplies a new general administrative checklist with no packet or selected sources yet
- **THEN** the owner's message MUST be able to start a goal that drafts the list through General's drafting capability and seeks owner confirmation for revision r1
- **AND** neither a preexisting revision nor source selection MAY be a prerequisite to that initial intake

#### Scenario: No selected match is missing rather than silently covered
- **WHEN** a clause has no selected or proposed source match
- **THEN** its state MUST be `missing` with an owner-visible door to select evidence or revise the list
- **AND** a later model-suggested match alone MAY change it only to `needs_review`, never `covered`

#### Scenario: Ambiguity remains reviewable
- **WHEN** one clause has competing or unconfirmed proposed matches
- **THEN** its state MUST be `needs_review` with the candidate source doors and the decision needed
- **AND** the system MUST NOT choose a winner by confidence score or filename similarity

#### Scenario: Unreadable accepted source is unavailable rather than absent
- **WHEN** an owner-accepted source cannot be read or its read authority cannot be proved
- **THEN** the clause MUST be `unavailable` with a typed reason and a retry-or-reselect action
- **AND** it MUST NOT become `missing` or `covered` because the source read failed

#### Scenario: An edit or declined clause creates a new revision
- **WHEN** the owner changes, reorders, or explicitly excludes a clause
- **THEN** General MUST create a successor revision and retain the prior revision unchanged
- **AND** the successor MUST retain the excluded clause's prior identity/text and owner-confirmation provenance separately from active clause states
- **AND** later revisions MUST keep that exclusion visible until the owner explicitly reintroduces the clause
- **AND** an LLM or silent filter MUST NOT exclude a clause from the current list

#### Scenario: Packet limits refuse without losing supplied clauses
- **WHEN** a proposed revision exceeds the clause, link, or per-clause accepted-link bound
- **THEN** General MUST refuse it with a typed limit result and leave the previous revision unchanged
- **AND** no response or export MAY present a truncated subset as the whole supplied list

#### Scenario: A draft built from third-party content stays tainted
- **WHEN** a goal exposed to captured third-party content drafts clauses or proposes matches
- **THEN** the stored draft and proposals MUST carry that goal's taint stamp
- **AND** they MUST NOT become an owner-confirmed revision or match without the owner's confirmation
- **AND** after confirmation through that goal's approved call the revision keeps the taint stamp

#### Scenario: An owner-authored revision is untainted
- **WHEN** the owner enters clauses on the owner surface, or edits an owner-authored revision, and submits them
- **THEN** the owner-origin dashboard call writes the revision untainted and the submit is the per-event approval
- **AND** no goal is involved

#### Scenario: Editing a goal-drafted revision does not launder it
- **WHEN** the owner edits a revision that a goal drafted
- **THEN** the edit is an RFC 0021 edit of that goal's stored call and the revision keeps the goal's lineage and taint
- **AND** a trivial dashboard edit never makes the exposed goal's text untainted

### Requirement: [TARGET-STATE] Evidence coverage uses source-owned version and read authority

General SHALL display an owner-accepted match as `covered` only when every
required selected source was readable under the owner's authority at the last
evaluation, has an authoritative immutable version for the exact reviewed
evidence, and has an owning source capable of a commit-valid version/read
fence. The status MUST name its evaluation time and MUST NOT itself be called
a preparation receipt. Final receipt issuance MUST obtain fresh fences valid
through commit. A mutable URL, blob
locator, row ID, filename, or fetch time alone MUST yield `unavailable` with
reason `version_unverifiable`. Finance receipt and transaction truth SHALL
remain Finance-owned, and trip document truth SHALL remain Travel-owned;
General MUST obtain any specialist resolution only through that specialist's
registered capabilities and MUST NOT read peer schemas or copy raw specialist
documents into its packet.

Before even a minimal specialist label, fact, existence signal, or version is
returned, the call SHALL lie inside a goal grant whose resource scope names that
exact owner-selected source, and the owning domain SHALL verify the broker
assertion for that call; the verified call arguments SHALL bind the packet, the
current revision/digest, the clause/match, the source and the read purpose. A
caller-supplied opaque ID or selection claim MUST NOT authorize that read. A
source the owner has not selected is not in any grant and is not callable even
when its identifier is known. Until both this pre-read authority and a
source-owned version/read fence exist, a Finance or Travel match MUST remain
`unavailable` for coverage.

The read-only specialist resolution (least clause-relevant fact, immutable
version, current read authority, bounded label) is a goal-plane capability
called inside that owner-selected grant with a verified assertion. A version or
fact relayed by a goal to General is a proposal, not evidence. Source-owned
prepare and release operations are not model-reachable. General's deterministic
code SHALL obtain them only through named, allowlisted service-plane endpoints
that start no goal and no reasoning session. They accept only a version General
already holds and return only an opaque fence or a content-blind refusal: no
label, fact, existence signal or version. A clause becomes `covered` only
against a version the owning source has itself verified through prepare.

ID: REQ-owner-checklist-preparation-002
Source: heart-and-soul/architecture.md §Why MCP as the Universal Interface; RFC 0038 D2, D4 and D7; REQ-capability-authorization-004 and -007; bu-ijzgu6 owner-adopted text (PR #4217)
Scope: v1-mandatory

#### Scenario: Verifiable selected source may support a covered clause
- **WHEN** the owner accepts a clause match to a readable source with an immutable version and an available source-owned commit-fence contract
- **THEN** the as-of-evaluation clause state MAY become `covered` against that exact source version
- **AND** the source owner, opaque door, version, and reviewed minimal fact MUST remain distinguishable in the owner-only index
- **AND** a final complete receipt MUST still revalidate the source under a fresh fence at commit

#### Scenario: Mutable locator cannot certify evidence
- **WHEN** a selected source offers only a filename, row ID, mutable URL/blob locator, or fetched timestamp
- **THEN** the clause MUST be `unavailable` with reason `version_unverifiable`
- **AND** a locator or model-rendered excerpt MUST NOT be promoted into an immutable evidence version

#### Scenario: Specialist source without a commit fence stays unavailable
- **WHEN** Finance or Travel can return a record but has no reviewed source-owned version/read fence through General's commit
- **THEN** General MUST keep that selected source `unavailable` for current completeness
- **AND** it MAY display only an owner-supplied opaque door until a validated read grant permits a source-derived label or fact, without copying a receipt image or travel document body

#### Scenario: Forged or unselected specialist source is refused before disclosure
- **WHEN** a goal calls a specialist read with a known opaque source ID that is outside its grant's resource scope, or with arguments that do not match the verified assertion digest
- **THEN** the source owner MUST refuse before returning even a specialist label, existence signal, clause fact, or version
- **AND** absent, mismatched, revoked, expired, unreadable, or fabricated authority MUST yield only a content-blind typed refusal with packet revision and history unchanged

#### Scenario: The service plane is never a disclosure path
- **WHEN** General's deterministic code calls a source-owned prepare or release endpoint
- **THEN** the endpoint returns only an opaque fence or a content-blind refusal
- **AND** a model-relayed version never makes a clause `covered`

#### Scenario: Specialist read failure does not become coverage
- **WHEN** the owning specialist refuses, times out, loses read authority, or reports a changed version
- **THEN** an unreadable or unauthorized match MUST be `unavailable`, while a readable changed version MUST be `needs_review`
- **AND** General MUST NOT use direct `finance.*` or `travel.*` SQL as a fallback

#### Scenario: A source-only payment or travel claim does not decide the clause
- **WHEN** a Finance transaction or Travel document is linked without an owner-reviewed match to this clause
- **THEN** the clause MUST NOT be called `covered` or the owner eligible
- **AND** General MUST NOT infer a legal, destination, or provider requirement from that source

### Requirement: [TARGET-STATE] Owner confirmation yields a bounded preparation receipt

An LLM MAY propose matches but MUST NOT mint an owner-confirmed preparation
receipt. Confirming a checklist revision and issuing a preparation receipt SHALL
each be a General `record` capability whose catalog entry declares an
always-per-event approval mode: the broker applies it as park-only in every
grant, whatever the origin or exposure, so the call parks as a per-event owner
approval through the approvals module and SHALL NOT be satisfied by a standing
rule. That mode is an addition this change depends on and does not itself
define (see the proposal). The owner's own submit on the owner surface is an
owner-origin dashboard call and is the server-recorded per-event approval
without a goal. The confirm and issue call arguments SHALL carry the exact
checklist revision digest, ordered active clause decisions, excluded-clause
manifest and provenance, and accepted source versions, so the approval digest
binds them together with the operation identity and canonical payload digest.
The approval SHALL present the same items on the owner surface, and on approval
the stored call, or the owner's edit of it, SHALL execute rather than any call a
model re-issues. At execution General SHALL refuse with a re-prepare result when
current state differs from those arguments. General SHALL
issue one immutable receipt and exportable index only for an executed call whose
verified assertion carries the approval reference covering the exact call
digest. A caller-supplied actor, confirmation flag, source reference, or
receipt-shaped object MUST NOT supply that authority, and no owner-decision
tool is grantable to a goal. The receipt MUST mean only **complete against the
active clauses of the owner-confirmed supplied-list revision and pinned
evidence versions at preparation time, with explicit exclusions disclosed**;
it MUST NOT mean submitted, accepted, eligible, legally sufficient, or
compliant. Receipt and index content SHALL remain owner-only.

A goal that is exposed may prepare, draft and propose, but confirmation SHALL
always require the owner's per-event approval, and an approval of an effect an
exposed invocation requested SHALL NOT count toward standing-rule promotion.

ID: REQ-owner-checklist-preparation-003
Source: heart-and-soul/vision.md Rules 1 and 6; RFC 0038 D4, D6, D13 and D19; REQ-capability-authorization-007, -010 and -014; bu-n7zmbp.2; bu-ijzgu6 owner-adopted text (PR #4217)
Scope: v1-mandatory

#### Scenario: Exact confirmed complete packet receives one immutable receipt
- **WHEN** the verified owner approves the exact current revision and every clause is `covered` at commit
- **THEN** General MUST return one immutable receipt and structured index naming the revision digest and every accepted source version
- **AND** the displayed completion wording MUST be limited to the supplied list and preparation time

#### Scenario: Excluded supplied clause remains visible after completion
- **WHEN** the owner confirms r2 with active clauses A and C after explicitly excluding B from r1 containing A, B, and C
- **THEN** a complete r2 receipt and owner-only export MUST list A and C as active with their states and B separately with its prior identity/text and verified owner-exclusion provenance
- **AND** the completion claim MUST name the active r2 list and its explicit exclusion rather than imply that B was covered or never supplied

#### Scenario: Partial or unverifiable packet has no complete receipt
- **WHEN** any current clause is `missing`, `needs_review`, or `unavailable`
- **THEN** General MUST return the typed clause states and no current-complete preparation receipt
- **AND** the owner MUST see the next review, source, or revision door rather than a calm all-clear

#### Scenario: Forged confirmation cannot settle a packet
- **WHEN** a goal supplies an owner label, copied quote, invented confirmation ID, mismatched match manifest, or an approval reference that does not cover the call digest
- **THEN** General MUST refuse the receipt and leave packet revision and receipt history unchanged
- **AND** a model session MUST NOT be able to turn its own proposal into owner approval

#### Scenario: An exposed goal prepares but cannot confirm
- **WHEN** a goal exposed to captured third-party content prepares a packet and requests a receipt
- **THEN** the request MUST park for the owner's per-event approval and MUST NOT be approved by any standing rule
- **AND** the exposed goal's drafts and proposals remain tainted, including after the owner approves them

#### Scenario: Approval binds the shown state
- **WHEN** another goal re-prepares the packet after the owner saw the approval
- **THEN** execution compares current state with the approved call arguments and refuses with a re-prepare result
- **AND** no receipt is issued for state the owner did not see

#### Scenario: Export retains source trace without public disclosure
- **WHEN** the owner exports a confirmed packet index
- **THEN** the export MUST retain exact checklist revision, ordered active clause states, separately confirmed exclusions, source doors, and immutable versions
- **AND** private checklist text and specialist details MUST NOT enter a public catalog, broad cross-butler summary, or unauthenticated status route

### Requirement: [TARGET-STATE] Current completion is revision-fenced and replay-safe

General SHALL compare the approved call's arguments (checklist revision digest,
clause decisions, exclusion manifest, accepted match manifest, accepted source
versions) with the current checklist revision, owner confirmation, accepted
match manifest, and every accepted source version/read fence at the final commit
boundary, when the approved call executes. An edit, privacy change
or source change between preparation and that boundary MUST refuse a
current-complete receipt even when the checklist revision itself did not change,
and the owner SHALL be told to prepare again. Equal-revision contenders MUST NOT
both settle. The receipt capability SHALL require an idempotency key: a retry
with the same approval and canonical input MUST return its original immutable
receipt without another receipt, index, or downstream commitment; reuse of that
approval or key with different input MUST fail. Source owners lacking an atomic
commit-valid fence MUST remain unavailable rather than receive a best-effort
completeness claim. When the outcome of the receipt write is unknown, the
effect SHALL be held until reconciled: it MUST NOT be replayed on expiry and
MUST NOT be replayed without a fresh owner approval.

ID: REQ-owner-checklist-preparation-004
Source: heart-and-soul/vision.md Rules 4 and 5; RFC 0038 D12 and D13; REQ-capability-authorization-009; bu-ijzgu6 owner-adopted text (PR #4217)
Scope: v1-mandatory

#### Scenario: Checklist edit racing preparation refuses the old revision
- **WHEN** checklist revision r2 commits while a prepare for r1 is in flight
- **THEN** the r1 attempt MUST refuse current completion with a recoverable changed-revision result
- **AND** it MUST NOT rewrite r1 or commit a receipt that claims r2 is covered

#### Scenario: Accepted source changes while r1 remains current
- **WHEN** the checklist remains r1 but a required accepted source changes version or loses read authority before prepare commits
- **THEN** the prepare MUST refuse a current-complete receipt and identify the changed or unavailable source door
- **AND** no stale evidence snapshot or unchecked remote read MAY substitute for a commit-valid fence

#### Scenario: A privacy change between preparation and confirmation refuses
- **WHEN** an accepted source becomes private or unreadable between the packet being prepared and the owner's approval executing
- **THEN** the receipt MUST be refused, the clause MUST read `unavailable`, and the owner MUST be told to prepare again
- **AND** no receipt MAY disclose that source's label or fact

#### Scenario: Equal-revision contenders have one current winner
- **WHEN** two different confirmed prepare operations race against one packet revision
- **THEN** at most one MUST commit current completion and the other MUST receive a typed conflict or superseded result
- **AND** the loser MUST NOT mint an additional current receipt or commitment

#### Scenario: Replayed confirmation is idempotent on the approval
- **WHEN** a committed prepare is retried with the same approval and canonical input
- **THEN** General MUST return the original receipt and index identity without another write
- **AND** a changed payload under that approval MUST fail without mutating the original receipt

#### Scenario: Unknown receipt outcome is held until reconciled
- **WHEN** the outcome of the receipt write is unknown
- **THEN** the packet MUST show the attempt as pending and MUST NOT claim current completion
- **AND** it MUST NOT be replayed on expiry, and MUST NOT be replayed without a fresh owner approval

#### Scenario: Interrupted source fence cannot produce false completion
- **WHEN** a specialist fence expires, is revoked, or cannot be acknowledged after a crash
- **THEN** General MUST leave the attempt pending or unavailable and MUST NOT claim current completion
- **AND** any source-side lease MUST expire or be released without deleting the source or minting a duplicate packet receipt

### Requirement: [TARGET-STATE] Preparation leaves external outcomes and history separate

General SHALL preserve checklist revisions, match decisions, and immutable
receipts across archive, reopen, supersession, and feature rollback. A later
source revision or revocation MUST mark the current preparation projection
superseded or unavailable without altering the historical receipt; a historical
receipt SHALL keep its original versions and prepared-at time, and SHALL NOT
disclose a source-derived label or fact from a source that has since become
private or unreadable. Preparing an index MUST NOT close an external submission
deadline, a promise to send documents, or an application outcome. Only an
explicitly preparation-specific commitment MAY close against the exact
preparation receipt under the existing commitment evidence contract; all other
commitments require their own proof.

Checklist preparation SHALL NOT run as a prompt-mode scheduled task and SHALL
NOT depend on classify-and-route. Any scheduled or background work it needs,
such as re-evaluating the current projection after a source change, SHALL run
in deterministic job mode with no reasoning session and SHALL NOT admit a goal.

ID: REQ-owner-checklist-preparation-005
Source: heart-and-soul/vision.md Rules 1 and 4; RFC 0038 D2 and D5; bu-ijzgu6 owner-adopted text (PR #4217)
Scope: v1-mandatory

#### Scenario: Preparation does not claim submission
- **WHEN** the owner confirms and exports a complete preparation index
- **THEN** an external submission deadline and promise to send documents MUST remain open
- **AND** no API, conversation, or receipt MAY call the application submitted, accepted, or eligible

#### Scenario: Preparation-specific commitment may close with exact evidence
- **WHEN** an existing commitment explicitly asks for this packet's preparation and its exact receipt is confirmed
- **THEN** that commitment MAY close using the existing evidence-based closure contract
- **AND** a retried prepare MUST NOT create or close another commitment

#### Scenario: Later source change preserves but supersedes history
- **WHEN** an accepted source changes version or its read authority is revoked after a receipt was issued
- **THEN** the immutable receipt MUST remain retrievable with its original versions and prepared-at time
- **AND** current status MUST be superseded or unavailable, not silently still complete

#### Scenario: Historical receipt does not disclose evidence that became private
- **WHEN** an accepted source becomes private after a receipt was issued and the owner reads that receipt
- **THEN** the receipt MUST still show its versions and prepared-at time
- **AND** it MUST NOT show a source-derived label or fact from that source

#### Scenario: Archive, reopen, and rollback do not erase proof
- **WHEN** a packet is archived/reopened or new packet tools are disabled during rollback
- **THEN** prior checklist revisions, review decisions, and receipts MUST remain readable to the owner
- **AND** rollback MUST NOT claim a physical document action or external submission was reversed

#### Scenario: Background re-evaluation is a deterministic job
- **WHEN** the current projection is re-evaluated after a source change without the owner present
- **THEN** it MUST run as a deterministic job with no reasoning session and no goal admission
- **AND** no prompt-mode schedule MAY be created for it
