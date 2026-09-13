## Context

[Observed] `deploy/backup/pg_dump.sh` already publishes only a non-empty,
gzip-valid artifact, writes a database-independent `last_run.json`, and names
its trusted-bootstrap exclusions. `src/butlers/core/backup_facts.py` verifies at
most seven artifacts synchronously but memoizes by path, mtime, and size, so the
old claim that every dashboard request necessarily decompresses seven unchanged
artifacts is stale.

[Observed] The isolated `restore_drill_executor` process and its private
`restore_drill_executor.restore_drill_results` ledger are present. The dashboard
reads the ledger only through `latest_result()`, and `BackupTile` already renders
pass, fail, pending, and degraded drill states. The active
`restore-drill-recovery-truthfulness` change owns result-aware retry, failure
provenance, attention, and failure-age work; `bu-kqnum.8.6` still owns a real
PostgreSQL dump-to-scratch proof.

[Observed] The current dump excludes `public.fleet_cases` and
`public.fleet_case_links` while the foreign-key child
`public.fleet_case_evidence` is not excluded. The existing coverage test proves
that exclusions match relations unreadable to the dump role, but it does not
prove that the published set is closed under foreign keys or that the result
restores.

[Observed] `docs/operations/backup-restore.md` rejects applying `--no-owner` to
the canonical artifact because that discards the artifact's only ownership
record. RFC 0006 defines `butler_secrets` and secured `entity_info` as
authoritative credential stores. Silently removing those rows would make a
backup less sensitive but would also make it unable to recover the running
system.

[Unknown] No production backup inventory or production restore result was read.
`bu-lw18o` remains deferred, so neither repository evidence nor a disposable
PostgreSQL test can establish the production data-loss window or prove a
production artifact recoverable.

## Goals / Non-Goals

**Goals:**

- Compose existing evidence into one precise recovery-proof state without
  weakening or duplicating its authorities.
- Make application-data coverage, credential recovery, foreign-key closure,
  ownership/ACL integrity, and exact-artifact binding prerequisites for a
  positive recovery claim.
- Keep operational evidence content-blind and make the owner-facing state fast,
  accessible, and explicit about what was and was not proven.
- Give later implementation work an additive rollout and rollback sequence that
  cannot turn unknown or partial evidence green.

**Non-Goals:**

- Encrypting backup media or introducing a recovery-key lifecycle. That is a
  separate security and owner-authority decision; this change neither claims
  plaintext artifacts are safe for broader access nor creates a key.
- Removing credentials from recovery, changing credential authorities, or
  adding a secret export surface.
- Replacing the protected drill ledger, adding `public.restore_drills`, or
  treating `public.audit_log` or `public.attention_ledger` as result authority.
- Adding a run-now API, changing the single-executor topology, or authorizing a
  dump, restore, deployment, migration, production read, or owner adoption.

## Decisions

### 1. Add one composition capability and no competing whole-requirement block

This change adds `backup-recovery-truth` rather than modifying `Backup State
Facts`, `Weekly Restore Drill`, `Backup And Restore Verification Path`, or
`Attention Ledger Reader`. Those requirements already have active whole-block
deltas. The new capability defines when their separate facts jointly support a
recovery claim and references their existing requirement IDs.

Alternative considered: add another `MODIFIED` block to each owning capability.
Rejected because OpenSpec archives whole requirements and the active changes
would race to delete each other's clauses.

### 2. Recovery coverage includes credential-bearing application rows

The recovery artifact remains a complete, high-sensitivity copy of recoverable
application state. It includes the authoritative credential-bearing rows needed
to restart the instance, including each schema's `butler_secrets` and secured
credential rows in `public.entity_info`. Neither values, keys, row counts, nor
digests derived from them may enter a manifest, API, UI, audit, attention, log,
metric, or trace.

The artifact is therefore handled as secret material at the deployment/storage
boundary. This change does not invent encryption keys or claim media encryption.
If the owner later chooses encrypted artifacts or explicit credential
reprovisioning, that is a separate exact-artifact security decision with its own
loss, rotation, restart, and recovery contract.

