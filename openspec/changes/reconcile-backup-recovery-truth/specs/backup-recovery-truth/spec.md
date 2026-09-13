## ADDED Requirements

### Requirement: Complete Recoverable Data Coverage

Every published recovery artifact SHALL contain all recoverable application
data required to restart the owner-controlled instance, including authoritative
credential-bearing rows. Its retained relation set SHALL be closed under
schema-qualified foreign-key dependencies in the exact exported PostgreSQL
snapshot supplied to `pg_dump`. The producer SHALL hold ordered relation locks
and its exporting transaction until dump and manifest capture complete. An
omission SHALL be limited to trusted-bootstrap control state that the managed
bootstrap reconstructs, and the omitted state and recovery cost SHALL be
documented.

ID: REQ-backup-recovery-truth-001
Source: Non-Negotiable Rule 1; RFC 0006 Credential Store; deployment-hardening REQ-deployment-hardening-007
Scope: v1-mandatory

#### Scenario: Credential-bearing application state remains recoverable

- **WHEN** a backup artifact is published
- **THEN** it contains the authoritative credential-bearing application rows
  needed to recover the instance, including applicable `butler_secrets` and
  secured `entity_info` rows
- **AND** a bootstrap-owned coverage function has enumerated every applicable
  Tier-1/Tier-2 store and proved complete non-RLS-filtered dump-role visibility
  in the exported snapshot
- **AND** no manifest, receipt, API, UI, audit, attention, log, metric, or trace
  exposes their values, keys, per-store counts, or value-derived digests

#### Scenario: Published coverage is closed under foreign keys

- **WHEN** the producer determines the retained relation set in the dump's
  captured PostgreSQL state
- **THEN** every retained foreign-key child has its schema-qualified referenced
  parent retained in the same artifact
- **AND** the producer exports one repeatable-read snapshot, locks the ordered
  relation set, rechecks the graph, and supplies that exact snapshot to
  `pg_dump` while retaining the transaction and locks through capture
- **AND** a matching unqualified relation name, an excluded parent with a
  retained child, or an unresolved dependency fails the run before publication

#### Scenario: Concurrent DDL cannot split closure from dump state

- **WHEN** a foreign-key add/drop or relation create/drop races backup capture
- **THEN** DDL committed before snapshot acquisition appears in both graph and
  dump, while conflicting DDL after relation locking waits until capture ends
- **AND** a post-snapshot new relation is absent from both outputs, and any
  changed identity, failed lock, or inconsistent recheck publishes no pair

#### Scenario: Credential aggregates match after restore

- **WHEN** the exact artifact is restored in the isolated verification target
- **THEN** the target enumerates the same authoritative store inventory and its
  one aggregate credential-row count equals the snapshot-bound source aggregate
- **AND** empty and populated stores may pass, while missing stores, filtered
  source visibility, inventory drift, or count mismatch fails without
  persisting or exposing per-store counts

#### Scenario: Trusted-bootstrap omission is explicit and reconstructible

- **WHEN** a relation is unavailable to the least-privileged dump identity
- **THEN** it is omitted only when the managed bootstrap or a deterministic
  projection reconstructs it without private source loss
- **AND** the operations contract names the omission, why privilege is not
  widened, what evidence survives, and what is unavailable after recovery

#### Scenario: Ordinary data is not silently sacrificed for a green run

- **WHEN** a new fenced object or dependency would make the dump fail
- **THEN** the producer fails closed and records its fixed run outcome
- **AND** it does not auto-exclude ordinary application data, enable row-level
  security during the dump, or broaden the dump identity to publish an
  apparently successful artifact

### Requirement: Bootstrap-First Ownership and ACL Recovery

The recovery artifact SHALL retain the ownership and ACL intent required to
reconstruct its database objects. A restore SHALL be called recovery-ready only
when the same protected attempt creates a disposable target isolated from the
live database, runs managed trusted bootstrap there, restores the exact
artifact, verifies the complete ownership/privilege policy, and durably records
that scoped result beside the artifact binding before destroying the target.

ID: REQ-backup-recovery-truth-002
Source: Non-Negotiable Rules 1 and 4; RFC 0006 Database Connection Scoping; restore-drill-recovery-truthfulness REQ-database-security-006; proposed artifact-bound-filtered-event-restore-verification REQ-database-security-011
Scope: v1-mandatory

