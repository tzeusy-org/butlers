# fleet-case-file Specification

## Purpose

Define durable, shared per-situation cases, attributed evidence, Switchboard-owned case/link mutation, bounded readers, case-scoped quiet-hours attention, safe lapse and historical backfill. This source translation preserves RFC0032 obligations; source archival does not certify whole-feature runtime conformance. Broker wiring, a dashboard write/UI container, joint objectives, delegate_act mandates, and native Fleet backup/offline restore adoption are outside this source change.

**Implementation Notes:** [Observed gap] REQ-fleet-case-file-007: list_fleet_cases currently catches all fetch exceptions and returns a clean empty page. This conflicts with response-conventions genuine-source honesty; it is not normative required behavior. The existing table-missing test injects generic RuntimeError and is retained as a historical behavior control without a qualified requirement citation. Honest outage classification/response is uncovered and allocated to bu-h40h2b.8 AC1/2/8 in the complete read-availability owner supplement. This source translation does not implement that API fix or select new dashboard DTOs.

## Requirements

### Requirement: Durable Situation Identity

The system SHALL retain a recognized situation as one durable public.fleet_cases object with a readable correlation_key, so later contributions accrete onto the same case across delivery cycles instead of depending on ephemeral insight-cluster reconstruction.

ID: REQ-fleet-case-file-001
Source: RFC 0032 §Decision and §Context; heart-and-soul/vision.md Rules 3 and 4
Scope: v1-mandatory

#### Scenario: Contribute across cycles

- **WHEN** a contributor recognizes a situation already represented by a non-closed case
- **THEN** find_open_case returns that same case and later evidence attaches to its durable ID

#### Scenario: Recognizable key

- **WHEN** a case is opened
- **THEN** its non-blank correlation_key identifies the situation in readable terms rather than being an opaque case ID

### Requirement: Case Lifecycle Posture And Terminal Outcome

A case SHALL use state open, watching, closing, or closed and posture silent, routine, active, or urgent. Only closed SHALL be terminal. An outcome SHALL be present exactly for a closed case and absent otherwise; lapsed SHALL be an outcome rather than a fifth state. A successful open SHALL start open and silent, and close SHALL require a non-blank outcome and refuse another close or posture update of that closed case.

ID: REQ-fleet-case-file-002
Source: RFC 0032 §Decision and §Slice plan S3/S5; [Observed] core_217_fleet_case_file.py and core/fleet_cases.py
Scope: v1-mandatory

#### Scenario: Terminal outcome

- **WHEN** a case is closed with an outcome
- **THEN** it has state closed, that outcome, and a closed_at timestamp
- **AND** a close without an outcome and an outcome on a non-closed case are refused

#### Scenario: Closed case is terminal

- **WHEN** a caller tries to close a closed case again or change its posture
- **THEN** the operation is refused and the original terminal case remains unchanged

#### Scenario: Vocabulary

- **WHEN** a case supplies an unsupported state or posture
- **THEN** the invalid value is rejected
- **AND** lapsed is represented by state closed and outcome lapsed

### Requirement: Single Nonclosed Case Per Correlation Key

The database SHALL permit at most one non-closed case per correlation_key, including competing opens, using the partial uniqueness of state <> closed. A closed historical case SHALL NOT prevent a new case for that key. A duplicate open SHALL report the existing case rather than create a second active case.

ID: REQ-fleet-case-file-003
Source: RFC 0032 §Decision; uq_fleet_cases_active_correlation_key
Scope: v1-mandatory

#### Scenario: Competing opens

- **WHEN** two opens target the same correlation_key while a case is non-closed
- **THEN** at most one non-closed case exists
- **AND** the losing open identifies the existing case for evidence contribution

#### Scenario: New episode after closure

- **WHEN** a new case is opened for the key of an already closed case
- **THEN** the closed row remains historical and the new non-closed row is allowed

### Requirement: Attributed Idempotent Evidence Contributions

Every butler role SHALL be able to INSERT evidence for a case it observed. The MCP contribution SHALL derive contributor from the calling butler context. The database SHALL uniquely identify evidence by (case_id, contributor, kind, ref): an identical repeat by one contributor is a no-op returning the existing evidence, while a different contributor of the same kind/ref retains its own attributed row.

ID: REQ-fleet-case-file-004
Source: RFC 0032 §Decision and §Write authority; §Slice plan S3; [Observed] core_tools/_fleet_cases.py
Scope: v1-mandatory

