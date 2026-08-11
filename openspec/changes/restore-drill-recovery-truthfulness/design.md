## Context

**[Observed]** The deterministic dashboard-api restore-drill loop restores the newest plain-SQL
backup into the fixed `butlers_restore_drill` scratch database, records its
result through the isolated executor boundary, and exposes that result through
`GET /api/system/backups`. Public audit telemetry is broad-DML and therefore
cannot be the authoritative result source. It currently records free-form
detail only and treats every recorded outcome alike for the seven-day cadence.
Consequently a `createdb` permission failure can postpone the next attempt for
a week while the owner has no durable attention event or failure age.

The existing role model is schema-scoped at runtime (`SET ROLE
butler_<schema>_rw` and `connector_writer`), while the dashboard-api loop calls
`db_params_from_env()` before it launches `createdb`/`psql` subprocesses.
Compose supplies that process with the shared `POSTGRES_USER`. Therefore
granting `CREATEDB` to the migration/connecting user would also give the
always-on dashboard process a `CREATEDB`-capable subprocess: `SET ROLE` scopes
its asyncpg connections, not the subprocess credential. This change rejects
that false bootstrap-only boundary. RFC 0006 and the security doctrine require
a real least-privilege distinction; RFC 0005 requires structured failure
evidence without secrets or high-cardinality metric labels; RFC 0007 requires
the dashboard to distinguish unavailable information from a truthful empty
state.

## Goals / Non-Goals

**Goals:**

- Make the restore drill a bounded proof that never writes to the live
  application database.
- Preserve dashboard, butler, and connector least privilege through a distinct,
  isolated restore-drill credential and execution boundary rather than a
  bootstrap-only grant to their shared connecting user.
- Make missing, failed, passed, and recovered drill states truthful in
  scheduling, stored provenance, the system API, and the dashboard.
- Require real PostgreSQL client/testcontainer proof of success, permission
  denial classification, and scratch cleanup.

**Non-Goals:**

- Running a production drill, changing a live role, deploying or restarting a
  service, or repairing data by hand.
- Adding an API that reveals a privileged database credential or a manual
  operator workaround that creates the scratch database.
- Retaining restore-drill execution in dashboard-api or mounting its credential
  through shared `POSTGRES_*`/`DATABASE_URL` runtime configuration.
- Sending an owner notification, changing the normal backup cadence, or adding
  multi-replica restore-drill execution in this change.

## Decisions

### 1. A dedicated executor owns a purpose-bound `CREATEDB` credential

The current dashboard loop cannot safely use a `CREATEDB` migration/connecting
user: it resolves the shared `POSTGRES_USER` through `db_params_from_env()` and
passes it to CLI subprocesses. The future implementation therefore moves both
due-time execution and the scratch lifecycle into a dedicated deterministic
restore-drill executor. Dashboard-api only reads the recorded result through
its ordinary pool; it never receives, resolves, or launches a process with the
executor credential.

Privileged bootstrap provisions one distinct executor login with `LOGIN`,
`CREATEDB`, `NOINHERIT`, `NOSUPERUSER`, `NOCREATEROLE`, `NOREPLICATION`, and no
membership in a butler, connector, or migration role. The password is a Tier-0
deployment secret mounted as a file only in that executor; it is never placed
in git, a dashboard/API response, `DATABASE_URL`, or the shared
`POSTGRES_USER`/`POSTGRES_PASSWORD` environment anchor. The executor receives
only non-secret endpoint configuration plus that secret file, uses an explicit
maintenance database for `createdb`/`dropdb`, and has no general live-schema
grants. Its only live-database interface is a narrow migration-owned,
fixed-safe-search-path security-definer read/write surface for restore-drill
schedule state, result persistence, and truthful attention provenance: each
path ends in explicit `pg_temp` (`pg_catalog, pg_temp`, or
`pg_catalog, public, pg_temp` where public resolution is required).

This is deliberately a privileged *runtime* boundary, not a bootstrap-only
claim: compromise of the executor can create scratch databases and read the
backup artifact required for recovery proof. The containment is explicit: the
executor is the sole service with that credential, joins only the dedicated
`restore_drill_db` bridge, and a host policy default-denies all traffic from
that bridge except TCP to its configured PostgreSQL endpoint and port. The
policy hooks both Docker's `DOCKER-USER`/`FORWARD` path and the bridge `INPUT`
path, because a host or Docker gateway destination does not traverse
`FORWARD`. The bridge is intentionally not Docker `internal` because PostgreSQL
is externally hosted; both supported launchers (`scripts/compose.sh` and
`butlers deploy`) stop/create the executor, invoke only the fixed root-owned
`/usr/local/libexec/butlers-restore-drill-firewall` wrapper, install the
default-deny policy, and only then start it. Neither launcher may elevate a
checkout-controlled script, broad `env`, or a shell. The executor has no
automatic restart policy, so a Docker daemon or host restart cannot start it
before the fence is restored.

