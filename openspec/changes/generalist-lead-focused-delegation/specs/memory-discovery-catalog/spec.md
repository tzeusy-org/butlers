## MODIFIED Requirements

### Requirement: Catalog provenance pointers are fetchable under held authority

The memory module SHALL provide a read-only `memory_catalog_fetch` tool that
dereferences one live `facts` or `rules` provenance pointer under the same
server-derived policy used by catalog search. The tool SHALL dereference only
pointers owned by its own butler. For a pointer owned by another butler it
SHALL NOT broker the read through Switchboard or call that butler's `memory_get`
itself; it SHALL return a content-free reference naming the owning butler and
the pointer, so the goal invocation fetches the canonical row through the
owning butler's memory read capability under its own grant and verified
assertion. The fetch path SHALL NOT grant cross-schema table access.

Source: RFC 0038 D7, D8, D15
Scope: v1-mandatory

#### Scenario: Authorized pointer returns its canonical memory

- **WHEN** `memory_catalog_fetch` receives a live catalog pointer whose
  sensitivity is at or below the butler's held ceiling
- **THEN** it MUST return the canonical source row from the owning schema
- **AND** the operation MUST be read-only and idempotent

#### Scenario: Pointer above authority is content-blind

- **WHEN** `memory_catalog_fetch` receives a pointer whose catalog sensitivity
  is above the butler's held ceiling
- **THEN** it MUST return only a stable withheld marker
- **AND** it MUST NOT return canonical content, the stored sensitivity value, or
  any additional source metadata
- **AND** the tool MUST NOT expose a caller-supplied sensitivity ceiling

#### Scenario: Scope and predicate filtering

- **WHEN** a cross-butler search includes `scope` or `predicate` filters
- **THEN** the catalog query MUST apply these as additional WHERE conditions
- **AND** `source_butler` MAY also be used as a filter to narrow results to specific butlers

#### Scenario: Foreign pointer is returned as a reference, not brokered

- **WHEN** `memory_catalog_fetch` receives a live pointer owned by another butler
- **THEN** it MUST return the owning butler and pointer without canonical content
- **AND** it MUST NOT make a Switchboard route call or any unasserted call to the owning butler's model-reachable tools
- **AND** the canonical row is reachable only if the invocation's grant includes the owning butler's memory read capability