Alternative considered: omit or redact `butler_secrets`. Rejected because it
silently turns a complete recovery claim into a partial application restore and
contradicts the current credential authority model.

### 3. Coverage is closed under schema-qualified dependencies

The producer computes the recoverable relation set from PostgreSQL catalogs in
the same captured state as the dump. Every retained foreign-key child requires
its referenced parent to be retained, or both must be excluded under one
documented reconstructible-control-plane decision. Fully qualified schema and
relation identity is mandatory; a matching unqualified name is not evidence.

The existing bidirectional fenced-object check remains necessary but is not
sufficient. It proves privilege/exclusion agreement; the new closure check
proves the resulting artifact is internally restorable. A run with an orphaned
dependency fails before publication and writes only its existing fixed run
failure receipt.

Alternative considered: list `fleet_case_evidence` as another exclusion.
Rejected as an implicit data-loss decision: the table is ordinary application
evidence, not proven reconstructible trusted-bootstrap state.

### 4. The scoped manifest does not become dashboard health authority

The adopted `REQ-deployment-hardening-008` manifest stays authoritative for the
exact `connectors.filtered_events` snapshot, structure, and counts it names. It
is paired atomically with its artifact and bound by basename, size, SHA-256, and
capture time as already specified. This change neither creates a second generic
manifest nor widens that manifest with credentials, row content, or unrelated
schema inventory.

The API continues to derive artifact health from the existing direct integrity
check and its bounded memoization. A manifest can make a scoped restore eligible
and can bind the protected result; it cannot make an unreadable artifact
healthy, substitute for a restore, or become a public manifest endpoint.

Alternative considered: replace API integrity reads with a producer manifest.
Rejected because it would make producer assertion substitute for independent
artifact verification and conflict with `REQ-system-overview-page-005`.

### 5. Ownership and ACL proof is bootstrap-first and two-part

The canonical recovery artifact retains ownership and ACL intent. The managed
recovery procedure establishes trusted roles and bootstrap-owned interfaces
before an artifact is eligible for promotion, restores application data only
into an isolated target, and then verifies the restored catalog against the
artifact's declared ownership/ACL intent and the managed bootstrap policy.

An owner-neutral scratch technique may be used to exercise data restoration
only when the original ownership/ACL intent is retained and checked separately.
It cannot by itself produce `recovery_proof.status="proven"`. The automated
weekly executor remains least-privileged and receives no cluster-superuser,
migration, credential-store, or fenced-owner membership. Full promotion-ready
ownership proof therefore lives in disposable real-PostgreSQL integration
evidence plus the documented managed recovery procedure; the weekly drill
records the exact data-restore and scoped-check results it actually performed.

Alternative considered: grant the executor broad role membership or use
`--no-owner`/`--no-acl` without replacement evidence. Rejected because either
widens live privilege or discards the provenance the proof needs.

### 6. One protected attempt row owns exact-artifact truth

The existing executor-owner ledger remains the sole result authority. The
artifact-bound transition in `REQ-database-security-009` extends the same row
and transaction; it does not add a receipt table. One attempt binds its overall
result, scoped result, artifact digest, manifest digest, and capture completion.
The public audit row is telemetry and the restore-drill attention row is a
failure signal, never authority.

The single executor serializes scratch lifecycle. Repeating the same artifact
recomputes verification and records a distinct attempt; an exact committed row
is immutable. A crash before commit leaves no authoritative attempt, a crash
after commit leaves one complete row, and no retry may reuse an older scoped
pass or associate it with another artifact. Multi-executor deployment remains
blocked on the cross-process guard required by `REQ-deployment-hardening-007`.

### 7. The API adds one content-blind recovery projection

`GET /api/system/backups` gains an additive `recovery_proof` object:

- `status`: `proven`, `unproven`, `failed`, `stale`, or `degraded`;
- `verified_at`: UTC timestamp or null;
- `artifact_completed_at`: UTC timestamp or null;
- `artifact_size_bytes`: non-negative integer or null;
- `scope`: fixed `application_data` or `application_data_with_filtered_events`;
- `failure_code`: fixed low-cardinality code or null.