When PostgreSQL is named by DNS, that hostname remains the executor's TLS
identity (including `verify-full`), while the host resolves a separate IPv4
firewall endpoint and Compose maps the hostname locally. Docker's resolver has
only the executor's container-loopback upstream, where no resolver runs; raw
DNS traffic is additionally covered by the two terminal-deny firewall hooks.
It mounts backups read-only, has no listener, Docker socket, `backend`,
`frontend`, or `egress` membership, and runs no LLM session. The dashboard,
butlers, and connectors remain `NOCREATEDB`; their shared credential is tested
as unable to create a scratch database. Granting `CREATEDB` to that shared user
is rejected because `SET ROLE` cannot constrain its subprocesses. Ad hoc
`ALTER ROLE` or a manually pre-created scratch database are rejected because
they turn a recovery proof into an untracked live mutation.

`verify-ca` and `verify-full` additionally require one dedicated,
noncredential CA-root source file. Compose mounts it read-only only into the
executor, libpq receives it through `PGSSLROOTCERT`, and asyncpg receives an
explicit verification context. Missing or malformed roots fail configuration
before the executor connects. `require` remains an encrypted non-verifying
mode and deliberately does not require the root-file setting.

### 2. The scratch database has an explicit, single-executor lifecycle

Each attempt first removes a stale scratch database, creates a fresh scratch
database, restores the selected artifact with the real PostgreSQL client,
verifies non-system data, and attempts post-run cleanup regardless of the
earlier outcome. No command may target the configured live application
database. A pre-cleanup or post-cleanup error is itself a failed attempt; a
pass is valid only after verification and cleanup complete. Failure metadata
distinguishes the lifecycle stage (`pre_cleanup`, `create`, `backup_read`,
`restore`, `verify`, or `post_cleanup`) from a stable code such as
`createdb_permission_denied`, `client_tool_unavailable`, `restore_timeout`,
or `scratch_cleanup_failed`.

The fixed scratch name is retained because it lets the next attempt recover a
leftover from a killed prior attempt. This release assumes exactly one dedicated
restore-drill executor. Before any deployment can run more than one executor,
it must add a cross-process exclusive guard (for example a database advisory
lock) around the whole lifecycle; no best-effort timing assumption is
sufficient.

### 3. Persisted result fields drive cadence and the failure epoch

The executor-owner result ledger is the scheduling authority. No record is immediately due;
`pass` is due after seven days; `fail` is due after 24 hours. A missing backup
file is a legitimate no-result skip and creates neither a failure record nor
an attention event. A degraded ledger read does not alter scheduling because
it is an API-read condition, not a recorded drill result.

Every recorded failure carries a closed `failure_stage` and `failure_code`,
plus a sanitized, bounded operator detail (maximum 512 characters). Raw
stderr, passwords, connection strings, dump content, and unbounded backup
paths must not enter the audit metadata, attention ledger, API response, logs,
or metrics. Sanitization is an allowlist rather than raw-output truncation:
unrecognized client output is withheld, so redacting a URL cannot accidentally
retain a dump row. Legacy records without structured fields remain readable
with unknown/null provenance; unsafe legacy detail is withheld and its text is
never parsed to infer a code or cadence.

The security-definer persistence function is the final enforcement point, not
an assumption about the Python executor: an executor credential holder can
call it directly. It therefore keeps its four-argument compatibility ABI but
discards caller-supplied backup name, detail, and table-count values; validates
only non-null `pass`/`fail`; writes the protected owner ledger; and emits only
a fixed canonical public audit projection. The private owner never inserts into
the shared audit table directly: it calls a second, purpose-bound NOLOGIN
`restore_drill_executor_audit_writer` definer, with a fixed
`pg_catalog, pg_temp` search path, only the required public-audit
INSERT/sequence rights, and no
private-ledger schema, table, function, or owner-membership capability. A
hostile public-audit trigger consequently executes as that constrained writer,
not as the private result owner. It can deny availability by rejecting its row,
but the outer result statement then rolls back the ledger insert too; it cannot
leave an unprojected authority result or escalate into the ledger. Due checks
and the dashboard reader use the protected ledger through fixed owner-side
functions, never a `public.audit_log` row. A normal-role or even a newer
administrative public audit spoof therefore cannot manufacture an API pass or
alter scheduling.
The ledger relation and its canonical signatures are created inside the
`core_196` transaction by one fixed no-argument SECURITY DEFINER installer
owned by a cluster-superuser bootstrap. Before exposing it, `init-db.sql`
rejects an existing admin schema, configuration, installer, or finalizer not
owned by that trusted bootstrap provenance; it never `CREATE OR REPLACE`s a
shared-owned no-op or silently reclaims it. `core_196` repeats the catalog
ownership/definer check before invocation. The shared migration/dashboard
credential gets neither protected-schema `CREATE` nor finalizer execution. The
finalizer accepts only clean objects owned by that bootstrap installer or an
already-finalized owner interface; it never infers trust from object shape or a
marker. Therefore a legacy shared credential that pre-created either the admin
bootstrap no-ops or a compatible ledger, trigger, and all signatures is
rejected before ownership transfer or executor/reader grants, both on the
migration and on a privileged `init-db.sql` rerun.
A degraded ledger-read exception follows the same rule:
the API and its log use a fixed unavailable diagnostic rather than exception
text or traceback, because both can carry a DSN, credential, SQL, or dump
fragment.