#### Scenario: Managed bootstrap precedes promotion-ready verification

- **WHEN** an artifact is evaluated as a candidate for recovery
- **THEN** a fresh isolated target uses only its per-attempt ephemeral local
  cluster-superuser to establish the exact trusted-bootstrap roles and
  interfaces required by the artifact's ownership and ACL intent
- **AND** no application role may connect to or promote the target until
  ownership, definer, search-path, role, membership, and ACL verification passes
- **AND** the executor receives no live superuser, migration, credential-store,
  or fenced-owner authority and target-cleanup failure makes the attempt fail

#### Scenario: Owner-neutral scratch restore is scoped honestly

- **WHEN** the isolated executor uses an owner-neutral technique to exercise
  application-data restoration without receiving fenced-owner or superuser
  authority
- **THEN** the original artifact still retains its ownership and ACL intent and
  the same protected attempt checks that intent in the isolated target
- **AND** the data-restore result alone cannot produce `full_recovery` scope or
  a `proven` status

#### Scenario: Complete ownership and ACL matrix is authoritative

- **WHEN** the isolated target completes bootstrap and artifact restoration
- **THEN** one normalized checker verifies every expected schema/table/sequence
  owner, role attribute/membership, function owner/definer/body/search path,
  explicit and default ACL, RLS enable/force flag, and RLS policy definition
- **AND** it rejects every missing or extra grant, membership, policy, function,
  owner, or other catalog drift without reading application row content
- **AND** only the checker result bound into that attempt's protected row may
  satisfy ownership/ACL recovery proof; repository tests and prose may not

#### Scenario: Removing ownership evidence is not accepted as recovery

- **WHEN** a proposed producer or restore path suppresses ownership or ACL
  application
- **THEN** it may proceed only if the canonical artifact retains the original
  intent and the managed verification proves the equivalent final boundary
- **AND** a dump that discards that intent without replacement evidence cannot
  produce a recovery-ready verdict

#### Scenario: Executor privilege remains purpose-bound

- **WHEN** the weekly restore executor performs a scratch attempt
- **THEN** it receives no cluster-superuser, migration-role, credential-store,
  fenced-owner membership, or live application-schema authority
- **AND** a missing privilege produces a fixed failed or scoped-unproven result
  rather than a privilege grant, manual workaround, or false pass

### Requirement: One Exact-Artifact Recovery Verdict

The existing executor-owner restore ledger SHALL remain the sole authority for
recovery attempts. One immutable attempt row and one database transaction SHALL
bind the overall result to the exact artifact, its verified completion time and
byte size, and every required scoped verdict and policy digest.
A public audit, attention row, filesystem timestamp, filename, run receipt, or
manifest alone SHALL NOT authorize or manufacture a recovery pass.

ID: REQ-backup-recovery-truth-003
Source: Non-Negotiable Rule 4; RFC 0005 Workflow and Recovery Telemetry; proposed artifact-bound-filtered-event-restore-verification REQ-database-security-011
Scope: v1-mandatory

#### Scenario: Separately adopted scoped manifest binds only its named scope

- **WHEN** the owner has separately adopted the exact proposed filtered-event
  artifact and a selected backup carries its implemented manifest
- **THEN** its exact basename, byte length, SHA-256, manifest SHA-256, and
  capture completion bind the filtered-event verdict as required by
  `REQ-deployment-hardening-008` and `REQ-deployment-hardening-009`
- **AND** the manifest cannot substitute for direct artifact integrity,
  application-data restoration, ownership/ACL verification, or cleanup

#### Scenario: Unapproved sibling supplies no implementation authority

- **WHEN** `artifact-bound-filtered-event-restore-verification` has not received
  exact-artifact independent review and explicit owner adoption
- **THEN** no implementation or recovery proof may rely on its manifest,
  checker, protected transition, or requirement IDs as adopted behavior
- **AND** review, CI, PR state, merge, or adoption of this umbrella alone cannot
  satisfy that sibling gate

#### Scenario: Projected artifact facts share the attempt binding

- **WHEN** a protected attempt records artifact completion time or byte size
- **THEN** those fields are persisted in the same immutable row as the verified
  artifact digest, manifest digest, scoped results, and overall result
