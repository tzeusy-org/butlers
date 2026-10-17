# QA Publication Boundary

QA automated publication is disabled (program bu-mc01y5, slice 1). The state described here is the
durable ledger the future publisher will use; nothing in the repository publishes yet. The governing
requirements are `REQ-qa-publication-authority-002` (immutable binding) and `-006` (durable ambiguous
outcome recovery) in `openspec/changes/confine-qa-publication-authority`.

## Durable operations and holds

`public.qa_publication_operations` (migration `core_266`) holds one row per `(attempt, operation
kind)`, where the kind is one of the closed set `branch_push`, `pr_create`, `pr_update`, `pr_labels`.
A row records the stage, a digest of the dispatcher-held binding (repository, branch, base, head SHA,
PR number), a digest of the validated payload, the expected and new object identifier, a sanitized
result and timestamps. It never stores a credential, provider body or private identifier.

The trusted dispatcher drives `butlers.core.qa.publication.state`:

1. `begin_operation` persists the row at `bound` before any egress. A duplicate request with the same
   binding returns the existing row (the caller egresses only from `bound`); a changed binding or
   payload is refused with `binding_changed` or `payload_changed` and the stored row is not touched.
   A datastore failure raises `PublicationRefused("datastore_unavailable")`: publication fails closed.
2. `mark_egress_started` is won by exactly one caller and is persisted before the provider call.
3. `record_outcome` stores `succeeded`, `failed` or `ambiguous` (for example a lost response).
4. `record_reconciliation` releases a hold only for an exact, read-only observation: `resource_found`
   must name the bound `expected_object_id`, or `resource_absent` confirms nothing exists.

An operation in `egress_started` or `ambiguous` holds its fingerprint lineage. While it does, a fresh
QA attempt is refused (`create_or_join_attempt(..., enforce_publication_hold=True)` raises
`PublicationLineageHeld`; the dispatcher answers `publication_held`), triage deduplicates the finding
as `publication_held`, and the cooldown gate (`get_recent_attempt`) reports the hold regardless of its
window. Watchdog timeout, `recover_stale_attempts` and process restart may close the old attempt, but
the hold lives in the ledger and survives them. Only a recorded reconciliation releases it.

## Access and rollback

Only `butler_qa_rw` can insert or update rows: forced row security admits no other writer, because
`scripts/init-db.sql` re-grants public-schema DML to every runtime role on each run and a policy is
not a grant. Every runtime role can read the ledger so any dispatcher sees a hold. The bootstrap
re-grants DELETE, so rows cannot be deleted only because no DELETE policy exists. Residual: the
owner/runtime login keeps TRUNCATE (an implicit right that cannot be revoked and is not guarded) and
ALTER/DROP POLICY; no runtime path truncates.

The core chain runs once per butler schema over one shared table, so only the last schema to leave
`core_266` drops it (earlier schemas leave it in place). That downgrade drops the table only while no row is `egress_started` or `ambiguous`; otherwise it
raises and each held operation needs a manual, recorded reconciliation first. Rows are never deleted
automatically.

Migration `core_267` makes the database enforce what the writer would otherwise only promise. A CHECK
(`ck_qa_publication_operations_exact_reconciliation`) allows a reconciled row only as `resource_found`
naming exactly the bound `expected_object_id`, or `resource_absent` naming no object, and refuses a
reconciliation on any other row. A BEFORE UPDATE trigger (`qa_publication_operations_guard`) refuses a
stage regression or same-order rewrite (for example `ambiguous` to `succeeded`), any change to a
reconciled row, and any change to the attempt binding, kind, `binding_digest`, `request_digest` or
`expected_object_id`. It fires for every role. As for `core_266`, only the last schema to leave
`core_267` removes the shared trigger, function and constraint.

`deploy/backup/pg_dump.sh` excludes the table because forced row security fences the dump role. A
restore therefore comes back without holds; the restore-time fence is owned by bu-mc01y5.7 and
publication stays disabled until it lands.
