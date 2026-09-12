## Why

The current restore drill proves only that a selected PostgreSQL backup can be
restored and contains at least one non-system table. It does not prove that the
partitioned `connectors.filtered_events` parent survived, that the artifact
contains the complete attached-child set and exact month bounds captured by the
backup, or that every child contains the same number of rows as it did at backup
time.

The independently reviewed development evidence packet for `bu-3lijn`
(`2026-09-06`, SHA256
`5feac3246b4d6ce86185e9047957127e670a6826a43d3cd3265924f4557f24ef`)
records this gap without inspecting a backup body or running a restore. It also
shows why comparing a restored artifact with the later live database is invalid:
the live table can grow or change after capture. This change uses that packet as
dated design evidence only. It conveys no authority to read a backup, run a
restore, deploy the checker, change retention, or mutate runtime state.

## What Changes

- Define a versioned, content-blind manifest owned by the backup producer. It
  binds the exact compressed artifact to one exported PostgreSQL snapshot and
  records only the `connectors.filtered_events` parent shape, complete attached
  child set, normalized monthly bounds, and exact artifact-time counts.
- Define pair publication semantics in which the manifest is the final commit
  marker for a verified artifact/manifest pair. Missing, partial, malformed,
  stale, or cross-artifact metadata cannot be selected as recovery proof.
- Extend the existing isolated restore-drill executor's verification stage with
  an all-or-nothing checker. The checker validates the artifact identity and
  manifest before comparing the authorized scratch restore with the manifest;
  it never compares with the later live database.
- Define a fixed, content-blind scoped verdict that binds a pass to the artifact
  digest, manifest digest, and capture completion time in the same immutable
  authoritative attempt row as the overall result. Any scoped failure makes the
  overall restore drill fail, with its verify classification retained unless a
  later cleanup failure becomes terminal; a crash or retry cannot reuse an
  earlier scope pass.
- Define the trusted-bootstrap-first transition for the protected ledger. The
  shared migration login may validate and invoke only the exact superuser-owned
  installer prepared by `scripts/init-db.sql`; it may not alter, adopt, or
  replace executor-owner objects.
- Require hermetic schema/checker fixtures and a real-PostgreSQL capture/restore
  fixture, including a concurrent-write witness that proves the dump and counts
  came from the same snapshot.

## Non-Goals And Authority Boundary

- No implementation, migration, backup-body or payload read, restore execution,
  production inspection, deployment, runtime/configuration change, retention or
  cleanup action is part of this change.
- The checker does not create a second restore path, credential, scheduler,
  scratch database, or result authority. It runs only inside the existing
  protected restore-drill executor after a separately authorized scratch restore.
- This change does not authorize deployment or execution. Those effects require
  their own owner authorization after implementation and independent review.
- This proposal does not select or activate a filtered-event retention window.
  Restore evidence is a prerequisite for a future decision, not permission to
  delete data.
- The change remains an unapproved proposal until the owner explicitly approves
  it. A draft pull request or passing validation is not owner approval.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `deployment-hardening` -- add the artifact-bound filtered-event manifest,
  checker, protected verdict, and fail-closed pair lifecycle.
- `database-security` -- add the trusted-bootstrap-first, exact-provenance and
  ACL transition for atomically recording scoped and overall attempt evidence.
- `testing` -- require hermetic and real-PostgreSQL evidence for snapshot
  consistency, structure/count parity, privacy, and failure behavior.

## Impact

Future implementation is expected to change the backup producer, restore-drill
verification stage, trusted bootstrap installer/finalizer, protected result
ledger migration interface, backup/restore operations documentation, and
focused script/job/integration tests. It must preserve the existing isolated
executor's file-secret, network, scratch-lifecycle, and sanitized-result
boundaries. This planning-only change changes no runtime behavior.
