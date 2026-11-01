# QA Publication Boundary

QA automated publication is disabled in production (program bu-mc01y5). The repository holds the
trusted deterministic publisher (slice 3, below) and the durable ledger it writes, but no production
configuration injects a publisher, so a committed investigation still fails `publication_unavailable`
until process isolation is proven (bu-mc01y5.7). The governing requirements are
`REQ-qa-publication-authority-002` (immutable binding), `-003` (closed operations), `-004` (whole
reachable artifact sanitization) and `-006` (durable ambiguous outcome recovery) in
`openspec/changes/confine-qa-publication-authority`.

## Publisher architecture

The package `butlers.core.qa.publication` has four parts. The dispatcher calls
`proposal.publish_investigation` only when `QaDispatchConfig.publisher` is set.

1. Sealed export (`export.py`). The dispatcher reads the QA branch head once with hardened git and
   binds it. `build_sealed_export` then reads raw objects with `git cat-file --batch`. It runs against
   a fresh, empty, publisher-owned repository whose `GIT_OBJECT_DIRECTORY` is the agent's object
   store, so no agent hook, config, `core.hooksPath`, fsmonitor, filter, driver, include or
   credential helper is ever consulted. Every object is re-hashed against its id, so a rewritten
   loose object is refused (`object_hash_mismatch`). The walk from head to the trusted base commit
   accepts only single-parent commits with plain `tree`/`parent`/`author`/`committer` headers. Author
   and committer must equal the configured QA identity (`BUTLERS_QA_GIT_AUTHOR_*`). The message,
   every new path and every added line of every new blob must pass `validate_anonymized()`. Git
   bytes are content-addressed, so agent content is validated, not rewritten. A leak in an earlier
   commit that a later commit removes is still reachable and still refuses. Base content is already
   public and is not re-validated. The result is a frozen `SealedExport` (objects plus SHA-256
   digest). `verify()` re-derives both before every transport call.
2. Closed transport (`transport.py`). `ClosedTransport` is the only caller of a
   `PublicationProvider`, a protocol with exactly four effects: a ref update, PR create, PR update
   and set labels. There is no merge, review, approval, queue or generic call. Each request is
   authorized against the binding before the provider is reached. A ref update must target the
   bound `qa/` branch, which must not be the base, and must move to the bound head. It is never
   forced, never deletes, and carries the expected old object id (`None` means the branch must not
   exist yet). The provider's atomic compare turns a stale head into a refusal. PR create must use
   the bound head and base. Update and labels must name the PR this attempt created, and labels
   must be exactly the fixed sanitized set. The provider's repository is the fixed origin; the
   binding cannot name another one.
3. Publisher (`publisher.py`). `QaPublisher.publish` verifies the export, refuses when the
   repository whitelist is missing or denies the repository, and anonymizes and validates the PR
   title, body (including the raw-evidence markers) and labels. A failure there is
   `anonymization_failed` before any egress. It then runs `branch_push`, `pr_create` and `pr_labels`.
   Each operation is persisted at `bound`, then moved to `egress_started` by `mark_egress_started`.
   That call locks the attempt row (`SELECT ... FOR UPDATE`) and refuses `attempt_not_active` unless
   the attempt is still active, so a timed-out or terminal attempt cannot start egress. The publisher
   then makes exactly one provider call and records the outcome. A lost response, or any unexpected
   error after egress started, is recorded `ambiguous` and stops the sequence. A rerun for the same
   attempt reuses recorded successes, returns a held operation as ambiguous, and never re-egresses.
   Nothing is ever deleted.
4. Dispatcher (`dispatch.py`). On `published`, the attempt moves to `pr_open` with the PR number, a
   `drafted` journal event is recorded and the local branch is kept. On `anonymization_failed`, the
   counter `qa_anonymization_failed_total` is incremented and the attempt moves to
   `anonymization_failed` with an `escalated` event that carries only the content-free category. On
   refused, failed or ambiguous, the attempt fails with `publication_<status>: <category>` and is
   escalated.

Review follow-ups still record `publication_unavailable`. The ledger allows one operation per attempt
and kind, so pushing a follow-up to an existing PR needs its own operation identity (follow-up bead).
No concrete credentialed provider ships yet. Activation needs bu-mc01y5.7 (isolation) and the
operational gates bu-nh37dt/bu-vc90le.

