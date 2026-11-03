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
No role can delete a row (there is no DELETE policy), and the only rewrite is a prune. The BEFORE
UPDATE trigger (`qa_publication_exports_immutable`) refuses every other rewrite, from a privileged login
too. Only the last schema to leave `core_294` drops the table, and only while no export row exists,
pruned or not. The table is excluded from `deploy/backup/pg_dump.sh` for the same reason as the ledger.

Retention (migration `core_301`, bu-mc01y5.25). A retained export's object bytes are kept until its
attempt has been terminal (`pr_merged`, `failed`, `unfixable`, `anonymization_failed`, `timeout`) for
30 days after `closed_at`, by database time, and none of its operations holds the lineage
(`egress_started` or `ambiguous`). Past that point `retention.prune_retained_exports` empties
`objects` and the guard stamps `pruned_at`. The row stays as a tombstone: base, head, commits and
digest keep binding the proposal, so the case API's `export_retained` proof and the sweep's binding
rebuild (`head_sha`) are unchanged. The guard enforces the same eligibility for every role and makes a
pruned row final. A BEFORE INSERT trigger (`qa_publication_exports_unpruned_insert`) refuses a row
inserted with `pruned_at` set, so the guarded UPDATE is the only way to a tombstone. The window is
measured from `healing_attempts.closed_at`, which no DML writer chooses: that table has no
row-security fence, so core_305's `healing_attempts_closed_at_stamp` trigger (every role) stamps
`closed_at` with database time when a row enters a terminal status, keeps it unchanged on any later
update of a terminal row, and clears it on a non-terminal row. Backdating a close needs the table
owner to disable the trigger. The lineage-hold check reads the fenced ledger. Only `butler_qa_rw` reaches a row for update (forced row security, UPDATE policy for
that role alone). A pruned export refuses with `export_pruned` on load and on re-retention. The QA
module runs the prune in the daily `qa_evidence_cleanup` job, at `retention_cleanup_hour`, 100 rows per
run. A prune failure is logged and does not fail the evidence cleanup. Only the last schema to leave
`core_301` removes the prune path, and only while no export is pruned, since an emptied row would
read back as tampered.

The retained bytes exist only for an attempt whose publisher reached retention. A refusal before
retention leaves no export, so the publisher records a receipt instead (migration `core_302`,
bu-mc01y5.28): one row per attempt in `public.qa_publication_refusals` holding the refusal's
content-free status (`refused` or `anonymization_failed`) and snake_case category code (for example
`repo_not_whitelisted`, `pr_labels_unsanitized`; the CHECK admits `^[a-z][a-z0-9_]{0,63}$` only). It covers every refusal
before retention: an export that cannot be sealed or bound, a failed preflight (tampered export,
unbound head, denied or missing whitelist), residual sensitive content in the PR text or labels, and
an export that cannot be retained. The insert policy refuses a receipt for an attempt that already
has a ledger operation, a retained export or a PR number, so a receipt only ever marks an attempt
that never reached retention. The first receipt wins and a BEFORE UPDATE trigger
(`qa_publication_refusals_immutable`) refuses rewrites from every role. The fence matches the
exports table: only `butler_qa_rw` inserts (forced row security), every runtime role reads, there is
no DELETE policy, the table is excluded from `deploy/backup/pg_dump.sh`, and only the last schema to
leave `core_302` drops it, and only while it is empty. A receipt that cannot be written is logged
and changes nothing, so the attempt reads `ambiguous`.

The case API reads `proposal_state` as `unpublished` only with a publisher trace (a retained export,
pruned or not, or a refusal receipt) and the ledger's proof that no PR egress can have landed. A
receipt alone never proves absence: a later run that reached egress is still judged by the ledger.

Rollback: remove the injected publisher (the default). Ledger rows, holds and diff snapshots are
kept, and no remote branch or PR is deleted.

## Durable operations and holds

The case dossier observes PR lifecycle read-only through the existing bounded GitHub metadata
client and TTL cache. An adopted PR's number, URL and branch remain visible when that lifecycle is
unavailable: the panel says `PR state unknown` and keeps Open PR. A failed, timed-out or otherwise
terminal investigation never proves that its PR closed. Explicit provider lifecycle supersedes the
local positive state, and only a positively merged observation supplies a merge timestamp; attempt
closure time is not that evidence. A checks lookup failure preserves a successful PR observation.
This display does not change attempt history, publication receipts, lineage holds or activation gates.

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
2. `mark_egress_started` is won by exactly one caller and is persisted before the provider call. It
   stamps `egress_started_at` with database time (core_300); `started_at` is the bind time.
3. `record_outcome` stores `succeeded`, `failed` or `ambiguous` (for example a lost response).
4. `record_reconciliation` releases a hold only for an exact, read-only observation: `resource_found`
   must name the bound `expected_object_id`, or `resource_absent` confirms nothing exists. A
   `pr_create` binds no PR number before egress, so its `resource_found` adopts the observed PR
   number instead (core_298). The same statement names the adopted PR on its attempt: it fills
   `healing_attempts.pr_number` and `pr_url` when the attempt names no PR yet, so the case API
   shows the PR the dispatcher never heard back about. An `INTEGER` overflow, or a URL that is not
   `https://github.com/.../pull/<number>`, skips that fill, never the reconciliation. It also takes
   the stage the caller observed (`expected_stage`): an outcome recorded between the read and the
   write moves the row off that stage, and the outcome wins.

