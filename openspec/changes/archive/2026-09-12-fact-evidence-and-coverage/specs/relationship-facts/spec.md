## ADDED Requirements

### Requirement: Fact writes persist an immutable typed evidence packet

Every write that makes a row in `relationship.entity_facts` active SHALL persist
the writer's cited evidence into `relationship.fact_evidence` on the same
connection and inside the same transaction as the fact row, so a fact can never
become active with its justification missing and a rolled-back write SHALL leave
no evidence behind.

An evidence row SHALL be a typed reference — `fact`, `entity`, `url`, or `text`
— carrying a `ref`, a `note`, and the assertion's `src`, `origin`, `session_id`,
and `action_id`. `ref` and `note` SHALL each be bounded at 512 characters, both
in the writer and as a database CHECK constraint, so the ledger is structurally
incapable of holding a copy of a source message, document, or transcript. A
packet SHALL carry at most 32 references.

Evidence rows SHALL be append-only: a BEFORE UPDATE trigger SHALL reject any
in-place rewrite. Re-citing a `(kind, ref)` already recorded for the same fact
SHALL be absorbed rather than duplicated. On supersession the replacement row
SHALL receive copies of the superseded row's evidence tagged with
`carried_from`, and the superseded row SHALL keep its own evidence unchanged.

Per-assertion provenance SHALL additionally be stamped on the fact row itself as
`assert_origin` (`direct` or `approved`), `assert_session_id`, and
`assert_action_id`, so a fact asserted with no cited evidence still records who
asserted it and from which session. A runtime session identifier that is not a
UUID SHALL be recorded as unknown rather than failing the write.

#### Scenario: Direct write records evidence and provenance atomically

- **WHEN** a caller asserts a fact with typed evidence
- **THEN** the fact row and its evidence rows are visible together
- **AND** the fact row records `assert_origin='direct'` and the session that
  authored it
- **AND** no evidence row carries a copy of the source content

#### Scenario: Rolled-back write leaves no evidence

- **WHEN** an assert runs inside a transaction that is rolled back
- **THEN** neither the fact row nor any evidence row for it exists

#### Scenario: Evidence rows cannot be rewritten

- **WHEN** an UPDATE is issued against a `relationship.fact_evidence` row
- **THEN** the write is rejected as an integrity-constraint violation

#### Scenario: Supersession carries the prior justification forward

- **WHEN** a re-assertion from a different source supersedes an active fact
- **THEN** the new active row carries copies of the prior row's evidence tagged
  with `carried_from`
- **AND** the superseded row's own evidence rows are unchanged

#### Scenario: Over-long reference is rejected before any write

- **WHEN** a caller cites a reference longer than the character bound
- **THEN** the assert raises before writing the fact or any evidence row

### Requirement: Predicate reads report an explicit composable coverage state

A read of a predicate for a subject SHALL report exactly one of `present`,
`absent_proven`, `unknown`, or `unavailable`, composed from the subject's
availability, the count of active facts for the predicate, and the coverage
receipts recorded in `relationship.fact_coverage`.

`relationship.fact_coverage` SHALL record, per `(subject, predicate, src)`, the
most recent outcome that source observed when it looked: `present` (it found a
value), `absent` (it looked and there was nothing), or `unavailable` (it could
not be consulted at all). A successful assert is itself an observation and SHALL
write a `present` receipt in the same transaction as the fact. A receipt whose
observation time precedes the stored one SHALL NOT overwrite it, so an
out-of-order replay cannot rewind coverage.

Composition SHALL be total and SHALL follow these rules: an unavailable subject
composes to `unavailable`; otherwise at least one active fact composes to
`present`; otherwise at least one `absent` receipt composes to `absent_proven`;
otherwise receipts that are all `unavailable` compose to `unavailable`; and
everything else composes to `unknown`.

