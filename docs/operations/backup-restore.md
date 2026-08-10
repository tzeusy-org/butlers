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
| Restore-drill executor | `restore-drill-executor` Compose service |
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

## Managed restore drill

The `restore-drill-executor` is the only process allowed to perform the
scratch-database lifecycle. It holds a distinct `restore_drill_executor`
database login, which is intentionally more capable than the dashboard,
butlers, and connectors only for creating and removing the fixed scratch
database. The normal `POSTGRES_USER`, every `butler_*_rw` role, and
`connector_writer` remain `NOCREATEDB`.

Dashboard-api has a read-only role in this flow: it reads the durable result
from `public.audit_log` for `GET /api/system/backups`; it receives neither the
executor credential nor a process path that can launch `createdb`, `psql`, or
`dropdb` for the drill.

### Bootstrap prerequisite

Before enabling the executor, use the reviewed privileged bootstrap procedure
to run `scripts/init-db.sql`, apply the application migrations, and invoke the
managed `scripts/provision_restore_drill_executor.sh` provisioner. The
provisioner expects the private file path named by this deployment setting:

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

The ownership handoff is also deliberate: the two constrained persistence
functions live in the `restore_drill_executor` schema and are owned by a
separate `restore_drill_executor_owner` `NOLOGIN` role. The normal migration
and dashboard login has neither schema usage nor function execution after the
migration finishes; it cannot bypass the executor interface through object
ownership. The privileged bootstrap is the only role boundary allowed to set
up that handoff.

Do not bypass that managed procedure. In particular, do not issue ad hoc
database-role changes, manually pre-create the scratch database, pass a shared
application credential to client tools, or make a live application database
mutation to force a drill through.

### Deployment boundary

The Compose service is deliberately narrow:

- It joins only the dedicated `restore_drill_db` bridge and exposes no
  listener or host port. That bridge is not marked Docker `internal` because
  PostgreSQL is externally hosted. A root-owned fixed wrapper at
  `/usr/local/libexec/butlers-restore-drill-firewall` installs project-scoped
  default-deny chains at Docker's `DOCKER-USER`/`FORWARD` hook and the bridge
  `INPUT` path. The only accepted route is TCP to the resolved PostgreSQL IPv4
  endpoint and port; every other forwarded packet and every executor-to-host or
  bridge-gateway packet is dropped. This second hook is required because host
  services and the bridge gateway do not traverse `FORWARD`.
- A configured DNS database host remains the executor's connection/TLS identity
  (including `sslmode=verify-full`), while Compose maps that name locally to
  the separately resolved IPv4 firewall endpoint. The service configures
  Docker's resolver with only the executor's `127.0.0.1` loopback upstream; no
  resolver runs in that container, and `/etc/hosts` resolves the sole required
  database identity before DNS is consulted. Thus an embedded-DNS request has
  no external upstream, while raw DNS packets are also denied by the two
  firewall hooks. IPv6 is disabled on this bridge. Verification modes
  additionally use the dedicated read-only CA-root mount described above;
  `verify-full` verifies the retained DNS hostname, never the firewall IPv4
  address.
- The supported launchers, `scripts/compose.sh` and `butlers deploy`, stop any
  old executor, create its network without starting the credentialed process,
  install that default-deny policy, and only then start the stack. The executor
  has `restart: "no"`, so a Docker daemon or host restart cannot auto-start it
  before the fence is recreated. A failed checked stop/down phase also ends the
  launcher before `create`, firewall invocation, or `up`. Do not start this
  service through a bare `docker compose up`; either supported launcher fails
  closed if it cannot stop the old executor or apply the required firewall policy.
- It mounts `butlers_backups` read-only and has no Docker socket, `backend`,
  `frontend`, or `egress` network membership.
- It does not inherit `x-postgres-env` and receives no `POSTGRES_USER`,
  `POSTGRES_PASSWORD`, or `DATABASE_URL` value.
- Its only credential is the private file-secret mount described above.

### Firewall wrapper prerequisite

Before enabling a supported launcher on a host, a root-controlled deployment
procedure must review the exact checkout source and install the immutable
runtime copy with `scripts/install_restore_drill_firewall_wrapper.sh`. That
installer writes only `/usr/local/libexec/butlers-restore-drill-firewall` with
`root:root` ownership and mode `0755`; it does not accept an alternate target.
The host's managed sudo policy may permit only that fixed wrapper and only its
literal `--project`, `--db-host`, and `--db-port` invocation form. The checked-in
`scripts/restore-drill-firewall.sudoers` is a policy template for that host
configuration step.

Never grant passwordless sudo for `scripts/restore-drill-firewall.sh`, a
checkout wildcard, `env`, a shell, or the installer. The checkout script is an
installation artifact, not a runtime elevated command. If the fixed wrapper or
its narrowly scoped sudo rule is absent, leave the executor stopped and repair
the root-controlled deployment configuration before retrying either supported
launcher.

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
dump content are withheld rather than retained in audit records or rendered by
the dashboard. The executor-facing SQL persistence function is the final
boundary: it ignores every caller-supplied `p_detail` and `p_backup_name`,
uses a fixed canonical audit target with no backup-path metadata, and accepts
only a non-null `pass` or `fail` result. It stores only its fixed safe diagnostic,
so a direct use of the executor credential cannot bypass the runner's sanitizer.

### Observing a drill

Use normal deployment observation surfaces; none should display the secret:

```bash
docker compose ps backup-cron restore-drill-executor
docker compose logs --tail=100 restore-drill-executor
```

The System page and `GET /api/system/backups` surface the most recently
recorded pass, failure, pending state, or a degraded read. A present backup is
not a restore proof by itself: the recorded executor result is the recovery
evidence.

## Failure and rollback boundary

If the executor cannot create its scratch database or cannot persist a result,
preserve the backup artifact and existing audit history. Investigate the
managed bootstrap/deployment configuration; do not grant recovery capability
to the dashboard, a butler, or a connector, and do not create scratch state by
hand.

To roll back this feature, remove or stop the executor deployment only after
it is no longer running, preserve the audit records and backup volume, and use
the reviewed migration and managed bootstrap procedures for any privilege
remediation. A rollback must never restore a dump into the live application
database or manually erase recovery evidence.