#### Scenario: Repeated observation

- **WHEN** one butler repeats the same case_id, kind, and ref
- **THEN** the existing evidence row is returned with newly_recorded false
- **AND** its payload is not replaced by the repeat

#### Scenario: Two contributors

- **WHEN** two distinct butlers report the same kind/ref for a case
- **THEN** there are two attributed evidence rows, one per contributor

#### Scenario: Server context attribution

- **WHEN** contribute_case_evidence is called
- **THEN** the evidence contributor comes from the registered calling butler context

### Requirement: Immutable Evidence Corrections

Recorded fleet_case_evidence SHALL never be updated. A correction SHALL add a new evidence row with a new contribution identity rather than mutate the recorded observation.

ID: REQ-fleet-case-file-005
Source: RFC 0032 §Write authority
Scope: v1-mandatory

#### Scenario: Correction preserves original

- **WHEN** a contributor corrects a previously recorded observation
- **THEN** the original evidence remains unchanged and the correction is a distinct new evidence row

#### Scenario: Mutation refused

- **WHEN** a runtime butler attempts UPDATE of existing evidence
- **THEN** the existing observation is not changed

### Requirement: Switchboard Case And Link Write Authority

Only effective current_user butler_switchboard_rw SHALL INSERT or UPDATE fleet_cases or fleet_case_links. Enabled and forced row-level security SHALL enforce that authority despite broad public GRANTs and bootstrap reruns. All roles SHALL retain SELECT as a shared case-file read surface; access to a peer private schema SHALL NOT be conferred by this contract.

ID: REQ-fleet-case-file-006
Source: RFC 0032 §Write authority and §Alternatives rejected; heart-and-soul/vision.md Rule 3
Scope: v1-mandatory

#### Scenario: Wrong-role insert or update

- **WHEN** a non-Switchboard runtime role attempts a case or link mutation
- **THEN** INSERT is refused and UPDATE cannot change the row
- **AND** the case and link remain readable

#### Scenario: Bootstrap preserves fence

- **WHEN** scripts/init-db.sql re-widens public DML grants after migration
- **THEN** only current_user butler_switchboard_rw can still insert or update cases and links

#### Scenario: Shared read boundary

- **WHEN** a butler reads a case file
- **THEN** it reads the public case/evidence/link surface without receiving peer private-schema authority

### Requirement: Bounded Switchboard Case Read Surface

The Switchboard API SHALL expose read-only GET /api/switchboard/cases with cursor pagination and GET /api/switchboard/cases/{case_id} with the case evidence and links. The existing read contract SHALL preserve state/posture filtering, updated_at/id descending list order, limit 1..200 with default 50, oldest-first detail history capped at 500 evidence and 500 links, and the existing validation responses and detail-unavailability response. Genuine case-source failure SHALL be distinguishable from a healthy empty result under the governing response-conventions classification; legitimately absent optional schema SHALL be distinguished from timeout, connection, permission or other genuine source failure. This contract does not select a new availability DTO or create a dashboard write surface.

ID: REQ-fleet-case-file-007
Source: RFC 0032 §Slice plan S2; docs/api_and_protocols/response-conventions.md Fleet-wide convention and Classify before flagging; [Observed] roster/switchboard/api/router.py and models.py
Scope: v1-mandatory

#### Scenario: Read list

- **WHEN** a valid filtered list request is made
- **THEN** a bounded descending keyset page and next_cursor/has_more are returned

#### Scenario: Read detail

- **WHEN** a valid existing case ID is requested
- **THEN** the case, evidence and links are returned as bounded oldest-first histories

#### Scenario: Validation and detail failure

- **WHEN** a detail request has a malformed ID, a missing case, or unavailable case storage
- **THEN** it returns 422, 404, or 503 respectively

#### Scenario: Honest source availability

- **WHEN** the list case source raises or is unreachable
- **THEN** genuine source failure is reported as unavailable rather than a truthful healthy empty page
- **AND** a legitimately absent optional schema is classified separately from a timeout, connection or permission failure

### Requirement: Fleet Case Tool Admission And Posture Forwarding

The fleet_cases core group SHALL provide find_open_case, open_case, contribute_case_evidence, propose_case_posture, close_case, record_case_link, and read_case wherever the governing runtime tool-surface contract enables the group, including Switchboard staffer registration. Reads and evidence INSERT SHALL use the caller own pool; non-Switchboard case/link writes SHALL forward through Switchboard route to its own writer pool. Posture proposals SHALL be plain last-write-wins at Switchboard, without quorum, decay, or per-butler cooldown. Typed domain-event evidence SHALL accept case references.