**Missing coverage SHALL always compose to `unknown`.** No configuration, source
allowlist, or freshness heuristic may upgrade "we never looked" into "it is not
there"; only an explicit `absent` receipt may do that. A subject that is missing
from `public.entities` or tombstoned by a merge SHALL be reported as
`unavailable` rather than absent, because its predicate reads are unanswerable
rather than empty.

A read SHALL return the per-source receipts backing each state alongside the
state, so a caller can see why a read is proven absent rather than taking the
verdict on faith.

#### Scenario: Never-observed predicate is unknown, not absent

- **WHEN** a predicate has no active facts and no coverage receipts
- **THEN** the read reports `unknown`

#### Scenario: Observed-and-empty predicate is proven absent

- **WHEN** a source records an `absent` receipt for a predicate with no active
  facts
- **THEN** the read reports `absent_proven`
- **AND** the read includes that source's receipt

#### Scenario: Unconsultable sources do not prove absence

- **WHEN** every receipt for a predicate with no active facts is `unavailable`
- **THEN** the read reports `unavailable`, not `absent_proven`

#### Scenario: Merged-away subject is unavailable

- **WHEN** the subject entity is tombstoned by a merge
- **THEN** every predicate for it reports `unavailable`

#### Scenario: Stale replay does not rewind coverage

- **WHEN** a receipt is recorded with an observation time older than the stored
  receipt for the same `(subject, predicate, src)`
- **THEN** the stored outcome and observation time are unchanged

### Requirement: Approved fact writes execute under server-recorded provenance

When the owner carve-out or the confidence gate parks a fact write, the
asserting `src` and `observed_at` SHALL be recorded in
`relationship.fact_approval_context` — a row only the writer ever writes — and
SHALL NOT be stored in `pending_actions.tool_args`. Approval dispatch replays
`tool_args` as keyword arguments to the MCP tool, so every key stored there is
necessarily a parameter a session could also supply, and `src` selects the
carve-out's trusted-source exemption.

The parked `tool_args` SHALL carry the action's own id as `approval_action_id`,
so the replay is recognisable as the execution of an approved decision rather
than a fresh proposal.

`approval_action_id` SHALL be treated as a claim to be verified, never as
authority in itself. The writer SHALL accept it only if a `pending_actions` row
exists for this tool, is in an executable status, matches the
`(subject, predicate, object, object_kind)` quadruple being written, and has a
recorded source; any mismatch SHALL raise rather than write a fact under
provenance that cannot be substantiated. A verified approval SHALL supply the
`src`, `observed_at`, evidence, and session from the parked row, and SHALL skip
the proposal-time gates the owner already cleared so the write lands instead of
re-parking. The fact SHALL record `assert_origin='approved'` and the approving
action's id, and SHALL keep the observation time from when the fact was
proposed rather than when it was approved.

The Relationship MCP assert tool SHALL expose no `src` or `observed_at`
parameter at all, in either its schema or its Python signature.

#### Scenario: Parked action keeps the source server-side

- **WHEN** a write to the owner entity is parked for approval
- **THEN** the stored `tool_args` contains neither `src` nor `observed_at`
- **AND** `relationship.fact_approval_context` records both for that action

#### Scenario: Approved replay writes the fact instead of re-parking

- **WHEN** an approved action is replayed with its stored arguments
- **THEN** the fact is written with `assert_origin='approved'`, the parked
  evidence, and the approving action's id
- **AND** no new pending action is created

#### Scenario: Approved observation time survives the approval delay

- **WHEN** an action parked with an old `observed_at` is approved much later
- **THEN** the written fact keeps the original observation time

#### Scenario: Unverifiable approval claim is refused

- **WHEN** a caller supplies an `approval_action_id` that is unknown, still
  pending, belongs to another tool, or was approved for a different triple
- **THEN** the write raises and no fact is written

#### Scenario: Tool surface offers no way to name a source

- **WHEN** the assert tool's signature is inspected
- **THEN** it has no `src` and no `observed_at` parameter
