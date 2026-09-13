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

### 3. Coverage is closed under schema-qualified dependencies in the dump snapshot

The producer opens one repeatable-read, read-only transaction, exports its
snapshot, enumerates the complete in-scope schema-qualified relation and
foreign-key graph, acquires `ACCESS SHARE` locks on that ordered relation set,
and rechecks the graph before starting `pg_dump --snapshot=<snapshot-id>`. It
keeps the exporting transaction and locks until dump and manifest capture
finish. Every retained foreign-key child requires its referenced parent in the
same artifact, or both must share one documented reconstructible-control-plane
omission. A matching unqualified relation name is never evidence.

A DDL change committed before snapshot acquisition appears in both outputs. An
`ALTER TABLE`, foreign-key add/drop, or relation drop that arrives after locks
waits until publication. A new relation committed after snapshot acquisition is
absent from both outputs. If catalog identity changes, a lock cannot be
acquired, or the recheck differs, no final pair is published. Real-PostgreSQL
tests force both orders for foreign-key add/drop and relation create/drop.

The existing bidirectional fenced-object check remains necessary but is not
sufficient: it proves privilege/exclusion agreement, while this boundary proves
the artifact is internally closed. Auto-excluding `fleet_case_evidence` is
rejected because it is ordinary application evidence, not reconstructible
trusted-bootstrap state.

### 4. One versioned manifest binds coverage without becoming dashboard authority

The adopted `REQ-deployment-hardening-008` manifest stays authoritative for the
exact `connectors.filtered_events` scope. The producer-first rollout advances
that single sibling to `backup-recovery.v2`, retaining the filtered-event scope
and adding only a digest of the ordered retained relation/FK graph, a digest of
the authoritative credential-store inventory, one aggregate credential-row
count, a digest of normalized ownership/ACL policy, `coverage_complete=true`,
and the same snapshot capture boundaries.

No per-store count, credential key/value, row content, private identifier,
owner name, ACL grantee list, function body, or raw catalog output is stored.
The digests remain private artifact bindings and never public telemetry.
Unknown fields, a v1-only manifest, invalid digests/counts, or a missing
completion marker cannot produce full recovery proof. There is exactly one
sibling manifest, not separate generic and filtered-event files.

The API still derives artifact health from direct integrity verification and
bounded memoization. A manifest makes scoped restore eligible and binds the
protected result; it cannot make an unreadable artifact healthy, substitute for
a restore, or become a public manifest endpoint.

### 5. Ownership, ACL, and credential proof runs in the same protected attempt

The canonical artifact retains ownership and ACL intent. The protected attempt
creates a fresh isolated PostgreSQL target with no network path or credential to
the live database. A per-attempt ephemeral cluster-superuser exists only inside
that disposable target. The executor may invoke it only through the target-
local bootstrap/restore/check lifecycle; it receives no live cluster-superuser,
migration, credential-store, or fenced-owner membership. Target destruction is
mandatory and a cleanup failure makes the attempt fail.

The target runs managed bootstrap first, restores the exact artifact, and
verifies its catalog against both the manifest's ownership-policy digest and
the checked-in bootstrap contract. The normalized check covers every expected
schema/table/sequence owner; role attributes and memberships; function owner,
SECURITY DEFINER flag, body digest, and fixed `search_path`; explicit relation,
schema, function, and sequence ACLs; default ACLs; RLS enable/force flags and
policy definitions; and absence of unexpected grants, memberships, policies,
functions, or owner drift. It reads no application row value.

The producer obtains credential inventory and one aggregate row count through
a narrow bootstrap-owned coverage function under the exported dump snapshot.
That function enumerates authoritative Tier-1 stores in every applicable schema
and Tier-2 secured `public.entity_info` rows, proves the dump identity has
complete non-RLS-filtered visibility for every store, and returns only an
inventory digest plus aggregate count. The isolated target recomputes the same
aggregate after restore. Empty and populated stores are valid; a missing store,
filtered visibility, inventory mismatch, or aggregate mismatch fails the
attempt. Per-store counts remain transient and are never persisted or exposed.

An owner-neutral scratch technique cannot produce
`recovery_proof.status="proven"` unless the same protected attempt completes all
ownership/ACL and credential checks above. CI fixtures and documentation verify
the path but are not per-artifact result authority. Broad live-role membership
and `--no-owner`/`--no-acl` without retained intent remain rejected.

### 6. One protected attempt row owns every projected artifact fact