`failing_since` is the timestamp of the oldest contiguous failed restore-drill
record ending at the current failed result. It is `null` unless the current
result is `fail`; a subsequent pass resets it to `null`. This is calculated
from persisted result ordering, not from an in-memory loop clock or error text.

### 4. Attention provenance records an honesty gap, not a notification

After the failed drill result is durably recorded, the job makes a best-effort
insert into `public.attention_ledger` with `source="restore_drill"`,
`outcome="failed"`, no channel/intent, and `notification_ref=null`. Its reason
is the stable failure code; metadata contains only the stage, code, bounded
sanitized detail, and the recorded-at reference needed for diagnosis. The
attention source check constraint and reader filter vocabulary are expanded in
the same migration. An attention-ledger write failure is logged and swallowed,
but cannot erase, downgrade, or prevent the authoritative result; a result
authority write failure cannot be represented as a durable attention success.

Using `source="notify"` or a synthetic notification reference is rejected
because no owner-facing delivery occurred. Reusing unstructured error text for
the ledger reason is rejected because it makes scheduling and observability
depend on unstable, potentially sensitive process output.

### 5. The monitoring surface leads with current truth

`RestoreDrillFacts` gains additive `failure_code`, `failure_stage`, and
`failing_since` fields. The System page shows the failure age adjacent to the
current failed restore-drill verdict, with code/detail as secondary diagnostic
information. It must not render a stale failure age for `pass`, `pending`, or
`degraded`, and `degraded` remains an explicit unavailable state rather than
an all-clear. The system verdict banner reflects the same current failed state
without inventing a notification or treating historical failure as current
after recovery.

This follows the monitoring hierarchy: the owner sees the current recovery
verdict first, the duration of a current failure next, then concise diagnostic
context on demand. Text and semantic status must communicate the state without
depending on color alone.

## Risks / Trade-offs

- **A recovery executor is a deliberately privileged runtime** → Isolate its
  distinct `CREATEDB` login and file-backed secret to the db-only deterministic
  executor, give it only the narrow restore-drill database interface, and assert
  that dashboard, butler, and connector credentials remain `NOCREATEDB`.
- **A fixed scratch name collides across replicas** → Keep the current
  single-executor assumption explicit and block multi-replica enablement until
  a cross-process lock is implemented.
- **A broad public audit writer forges a restore-shaped row** → Keep the
  executor-owner ledger as the sole due/API authority, make the public audit
  projection fixed and unauthoritative, and prove the effective role matrix
  against a real PostgreSQL core chain.
- **Old audit rows lack structured fields** → Preserve their result and
  timestamp, return unknown/null structured provenance, and never infer it
  from legacy error text.
- **A mock can hide client-tool behavior** → Require integration coverage that
  invokes real `pg_dump`, gzip, `createdb`, `psql`, and `dropdb` against a
  PostgreSQL testcontainer; intentional environment-unavailability is the only
  allowed skip condition.

## Migration Plan

1. Add the checked-in privileged bootstrap provisioner and idempotent bootstrap
   SQL support for the distinct executor login, its file-backed deployment
   secret contract, and its narrow restore-drill database interface. The shared
   migration/connecting user and all runtime roles remain `NOCREATEDB`; update
   the operator procedure before enabling the executor.
2. Add `core_180_restore_drill_executor_contract.py` from the current
   `core_179` head. It expands the `attention_ledger` source vocabulary to
   `restore_drill`, preserves existing rows, and creates/grants the bounded
   restore-drill persistence interface. Immediately before creating that
   migration, rebase and inspect the core chain: use its sole current head as
   `down_revision` and the next unclaimed `core_<n>` identifier. If the chain
   has advanced or has multiple heads, update/reconcile it first; never create a
   duplicate revision ID or attach to a stale head.
3. Ship the isolated executor/service, structured result writing,
   result-aware scheduling, attention recording, API/types/UI updates, and
   focused tests together after the prerequisite migration is available.
4. Roll back application code and the executor deployment without restoring a dump into the live database.
   If the source-constraint migration is downgraded, remove only
   `source="restore_drill"` attention observations before narrowing the
   constraint and revoke the executor's bounded interface before removing its
   service/secret mount; retain authoritative result records, their fixed audit
   projections, and backup artifacts. Do not issue ad hoc `ALTER ROLE`
   statements or manually delete failed-drill evidence as rollback.

## Open Questions

None. Multi-replica execution is explicitly deferred until its exclusive-lock
contract is proposed and implemented.
