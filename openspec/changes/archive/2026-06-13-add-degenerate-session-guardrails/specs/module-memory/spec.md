## ADDED Requirements

### Requirement: memory_entity_resolve Raises on Invalid Input
The `memory_entity_resolve` MCP tool SHALL raise `ValueError` when invoked with invalid input. The tool accepts a unified `identifier` argument (preferred) or a legacy `name` argument; exactly one must be supplied with a usable value. Invalid input includes: the resolved lookup string being `null`/`None`, missing, or empty/whitespace-only; and both `name` and `identifier` being provided together. The tool SHALL NOT return an empty list in these cases. The "no candidates found" empty-list return is reserved for a well-formed non-empty lookup string that simply does not match any entity under any tier.

This requirement was motivated by a real incident (session `46f18840-4f74-4e0a-a3bf-cafa2b579f3a`, 2026-04-15) in which the lifestyle butler looped 41 times on `memory_entity_resolve` with a null lookup because the tool returned `[]` as a success, indistinguishable from a valid-query-no-match. The tool now distinguishes invalid input from no-match.

This requirement composes with the cross-cutting "MCP Tools Raise on Invalid Input" rule in `core-modules`. The cross-cutting rule is the contract; this requirement is the module-specific expression of that contract for the tool that triggered the incident, so regressions can be caught by module-local tests.

#### Scenario: Null/empty lookup raises
- **WHEN** `memory_entity_resolve` is called such that neither `identifier` nor `name` resolves to a non-empty string (the lookup is `None`, the JSON `null`, absent, `""`, or whitespace-only)
- **THEN** the tool SHALL raise `ValueError`
- **AND** SHALL NOT return an empty list

#### Scenario: Both name and identifier provided raises
- **WHEN** `memory_entity_resolve` is called with both a non-empty `name` and a non-empty `identifier`
- **THEN** the tool SHALL raise `ValueError`
- **AND** SHALL NOT return an empty list

#### Scenario: Well-formed lookup with no match returns empty list
- **WHEN** `memory_entity_resolve` is called with a non-empty `identifier` (or legacy `name`) that does not match any entity under any tier (role, exact, alias, prefix/substring, optional fuzzy)
- **THEN** the tool SHALL return an empty list
- **AND** SHALL NOT raise
