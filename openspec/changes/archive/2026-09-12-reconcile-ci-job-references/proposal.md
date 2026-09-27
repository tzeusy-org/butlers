## Why

PR #3946 has landed the hosted Python gate as `check-preflight`, five
`check-unit-N` shards, and five `check-integration-N` shards, with the historic
`check` status retained as a fail-closed coverage fan-in. The testing baseline
now records that topology, while several operational references still describe
smoke, unit, and integration tests as steps of an older serialized CI job.
Those claims erase the distinction between the jobs that execute tests and the
status that aggregates their results.

## What Changes

- Rebuild the active testing delta from the merged baseline so it remains
  archive-safe and carries the current preflight and shard topology.
- Reconcile the identified testing and agent guidance with the preflight plus
  ten-shard fan-out/fan-in topology.
- Preserve the distinction between local convenience targets and the hosted
  `check-preflight`, `check-unit-N`, and `check-integration-N` lanes.
- Leave workflow execution behavior unchanged.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `testing`: carry the merged smoke-gate contract forward while retaining the
  existing smoke selection, E2E exclusion, and failure guarantees.

## Impact

- Documentation and one OpenSpec delta only; no runtime, workflow, database,
  API, or test-selection behavior changes.
- The baseline `openspec/specs/testing/spec.md` remains untouched until this
  change is archived, avoiding a direct-edit race with OpenSpec's whole-
  requirement replacement semantics.