- **AND** the protected writer accepts them only with the exact verified digest,
  and `latest_result()` returns them from that row rather than filesystem recency
- **AND** a digest-verified internal join may resolve the artifact but cannot
  combine an older pass with a newer artifact's time or size

#### Scenario: Full proof is one atomic attempt

- **WHEN** an attempt reaches a terminal result
- **THEN** the same protected row contains its FK-closure result,
  credential-coverage result, filtered-event result, ownership/ACL result and
  policy digest, cleanup result, fixed scope, and artifact bindings
- **AND** `proven` is impossible unless every required result in that row passes

#### Scenario: Partial or cross-artifact evidence cannot pass

- **WHEN** a required manifest is missing, malformed, stale, incomplete,
  mismatched, or belongs to another artifact, or any required checker is
  unavailable or fails
- **THEN** the attempt is `failed` or `unproven` according to its completed
  stage and never `proven`
- **AND** no last-known pass, live-database comparison, filename, mtime, or
  successful subset fills the missing evidence

#### Scenario: Crash and retry preserve attempt identity

- **WHEN** the executor crashes before or after authoritative persistence or
  retries the same artifact
- **THEN** a pre-commit crash leaves no authoritative attempt and a post-commit
  crash leaves one complete immutable attempt
- **AND** every retry recomputes its own verdict and cannot copy or associate an
  earlier scoped pass or binding with a later attempt

#### Scenario: Concurrent execution remains serialized

- **WHEN** another restore attempt is requested while the single executor owns
  the fixed scratch lifecycle
- **THEN** no second lifecycle overlaps it or mutates its scratch database or
  attempt row
- **AND** any deployment with multiple executors remains disabled until the
  cross-process exclusion required by `REQ-deployment-hardening-007` exists

### Requirement: Truthful Owner-Facing Recovery State

`GET /api/system/backups` SHALL expose an additive, content-blind
`recovery_proof` object with exactly `status`, `attempted_at`,
`artifact_completed_at`, `artifact_size_bytes`, `scope`, and `failure_code`.
Status SHALL be `proven`, `unproven`, `failed`, `stale`, or `degraded`; scope
SHALL be null, `application_data`, `application_data_with_filtered_events`, or
`full_recovery`; and failure code SHALL be null or one value from the closed
vocabulary below. The newest protected row alone SHALL determine status and all
non-null fields. The tile SHALL distinguish artifact health, last run, and last
proven restore at first glance, with recovery green only for a current
same-attempt full pass.

ID: REQ-backup-recovery-truth-004
Source: Non-Negotiable Rule 1; RFC 0007 Amendment 1; system-overview-page REQ-system-overview-page-005 and REQ-system-overview-page-006
Scope: v1-mandatory

#### Scenario: Unavailable authority is degraded

- **WHEN** the protected reader is unavailable
- **THEN** status is `degraded`, failure code is `authority_unavailable`, and
  attempted time, artifact completion time, artifact size, and scope are null
- **AND** no filesystem, audit, attention, manifest, or prior pass fills a field

#### Scenario: No authoritative attempt is unproven

- **WHEN** the protected reader succeeds but returns no attempt
- **THEN** status is `unproven`, failure code is `no_attempt`, and attempted
  time, artifact completion time, artifact size, and scope are null

#### Scenario: Legacy or incomplete binding is unproven

- **WHEN** the newest protected row predates complete binding or lacks a
  required scope result
- **THEN** status is `unproven`, failure code is `legacy_unbound_artifact`, and
  attempted time is the row time
- **AND** artifact completion/size are present only after digest verification,
  and scope is null or `application_data`, never `full_recovery`

#### Scenario: Newest failed attempt outranks an older pass

- **WHEN** the newest protected row is failed even though an older row passed
- **THEN** status is `failed`, attempted time comes from the newest row, and
  failure code is its fixed mapped code
- **AND** artifact completion/size are present only when bound in that row, scope
  is its last completed fixed scope or null, and no older field is reused

#### Scenario: Artifact or manifest binding failure exposes no artifact facts

- **WHEN** the newest attempt fails with `artifact_identity_mismatch`,
  `manifest_missing`, `manifest_malformed`, `manifest_unsupported`,
  `manifest_incomplete`, or `capture_consistency_invalid`
