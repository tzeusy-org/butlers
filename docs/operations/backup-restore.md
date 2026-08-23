# Backup and Restore

> **Purpose:** Document the backup cadence and the managed proof that a recent
> PostgreSQL backup can be restored without touching the live application
> database.
> **Audience:** The owner/operator responsible for recovery readiness.

---

## Overview

Butlers writes a timestamped, gzip-compressed plain-SQL dump to the
`butlers_backups` Docker volume each night. The dashboard reports backup
recency, artifact integrity, and the durable result of the managed restore
drill; it never launches a restore itself.

| What | Where |
|---|---|
| Backup producer | `backup-cron` / `deploy/backup/pg_dump.sh` |
| Restore-drill executor | Protected `restore-drill-executor` Compose service |
| Executor bootstrap contract | `scripts/init-db.sql` and `scripts/provision_restore_drill_executor.sh` |
| Backup volume | `butlers_backups` |
| Default backup schedule | 02:00 UTC daily (`BACKUP_CRON`) |
| Default backup retention | 14 days (`BACKUP_RETAIN_DAYS`) |
| Restore scratch database | `butlers_restore_drill` (never the live application database) |

## Automated backups

The `backup-cron` service mounts `deploy/backup/pg_dump.sh` into a
`postgres:17-alpine` container. Each run writes to a temporary file, verifies
that the write completes, atomically publishes a `.sql.gz` artifact, and
prunes artifacts beyond `BACKUP_RETAIN_DAYS`.

Configure the cadence in the deployment environment:

```dotenv
BACKUP_CRON=0 2 * * *
BACKUP_RETAIN_DAYS=14
```

The `butlers_backups` volume remains the local recovery artifact store. Keep a
separate, owner-controlled off-host copy for disaster recovery: a host failure
can otherwise destroy both the database and the volume that holds its dumps.

### How a failed run reports itself

A failed run publishes nothing and deletes nothing, so the backup directory
after a failure looks exactly as it did before: yesterday's good dump, still
there, still fresh. Artifact recency alone therefore cannot tell "last night's
run failed" from "last night's run has not happened yet", and would not raise
anything until the last *success* crossed the 36-hour staleness threshold — a
late alarm, for the wrong reason.

Every run therefore rewrites one file, `last_run.json`, beside the dumps:

```json
{"result":"failed","reason":"pg_dump_failed","exit_code":1,"finished_at":"2026-08-23T02:00:11Z","artifact":null}
```

| `reason` | What happened |
|---|---|
| `ok` | The run published an artifact (`result: "success"`) |
| `pg_dump_failed` | `pg_dump` exited non-zero; nothing was published |
| `artifact_undersize` | The dump was below the 256-byte floor; not published |
| `artifact_corrupt` | The dump did not decompress cleanly; not published |
| `unexpected_error` | The run aborted somewhere else entirely (a full disk at the publish step, for example) |

It is written from the script's `EXIT` trap rather than from the success path or
from each failure branch, so no exit route — including one nobody enumerated —
can leave without recording an outcome. It is a file rather than a database row
because the producer is the backup sidecar: a signal that needed a live database
connection could not report the failures that involve the database.

`GET /api/system/backups` reports it as `last_run`. **No receipt, or one that
does not parse, reads as `"unknown"` — never as a successful run.** That is what
an older deployment and a first-ever run both look like, and neither is evidence
that a backup ran. The QA infra-state patrol raises a failed run as its own
finding (`BackupRunFailed`) on the night it happens.

### What the backup does not contain

The dump runs as the shared `POSTGRES_USER` migration/runtime login. That login
is deliberately fenced away from the trusted-bootstrap control plane, and
`pg_dump` locks every relation in scope before writing a byte — so an
unexcluded fenced object aborts the entire run and publishes **no file at all**,
not a partial one. `deploy/backup/pg_dump.sh` therefore excludes that control
plane by name:

