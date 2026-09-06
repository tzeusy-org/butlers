## 1. Implement the producer-owned manifest

- [ ] 1.1 Define a strict versioned model for the content-blind manifest,
  rejecting unknown/duplicate fields, invalid types, noncanonical digests,
  impossible counts, invalid status maps, incomplete capture, and invalid month
  names or bounds.
- [ ] 1.2 Capture the dump, complete attached-child inventory, structure, bounds,
  and exact counts through one exported repeatable-read PostgreSQL snapshot; do
  not select payload or identity columns.
- [ ] 1.3 Publish temporary artifact and manifest files as a validated pair with
  the manifest renamed last, and make backup success/candidate selection require
  the complete pair.
- [ ] 1.4 Keep artifact movement and backup pruning pair-aware; do not connect
  this lifecycle to filtered-event retention or authorize deletion.

## 2. Implement the protected checker

- [ ] 2.1 Add a deterministic checker called only from the existing executor's
  scratch `verify` stage, with no new endpoint, scheduler, credential, live
  comparison, or database lifecycle authority.
- [ ] 2.2 Verify exact artifact basename/size/SHA-256 and hash the exact manifest
  before trusting its expectations.
- [ ] 2.3 In one repeatable-read, read-only scratch transaction, verify parent
  relkind/strategy/key, the exact attached-child set, normalized half-open month
  bounds, per-child counts, and optional complete status-count parity.
- [ ] 2.4 Return one fixed scoped verdict and make every missing, malformed,
  incomplete, stale, cross-artifact, partial, mismatched, or query-failed case an
  overall `verify`/`integrity_check_failed` result.

## 3. Preserve protected result authority and content blindness

- [ ] 3.1 Extend the migration-owned executor result interface and private ledger
  to bind the scoped verdict to artifact digest, manifest digest, and capture
  completion time without accepting evidence from dashboard or normal runtime
  callers.
- [ ] 3.2 Keep public audit/API/log/metric projections fixed and sanitized; do
  not expose artifact paths, manifest bodies, counts, query/client output, row
  content, credentials, or high-cardinality digest labels.
- [ ] 3.3 Preserve the existing file-secret, network, single-executor, scratch-
  lifecycle, cleanup, and live-database exclusion contracts.

## 4. Add future verification evidence

- [ ] 4.1 Add hermetic manifest fixtures for valid, missing, malformed,
  unsupported, incomplete, stale, cross-artifact, duplicate-key, unknown-field,
  impossible-count, invalid-status, and invalid-bound cases.
- [ ] 4.2 Add hermetic checker fixtures for parent relkind/key, missing and extra
  attached children, unattached name matches, bounds, counts, optional statuses,
  and query failures.
- [ ] 4.3 Add a real-PostgreSQL testcontainer fixture that creates the actual
  partitioned parent/children, captures a dump and manifest through one exported
  snapshot, restores the artifact with real client tooling, and proves exact
  checker parity.
- [ ] 4.4 In that fixture, commit a concurrent insert and a partition/catalog
  change after snapshot acquisition; prove the dump and manifest stay on the
  same captured state or the producer fails closed, never a mixed state.
- [ ] 4.5 Seed sentinel payload, preview, identity, and error-detail values and
  prove none appears in the manifest, protected verdict, API/audit projection,
  logs, or test diagnostics.
- [ ] 4.6 Prove shared/dashboard-style credentials cannot invoke the privileged
  restore path and that scratch cleanup still gates the overall pass.

## 5. Document and roll out safely

- [ ] 5.1 Update backup/restore operations documentation with the pair format,
  snapshot method, checker semantics, stable failures, content-blind boundary,
  producer-first rollout, and rollback behavior.
- [ ] 5.2 Run focused producer/checker tests, real-PostgreSQL integration
  evidence, repository guards, and terminal hosted CI for the implementation's
  exact head.
- [ ] 5.3 Obtain independent exact-head review and explicit owner approval before
  implementation is merged; obtain separate operational authorization before
  any deployment or restore execution.

## 6. Keep this planning change effect-free

- [x] 6.1 Specify the seam without reading a backup body or payload, querying a
  live database, running a restore, changing retention, or implementing code,
  migrations, runtime configuration, or deployment state.
