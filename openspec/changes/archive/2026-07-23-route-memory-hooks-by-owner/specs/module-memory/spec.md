## ADDED Requirements

### Requirement: Owner-Isolated Session Memory Hooks

The memory module SHALL register context-retrieval and successful-session
episode-storage hooks as one runtime owned by the started daemon's butler
identity.  Core session dispatch SHALL resolve that runtime using the invoking
butler identity, so one daemon's module-private memory pool cannot service
another daemon's session work.

#### Scenario: Multiple started daemons select their own memory pools

- **WHEN** General, Travel, and Chronicler memory modules are active in one
  process and Chronicler uses a private `chronicler_mem` pool
- **THEN** context retrieval and episode storage for each invoking butler SHALL
  call only that owner's registered runtime
- **AND** Chronicler's calls SHALL use its private memory pool rather than its
  domain pool or another daemon's pool

#### Scenario: Unknown or stopped owner has no fallback runtime

- **WHEN** context retrieval or episode storage is requested for an owner with
  no active memory runtime
- **THEN** context retrieval SHALL return `None` and episode storage SHALL
  return `False`
- **AND** neither operation SHALL call a runtime registered for another
  butler

#### Scenario: Stale shutdown preserves a replacement runtime

- **WHEN** a second memory module instance replaces the session runtime for an
  owner before the first instance finishes shutdown
- **THEN** shutdown of the first instance SHALL not unregister the replacement
- **AND** subsequent session dispatch for that owner SHALL continue to use the
  replacement runtime