- **THEN** status is `failed`, attempted time is non-null, artifact completion
  time, artifact size, and scope are null, and failure code is that exact value
- **AND** filesystem metadata or a partially parsed manifest cannot fill a field

#### Scenario: Pre-restore coverage failure is bound but has no completed scope

- **WHEN** exact artifact/manifest binding succeeds but FK closure or source
  credential visibility fails
- **THEN** status is `failed`, attempted time and both protected artifact facts
  are non-null, scope is null, and failure code is respectively
  `coverage_not_fk_closed` or `credential_visibility_incomplete`

#### Scenario: Restore and scoped checker failures expose only completed scope

- **WHEN** the exact bound attempt fails during restore, credential-count
  comparison, filtered-event verification, or ownership/ACL verification
- **THEN** failure code is respectively `restore_failed`,
  `credential_count_mismatch`, `filtered_event_verification_failed`, or
  `ownership_acl_mismatch`, with non-null attempted time and artifact facts
- **AND** scope is null for `restore_failed`, `application_data` for credential
  or filtered-event failure, and `application_data_with_filtered_events` only
  when filtered-event verification passed before ownership/ACL failed

#### Scenario: Cleanup failure overrides a prior passing stage

- **WHEN** target cleanup fails after one or more verification stages complete
- **THEN** status is `failed`, failure code is `cleanup_failed`, attempted time
  and both artifact facts are non-null, and scope is the highest completed scope
  below `full_recovery`
- **AND** scope is null before generic restore completion,
  `application_data` after generic restore only, or
  `application_data_with_filtered_events` after that scoped checker passes
- **AND** no earlier passing stage or older attempt can make the result green

#### Scenario: Expired complete pass is stale

- **WHEN** the newest protected row is a complete full-recovery pass outside the
  governing cadence
- **THEN** status is `stale`, failure code is `proof_stale`, both artifact facts
  and attempted time are non-null, and scope is `full_recovery`

#### Scenario: Current complete proof is green

- **WHEN** the protected authority records a complete passing attempt within
  the governing cadence
- **THEN** status is `proven`, failure code is null, attempted time and both
  bound artifact facts are non-null, and scope is `full_recovery`
- **AND** the tile renders `Last proven restore` with that age and does not
  imply that artifact presence or gzip health supplied the proof

#### Scenario: Scope progression cannot overstate partial proof

- **WHEN** generic application restore completes without filtered-event,
  ownership/ACL, credential, or cleanup completion
- **THEN** scope is at most `application_data` and status is not `proven`
- **AND** `application_data_with_filtered_events` requires the exact scoped
  manifest/checker result, while `full_recovery` requires every same-row stage

#### Scenario: Failure vocabulary is closed

- **WHEN** recovery cannot be proven
- **THEN** failure code is exactly one of `authority_unavailable`, `no_attempt`,
  `legacy_unbound_artifact`, `artifact_identity_mismatch`, `manifest_missing`,
  `manifest_malformed`, `manifest_unsupported`, `manifest_incomplete`,
  `capture_consistency_invalid`, `coverage_not_fk_closed`,
  `credential_visibility_incomplete`, `credential_count_mismatch`,
  `restore_failed`, `ownership_acl_mismatch`,
  `filtered_event_verification_failed`, `cleanup_failed`, or `proof_stale`
- **AND** no dynamic exception, client diagnostic, path, identifier, digest, or
  request-derived string is used as or appended to that code

#### Scenario: Artifact health is labeled as narrower evidence

- **WHEN** a latest artifact directly passes the existing integrity check
- **THEN** its green label states `Artifact healthy` rather than an unqualified
  recovery verdict
- **AND** a failed or unproven restore remains visible in the same card without
  requiring the owner to infer the difference from color alone

#### Scenario: Missing additive field is backward-compatible uncertainty

- **WHEN** a new frontend receives a pre-change response without
  `recovery_proof`
- **THEN** it renders the same amber `No proven restore` state as `unproven`
- **AND** an old frontend ignores the additive object while retaining existing
  artifact, run, and drill facts

#### Scenario: Recovery detail is accessible and non-blocking

- **WHEN** the tile loads, fails, or exposes fixed failure detail
- **THEN** it acknowledges loading promptly, keeps the rest of the System page
  usable, and exposes state and detail through semantic text usable by keyboard
  and assistive technology
