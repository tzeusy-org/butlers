## ADDED Requirements

### Requirement: Wake-Recovery Least-Privilege Coordination
Wake-recovery implementation SHALL preserve runtime `SET ROLE` schema
isolation. Switchboard SHALL NOT receive `SELECT`, write, pool, DSN, or direct
queue-table access for any origin schema; an origin SHALL NOT receive another
origin's queue access; and no participant SHALL receive direct Messenger
provider authority. Cross-origin preparation, commit, abort, release, and
metadata exchange SHALL use authenticated versioned MCP contracts mediated by
Switchboard.

Any new shared-control records or grants in this parent packet SHALL be limited
to the participants that require their own durable run/fence or
action-reconciliation operation, with the smallest CRUD surface needed for
that operation. The landed `canonical-dnd-generation-guard` (`bu-12iab`) and
`durable-precommit-cancellation-admission` (`bu-qs702`) contracts supply the
only DND guard and complete-cohort post-prepare cancellation boundaries
consumed here; this
packet MUST NOT introduce a shared-control substitute, a second DND writer, or
a peer-schema cancellation path. The database role model SHALL reject an
unrecognized caller, mismatched owner/run, or attempt to use a shared-control
record as a substitute for reading another schema's notification content.

#### Scenario: Switchboard cannot scan origin holds
- **WHEN** Switchboard runs under its normal runtime role during a
  wake-recovery coordination attempt
- **THEN** it has no direct SQL permission to read an origin's
  `deferred_notifications` rows
- **AND** it receives only the metadata/content contract returned by that
  origin's authenticated prepare MCP operation

#### Scenario: Unauthorized participant cannot mutate a run
- **WHEN** a role or MCP caller that is not an authenticated protocol
  participant attempts to prepare, commit, abort, release, or reconcile a wake
  run
- **THEN** the request is rejected without queue, context, or egress mutation
- **AND** the rejection is auditable without exposing another origin's content
