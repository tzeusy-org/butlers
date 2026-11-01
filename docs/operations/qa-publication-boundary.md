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

1. Sealed export (`export.py`). The dispatcher reads the QA branch head once from the ref files
   (no git process, no symlinks) and binds it. `build_sealed_export` then parses raw loose and
   packed objects in-process (`object_store.py`); no git process runs against the agent's object
   store, so no agent hook, config, `core.hooksPath`, fsmonitor, filter, driver, include or
   credential helper is ever consulted. Every object-store file is opened relative to a directory
   descriptor with `O_NOFOLLOW | O_NONBLOCK` and must be a bounded regular file. A planted
   `objects/info/alternates` or `http-alternates`, or a symlinked object, fan-out directory, pack or
   object directory, or a FIFO, refuses (`object_store_unsafe`) instead of widening which files the
   reader opens or hanging it. Delta chains (including `REF_DELTA` cycles) resolve
   iteratively under a depth cap and one per-read byte budget, and pack offsets and delta opcodes
   are bounds-checked, so hostile pack bytes refuse (`malformed_object`) instead of exhausting
   memory or escaping as an uncaught error. A blob declared over 1 MiB (any object over 20 MiB)
   refuses (`export_too_large`) from its loose header or delta target size, before its body is
   inflated or its chain resolved, and one 512 MiB work budget across every read of the export
   bounds total work (compressed bytes consumed included) however many hostile objects are
   planted; no zlib stream may consume more input than a deflate bound of its declared size. Every object is re-hashed against its id, so a rewritten loose object
   is refused (`object_hash_mismatch`). The walk from head to the trusted base commit
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
   error after egress started, is recorded `ambiguous` and stops the sequence. That unexpected error
   is logged by exception type name only, never its message or traceback. A definite provider
   refusal (`ProviderRejected`) reports `provider_rejected_<code>`, where `<code>` is one of
   `forbidden`, `not_found`, `conflict`, `validation_failed` or `rate_limited`; any other provider
   code becomes `unrecognized`, so provider text never reaches a category. On `pr_create` only
   the access refusals `forbidden` and `not_found` stay definite: a `failed` `pr_create` row is
   the case API's proof that no PR exists, and any other refusal may front a live PR, so the
   transport records it `ambiguous`. That covers GitHub's 422 "a pull request already exists for
   <head>" and `rate_limited`, which a retrying client can receive after an earlier try created
   the PR behind a lost response. A rerun for the same attempt reuses recorded successes, returns a held operation as ambiguous, and never re-egresses.
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

Before its first egress the publisher retains the sealed export's exact bytes in
`public.qa_publication_exports` (migration `core_294`, `butlers.core.qa.publication.retention`): one
immutable row per attempt with base, head, commit list, framed objects and digest. The agent worktree,
its branch and its objects are deleted at teardown, so a push or PR creation that fails or ends
ambiguous still leaves the proposal recoverable for read-only reconciliation, next to the persisted
diff snapshot, investigation notes and ledger stage. Retaining the same export again is a no-op; a
different export for the same attempt refuses (`export_changed`), and a store failure refuses
(`export_unretained`) with nothing sent. `load_retained_export` re-derives every object id and the
digest, so altered stored bytes refuse with `export_tampered`.

Only `butler_qa_rw` inserts (forced row security, as for the ledger); every runtime role reads, since
the bytes already passed `validate_anonymized()` and are what a successful publication makes public.
No role can update or delete a row: there is no UPDATE or DELETE policy, and a BEFORE UPDATE trigger
(`qa_publication_exports_immutable`) refuses rewrites from a privileged login too. Only the last schema
to leave `core_294` drops the table, and only while no export is retained. The table is excluded from
`deploy/backup/pg_dump.sh` for the same reason as the ledger. Pruning old retained exports is not
implemented.

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
   must name the bound `expected_object_id`, or `resource_absent` confirms nothing exists. A
   `pr_create` binds no PR number before egress, so its `resource_found` adopts the observed PR
   number instead (core_298).

`butlers.core.qa.publication.reconcile.reconcile_operation` is the read-only reconciler. It takes the
dispatcher-held binding (refused with `binding_mismatch` unless its digest matches the stored one) and
a `PublicationReader`, whose protocol has only two reads: `read_ref` and `find_pull_requests`. For
`branch_push`, the bound `qa/` branch at exactly the bound head is found and a missing branch is
absent; any other head stays held (`unexpected_head`). For `pr_create`, the adoption rule is a
head/base match: exactly one PR, open or closed, from the bound head branch onto the bound base is
adopted, none is absent, and more than one stays held (`multiple_candidates`). `pr_update` and
`pr_labels` bind no observable object and stay held (`unsupported_kind`). A reader error, a foreign
reader origin or a lost ledger write also keeps the hold. A rerun of the publisher treats a
`resource_found` reconciliation as success: it adopts the PR and continues to the labels, while a
`resource_absent` `pr_create` ends `failed` (`pr_create_reconciled`) without a fresh create. No caller
drives the reconciler yet: it needs a concrete read-only provider, which ships with the real GitHub
provider.

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

Migration `core_298` replaces that CHECK under the same name, keeping every `core_267` rule, and adds
one adoption arm: a `pr_create` that bound no expected object and recorded no new object may reconcile
`resource_found` naming a PR number, and its `new_object_id` must equal that number. The database
checks the shape; the reconciler supplies the head/base proof. Only the last schema to leave
`core_298` restores the `core_267` CHECK, `NOT VALID` while an adopted row exists.

Migration `core_268` extends that guard without replacing it. `new_object_id` and `result` are
set-once: the writer may fill either (NULL to a value) or repeat the same value, but a recorded value is
never changed or cleared, so a reconciled row keeps the outcome it was reached from. A CHECK
(`ck_qa_publication_operations_stage_result`) ties `result` to `stage` using only the values the writer
produces: `bound` and `egress_started` carry none, `succeeded` carries `success`, `failed` carries
`failed`, `ambiguous` carries `ambiguous`, and a `reconciled` row carries none (reached from
`egress_started`) or `ambiguous` (reached from `ambiguous`). A `succeeded` or `failed` row without
its result is refused. A BEFORE DELETE trigger (`qa_publication_operations_no_held_delete`) refuses deleting a row in
the hold predicate's stages (`egress_started`, `ambiguous`), including from a privileged login; rows in
any other stage can still be deleted by a role that can reach them. Only the last schema to leave
`core_268` removes the delete trigger, its function and the CHECK, and restores the `core_267` guard
body.

Migration `core_299` closes the historical ambiguous-NULL gap: both INSERT and UPDATE must
carry result `ambiguous` when stage is `ambiguous`. Upgrade canonicalizes only that result on
historical ambiguous-NULL rows through the existing QA write role under forced row security.
The transaction locks the table across backfill and CHECK replacement; bindings, stages,
reconciliation, effects and lineage holds remain unchanged. Every other stage/result pair and
the set-once, reconciliation and held-delete guards remain unchanged. Only the last schema to
leave `core_299` restores the historical `core_268` CHECK. Downgrade retains canonicalized
results, preserving set-once semantics and every unresolved hold.

`deploy/backup/pg_dump.sh` excludes the table because forced row security fences the dump role. A
restore therefore comes back without holds; the restore-time fence is owned by bu-mc01y5.7 and
publication stays disabled until it lands.
