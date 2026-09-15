## ADDED Requirements

### Requirement: Filtered-Event Verdict Authority Transition

The protected restore-drill result authority SHALL be extended only through the
existing trusted-bootstrap installer/finalizer pattern. The shared migration
login SHALL NOT alter, replace, adopt, own, or directly write the executor-owner
ledger or functions. One immutable ledger row and one database transaction SHALL
associate the exact overall restore attempt with its filtered-event scoped
result, fixed reason, artifact SHA-256, manifest SHA-256, and manifest capture
completion time; no separately persisted receipt may be reused across attempts.

ID: REQ-database-security-009
Source: Non-Negotiable Rules 1 and 4; RFC 0006 § Database Connection Scoping; RFC 0008 § Invariants
Scope: v1-mandatory

#### Scenario: Privileged bootstrap precedes the interface migration

- **WHEN** a clean installation or existing finalized predecessor is upgraded
  to the scoped-attempt interface
- **THEN** privileged `scripts/init-db.sql` first creates or stages the exact
  cluster-superuser-owned, zero-argument, SECURITY DEFINER
  `restore_drill_executor_admin.install_interface()` and
  `finalize_interface()` with `search_path=pg_catalog, pg_temp`
- **AND** the following core migration may only validate an already-finalized
  current interface or invoke that exact trusted installer
- **AND** an absent, old, partial, or untrusted interface fails closed with the
  managed privileged-bootstrap prerequisite; the migration does not create,
  alter, adopt, or stamp past protected objects itself

#### Scenario: Exact predecessor provenance is required for upgrade

- **WHEN** bootstrap finds pre-existing restore-drill authority objects
- **THEN** it stages the transition only when the predecessor has the exact
  expected relation/sequence/function signatures, constraints, owners,
  SECURITY DEFINER flags, fixed search paths, no user triggers, and finalized
  ACLs under the existing superuser admin and `restore_drill_executor_owner`
  provenance
- **AND** shared-owned, trigger-bearing, wrong-signature, wrong-owner,
  wrong-search-path, partially upgraded, or lookalike objects are rejected before
  DDL, ownership transfer, executor grants, or reader grants

#### Scenario: One constrained row owns overall and scoped truth

- **WHEN** the executor records a completed restore attempt
- **THEN** the migration-owned writer inserts the overall result and the scoped
  `not_run|pass|fail` result/reason/binding fields into the same
  `restore_drill_results` row and emits its fixed audit projection in the same
  transaction
- **AND** an overall pass requires scoped `pass`, reason `ok`, and non-null exact
  artifact digest, manifest digest, and manifest capture-completion timestamp
- **AND** scoped `fail` requires the same row's overall result to be a failure;
  it uses verify-stage `integrity_check_failed` unless a later post-cleanup
  failure supplies the terminal overall stage/code
- **AND** scoped `not_run` requires null scope bindings and an overall failure
  where this checker did not complete, while scoped `pass` or `fail` MAY coexist
  with a post-cleanup overall failure but can never make that attempt pass

#### Scenario: Exact writer and ACL surface is finalized

- **WHEN** the trusted transition finalizes
- **THEN** the new fixed writer is
  `restore_drill_executor.record_attempt(text,text,text,text,text,text,text,timestamptz)`
  for overall result, failure stage, failure code, scoped result, scoped reason,
  artifact SHA-256, manifest SHA-256, and manifest capture completion time
- **AND** only `restore_drill_executor` receives schema USAGE and EXECUTE on
  `is_due(integer)` and that writer, with no direct ledger/sequence DML, owner
  membership, predecessor-writer execution, or `latest_result()` execution
- **AND** the shared migration/dashboard role receives only result-schema USAGE
  and `latest_result()` execution, with no direct DML, owner membership,
  executor-writer execution, admin-schema access, or installer/finalizer
  execution; other normal roles receive none
- **AND** `latest_result()` keeps the exact existing result columns
  `checked_at`, `result`, `detail`, `failure_stage`, `failure_code`, and
  `failing_since`, and adds only `filtered_events_result`,
  `filtered_events_reason_code`, `filtered_events_artifact_sha256`,
  `filtered_events_manifest_sha256`, and
  `filtered_events_manifest_capture_completed_at`
- **AND** the purpose-bound audit writer retains only its fixed public-audit
  projection capability and no private-ledger schema, table, writer, reader, or
  owner-membership access

#### Scenario: Legacy writer cannot manufacture a current pass

- **WHEN** the four-argument
  `restore_drill_executor.record_result(text,text,text,integer)` predecessor is
  retained temporarily for compatibility
- **THEN** it is not executable by the finalized executor and cannot record an
  overall pass without same-row scoped evidence
- **AND** no compatibility call or fallback can attach a prior scoped receipt to
  a new overall attempt

#### Scenario: Installation retry and late-schema replay converge safely

- **WHEN** the interface is installed cleanly, upgraded from the exact finalized
  predecessor, retried after interruption, revisited by privileged bootstrap, or
  encountered by a later schema replaying the core chain
- **THEN** exact catalog and ACL checks converge on one current finalized
  interface without duplicate attempt rows, duplicate functions, widened grants,
  or owner drift
- **AND** a late schema recognizes the finalized interface and no-ops without
  rerunning the upgrade or reacquiring installer/finalizer authority

### Source References

- Non-Negotiable Rule 1 (`about/heart-and-soul/vision.md`): recovery evidence
  remains under an owner-controlled, non-forgeable authority boundary.
- Non-Negotiable Rule 4 (`about/heart-and-soul/vision.md`): result persistence
  and retry behavior are deterministic and testable.
- `about/heart-and-soul/security.md` § Schema Isolation: the shared migration and
  runtime identities do not inherit protected owner capabilities.
- RFC 0006 § Database Connection Scoping: exact roles and ACLs enforce the
  least-privilege database boundary.
- RFC 0008 § Invariants: trusted bootstrap provenance and fail-closed transition
  order are explicit deployment invariants.
