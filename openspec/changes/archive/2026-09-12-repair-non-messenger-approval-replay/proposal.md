## Why

Non-Messenger approval producers can currently park rows whose stored tool
name and arguments cannot be executed by the owning daemon. The owner can
approve a real-world action and still receive an approved-but-unexecuted row
only when dispatch is attempted. The producer boundary must prove that a
durable command is executable before it reaches the approval queue.

## What Changes

- Define explicit executable-command contracts for direct non-Messenger
  approval producers, including owning daemon, registered tool name, and exact
  replay arguments.
- Add native Switchboard and Relationship handlers for the currently
  replayable connector-disconnect and memory-reclassification actions.
- Reject credential rotation before parking when no safe credential reference
  exists to reproduce the requested operation, and emit an auditable failure
  signal without persisting a misleading pending action.
- Validate registered handlers against declared command contracts at daemon
  startup and guard the inventoried producer set with regression coverage.
- Preserve historical malformed rows unchanged; dispatch continues to report a
  truthful failure rather than guessing arguments or rewriting provenance.

## Capabilities

### New Capabilities

- `approval-command-contracts`: Durable executable-command requirements for
  direct approval producers outside Messenger delivery.

### Modified Capabilities

- `module-approvals`: Approved dispatch must use the owning daemon's declared,
  registered command surface for direct producer rows and retain truthful
  failure semantics for historic malformed rows.
- `relationship-curation`: Episodic-fact reclassification proposals carry an
  executable relationship-memory command rather than an orphaned queue label.

## Impact

- `src/butlers/modules/approvals/` command-contract validation and daemon
  startup wiring.
- Switchboard connector lifecycle MCP registrations and Switchboard roster
  approval configuration.
- Relationship memory tool registration plus the episodic curation producer.
- Dashboard connector lifecycle API behavior for unrepresentable token
  rotation requests.
- Focused daemon, API, module, and curation regression tests; no migration or
  historical-row rewrite.