| Excluded | What it holds |
|---|---|
| `restore_drill_executor` schema | The restore-drill result ledger and its three constrained functions |
| `restore_drill_executor_admin`, `dnd_generation_admin`, `runtime_attention_admin` schemas | Bootstrap configuration rows and the fixed installer/finalizer functions |
| `public.dnd_generation_mutations` | DND generation mutation audit |
| `public.user_context` | Context-bus signals; every row carries a hard `expires_at` |
| `public.runtime_attention_outbox`, `public.runtime_attention_delivery_lease`, `public.runtime_attention_producer_control` | Runtime-attention delivery state and its producer control row |

This is a deliberate completeness decision, not a workaround for a failing
command. These objects are not recovered from a dump in the first place: the
cluster-superuser bootstrap (`scripts/init-db.sql`) is the only supported way to
reconstruct them, and restoring a dumped copy would be worse than omitting it —
every one is owned by a fenced role the restoring login is not a member of, so
the dump's `ALTER ... OWNER TO` statements would fail and leave the object owned
by whoever ran the restore, dissolving the fence.

What that costs, stated plainly: **restore-drill history is not backed up.** The
`restore_drill_executor.restore_drill_results` ledger is recovery *evidence* —
whether a drill has ever passed — and a disaster that loses the database loses
that history with it. It is not needed to recover the application, and a restore
drill run after recovery re-establishes the evidence from scratch. Everything
else in the table above is either reconstructed by the bootstrap or expiring
runtime state. No ordinary application data is excluded.

`tests/scripts/test_pg_dump_backup.py` pins that claim against a real
bootstrapped database, in both directions: it fails if a fenced object appears
that the script does not exclude (which would silently stop producing backups),
and equally if the script excludes something the dump role can in fact read
(which would silently narrow them). Do not add an entry to that set to make a
red run go green — an entry there is a decision that data will not be in the
backup.

A restore of one of these dumps therefore reconstitutes the application schema
and data only. Re-run the managed bootstrap procedure to restore the executor
boundary itself.

## Managed restore drill

The `restore-drill-executor` is the only process allowed to perform the
scratch-database lifecycle. It holds a distinct `restore_drill_executor`
database login, which is intentionally more capable than the dashboard,
butlers, and connectors only for creating and removing the fixed scratch
database. The normal `POSTGRES_USER`, every `butler_*_rw` role, and
`connector_writer` remain `NOCREATEDB`.

Dashboard-api has a read-only role in this flow: it calls the fixed
`restore_drill_executor.latest_result()` reader for `GET /api/system/backups`.
That reader exposes only the latest executor-owner ledger result; the matching
`public.audit_log` row is an unauthoritative telemetry projection because normal
application roles can write public audit events. Dashboard-api receives neither
the executor credential nor a process path that can launch `createdb`, `psql`,
or `dropdb` for the drill.

### Bootstrap prerequisite

Before enabling the executor, use a reviewed cluster-superuser bootstrap
procedure to run `scripts/init-db.sql`, apply the application migrations, and
invoke the managed `scripts/provision_restore_drill_executor.sh` provisioner.
The shared `butlers` database owner/migration login is not a substitute for
this step. An ordinary dev `./scripts/compose.sh` launch does not need this
deployment setting and must not invent it. After the bootstrap is complete, use
`./scripts/compose.sh --with-restore-drill` to enable the protected boundary in
dev. `./scripts/compose.sh --prod` and `butlers deploy` always require the
bootstrap. The provisioner expects the private file path named by this deployment
setting:

```dotenv
RESTORE_DRILL_EXECUTOR_PASSWORD_FILE=/secure/managed/path/restore-drill-executor-password
```

The file is a Tier-0 deployment secret. It is created and retained outside the
repository, is never copied into `.env` as a value, and is mounted by Compose
only at `/run/secrets/restore_drill_executor_password` in the executor
container. Its content is UTF-8 with at most one terminal LF; embedded/multiple
LF, CR, and NUL are rejected. The bootstrap procedure creates or repairs the distinct login with
`LOGIN CREATEDB NOINHERIT NOSUPERUSER NOCREATEROLE NOREPLICATION` and maintains
the normal-role `NOCREATEDB` boundary.