`butlers.core.qa.publication.reconcile.reconcile_operation` is the read-only reconciler. It takes the
dispatcher-held binding (refused with `binding_mismatch` unless its digest matches the stored one) and
a `PublicationReader`, whose protocol has only three reads: `read_ref`, `find_pull_requests` and
`read_pull_request`. For
`branch_push`, the bound `qa/` branch at exactly the bound head is found and a missing branch is
absent; any other head stays held (`unexpected_head`). For `pr_create`, the adoption rule is a
head/base match: exactly one PR, open or closed, from the bound head branch onto the bound base is
adopted, none is absent, and more than one stays held (`multiple_candidates`). `pr_update` and
`pr_labels` bind the bound PR's number as `expected_object_id`, and the reconciler reads that PR, which
must still run from the bound head onto the bound base (`foreign_pr`; a PR that cannot be found stays
held as `pr_missing`, and a row bound without a PR number as `unbound_pr`). A `pr_update` is found when
the digest of the observed title and body equals the stored request digest, else absent. A
`pr_labels` takes the dispatcher's labels, refused with `payload_mismatch` unless their digest equals
the stored request digest (`labels_unbound` without them). It is found when every bound label is on
the PR, else absent. Both effects are idempotent, so a late landing after a recorded absence
duplicates nothing. A reader error, a foreign
reader origin or a lost ledger write also keeps the hold. A rerun of the publisher treats a
`resource_found` reconciliation as success: it adopts the PR and continues to the labels, while a
`resource_absent` `pr_create` ends `failed` (`pr_create_reconciled`) without a fresh create (likewise
`pr_labels_reconciled` without reapplying labels). Absence
is believed only once it has settled: a `resource_absent` observation stays held (`absence_settling`)
until the operation's outcome (`ended_at`) or, while still in flight, its egress (`egress_started_at`)
is at least `ABSENCE_SETTLE` (15 minutes) old, so a create or push that timed out but still lands
cannot free the lineage and let a fresh attempt open a duplicate PR. The bind time never counts: a bind
resumed long after it was written egresses fresh. A row with neither timestamp never settles
(`absence_unanchored`). The reconciler's own clock is only a pre-filter; `record_reconciliation`
enforces the settle against database `now()` inside its guarded UPDATE, so a skewed host clock cannot
release a hold (`not_recorded`).

`butlers.core.qa.publication.github_reader.GithubPublicationReader` is the concrete reader. It is
fixed to one `owner/name` origin, sends only `GET`s, never follows a redirect, and raises
`PublicationReadError` (closed code, no provider text) on anything it cannot prove, which keeps the
hold. A ref 404 counts as absent only after `GET /repos/{origin}` answers with the origin's own name
(GitHub also answers 404 for a repository the token cannot see), and every PR it lists must run from
the bound head branch in the origin onto the bound base in the origin. A read PR must be the requested
number with both sides in the origin; a PR 404 is absent only after the same repository check. It
ignores environment proxy,
netrc and CA settings (`trust_env=False`), and streams every body under a 2 MiB cap (checked against
`Content-Length` first), raising `response_too_large` past it.

`reconcile.sweep_held_operations` is the batch entry point. It visits every held operation oldest
first and rebuilds each binding from dispatcher records: the reader's origin, the attempt's
`branch_name`, `QA_BASE_REF`, the retained export's `head_sha` and, for `pr_update` and `pr_labels`, the
PR number the attempt's own `pr_create` recorded (succeeded, or reconciled `resource_found`). The
stored digest still decides, so a rebuilt binding that differs is refused (`binding_mismatch`), and a
row whose records cannot form a binding is reported `unbound_attempt` without a read. Operators run it
with `scripts/qa_reconcile_publications.py --repository <owner/name>`, repeating `--label` when the
configured QA PR labels differ from the dispatch defaults: the connection string is read from stdin, the token from `BUTLERS_QA_GH_TOKEN` in the
environment (`bws run -- ...` or a pod `secretKeyRef`), the pool assumes `butler_qa_rw`, and the report
is identifiers and categories only (exit 2 while anything stays held). Nothing schedules the sweep yet;
the patrol wiring lands with the production publisher, which owns the fixed origin.

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
its result is refused. Residual: an `ambiguous` row may carry a NULL result. The writer never produces
one and holds are keyed on stage, not result, so it cannot release a hold; bu-mc01y5.15 closes it
later. A BEFORE DELETE trigger (`qa_publication_operations_no_held_delete`) refuses deleting a row in
the hold predicate's stages (`egress_started`, `ambiguous`), including from a privileged login; rows in
any other stage can still be deleted by a role that can reach them. Only the last schema to leave
`core_268` removes the delete trigger, its function and the CHECK, and restores the `core_267` guard
body.

Migration `core_300` adds `egress_started_at` and extends the guard again, keeping every earlier rule.
A row leaving `bound` without the stamp gets database time (so a writer that predates the column still
anchors the settle), and the stamp is set-once afterwards. A CHECK
(`ck_qa_publication_operations_stage_stamp`) keeps it NULL on a `bound` row. Rows already in
`egress_started` are backfilled with the migration's time, the latest moment their egress can have
begun. Only the last schema to leave `core_300` drops the column and CHECK and restores the `core_268`
guard body.

`deploy/backup/pg_dump.sh` excludes the table because forced row security fences the dump role. A
restore therefore comes back without holds; the restore-time fence is owned by bu-mc01y5.7 and
publication stays disabled until it lands.
