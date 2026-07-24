## ADDED Requirements

### Requirement: Precommit Cancellation Admission Preserves Isolation
The future cancellation-admission records and prepared-release gate SHALL be
owned by Messenger's private schema and writable only by Messenger's verified
runtime role through its authenticated MCP surface. Origin cancellation states
remain in their respective origin schemas with their immutable frozen-subset,
finalization, publish, and parent DND-abort receipts, and Switchboard's
run/manifest/finalization/fanout records remain in its own schema or approved
shared-control surface. No participant SHALL gain a shared DSN, direct
peer-schema table access, or direct provider authority to implement
cancellation.

Health and origin Schedulers may read only the public canonical DND snapshot
they require for their local decision. Messenger SHALL perform the final guard
admission while writing only its own local durable record. Switchboard SHALL
carry DND generation and opaque correlation through authenticated MCP packets;
it SHALL not read an origin queue or Messenger release gate through SQL. An
origin validates authenticated `cancel_finalize.v1` only against its own
frozen-subset digest and accepted Messenger evidence, then validates
`cancel_publish.v1` against that same local subset and durable finalization
receipt; it does not validate the global cohort by inspecting another origin.
After a Messenger DND rejection, only the parent authenticated
`abort.v1(reason=blocked_dnd)` receipt may create `release_retained_dnd`; no
participant gains a separate DND-derived cross-schema write path.

#### Scenario: Runtime roles prove their own boundaries
- **WHEN** a PostgreSQL integration test uses actual
  `butler_health_rw`, `butler_switchboard_rw`, and `butler_messenger_rw`
  `SET ROLE` sessions
- **THEN** Health can read required public DND evidence, Messenger can write its
  private admission record, and Switchboard can mediate only through MCP
- **AND** no session relies on migration-owner, superuser, or peer-schema
  privilege to prove the runtime path

#### Scenario: Direct peer access is denied
- **WHEN** Switchboard attempts to SQL-read a Messenger release gate or origin
  deferred queue, or Health/origin attempts to write Messenger cancellation data
- **THEN** the database rejects the attempt before protocol state changes
- **AND** the caller must use its authenticated versioned MCP role instead