When the executor uses `RESTORE_DRILL_EXECUTOR_SSLMODE=verify-ca` or
`verify-full`, configure a separate, noncredential CA-root source file as
well:

```dotenv
RESTORE_DRILL_EXECUTOR_SSLROOTCERT_SOURCE_FILE=/secure/managed/path/postgresql-ca-root.pem
```

Compose mounts that file read-only at
`/run/configs/restore_drill_executor_ca.pem`; it is not a password secret and
is visible only to the executor. Both `psql`/`createdb`/`dropdb` and asyncpg
use that same mounted root. A missing, unreadable, or invalid CA root makes a
verification-mode executor fail before it connects. Ordinary
`sslmode=require` still uses TLS without requiring this CA-file setting.

The ownership handoff is also deliberate: the private
`restore_drill_executor.restore_drill_results` ledger and its three constrained
functions live in the `restore_drill_executor` schema and are owned by a
separate `restore_drill_executor_owner` `NOLOGIN` role. The executor receives
only `is_due()` and `record_result()` execution; it has no direct ledger table
access. The shared dashboard/migration login receives only schema usage plus
the fixed `latest_result()` reader; it has no writer execution, direct ledger
access, or owner membership. Butler and connector roles receive none of those
privileges. The public audit projection is never a due-check or API authority.
The cluster-superuser bootstrap is the only role boundary allowed to set up
that handoff. Before it exposes the fixed installer, `init-db.sql` verifies
that `restore_drill_executor_admin`, its configuration, and the exact
zero-argument installer/finalizer are owned by trusted bootstrap provenance.
A shared-owned precreated schema or no-op is rejected before `CREATE OR
REPLACE`, ownership transfer, or executor/reader grants; do not attempt to
repair it through the shared login. `core_196` repeats the owner/definer check
before calling the installer. The shared login gets neither protected-schema
`CREATE` nor ownership-finalizer execution. A clean first install or retry, and
a privileged rerun retaining that trusted admin owner, complete normally.

The fixed public audit projection uses a separate
`restore_drill_executor_audit_writer` `NOLOGIN` security-definer rather than
allowing the private ledger owner to insert into `public.audit_log`. Its only
effective database capability is the fixed public-audit INSERT/sequence path;
it has no private ledger schema/table/function grant or owner membership. Thus
a hostile `public.audit_log` trigger runs as the constrained audit writer and
cannot create or read an authoritative result. Such a trigger can still reject
the public projection, which is an availability denial: the enclosing
`record_result()` statement then rolls back its ledger insert rather than
retaining a partial or false recovery result. Repair the audited trigger through
normal privileged database governance; never bypass the boundary by granting
the private owner public-audit DML.

Do not bypass that managed procedure. In particular, do not issue ad hoc
database-role changes, manually pre-create the scratch database, pass a shared
application credential to client tools, or make a live application database
mutation to force a drill through.

### Deployment boundary

The Compose services are deliberately narrow:

- The credentialed executor joins only the dedicated Docker `internal`
  `restore_drill_executor` network and exposes no listener or host port. Its
  network membership excludes the ordinary service and external relay bridges,
  but an internal network alone does not deny bridge-gateway or host traffic.
  The prepared root-owned executor bridge policy is default-denied except for
  its created relay peer at the configured PostgreSQL port, so it cannot route
  directly to PostgreSQL, the host, or ordinary service networks.
- The executor connection identity MUST be an untrimmed DNS hostname (including
  `sslmode=verify-full`), never `localhost` or a numeric IPv4 spelling. Docker
  resolves that name only as an alias for the internal relay. The relay carries no private secret,
  `POSTGRES_*`, or `DATABASE_URL`; it alone joins the non-internal
  `restore_drill_db` egress bridge and accepts only the separately resolved
  PostgreSQL IPv4 and port. Verification modes additionally use the dedicated
  read-only CA-root mount described above; `verify-full` verifies the retained
  DNS hostname, never the relay's IPv4 target.
