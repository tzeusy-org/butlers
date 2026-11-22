# Migrator Login

> **Status:** dark. Nothing here changes a running deployment until the owner runs the
> steps below. Tracked by bu-dss7he.6 (slice S3); the owner provisioning is bu-dss7he.21.

Today one shared login migrates, serves the dashboard and runs the daemons, and it owns
the tables. An owner can disable triggers, alter policies and drop objects, so runtime
code holding that login could undo the guards migrations install. The split moves
ownership to a dedicated **migrator** login that only the migrations step uses. The
shared login stays the runtime login: it keeps its role memberships (the daemons'
`SET ROLE butler_<schema>_rw` keeps working) but owns nothing.

## What ships

| Piece | Where | Default |
| --- | --- | --- |
| `butlers db transfer-ownership` | `src/butlers/db_authority/ownership.py` | dry run |
| `database.migratorLogin.enabled` | `deploy/helm/butlers/values.yaml` | `false` |
| `externalSecrets.migratorData` | same file; synced only when enabled | `BUTLERS_RUNTIME_MIGRATOR_DB_USER`, `BUTLERS_RUNTIME_MIGRATOR_DB_PASSWORD` |
| `BUTLERS_MIGRATOR_DB_USER` / `_PASSWORD` | `docker-compose.yml` `migrations` service | unset: shared login |

With the flag on, only the `dashboard-api` pod's `migrations` initContainer connects as
the migrator; every other container keeps the shared login
(`tests/scripts/test_chart_migrator_login.py`).

## The transfer

`butlers db transfer-ownership --from-login <shared> --to-login <migrator>` runs as a
superuser. In one transaction, under an advisory lock, it:

1. Refuses unless the connecting login is a superuser, both logins exist and differ, the
   migrator is an ordinary `LOGIN` (no `SUPERUSER`, `CREATEROLE`, `CREATEDB`,
   `REPLICATION` or `BYPASSRLS`) and not a runtime or purpose role name, and the shared
   login owns no other database or tablespace. `REASSIGN OWNED` moves those cluster-wide,
   so they are refused, never moved silently.
2. Runs `REASSIGN OWNED BY <shared> TO <migrator>`. Only objects the shared login owns
   move. Purpose-owned objects keep their owners: `dnd_generation_owner` (the DND
   FORCE-RLS tables and their definers), `runtime_attention_outbox_owner` and the
   dashboard capability owners. Their fencing does not change.
3. Grants the migrator every role membership the shared login holds, with the same
   `INHERIT`/`SET`/`ADMIN` options. The migrator can then do anything a migration could
   do before the split.
4. Verifies that the shared login owns nothing in the database and that every membership
   was copied. Otherwise it rolls back.

The dry run prints names only: `OWNED`, `MEMBERSHIP`, `ACL-NOT-COPIED` and `REFUSED`
lines. `ACL-NOT-COPIED` lists privileges granted *to* the shared login on objects it
does not own, such as schema `CREATE` and database `CONNECT`, and the shared login's
default privileges. `REASSIGN OWNED` leaves default privileges with the old role, and
they only govern objects that login creates. The init-db replay in step 4 below grants
all of these to the migrator. A second
run after a successful transfer is a no-op.

`tests/integration/test_db_authority_ownership.py` runs the transfer on a migrated
database and checks that a rerun is a no-op. It also pins the init-db blocker described
below. It needs Docker, so CI runs it.

## Activation (owner acts, in order)

**Not yet activatable.** Step 3 (the transfer) waits for every precondition below.
After the transfer the shared login owns nothing, so it loses whatever it could do only
as an owner.

- **Daemon startup must not migrate.** The daemons still run their Alembic chains at
  startup as the runtime login (`_startup_core_migrations` in `src/butlers/lifecycle.py`).
  Startup must first become a read-only head check (bu-dss7he.6 S4).
- **Runtime code must not rely on owner-only operations.** The shared login can no longer
  run `ALTER`, `TRUNCATE`, `REFRESH MATERIALIZED VIEW` or `DROP`, or enable and disable
  triggers, on objects it used to own. Any runtime path that does must move to a
  migration or to a purpose-owned definer first.
- **Runtime code must not rely on owner RLS bypass.** A table's owner bypasses its
  row-level security unless the table is `FORCE ROW LEVEL SECURITY`. On non-FORCE RLS
  tables the shared login owned, it becomes subject to the policies. Runtime reads and
  writes there must pass the policies on their own.
- **The ownership-based bunkers must be gone.** These are the `*_admin` bunkers, their
  `bootstrap_configuration` singletons and the runtime-attention v2–v5 ladder, all
  deleted by a later bu-dss7he.6 slice. Six of them pin the configured migration role
  and refuse an init-db replay that names a different one ("bootstrap configuration
  differs"): `dashboard_attention`, `dashboard_stepup`, `dnd_generation_admin`,
  `perception_admin`, `restore_drill_executor_admin` and `runtime_attention_admin`. The
  runtime-attention upgrades also require that role. Step 4 is impossible until they
  are deleted. A replay that names the shared login after the transfer is expected to
  refuse as well, because the `insight_amendments` owner preflight only accepts the
  configured login; this case is untested.

1. Provision the `butlers_migrator` login and its two BWS keys (bu-dss7he.21). Leave
   `BUTLERS_RUNTIME_DB_USER`/`_PASSWORD` in place: they remain the runtime login, and
   rollback needs them.
2. Stop the migrations step from running mid-change, for example by scaling
   `dashboard-api` to 0.
3. Run `butlers db transfer-ownership --from-login <shared> --to-login butlers_migrator`
   with superuser connection env. Read the dry run, then repeat it with `--execute`.
4. Replay `scripts/init-db.sql` as the superuser with the migrator as the connecting user:
   `PGOPTIONS="-c butlers.connecting_user=butlers_migrator" psql ... -f scripts/init-db.sql`.
   This records the migrator as the migration role in the bootstrap configuration and
   grants it schema `CREATE`/`USAGE` and the default privileges.
5. Set `database.migratorLogin.enabled: true` in `values.dev.yaml` (a PR), deploy, and
   confirm the `migrations` initContainer succeeds. Then run
   `butlers db check-grants --after-init-db-replay`. The replay re-widens grants, and
   `apply-grants` narrows them again when `database.applyGrants` is on.

**Rollback:** set the flag back to `false`. Then transfer the other way
(`--from-login butlers_migrator --to-login <shared>`) and replay init-db with the shared
login as the connecting user.

## Not in this slice

- **Dashboard login.** `butlers_dashboard` is not wired yet. The dashboard API runs
  `SET LOCAL ROLE` into capability roles (`dashboard_vendor_doors`,
  `perception_owner_reader`, `butler_general_rw`). A dedicated login needs an explicit
  dashboard role and an explicit membership list, which is the next slice. Until then
  the dashboard keeps the shared login, which after the transfer acts through its
  memberships, not through ownership.
- **Narrowing the shared login.** It keeps every membership, including purpose roles,
  if it holds any.
- **Removing the shared login's ownership-based paths.** These are the `*_admin`
  bunkers, `bootstrap_configuration` and the runtime-attention v2–v5 ladder, all
  deleted by later bu-dss7he.6 slices.