- **AND** this capability adds no restore/run-now control or repeated-action
  surface

### Requirement: Content-Blind Recovery Evidence

Every recovery manifest, protected result, public projection, API response, UI
state, audit, attention event, log, metric, trace, and test diagnostic SHALL be
limited to the minimum structural and aggregate evidence required by its
contract. No surface outside the recovery artifact SHALL contain a credential
value, application row content, private identifier, dump fragment, SQL/client
output, absolute path, or unbounded diagnostic.

ID: REQ-backup-recovery-truth-005
Source: about/heart-and-soul/security.md Sensitive Data Categories; RFC 0005 Cardinality Discipline; core-notify REQ-core-notify-026
Scope: v1-mandatory

#### Scenario: Positive allowlists carry useful evidence

- **WHEN** recovery succeeds or fails
- **THEN** the protected result and owner-facing projection contain only fixed
  status/stage/code/scope values, UTC timestamps, non-negative artifact size,
  and private-ledger bindings required by separately adopted scoped verification
- **AND** public metrics exclude digests, paths, filenames, role/object names,
  receipts, and any dynamic error text

#### Scenario: Caller-supplied counts do not become proof

- **WHEN** the executor reports an aggregate table count from its scratch check
- **THEN** the protected writer continues to discard that caller-supplied value
  as required by its authority boundary
- **AND** no API, UI, audit, attention, log, metric, or trace represents the
  count as authoritative recovery evidence

#### Scenario: Synthetic sentinels remain absent end to end

- **WHEN** disposable fixtures plant distinct sentinel values in credential,
  identity, application payload, diagnostic, path, and SQL/client-output fields
- **THEN** none appears in manifests outside an allowed structural name,
  protected/public projections, API response or headers, rendered UI, audit,
  attention, logs, metrics, traces, or test failure diagnostics
- **AND** manifest evidence contains at most the one aggregate credential-row
  count and opaque inventory/policy digests, never values or per-store counts
- **AND** the test positively asserts each permitted field set so an empty
  projection cannot make the absence proof pass

#### Scenario: Attention never becomes result authority

- **WHEN** a failed attempt is durable
- **THEN** attention provenance follows `REQ-core-notify-026` with fixed failure
  semantics and no notification claim
- **AND** an attention write failure cannot erase the attempt, while an
  attention row cannot create, upgrade, or replace a recovery verdict

### Requirement: Recovery Authority and Compatibility Gates

The recovery contract SHALL roll out additively and fail closed. Specification,
owner adoption, implementation, merge, deployment, restore execution, and
production evidence SHALL remain separate authorities. Repository or disposable
PostgreSQL evidence SHALL NOT be represented as proof that production backups
exist or that a production artifact restored.

ID: REQ-backup-recovery-truth-006
Source: Non-Negotiable Rules 1 and 4; craft-and-care testing and verification; bu-e1410 and bu-lw18o
Scope: v1-mandatory

#### Scenario: Producer-first rollout preserves old readers

- **WHEN** the new recovery evidence is introduced
- **THEN** producer coverage and artifact binding land before fail-closed
  consumers, and existing backup facts remain readable throughout rollout
- **AND** a new frontend treats an absent `recovery_proof` as `unproven`, while
  an old frontend ignores the additive field

#### Scenario: Rollback cannot improve or erase truth

- **WHEN** the newest API/UI or verifier layer is rolled back
- **THEN** existing artifacts, run receipts, and protected attempt history stay
  intact and the prior surfaces continue reporting their narrower facts
- **AND** rollback neither deletes evidence nor reinterprets missing proof as a
  successful recovery

#### Scenario: Production evidence remains an external gate

- **WHEN** specification or implementation passes review and disposable tests
- **THEN** `bu-e1410` remains open until deferred owner/operations task
  `bu-lw18o` supplies the separately authorized production observations
- **AND** no code, test, CI, PR, or merge result claims a production backup
  exists, establishes a production loss window, or records a production restore

#### Scenario: Adoption does not authorize operation

- **WHEN** the owner later adopts the exact reviewed specification
- **THEN** that adoption authorizes only subsequent implementation planning
- **AND** it grants no credential access, dump/restore execution, migration,
  deployment, production read, merge, or operational evidence authority