- Only the separately supplied/resolved relay/firewall target may be a
  canonical dotted-decimal remote-unicast IPv4, with a canonical ASCII-decimal port
  matching `[1-9][0-9]{0,4}` in `1..65535`. Every boundary rejects
  noncanonical, loopback,
  unspecified, link-local, multicast, documentation, and policy-reserved
  addresses while retaining RFC1918, CGNAT/tailnet, and valid public unicast
  database routes. Legacy decimal, octal, hexadecimal, and abbreviated
  `inet_aton` spellings are rejected before DNS resolution. The pre-source
  endpoint-literal grammar supports simple `KEY=value` or `export KEY=value`
  with optional leading spaces/tabs; raw RHS whitespace is rejected before
  sourcing. Other Bash command forms are outside this pre-source
  endpoint-literal grammar; their resulting endpoint values are validated
  without trimming or reinterpretation.
  The executor hostname is not that route: it resolves
  only to the relay alias on the internal network.
- The uncredentialed relay immediately rejects a client when its fixed two-slot
  admission cap is full, uses a bounded listener backlog, and closes both sides
  on a 10-second upstream-connect deadline, a two-hour idle deadline, or a
  six-hour total-session deadline. Each close has at most one second to flush;
  the relay then aborts the transport so a non-reading peer cannot pin a slot.
  It never queues accepted client sockets or exposes a host port.
- A root-owned fixed wrapper at
  `/usr/local/libexec/butlers-restore-drill-firewall` derives both project
  bridges and the created relay's internal address before installing two
  default-deny policies. The relay egress bridge accepts only TCP to the
  resolved PostgreSQL IPv4 endpoint and port; the executor bridge is
  default-denied except for TCP to that created relay peer at the same port.
  Both policies hook Docker's `DOCKER-USER`/`FORWARD` path and their bridge
  `INPUT` paths, so every other forwarded packet and every host or
  bridge-gateway packet is dropped. This second hook is required because host
  services and the bridge gateway do not traverse `FORWARD`. IPv6 is disabled
  on both dedicated networks.
- The ordinary `docker-compose.yml` deliberately omits the executor, its
  bridge, its CA config, and its private secret. Ordinary dev
  `scripts/compose.sh` uses that base topology only. The protected launch paths,
  `scripts/compose.sh --with-restore-drill`, `scripts/compose.sh --prod`, and
  `butlers deploy`, are the only supported lifecycle paths that include
  `docker-compose.restore-drill.yml` to start either protected service; the
  read-only inspection helper is the sole non-lifecycle exception. They stop the
  old relay and executor, call a versioned root-owned preparation verb before
  `create`, inject its generation-bound nonce into the created executor, attest
  and fence that exact container/relay topology, and only then start the merged
  stack. Selection is explicit: a configured secret does not enable the
  protected services in an ordinary dev launch. Returning an opted-in
  `butlers-dev` project to ordinary dev runs base-only `down --remove-orphans`,
  removing the former relay and executor before the base stack starts; continue
  passing `--with-restore-drill` on later dev launches to keep them enabled. The
  wrapper discovers that host-side topology while fencing it; the post-fence
  marker binds the current host boot, project, nonce, executor generation,
  executor IPv4/gateway, and relay-alias IPv4, which the socketless executor can
  verify before reading its secret. A same-boot manual down/recreate cannot
  replay a prior authorization. An older installed wrapper rejects the
  preparation verb before `create`/`up`. The services have `restart: "no"`, so a
  Docker daemon or host restart cannot auto-start them before the fence is
  recreated. A direct merged invocation has no valid prepared marker for its
  new executor generation and fails before reading the secret. A failed checked
  stop/down phase also ends the launcher before preparation, `create`, firewall
  invocation, or `up`.
- It mounts `butlers_backups` read-only and has no Docker socket, `backend`,
  `frontend`, or `egress` network membership.
- It does not inherit `x-postgres-env` and receives no `POSTGRES_USER`,
  `POSTGRES_PASSWORD`, or `DATABASE_URL` value.
- Its only credential is the private file-secret mount described above.

### Firewall wrapper prerequisite

