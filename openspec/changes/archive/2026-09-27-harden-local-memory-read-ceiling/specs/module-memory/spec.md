## ADDED Requirements

### Requirement: Server-Held Ceiling for Every Local Memory Read

The memory module SHALL apply one server-held sensitivity ceiling from the
owning runtime configuration to every local memory retrieval surface, including
recall, search, context assembly, and direct `memory_get` retrieval by UUID.
No caller-supplied argument SHALL raise that ceiling. Unknown authority or an
unknown stored sensitivity SHALL fail closed; a NULL stored sensitivity SHALL be
treated as `normal`.

#### Scenario: Local retrieval is filtered by held authority

- **WHEN** a local memory retrieval runs under a ceiling below one or more
  stored rows' sensitivity
- **THEN** it MUST return only rows at or below that held ceiling
- **AND** the decision MUST be enforced before a more-sensitive row is
  returned to the caller

#### Scenario: Direct UUID retrieval cannot bypass the ceiling

- **WHEN** `memory_get` is called with the UUID of a row above the held
  sensitivity ceiling
- **THEN** it MUST return the same absent result as for an unknown UUID
- **AND** it MUST NOT update that row's reference count or last-reference
  timestamp

#### Scenario: Withheld receipt remains within the context budget

- **WHEN** Profile Facts excludes one or more rows because of the held ceiling
  and the section has budget for a receipt
- **THEN** the Profile Facts section MUST report the excluded count without
  including excluded content
- **AND** the section header, rendered facts, and receipt together MUST remain
  within that section's allocation
- **AND** the complete rendered context MUST remain within the requested
  context budget

#### Scenario: Insufficient budget omits the Profile Facts section safely

- **WHEN** the Profile Facts allocation cannot fit both its header and a
  withheld-count receipt
- **THEN** the module MUST omit that section rather than exceed the requested
  context budget or expose excluded content
