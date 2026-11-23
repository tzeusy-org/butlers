# Migrator Login

> **Status:** dark. Nothing here changes a running deployment until the owner runs the
> steps below. Tracked by bu-dss7he.6 (slices S3, S4 and S5); the owner provisioning is bu-dss7he.21.

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
| `BUTLERS_STARTUP_MIGRATIONS` (`run` or `check`) | `src/butlers/lifecycle.py`; chart and compose env | `run` |

With the flag on, only the `dashboard-api` pod's `migrations` initContainer connects as
the migrator. Every other container keeps the shared login and gets
`BUTLERS_STARTUP_MIGRATIONS=check` (`tests/scripts/test_chart_migrator_login.py`).

## Daemon startup: head check instead of migrating

In `run` mode (the default) a daemon upgrades its own core, butler and module chains at
startup, as it did before the split. In `check` mode it never migrates.
`butlers.migrations.check_chain_at_head` reads each schema's version table once, with no
Alembic environment, DDL or extension bootstrap, so a login that owns nothing can run
it. If a core or butler chain is not exactly at its head, the daemon refuses to start
with `MigrationsNotAtHeadError`. The error names the chain, the schema, and the applied
and expected revisions, and says to run `butlers db migrate`. A module chain that is
behind disables only that module, as a failed module migration does today. An unknown
mode value refuses to start. With Compose, set `BUTLERS_STARTUP_MIGRATIONS=check`
together with `BUTLERS_MIGRATOR_DB_USER`.

The daemon pods wait for `dashboard-api` health, so its migrations initContainer has
normally finished first. If a daemon still finds the database behind, it fails, and
Kubernetes restarts it until the migrations step has run.

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

`tests/integration/test_db_authority_ownership.py` runs the whole sequence on a migrated
database:
- the rotation is refused before the transfer;
- the transfer runs, and a rerun is a no-op;
- a replay that names the migrator and rotates the role succeeds, and all six records
  then name the migrator;
- after the replay, the migrator's default privileges (schema, object type, grantee,
  privilege) cover the shared login's;
- the migrator then migrates `core` and `switchboard`.

It needs Docker, so CI runs it.

## Activation (owner acts, in order)

**Not yet activatable.** Step 3 (the transfer) waits for every precondition below.
After the transfer the shared login owns nothing, so it loses whatever it could do only
as an owner.

- **Daemon startup must not migrate.** This one is met: with the flag on, the daemons
  run the head check described above.
- **Runtime code must not rely on owner-only operations.** The shared login can no longer
  run `ALTER`, `TRUNCATE`, `REFRESH MATERIALIZED VIEW` or `DROP`, or enable and disable
  triggers, on objects it used to own. Any runtime path that does must move to a
  migration or to a purpose-owned definer first.
- **Runtime code must not rely on owner RLS bypass.** A table's owner bypasses its
  row-level security unless the table is `FORCE ROW LEVEL SECURITY`. On non-FORCE RLS
  tables the shared login owned, it becomes subject to the policies. Runtime reads and
  writes there must pass the policies on their own.
- **Nothing may rely on being the configured migration role.** Six init-db bunkers
  record the migration role in a `bootstrap_configuration` singleton:
  `dashboard_attention`, `dashboard_stepup`, `dnd_generation_admin`, `perception_admin`,
  `restore_drill_executor_admin` and `runtime_attention_admin`. Step 4 rotates that
  record to the migrator. Some `dashboard_attention` functions (`remember`, `evaluate`
  and the claim functions) also accept the configured migration role as a caller,
  besides the butler roles. Once the record is rotated, a caller that reaches them as
  the shared login without `SET ROLE` is refused. Every runtime caller must use its
  butler or capability role first. The bunkers themselves, and the runtime-attention
  v2–v5 ladder, are deleted by a later slice. Deleting them needs an owner decision on
  whether the migrator may act as the purpose owner roles.
- **The shared login must not keep install and rollback EXECUTE.** It held `EXECUTE` on
  the `SECURITY DEFINER` functions `dashboard_attention` and `dashboard_stepup`
  `install`/`finalize`/`deactivate_interface`, and `perception_admin`
  `install`/`rollback_interface`. They have no caller check, and `deactivate_interface`
  revokes every claim and binding. The step 4 replay revokes that `EXECUTE` from the
  shared login when `butlers.rotate_migration_role_from` is set (bu-dss7he.25), in a
  separate last block, not in the rotation block. Without the setting nothing is revoked,
  which keeps the shared login usable as the migrator until activation.

1. Provision the `butlers_migrator` login and its two BWS keys (bu-dss7he.21). Leave
   `BUTLERS_RUNTIME_DB_USER`/`_PASSWORD` in place: they remain the runtime login, and
   rollback needs them.
2. Suspend the release (`suspend: true`). Every Deployment scales to 0 and CronJobs
   stop. No daemon can restart in `run` mode against a database its login no longer
   owns, and no migrations step can run mid-change.
3. Run `butlers db transfer-ownership --from-login <shared> --to-login butlers_migrator`
   with superuser connection env. Read the dry run, then repeat it with `--execute`.
4. Replay `scripts/init-db.sql` as the superuser. Name the migrator as the connecting user
   and rotate the migration role from the shared login:
   `PGOPTIONS="-c butlers.connecting_user=butlers_migrator -c butlers.rotate_migration_role_from=<shared>" psql ... -f scripts/init-db.sql`.
   - The read-only preflight refuses the rotation unless the shared login owns nothing
     in the database (step 3). Default privileges are exempt and are reported in a
     notice.
   - One block then re-points the six `bootstrap_configuration` records to the migrator,
     all six or none.
   - The rest of the replay grants the migrator schema `CREATE`/`USAGE` and the default
     privileges.
   - The last block of the replay revokes the shared login's `EXECUTE` on the eight
     install and teardown functions above. The migrator keeps it.
   - Without the rotation setting, a replay naming a different login is refused
     ("<schema> bootstrap configuration differs") for all six pins.
5. Set `database.migratorLogin.enabled: true` in `values.dev.yaml` (a PR) and deploy
   with `suspend: false`. Confirm the `migrations` initContainer succeeds and the daemons
   pass their head check. Then run
   `butlers db check-grants --after-init-db-replay`. The replay re-widens grants, and
   `apply-grants` narrows them again when `database.applyGrants` is on.
6. Verify that no caller is refused. The shared login keeps its `butler_*_rw` memberships
   with `INHERIT`, so it still reaches the attention functions, but after the rotation the
   caller check refuses it unless it has done `SET ROLE` into its butler role. Without
   `SET ROLE`:
   - `dashboard_attention.remember` and `final_claim` return `false`. That is silent,
     dropped work, so a green deploy proves nothing.
   - `dashboard_attention.consume` returns `{"state":"refused"}`, and `owner_creation`
     raises "Owner creation refused". That is loud.
   - `evaluate`, `candidate_origin` and `decay_*` have no caller check and keep working.

   Probe it deterministically, connected as the shared login with no `SET ROLE`:
   `SELECT dashboard_attention.final_claim('general', gen_random_uuid(), NULL);`.
   It returns `true` before the rotation and `false` after. A random id matches no row, so
   the probe has no other effect. Then:
   - look for runtime sessions that connect as the shared login and call `remember` or
     `final_claim` without a prior `SET ROLE` (`pg_stat_activity`, or the statement log);
   - fix any such caller to `SET ROLE` into its butler role (bu-dss7he.24 found none in
     the code) before relying on the switch.

**Rollback:** suspend, then set the flag back to `false`. Then transfer the other way
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
