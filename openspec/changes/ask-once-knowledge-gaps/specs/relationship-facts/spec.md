## ADDED Requirements

### Requirement: The central writer answers matching knowledge gaps

When `relationship_assert_fact()` writes an active fact, it SHALL, on the same connection and in
the same transaction, move an `open` owner knowledge gap for the same `(subject, predicate)` to
`answerable`, recording the fact reference, a bounded value excerpt and the write's stored
authority (REQ-relationship-facts-008). A generated inverse edge SHALL answer the object entity's
open gap for the inverse predicate in the same way, citing its own committed row. It SHALL do
nothing when the schema has no knowledge-gap table, a candidate report SHALL NOT answer a gap, and
an unchanged re-assertion SHALL NOT answer a gap again. After the generalist switch the writer is
reached only through the Relationship domain's granted `record` capabilities, and the stored
authority still comes from the server-held goal origin, never from arguments or third-party text,
so a third-party report can never close a gap as established fact.

ID: REQ-relationship-facts-009
Source: bu-q7vx1q.9 S3 (merged PR #4354); bu-q7vx1q.74 inverse-edge closure (PR #4510); bu-q7vx1q.76; re-scope bu-q7vx1q.93.2 per generalist-lead-focused-delegation collision-dispositions.md; RFC 0038 D2, D4, D6, D15; REQ-relationship-facts-008; REQ-module-memory-018
Scope: v1-mandatory

#### Scenario: An assert closes a relationship-owned gap

- **WHEN** a gap is open for an entity and predicate and `relationship_assert_fact` writes that pair
- **THEN** the gap SHALL be `answerable` once the write commits, and SHALL stay `open` if the write rolls back

#### Scenario: An inverse edge answers the object entity's gap

- **WHEN** an assert generates the inverse edge for an object entity that has an open gap on the inverse predicate
- **THEN** that gap SHALL become `answerable` with the inverse row's own reference in the same transaction
