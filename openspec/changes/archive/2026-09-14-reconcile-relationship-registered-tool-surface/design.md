## Context

The old requirement was useful as a capability inventory but is not an exact
registration contract. In particular, it names bare entity tools that the
current roster deliberately prunes. Keeping that line as a normative scenario
would require mutually exclusive present and absent states.

## Decision

Retire the old requirement by name and add a distinct exact registered-surface
requirement. This preserves the historical archived artifact unchanged while
letting the canonical spec state only current callable behavior. The separately
adopted read-only entity-group change will modify the new requirement when its
implementation receives authority and lands.

## Boundaries

- No entity read or write is activated here.
- `relationship_assert_fact` remains unconditional for approved replay.
- The memory module remains the only owner of `memory_entity_create`.
- No runtime, database, provider, or owner data is accessed.