ID: REQ-fleet-case-file-008
Source: RFC 0032 §Decision posture and §Slice plan S3/S7; governing active add-runtime-tool-surface-discovery REQ-core-daemon-002; [Observed] EVIDENCE_KINDS
Scope: v1-mandatory

#### Scenario: Non-Switchboard write

- **WHEN** a non-Switchboard butler opens, proposes posture, closes, or records a link
- **THEN** the write is forwarded to Switchboard instead of mutating case/link rows through a peer role

#### Scenario: Switchboard writer reachable

- **WHEN** Switchboard staffer has fleet_cases enabled by governing group policy
- **THEN** all seven tools are registered and its case/link writes use its own pool

#### Scenario: Posture proposal

- **WHEN** Switchboard accepts a valid proposal on a non-closed case
- **THEN** that write sets the posture in last-write-wins order with no voting mechanism

#### Scenario: Typed case reference

- **WHEN** a domain-event reaction includes valid evidence kind case with a case ref
- **THEN** the shared typed-evidence validator accepts that kind

### Requirement: One Urgent Quiet Window Bypass Per Situation

The case attention primitive SHALL record at most one urgent quiet-hours bypass per correlation_key per quiet window, independent of the contributing butler/call. It SHALL do nothing for a closed case, non-urgent posture, or inactive quiet hours. Contribute/propose paths SHALL use this primitive; duplicate evidence SHALL not re-evaluate attention. The case-scoped dedup key SHALL remain separate from candidate keys, with existing fleet_case: namespacing, and the check/record SHALL be serialized atomically for the same case key.

ID: REQ-fleet-case-file-009
Source: RFC 0032 §Slice plan S4; [Observed] case_attention_dedup_key/evaluate_case_attention; closed bu-zss8w correction
Scope: v1-mandatory

#### Scenario: Multiple notices

- **WHEN** concurrent urgent contributions/proposals share a correlation_key within one quiet window
- **THEN** exactly at most one recorded bypass is allowed and the others report already_bypassed_this_window

#### Scenario: No attention need

- **WHEN** the case is closed, steps down from urgent, or quiet hours are inactive
- **THEN** no bypass is recorded

#### Scenario: Independent cases

- **WHEN** one case dedup key is locked
- **THEN** a different case key can still record its bypass without waiting on that lock

#### Scenario: Repeat evidence

- **WHEN** an existing evidence contribution is repeated
- **THEN** its no-op does not trigger another case attention evaluation

### Requirement: Switchboard Owned Atomic Stale Case Lapse

Switchboard SHALL run fleet_case_lapse_sweep daily at 04:10 UTC. The sweep SHALL close only non-closed silent/routine cases whose updated_at and latest evidence are older than the seven-day default staleness window, writing state closed, outcome lapsed, and closed timestamps. Eligibility and mutation SHALL be one atomic UPDATE. Active/urgent, recently updated/evidenced, and already closed cases SHALL remain untouched; reruns SHALL never resurrect cases.

ID: REQ-fleet-case-file-010
Source: RFC 0032 §Slice plan S5; [Observed] scheduled_jobs.py and roster/switchboard/butler.toml
Scope: v1-mandatory

#### Scenario: Eligible stale case

- **WHEN** the sweep sees a non-closed silent/routine case with no recent update or evidence
- **THEN** it closes the case with outcome lapsed and a closed timestamp

#### Scenario: Spared or terminal case

- **WHEN** a case is active/urgent, recently updated/evidenced, or already closed
- **THEN** the sweep leaves it unchanged

#### Scenario: Idempotent sweep

- **WHEN** the sweep reruns after lapsing eligible cases
- **THEN** no case is reopened or lapsed again

### Requirement: Historical Closed Only Backfill And Honest Source

The one-time/idempotent-rerun backfill SHALL use resolved public.owner_conditions episodes rather than fabricate discarded insight-cluster history. Each resolved episode SHALL map to backfill:owner_condition:{source}:{fingerprint}:{episode}, preserving first_detected_at/resolved_at as case opened/closed timestamps and resolution_reason as outcome or resolved as its fallback. Its first INSERT SHALL hard-code state closed and silent posture; unresolved episodes SHALL not become cases. Reruns SHALL skip existing correlation keys and SHALL never create active, watching, or closing historical cases. It SHALL be an operator script using the Switchboard writer role, not a scheduled job.