No path, filename, schema/table name, owner/role, digest, credential metadata,
row count by table, or free-form client output is exposed. Pre-change backends
omit the field; a new frontend treats omission exactly like `unproven`.
Rollback removes the field and returns to the existing three-row presentation
without changing artifacts or protected history.

The projection deliberately omits `table_count`. The protected writer already
discards the executor's caller-supplied count because the executor credential
must not be able to manufacture authoritative evidence. A count may remain in
ephemeral scratch diagnostics, but it cannot become owner-facing proof without
a separately specified authority that derives it rather than trusting the
caller.

The Backups tile relabels its existing green artifact verdict as `Artifact
healthy`, then presents `Last proven restore` separately. Only a current
protected pass within the policy window is green. Pending/missing proof is
amber and says `No proven restore`; stale is amber with its age; current failure
is red with a fixed reason and accessible detail disclosure; degraded is an
unavailable state, never calm. The tile remains useful while loading and
exposes no run-now control in this change.

### 8. Attention reports failure but never claims contact or proof

The implementation reuses `REQ-core-notify-026`: only a durably recorded failed
attempt may create a `source="restore_drill", outcome="failed"` attention row.
Pass, no artifact, unproven compatibility state, and an API read failure create
no synthetic failure or notification. Attention failure cannot erase or alter
the authoritative attempt, and attention success cannot make recovery proven.

### 9. Operational evidence and authority remain separate

Repository tests can prove the contract against synthetic data and disposable
PostgreSQL. They cannot establish whether production backups exist, when the
last one ran, or whether a production artifact has restored. `bu-e1410` remains
open behind deferred owner/operations evidence `bu-lw18o`. Spec review, owner
adoption, implementation review, merge, deployment, and operational execution
are separate gates.

## Risks / Trade-offs

- [Risk] Including credentials makes the artifact highly sensitive. -> Treat
  the whole artifact as secret material and keep all projections content-blind;
  do not imply encryption or broaden access.
- [Risk] A composition spec can drift from its source capabilities. -> Cite the
  source requirement IDs, keep this change additive, and require terminal
  cross-change reconciliation before implementation completion.
- [Risk] Foreign-key closure can expose an existing exclusion as data loss. ->
  Fail publication rather than auto-excluding the child or widening dump-role
  privileges.
- [Risk] The owner may read `Artifact healthy` as recovery proof. -> Use distinct
  labels and state hierarchy; only `Last proven restore` communicates recovery.
- [Risk] Older backends and artifacts lack the new projection or scoped
  manifest. -> Render `unproven`, retain current artifact/run facts, and activate
  fail-closed proof only after a new valid pair and protected attempt exist.
- [Risk] A weekly drill cannot itself exercise cluster-superuser bootstrap. ->
  Keep its claim scoped and require real disposable-cluster bootstrap/restore
  evidence before calling the overall procedure promotion-ready.

## Migration Plan

1. Land this specification only after exact-head independent security,
   database, API, and UX review plus separate owner adoption.
2. Repair producer coverage first: schema-qualified foreign-key closure,
   credential inclusion assertions, fixed failure classification, and no
   partial publication. Preserve current artifact consumers.
3. Implement the already-adopted artifact/manifest pair and protected attempt
   transition, then prove bootstrap-first ownership/ACL recovery and real
   dump-to-scratch behavior in disposable PostgreSQL.
4. Extend the existing API and frontend additively. Old frontends ignore
   `recovery_proof`; new frontends treat an absent field as `unproven`.
5. Complete result-aware retry and attention work through the existing active
   change, then reconcile all source requirements and tests once.

Rollback reverses only the newest code/schema layer in its separately reviewed
migration. Existing artifacts, run receipts, and protected result rows remain;
the prior API/UI continues showing artifact, run, and drill facts. No rollback
may delete recovery evidence or reinterpret an unproven state as healthy.
