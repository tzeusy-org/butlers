## MODIFIED Requirements

### Requirement: Fact writes persist an immutable typed evidence packet

Every write that makes a row in `relationship.entity_facts` active SHALL persist
the writer's cited evidence into `relationship.fact_evidence` on the same
connection and inside the same transaction as the fact row, so a fact can never
become active with its justification missing and a rolled-back write SHALL leave
no evidence behind.

An evidence row SHALL be a typed reference — `fact`, `entity`, `url`, or `text`
— carrying a `ref`, a `note`, and the assertion's `src`, `origin`, `goal_id`,
`invocation_id`, and `action_id`. `ref` and `note` SHALL each be bounded at 512
characters, both in the writer and as a database CHECK constraint, so the ledger
is structurally incapable of holding a copy of a source message, document, or
transcript. A packet SHALL carry at most 32 references.

Evidence rows SHALL be append-only: a BEFORE UPDATE trigger SHALL reject any
in-place rewrite. Re-citing a `(kind, ref)` already recorded for the same fact
SHALL be absorbed rather than duplicated. On supersession the replacement row
SHALL receive copies of the superseded row's evidence tagged with
`carried_from`, and the superseded row SHALL keep its own evidence unchanged.

Per-assertion provenance SHALL additionally be stamped on the fact row itself as
`assert_origin` (`direct` or `approved`), `assert_goal_id`,
`assert_invocation_id`, and `assert_action_id`, read only from the verified
broker-assertion lineage, so a fact asserted with no cited evidence still
records which goal and invocation asserted it. A write whose verified lineage
carries no goal, such as an owner dashboard call, SHALL record the goal and
invocation as unknown rather than failing the write. Rows written before
cutover SHALL keep their recorded session identifier unchanged.

Source: RFC 0038 D7 and D17
Scope: v1-mandatory

#### Scenario: Direct write records evidence and provenance atomically

- **WHEN** a caller asserts a fact with typed evidence
- **THEN** the fact row and its evidence rows are visible together
- **AND** the fact row records `assert_origin='direct'` and the goal and
  invocation that authored it
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

#### Scenario: Lineage comes only from the verified assertion

- **WHEN** a fact write arrives with a goal or invocation identifier in its arguments that differs from the verified assertion lineage
- **THEN** the fact row and its evidence record the lineage from the verified assertion, not the argument values