ID: REQ-fleet-case-file-011
Source: RFC 0032 §Slice plan S6 and §Non-goals; [Observed] backfill_from_owner_conditions and scripts/backfill_fleet_cases.py
Scope: v1-mandatory

#### Scenario: Resolved episode

- **WHEN** an already resolved owner-condition episode is backfilled
- **THEN** one closed historical case retains its episode identity, timestamps and resolution outcome

#### Scenario: Unresolved episode or rerun

- **WHEN** an episode is unresolved or its historical correlation key already exists
- **THEN** the run creates no new case for that episode

#### Scenario: Safe first write

- **WHEN** historical backfill writes a case
- **THEN** the case is closed with a valid outcome from its first insertion and cannot resurrect a live case

#### Scenario: Dry run

- **WHEN** the operator requests dry-run
- **THEN** only the resolved-episode count is reported and no backfill writes are performed

### Requirement: Generic Unique Links And Admitted Three Ledger Bindings

The fleet_case_links storage model SHALL identify each binding uniquely by (case_id, link_kind, ref), retaining the generic ability to represent another ledger entry, including another case. The shipped S7 write_case_link/record_case_link surface SHALL admit only insight_candidate, owner_condition, and attention_record, with a non-blank ref identifying that ledger own entry. Repeating a binding SHALL return the existing row without duplication. A case read SHALL surface its links. No speculative correlation or additional automatic case-to-case producer SHALL be inferred from the generic storage shape.

ID: REQ-fleet-case-file-012
Source: RFC 0032 §Decision fleet_case_links and §Slice plan S7; [Observed] LINK_KINDS and core_217 schema
Scope: v1-mandatory

#### Scenario: Three admitted ledgers

- **WHEN** a caller explicitly binds a case to an insight candidate, owner condition, or attention record
- **THEN** the stored link uses that ledger entry ID and appears in the case read

#### Scenario: Repeat binding

- **WHEN** the same case_id/link_kind/ref is submitted again
- **THEN** the existing link is returned with newly_recorded false

#### Scenario: Unsupported S7 tool kind

- **WHEN** record_case_link receives another kind including a speculative case-to-case kind
- **THEN** the S7 tool refuses it before writing
- **AND** the generic schema is not narrowed into a three-kind database CHECK by source condensation

### Requirement: Existing Call Site Genuine Reference Links

New evidence with a reserved S7 kind/ref SHALL attempt its explicit ledger binding through the sanctioned Switchboard write path. A newly recorded urgent bypass SHALL bind the case to the actual attention_ledger ID created by that call, from both contribution and posture proposal paths. Missing attention IDs, ordinary unreserved evidence, or duplicate reports SHALL NOT fabricate a link. The binding SHALL use existing call sites without a new scheduled linker or automatic broker calls.

ID: REQ-fleet-case-file-013
Source: RFC 0032 §Slice plan S7; [Observed] core_tools/_fleet_cases.py contribution/proposal paths
Scope: v1-mandatory

#### Scenario: Explicit ledger evidence

- **WHEN** a newly recorded evidence contribution cites one of the three reserved kinds with its ledger entry ID
- **THEN** the case link is attempted using that kind/ref and Switchboard role

#### Scenario: Actual attention link

- **WHEN** a contribution or posture proposal records an urgent bypass with an actual attention ledger ID
- **THEN** its attention_record link uses that ID

#### Scenario: No fabricated reference

- **WHEN** a bypass has no recorded ledger ID, evidence is unreserved, or the report is a no-op
- **THEN** no link is fabricated for that absent reference

### Requirement: Backfill Repairs Old Episode Links

The owner-condition backfill SHALL write the owner_condition link using the source episode own ID for every case it touches, including existing historical cases from before S7. A rerun SHALL repair a missing old-case link without duplicating the case or link.

ID: REQ-fleet-case-file-014
Source: RFC 0032 §Slice plan S7 superseding historical S6 no-link stage
Scope: v1-mandatory

#### Scenario: Repair pre-S7 historical case

- **WHEN** a rerun encounters an already-backfilled historical case with no source episode link
- **THEN** the existing case receives the missing owner_condition link and remains the same closed historical case

#### Scenario: Repeat completed repair

- **WHEN** the same episode is backfilled again
- **THEN** there is still only one case and one source-episode link