The existing executor-owner ledger remains sole authority. The artifact-bound
transition in `artifact-bound-filtered-event-restore-verification`
`REQ-database-security-011` extends the same row and transaction; it adds no
receipt table. The row binds overall/scoped results, exact artifact and manifest
digests, manifest capture completion, verified artifact completion timestamp
and byte size, coverage-manifest digest, ownership/ACL result and policy digest,
credential-coverage result, and fixed overall recovery scope.

The protected writer accepts completion/size only with a verified artifact
digest and enforces all pass/fail/nullability constraints. `latest_result()`
returns time and size from that row. The API may use a digest-verified internal
join, but must never combine the row with whichever filesystem artifact is
newest. Public audit is telemetry and attention is a failure signal, never
authority.

The single executor serializes scratch lifecycle. Repeating an artifact
recomputes verification and creates a distinct immutable attempt. A pre-commit
crash leaves no row, a post-commit crash leaves one complete row, and retries
cannot reuse an older scoped pass. Multi-executor operation remains blocked on
the cross-process guard in `REQ-deployment-hardening-007`.

### 7. The API adds one closed content-blind recovery projection

`GET /api/system/backups` gains additive `recovery_proof` fields:

- `status`: `proven`, `unproven`, `failed`, `stale`, or `degraded`;
- `attempted_at`: UTC timestamp or null;
- `artifact_completed_at`: UTC timestamp or null;
- `artifact_size_bytes`: non-negative integer or null;
- `scope`: `application_data`, `application_data_with_filtered_events`,
  `full_recovery`, or null;
- `failure_code`: the fixed value selected below or null.

`failure_code` is exactly one of `authority_unavailable`, `no_attempt`,
`legacy_unbound_artifact`, `artifact_identity_mismatch`, `manifest_missing`,
`manifest_malformed`, `manifest_unsupported`, `manifest_incomplete`,
`capture_consistency_invalid`, `coverage_not_fk_closed`,
`credential_visibility_incomplete`, `credential_count_mismatch`,
`restore_failed`, `ownership_acl_mismatch`,
`filtered_event_verification_failed`, `cleanup_failed`, or `proof_stale`.

| Source condition | Status | attempted_at | artifact time/size | scope | failure_code |
| --- | --- | --- | --- | --- | --- |
| protected reader unavailable | `degraded` | null | null | null | `authority_unavailable` |
| no protected row | `unproven` | null | null | null | `no_attempt` |
| legacy row or incomplete binding | `unproven` | row time | only when digest-verified | `application_data` or null | `legacy_unbound_artifact` |
| artifact/manifest binding failed | `failed` | row time | null | null | exact binding/capture code |
| bound FK/credential visibility failed before restore | `failed` | row time | both non-null | null | exact coverage code |
| bound restore or scoped check failed | `failed` | row time | both non-null | highest completed scope or null | exact stage code |
| cleanup failed | `failed` | row time | both non-null | highest completed scope below `full_recovery` | `cleanup_failed` |
| newest complete pass outside cadence | `stale` | row time | both non-null | `full_recovery` | `proof_stale` |
| newest complete pass inside cadence | `proven` | row time | both non-null | `full_recovery` | null |

`application_data` is legal only for a non-proven attempt that reached generic
restore verification. `application_data_with_filtered_events` is legal only
when exact filtered-event verification completed but ownership/ACL or credential
coverage did not complete. `full_recovery` requires same-attempt artifact
identity, FK closure, credential coverage, filtered-event scope, ownership/ACL,
and cleanup passes. The newest authoritative row wins: a new failure outranks an
older pass, and no field comes from another row or a newer filesystem artifact.
Within one row, cleanup failure is terminal; otherwise the first failed ordered
stage selects its exact code. Artifact/manifest binding failures expose no
artifact facts; pre-restore coverage failures expose bound facts with null
scope; restore failure has null scope; credential or filtered-event failure has
`application_data`; and ownership/ACL failure may use
`application_data_with_filtered_events` only after that checker passed.

No path, filename, schema/table name, owner/role, digest, credential metadata,
per-table/store count, or free-form client output is exposed. `table_count`
remains absent because the protected writer discards the caller-supplied value.
Pre-change backends omit `recovery_proof`; new frontends treat omission as
`unproven`. Rollback removes the projection without changing artifacts/history.

The tile relabels its narrow green verdict `Artifact healthy`, presents `Last
proven restore` separately, and follows the same precedence matrix. Only current
`proven` is green. Missing/stale is amber, current failure red, and degraded
unavailable. Text, not color alone, carries state; detail is keyboard and
assistive-technology accessible; loading does not block the System page. No
run-now control is added.

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
