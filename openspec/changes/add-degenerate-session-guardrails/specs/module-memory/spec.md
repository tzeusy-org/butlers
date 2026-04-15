## ADDED Requirements

### Requirement: memory_entity_resolve Raises on Invalid Input
The `memory_entity_resolve` MCP tool SHALL raise an exception when invoked with invalid input — specifically when the required `name` argument is `null`/`None`, missing, or an empty (or whitespace-only) string. It SHALL NOT return an empty list in these cases. The "no candidates found" empty-list return is reserved for well-formed non-empty `name` inputs that simply do not match any entity.

This requirement was motivated by a real incident (session `46f18840-4f74-4e0a-a3bf-cafa2b579f3a`, 2026-04-15) in which the lifestyle butler looped 41 times on `memory_entity_resolve(name=null)` because the tool returned `[]` as a success, indistinguishable from a valid-query-no-match. The tool now SHALL distinguish invalid input from no-match.

This requirement composes with the cross-cutting "MCP Tools Raise on Invalid Input" rule in `core-modules`. The cross-cutting rule is the contract; this requirement is the module-specific expression of that contract for the tool that triggered the incident, so regressions can be caught by module-local tests.

#### Scenario: Null name raises
- **WHEN** `memory_entity_resolve` is called with `name=None` (or the equivalent JSON `null`)
- **THEN** the tool SHALL raise an exception (e.g. `ValueError` or a module-defined `InvalidInputError`)
- **AND** SHALL NOT return an empty list

#### Scenario: Empty string name raises
- **WHEN** `memory_entity_resolve` is called with `name=""` or a whitespace-only string
- **THEN** the tool SHALL raise an exception
- **AND** SHALL NOT return an empty list

#### Scenario: Well-formed name with no match still returns empty list
- **WHEN** `memory_entity_resolve` is called with a non-empty `name` that does not match any entity under any tier (exact, alias, prefix, fuzzy)
- **THEN** the tool SHALL return an empty list
- **AND** SHALL NOT raise

#### Scenario: Missing required name argument raises
- **WHEN** `memory_entity_resolve` is invoked with the `name` argument entirely absent from the tool-call arguments
- **THEN** the tool SHALL raise an exception
- **AND** SHALL NOT treat the missing argument as an implicit empty-name query
