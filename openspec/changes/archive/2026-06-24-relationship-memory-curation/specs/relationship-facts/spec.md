# relationship-facts

## ADDED Requirements

### Requirement: Extraction emits structured edges from relational prose

The `fact-extraction` skill SHALL emit a registry-relational edge whenever
extracted prose asserts a *standing* relationship between the subject and a
nameable entity (partner/spouse, parent/child, sibling, friend, colleague,
employer/membership): it resolves-or-creates the object entity and asserts the
edge via `relationship_assert_fact(object_kind='entity')`, in addition to any
narrative fact — rather than recording the relationship only as free-text.
Episodic mentions (one-off events, coordination) remain narrative and MUST NOT
produce an edge.

#### Scenario: Standing relationship in prose produces an edge
- **WHEN** prose asserts a durable relationship to a resolvable entity (e.g.
  "cohabiting partner with Chloe Wong")
- **THEN** the skill MUST resolve-or-create the object entity
- **AND** assert the registry-relational edge (e.g. `partner-of`) via the central
  writer
- **AND** an owner-subject edge MUST route through the carve-out for approval

#### Scenario: Episodic prose does not produce an edge
- **WHEN** prose describes a one-off event (e.g. "planned dinner with X",
  "coordinated a move with Y")
- **THEN** the skill MUST store it as a narrative fact only
- **AND** MUST NOT assert a registry-relational edge

### Requirement: Inferred relationship facts pass a confidence gate

An inferred relationship fact MUST carry a confidence value and provenance, and
an inferred **family** relationship below the confidence bar MUST be proposed for
confirmation rather than written as an active fact. ("Inferred" means derived by
the system rather than stated directly by the owner.)

#### Scenario: Low-confidence inferred family fact is not written active
- **WHEN** extraction infers a family relationship (e.g. "has a son") without
  direct owner confirmation and below the confidence bar
- **THEN** it MUST NOT be stored as an active fact
- **AND** it MUST be surfaced for owner confirmation before becoming active

#### Scenario: Inferred fact records provenance
- **WHEN** any relationship fact is stored from inference
- **THEN** it MUST record its confidence and the source it was inferred from

### Requirement: Re-home and backfill must not retract a parked write

Any re-home or backfill path MUST inspect the central writer's outcome and
retract the source memory edge-fact only when the write committed an active row.
When `relationship_assert_fact()` returns `pending_approval` (the owner carve-out
parked the write), the source memory fact MUST be left active so the edge is
never lost between stores. This corrects the backfill behavior specified in
`relational-edges-single-home`.

#### Scenario: Parked owner write leaves the source intact
- **WHEN** the backfill re-homes an edge whose subject is the owner and
  `relationship_assert_fact()` returns `pending_approval`
- **THEN** the source memory edge-fact MUST remain `validity='active'`
- **AND** the summary MUST count it as parked, distinct from migrated

#### Scenario: Committed write retracts the source
- **WHEN** the backfill re-homes an edge and `relationship_assert_fact()` commits
  an active row
- **THEN** the source memory edge-fact MUST be retracted exactly once
