## ADDED Requirements

### Requirement: Complete Recoverable Data Coverage

Every published recovery artifact SHALL contain all recoverable application
data required to restart the owner-controlled instance, including authoritative
credential-bearing rows. Its retained relation set SHALL be closed under
schema-qualified foreign-key dependencies. An omission SHALL be limited to
trusted-bootstrap control state that the managed bootstrap reconstructs, and
the omitted state and recovery cost SHALL be documented.

ID: REQ-backup-recovery-truth-001
Source: Non-Negotiable Rule 1; RFC 0006 Credential Store; deployment-hardening REQ-deployment-hardening-007
Scope: v1-mandatory

#### Scenario: Credential-bearing application state remains recoverable

- **WHEN** a backup artifact is published
- **THEN** it contains the authoritative credential-bearing application rows
  needed to recover the instance, including applicable `butler_secrets` and
  secured `entity_info` rows
- **AND** no manifest, receipt, API, UI, audit, attention, log, metric, or trace
  exposes their values, keys, per-store counts, or value-derived digests

#### Scenario: Published coverage is closed under foreign keys

- **WHEN** the producer determines the retained relation set in the dump's
  captured PostgreSQL state
- **THEN** every retained foreign-key child has its schema-qualified referenced
  parent retained in the same artifact
- **AND** a matching unqualified relation name, an excluded parent with a
  retained child, or an unresolved dependency fails the run before publication

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
after the managed trusted bootstrap has established the expected roles and
interfaces, application data has restored into an isolated target, and the
target's ownership, SECURITY DEFINER, search-path, role, membership, and ACL
state has passed the governed verification contract.

ID: REQ-backup-recovery-truth-002
Source: Non-Negotiable Rules 1 and 4; RFC 0006 Database Connection Scoping; database-security REQ-database-security-006 and REQ-database-security-009
Scope: v1-mandatory

#### Scenario: Managed bootstrap precedes promotion-ready verification

- **WHEN** an artifact is evaluated as a candidate for recovery
- **THEN** the target first establishes the exact trusted-bootstrap roles and
  interfaces required by the artifact's ownership and ACL intent
- **AND** no application role may connect to or promote the target until
  ownership, definer, search-path, role, membership, and ACL verification passes

#### Scenario: Owner-neutral scratch restore is scoped honestly

- **WHEN** the isolated executor uses an owner-neutral technique to exercise
  application-data restoration without receiving fenced-owner or superuser
  authority
- **THEN** the original artifact still retains its ownership and ACL intent and
  that intent is checked by the separate governed recovery verification
- **AND** the scratch result alone does not claim that a promotion-ready
  ownership boundary was restored

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
bind the overall result to the exact artifact and every required scoped verdict.
A public audit, attention row, filesystem timestamp, filename, run receipt, or
manifest alone SHALL NOT authorize or manufacture a recovery pass.

ID: REQ-backup-recovery-truth-003
Source: Non-Negotiable Rule 4; RFC 0005 Workflow and Recovery Telemetry; database-security REQ-database-security-009
Scope: v1-mandatory

#### Scenario: Scoped manifest binds only the scope it describes

- **WHEN** the selected artifact carries the adopted filtered-event manifest
- **THEN** its exact basename, byte length, SHA-256, manifest SHA-256, and
  capture completion bind the filtered-event verdict as required by
  `REQ-deployment-hardening-008` and `REQ-deployment-hardening-009`
- **AND** the manifest cannot substitute for direct artifact integrity,
  application-data restoration, ownership/ACL verification, or cleanup

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
`recovery_proof` object whose status is exactly `proven`, `unproven`, `failed`,
`stale`, or `degraded`. The System-page Backups tile SHALL distinguish artifact
health, last backup-run outcome, and last proven restore at first glance. Only a
current protected pass within the governing cadence SHALL render recovery as
green.

ID: REQ-backup-recovery-truth-004
Source: Non-Negotiable Rule 1; RFC 0007 Amendment 1; system-overview-page REQ-system-overview-page-005 and REQ-system-overview-page-006
Scope: v1-mandatory

#### Scenario: Current complete proof is green

- **WHEN** the protected authority records a complete passing attempt within
  the governing cadence
- **THEN** `recovery_proof.status` is `proven` with its UTC verification time,
  artifact completion time, artifact size, and fixed scope
- **AND** the tile renders `Last proven restore` with that age and does not
  imply that artifact presence or gzip health supplied the proof

#### Scenario: Missing or legacy proof remains unproven

- **WHEN** no authoritative attempt exists, a pre-change backend omits
  `recovery_proof`, or an otherwise healthy legacy artifact lacks required
  scoped evidence
- **THEN** the frontend renders an amber `No proven restore` state
- **AND** it retains truthful artifact-health and last-run facts without
  defaulting the recovery state to green or red

#### Scenario: Failed, stale, and degraded states stay distinct

- **WHEN** the current authoritative attempt failed, the last pass exceeded its
  cadence, or the protected result source is unavailable
- **THEN** the API and tile render `failed`, `stale`, or `degraded` respectively
- **AND** a historical pass does not hide a current failure, stale age is
  visible, and unavailable evidence is never presented as calm

#### Scenario: Artifact health is labeled as narrower evidence

- **WHEN** a latest artifact directly passes the existing integrity check
- **THEN** its green label states `Artifact healthy` rather than an unqualified
  recovery verdict
- **AND** a failed or unproven restore remains visible in the same card without
  requiring the owner to infer the difference from color alone

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
  and the exact private-ledger bindings required by adopted scoped verification
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