The sealed export lives in publisher memory for one run. The ledger keeps only its digest (inside
the `branch_push` payload digest), next to the persisted diff snapshot. Durable retention of the
export bytes before worktree teardown is a follow-up.

Rollback: remove the injected publisher (the default). Ledger rows, holds and diff snapshots are
kept, and no remote branch or PR is deleted.

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

The dashboard's admin retry (`POST /api/healing/attempts/{id}/retry`) skips the cooldown gate, so it
checks the hold itself (`get_publication_hold`): while the fingerprint is held it answers HTTP 409
`publication_lineage_held`, inserts no attempt and dispatches no agent.

## Access and rollback

Only `butler_qa_rw` can insert or update rows: forced row security admits no other writer, because
`scripts/init-db.sql` re-grants public-schema DML to every runtime role on each run and a policy is
not a grant. Every runtime role can read the ledger so any dispatcher sees a hold. The bootstrap
re-grants DELETE, so a QA-role DELETE silently affects 0 rows (forced row security, no DELETE policy);
it is not refused with an error. `core_268` additionally refuses deleting a held row for any role that
reaches the row, including a privileged login that bypasses row security. Residual: the owner/runtime
login keeps TRUNCATE (an implicit right that cannot be revoked) and ALTER/DROP POLICY; no runtime path
truncates. TRUNCATE remains out of scope and is not guarded. The triggers are not absolute against a
privileged operator either: a superuser can `SET session_replication_role = replica`, which skips
ordinary triggers, and the table owner can disable or drop a trigger.

The core chain runs once per butler schema over one shared table, so only the last schema to leave
`core_266` drops it (earlier schemas leave it in place). That downgrade drops the table only while no row is `egress_started` or `ambiguous`; otherwise it
raises and each held operation needs a manual, recorded reconciliation first. Rows are never deleted
automatically.

Migration `core_267` makes the database enforce what the writer would otherwise only promise. A CHECK
(`ck_qa_publication_operations_exact_reconciliation`) allows a reconciled row only as `resource_found`
naming exactly the bound `expected_object_id`, or `resource_absent` naming no object, and refuses a
reconciliation on any other row. A BEFORE UPDATE trigger (`qa_publication_operations_guard`) refuses a
stage regression or same-order rewrite (for example `ambiguous` to `succeeded`), any change to a
reconciled row, and any change to a frozen column: `id`, `attempt_id`, `kind`, `binding_digest`,
`request_digest`, `expected_object_id` and `started_at`. It fires for every role. As for `core_266`, only the last schema to leave
`core_267` removes the shared trigger, function and constraint.

Migration `core_268` extends that guard without replacing it. `new_object_id` and `result` are
set-once: the writer may fill either (NULL to a value) or repeat the same value, but a recorded value is
never changed or cleared, so a reconciled row keeps the outcome it was reached from. A CHECK
(`ck_qa_publication_operations_stage_result`) ties `result` to `stage` using only the values the writer
produces: `bound` and `egress_started` carry none, `succeeded` carries `success`, `failed` carries
`failed`, `ambiguous` carries `ambiguous`, and a `reconciled` row carries none (reached from
`egress_started`) or `ambiguous` (reached from `ambiguous`). A `succeeded` or `failed` row without
its result is refused. Residual: an `ambiguous` row may carry a NULL result. The writer never produces
one and holds are keyed on stage, not result, so it cannot release a hold; bu-mc01y5.15 closes it
later. A BEFORE DELETE trigger (`qa_publication_operations_no_held_delete`) refuses deleting a row in
the hold predicate's stages (`egress_started`, `ambiguous`), including from a privileged login; rows in
any other stage can still be deleted by a role that can reach them. Only the last schema to leave
`core_268` removes the delete trigger, its function and the CHECK, and restores the `core_267` guard
body.

`deploy/backup/pg_dump.sh` excludes the table because forced row security fences the dump role. A
restore therefore comes back without holds; the restore-time fence is owned by bu-mc01y5.7 and
publication stays disabled until it lands.