Before enabling a protected launch path on a host, a root-controlled deployment
procedure must review the exact checkout source and install the immutable
runtime copy with `scripts/install_restore_drill_firewall_wrapper.sh`. That
installer writes only `/usr/local/libexec/butlers-restore-drill-firewall` with
`root:root` ownership and mode `0755`; it does not accept an alternate target.
The host's managed sudo policy may permit only that fixed wrapper and its two
literal versioned forms: `--prepare-executor-capability-v1 --project`, then
`--project --db-host --db-port --require-executor-capability-v1`. The checked-in
`scripts/restore-drill-firewall.sudoers` is a policy template for that host
configuration step. These attest the protected launch sequence against stale
wrappers and stale container generations; a root-level firewall/Docker reset
still requires the canonical launcher to prepare and fence a fresh topology.

Never grant passwordless sudo for `scripts/restore-drill-firewall.sh`, a
checkout wildcard, `env`, a shell, or the installer. The checkout script is an
installation artifact, not a runtime elevated command. If the fixed wrapper or
its narrowly scoped sudo rule is absent, leave the executor stopped and repair
the root-controlled deployment configuration before retrying a protected launch
path.

This is a single-executor design. Do not scale `restore-drill-executor` above
one replica until a reviewed cross-process exclusive guard protects the entire
fixed-scratch lifecycle.

### Scratch lifecycle and cadence

On an hourly check, the executor consults its migration-owned database
interface. A missing durable result or a result older than seven days is due;
an absent backup produces no fabricated success record. For a due backup, the
executor performs this fixed sequence:

1. Remove any stale `butlers_restore_drill` database through the explicit
   maintenance database.
2. Create a fresh scratch database through that same maintenance database.
3. Restore the selected gzip/plain-SQL artifact with the PostgreSQL client.
4. Verify that non-system tables exist in the scratch database.
5. Attempt post-run cleanup of the scratch database and persist the result via
   the constrained executor interface.

The live application database is never a restore target. The executor's
maintenance connection is used only to create or remove the fixed scratch
database.

Only a verified post-run cleanup can produce a `pass`. A failed `dropdb` makes
the result a failed drill even after a successful restore/verification, so a
leftover scratch database is never reported as recovery evidence. Stored and
API-visible diagnostic detail is at most 512 characters from a controlled safe
vocabulary; raw PostgreSQL stdout/stderr, connection strings, passwords, and
dump content are withheld rather than retained in the protected result ledger,
its fixed audit projection, or rendered by the dashboard. The executor-facing
SQL persistence function is the final boundary: it ignores every
caller-supplied `p_detail`, `p_backup_name`, and `p_table_count`, uses a fixed
canonical audit target with no backup-path or count metadata, and accepts only
a non-null `pass` or `fail` result. It stores only its fixed safe diagnostic,
so a direct use of the executor credential cannot bypass the runner's sanitizer
or influence the durable/API-visible result shape.

### Observing a drill

Use normal deployment observation surfaces; none should display the secret:

```bash
# Read-only merged-topology inspection; this helper rejects `up` and other lifecycle verbs.
./scripts/restore-drill-compose-inspect.sh ps
./scripts/restore-drill-compose-inspect.sh logs --tail=100 restore-drill-executor
```

Its rendered Compose output is inspection only: use
`scripts/compose.sh --with-restore-drill`, `scripts/compose.sh --prod`, or
`butlers deploy` to validate the endpoint and prepare the firewall before any
protected service starts.

The System page and `GET /api/system/backups` surface the most recently
recorded pass, failure, pending state, or a degraded read. A present backup is
not a restore proof by itself: the recorded executor result is the recovery
evidence.

## Failure and rollback boundary

If the executor cannot create its scratch database or cannot persist a result,
preserve the backup artifact, protected result ledger, and audit history.
Investigate the managed bootstrap/deployment configuration; do not grant
recovery capability to the dashboard, a butler, or a connector, and do not
create scratch state by hand.

To roll back this feature, remove or stop the executor deployment only after
it is no longer running, preserve the authoritative ledger, audit projection,
and backup volume, and use the reviewed migration and managed bootstrap
procedures for any privilege remediation. A rollback must never restore a dump
into the live application database or manually erase recovery evidence.
