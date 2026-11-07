# Controlled Switchboard retention drain

This tool implements a fixed-cutoff read-only inventory and the same bounded
atomic batch engine on isolated rehearsal databases. It does **not** currently
admit live deletion. The original four outcomes of `bu-kqnum.13.9` remain open
until the complete tool is reviewed/merged, both real PostgreSQL rehearsals have
passed, the one authorized dev dry run has executed, and the exact plan has
returned to the owner. Source or fixture evidence alone does not close it.

Owner decision `switchboard-retention-drain-target` (2026-11-04, choice A)
authorizes exactly one content-blind read-only `butlers-dev` invocation through
the existing Switchboard runtime role. It does not authorize deletion, schedule
changes, re-enabling the legacy cleanup handler, production access, new grants,
backup/restore, notifications or a repeated dry run after uncertainty.

## Fixed plan and read-only invocation

Use `uv run --no-sync python -m scripts.retention_drain --dry-run --plan <plan>`.
Supply the existing connection URL through stdin from the approved credential
consumer; never put it in argv, logs, this document or a tracked plan. The CLI
reuses the existing exact Switchboard connection parser and role-enforcing
`Database` context from the attention-ledger operator. It does not call that
operator's repair path. Run from the reviewed checkout with its own environment.

The plan is strict JSON matching `retention_drain.Plan`: `run_id` (fresh UUID),
`environment` (`butlers-dev`), `database`, `schema` (`switchboard`), `role`
(`butler_switchboard_rw`), named `operator`, fixed UTC `cutoff`, UTC
`window_start`/`window_end`, `batch_rows`, `total_rows`, `max_batches`,
`duration_seconds`, `statement_ms`, `lock_ms`, and exact `source_sha256` from
`retention_drain.source_identity()`. That digest covers the engine, CLI, reused
connection helper, Database implementation and `mem_010`. Do not advance the
cutoff, refresh a run ID, broaden bounds or silently reuse an expired window.

Hard ceilings are 1,000 rows/batch, 10,000 rows in the complete fixed candidate
scope, 100 batches, 300 seconds/run, 30 seconds/statement, 2 seconds/lock wait,
and a one-hour execution window. Every value must be explicit and positive;
booleans do not substitute for integers. More candidates than the scope bound
refuse; the tool never silently truncates the fingerprint. Inventory streams
fixed-size cursor batches and retains at most the scope bound plus one working
identity. Its complete aggregate/fingerprint evidence remains available on
UNKNOWN-provenance or over-limit refusal; an incomplete/time-limited scan is
UNKNOWN, never a complete inventory.

A PostgreSQL `REPEATABLE READ READ ONLY` transaction begins before identity,
provenance and candidate queries. Actual database/current role/non-elevated
identity and the installed `mem_010` trigger/function are checked. A nonblocking
session advisory fence serializes this operator and is released on all settled
paths; unproven release terminates the owned connection. Neither read-only
planning nor the CLI emits DML or DDL.

Only canonical ordinary trigger kinds or an exact current scheduled-task name
count as known ordinary provenance. Only expiry before the fixed cutoff qualifies. Pending episodes additionally
need seven days beyond expiry relative to that same cutoff. The deterministic
order is `(created_at, id)`. Sessions with the two known consolidation triggers,
quarantine metadata and archive-required retention classes are excluded.
Missing/malformed session lineage, unrecognized trigger kinds, metadata or
retention policy refuses as unknown (with aggregate evidence); it never becomes evidence of ordinary provenance. Active leases refuse
a deleting batch. No episode content, prompt, raw row, source identifier or
credential is output. The result contains aggregate eligible/excluded/unknown
counts, a candidate fingerprint, public plan and endpoint fingerprint only.

## Isolated PostgreSQL rehearsal

`tests/modules/memory/test_retention_drain.py` uses the current shared database
factory with real core and memory migrations in `switchboard`, and the ordinary
`butler_switchboard_rw` role. It does not replace migrations with hand-written
fixture tables or change scheduler state. A private local PostgreSQL adapter,
when used, must separately record its version, root-autouse exclusion, package
identity and owned server/child cleanup; it is not hosted equivalence.

`RETENTION-NO-DELETE-REHEARSAL` executes the actual dry-run engine. An injected
real DELETE must fail at PostgreSQL's write boundary and leave episodes,
tombstones, audit rows and a positively present disabled schedule unchanged.
Neutralizing that boundary must make the same test fail. No live DELETE+ROLLBACK
is an acceptable substitute.

`ATOMIC-BATCH-RECOVERY-REHEARSAL` calls the actual batch engine. The source entry
is restricted to `isolated` plans and actual `test_retention_*` databases; there
is no live-write CLI. Each serializable batch locks only its bounded candidate
rows, rechecks the exact remaining scope and fixed predicate, deletes, checks
content-free tombstones and appends its `memory_events` audit/progress in the
same transaction. Generic links and fact/rule source IDs remain, using the
existing source-expired contract. No schema/role/DDL changes occur in the engine.

A sequence-zero event records immutable run/plan identity, then each committed
batch records counts, candidate fingerprint and durable cumulative progress.
Audit/provenance failure rolls back the current batch; earlier commits remain.
Replay, drift, unknown provenance, lost fence, bounds and expired window stop
further work. COMMIT acknowledgement loss is `UNKNOWN`; never infer rollback or
automatically retry. `readback()` observes the same-run committed events without
authorizing continuation. Run evidence is application-owned operation audit,
not a substitute for protected restore authority or owner approval.

## Live deletion authority remains unavailable

The existing protected restore owner is authoritative, but its current
`latest_result()` returns only `checked_at/result/detail`. Its final
`record_result()` deliberately ignores the supplied backup name and detail; a
latest pass cannot be bound to this target and backup artifact. Public audit,
caller-supplied references, a dump's existence and a closed bead are not proof.

Before the owner-run deleting path can be admitted, the existing protected
restore contract must supply genuinely executor-derived target/artifact identity
and successful verification/cleanup/freshness. The exact owner-approved run plan
must bind that proof and backup coverage/exclusions, source/provenance versions,
operator, role, target, window, cutoff, limits, observation and recovery owner.
No field or boolean currently supplied to this tool grants that authority.
`bu-viat6h.9` is an unadopted spec amendment, not an implemented restore executor.
This is an explicit remaining implementation/prerequisite frontier for the
whole controlled tool and parent `bu-kqnum.13.5`, not a request to repeat the
already authorized read-only permission.

Return the actual dry-run receipt and exact plan to the owner. Record a failed
connection/read as UNKNOWN/NONPASS and do not consume a second invocation by
retry. Owner alone decides eventual deletion after exact-plan review and valid
managed recovery proof. Stopping future batches does not restore deleted data;
retain all committed evidence and use the approved managed recovery procedure.
