## ADDED Requirements

### Requirement: The central writer answers matching knowledge gaps

When `relationship_assert_fact()` writes an active fact, it SHALL, on the same connection and in
the same transaction, move an `open` owner knowledge gap for the same `(subject, predicate)` to
`answerable`, recording the fact reference, a bounded value excerpt and the write's server-derived
authority. It SHALL do nothing when the schema has no knowledge-gap table, and an unchanged
re-assertion SHALL NOT answer a gap again.

#### Scenario: An assert closes a relationship-owned gap

- **WHEN** a gap is open for an entity and predicate and `relationship_assert_fact` writes that pair
- **THEN** the gap SHALL be `answerable` once the write commits, and SHALL stay `open` if the write rolls back
