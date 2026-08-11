# Scripts

Utility scripts for repository maintenance and fixes.

## init-db.sql

Privileged PostgreSQL bootstrap script to run **before** the first Alembic
migration on a fresh database. It must be executed by a privileged cluster
superuser. Supply the normal connecting/migration user separately through the
`butlers.connecting_user` GUC; it must not be the active bootstrap identity.
It is safe to re-run later if the managed schema/role surface expands.

**Prerequisites:** The PostgreSQL server must have the `pgvector` binary
installed.  The standard `postgres` Docker image does not include it; use
`pgvector/pgvector:pg16` (or later) instead, or install the extension manually.

What it does:

1. Installs required extensions: `pgcrypto`, `uuid-ossp`, `vector` (pgvector),
   and `pg_trgm`.
2. Creates the managed schemas and runtime roles if they do not already exist.
3. Grants each runtime role (`butler_{schema}_rw`, `butler_qa_rw`,
   `connector_writer`) to the connecting user (`POSTGRES_USER`, typically
   `butlers`) so `SET ROLE` works at runtime.
4. Grants database/schema ACLs to runtime roles.
5. Grants schema `USAGE, CREATE` to the migration/runtime user so Alembic can
   create future objects without a second privileged follow-up step.
6. Configures `ALTER DEFAULT PRIVILEGES FOR ROLE <connecting user>` so objects
   created later by Alembic inherit the runtime ACLs immediately.

**Why this is privileged:** Database-level grants, schema ownership/ACLs, and
`ALTER DEFAULT PRIVILEGES FOR ROLE <connecting user>` require a privileged
cluster-superuser bootstrap role. After this script runs once, normal `docker compose` /
Alembic flows can continue using the lower-privilege `butlers` user.

### Usage

```bash
# Typical dev: run as a cluster superuser; grants to 'butlers' (default connecting user)
psql -h localhost -U postgres -d butlers -f scripts/init-db.sql

# Targeting a different connecting user
PGOPTIONS="-c butlers.connecting_user=myappuser" \
    psql -h localhost -U postgres -d butlers -f scripts/init-db.sql
```

The script is idempotent — safe to re-run on an already-provisioned database.

**Note on ordering:**
- Run `init-db.sql` **after** the database and connecting user exist.
- Run Alembic migrations next: `butlers db migrate` (or `docker compose run
  migrations`).
- You should not need a post-migration privileged grant pass for new objects
  created by the connecting user; re-run the bootstrap only when the managed
  schema/role surface itself changes.

## provision_restore_drill_executor.sh

The managed one-shot provisioner enables the distinct
`restore_drill_executor` login after `init-db.sql` has reserved it and the
restore-drill migration has installed its constrained persistence functions.
It reads the executor password only from the private path named by
`RESTORE_DRILL_EXECUTOR_PASSWORD_FILE`; do not pass that value through shared
database environment variables or tracked configuration. See
[`docs/operations/backup-restore.md`](../docs/operations/backup-restore.md)
for the deployment boundary and rollback rules.

## restore-drill-firewall.sh

`restore-drill-firewall.sh` is the reviewed installation source for the
root-owned fixed runtime wrapper at
`/usr/local/libexec/butlers-restore-drill-firewall`. It accepts only validated
literal project, PostgreSQL IPv4, and port arguments. Its policy default-denies
the executor bridge at both Docker's `DOCKER-USER`/`FORWARD` hook and the
bridge-to-host `INPUT` path, allowing only TCP to the configured PostgreSQL
endpoint. The executor keeps a configured DNS database host as its TLS identity
(including `verify-full`), maps that name locally to the supplied IPv4, and has
only a container-loopback DNS upstream; raw DNS and host/gateway paths remain
denied.

Use `install_restore_drill_firewall_wrapper.sh` only in a root-controlled
deployment setup to install that immutable target. The checked-in
`restore-drill-firewall.sudoers` template grants a deployment group access only
to the fixed wrapper's normal three-argument form. Never grant sudo for the
checkout script, a checkout wildcard, `env`, a shell, or the installer. Both
`scripts/compose.sh` and `butlers deploy` stop/create the executor, invoke the
fixed wrapper, and only then start the stack; `restart: "no"` prevents an
unfenced daemon/host auto-start. Do not start `restore-drill-executor` through
a bare `docker compose up` command.

## dev.sh

Bootstraps the full local Butlers development stack in `tmux` (dashboard, frontend, connectors, backend, OAuth gate, and postgres preflight).

Contacts sync contract: contacts incremental sync is a module-internal poller
inside `uv run butlers up`. `dev.sh` does not launch a standalone contacts
connector process.

### Usage

```bash
# Preferred compatibility entrypoint
./dev.sh

# Direct script path
./scripts/dev.sh
```

## clear-processes.sh

Kills processes currently listening on the expected local dev ports.

Default ports:
- `POSTGRES_PORT` (default `54320`)
- `FRONTEND_PORT` (default `41173`)
- `DASHBOARD_PORT` (default `41200`)

You can override with `EXPECTED_PORTS` (comma/space separated), for example:

```bash
EXPECTED_PORTS="54320,41173,41200" ./scripts/clear-processes.sh
```

## cleanup_logs.sh

Removes old log files and prunes empty directories under `logs/`.

- Deletes files older than 3 days (default retention)
- Removes empty subdirectories after file cleanup

### Usage

```bash
# Use repository logs/ directory (default)
./scripts/cleanup_logs.sh

# Use a custom logs directory
./scripts/cleanup_logs.sh /path/to/logs
```

Optional environment variable:
- `RETENTION_DAYS` (default: `3`)

## merge_pr_exact_base.py

Performs the final REST squash merge for a reviewed pull request without
silently accepting a target-branch retarget or base-branch advance. GitHub's
REST merge endpoint can condition only on the pull request head SHA; it cannot
atomically require the reviewed target ref or base SHA. This helper therefore:

1. confirms the currently open PR still has the final reviewed head, target
   branch name, and live target-branch SHA,
2. keeps the supported head-SHA pin on the REST squash request, and
3. re-reads the merged PR's retained target branch name through GraphQL, and
4. verifies that the resulting squash commit has exactly the reviewed base as
   its sole parent and the same immutable result tree as the reviewed head.

It is the sole final merge route, not a substitute for terminal hosted CI,
independent review, or resolved review threads. Every hosted check must be
terminal green before invoking it; branch-protection required-check settings do
not relax that gate. Do not use bare REST merge requests, `gh pr merge`, or
automatic merge. Capture `headRefOid`, `baseRefName`, and the *live target
branch tip* from the same final revalidation. Do not use a PR's `baseRefOid` as
the expected base: it can remain stale while the target branch has advanced.

```bash
HEAD_SHA=$(gh pr view "$PR" --json headRefOid --jq .headRefOid)
BASE_REF=$(gh pr view "$PR" --json baseRefName --jq .baseRefName)
BASE_SHA=$(gh api "repos/Tzeusy/butlers/git/ref/heads/$BASE_REF" --jq .object.sha)
```

Then pass all three exact values to the helper:

```bash
python3 scripts/merge_pr_exact_base.py \
  --pr "$PR" \
  --expected-head "$HEAD_SHA" \
  --expected-base-ref "$BASE_REF" \
  --expected-base "$BASE_SHA"
```

Only `merged-exact-base` (exit `0`) permits the coordinator to close the source
Bead. A `premerge-head-drift`, `premerge-base-ref-drift`, or
`premerge-base-drift` result sends no merge request; rebase onto current
`origin/main`, then repeat the full exact-head review and CI gates.
`postmerge-base-drift` means GitHub merged the SHA-pinned head on a newer base
during the unavoidable API race: leave the source Bead open and record/run the
required post-merge race audit instead of treating it as exact-current-base
evidence. `postmerge-base-ref-drift` means the post-merge GraphQL lookup either
could not verify the retained target ref or found a different ref name. It is
also exit `4` and blocks closure even if the squash commit's sole parent still
matches the reviewed base SHA.
`postmerge-patch-drift` is likewise exit `4`: it means the helper could not
obtain immutable commit-tree evidence for both commits, or their result trees
differed. With the verified sole parent equal to the reviewed base, matching
tree IDs are an authoritative proof that the landed squash has the reviewed
net patch, including binary, rename, and empty changes. The JSON audit records
`expected_patch_tree_sha`, `landed_patch_tree_sha`, and
`patch_identity_matches`; no nonmatching or unavailable evidence permits
source-Bead closure.
`postmerge-unexpected-squash-parent-shape` is also exit `4`: GitHub has already
merged the PR, but the result did not have exactly one parent. Its audit retains
the parent evidence; leave the source Bead open and run the documented
post-merge audit/investigation rather than treating it as exact-base evidence.

## fix_beads_dependency_timestamps.py

Detects and fixes dependency records with zero timestamps (`created_at="0001-01-01T00:00:00Z"`) in `.beads/issues.jsonl`.

### Background

Due to a bug in the `bd` CLI when running in no-daemon worktree flows, dependency records created via `bd dep add` may have their `created_at` timestamp set to the zero timestamp instead of a real timestamp. This breaks downstream auditing and timeline reasoning.

### Usage

```bash
# Dry-run mode (shows what would be fixed without making changes)
python scripts/fix_beads_dependency_timestamps.py --dry-run

# Apply fixes
python scripts/fix_beads_dependency_timestamps.py

# Specify custom path
python scripts/fix_beads_dependency_timestamps.py --jsonl-path /path/to/issues.jsonl
```

### How it works

1. Scans all issues in `issues.jsonl`
2. Finds dependency records with `created_at="0001-01-01T00:00:00Z"`
3. Replaces the zero timestamp with the parent issue's `updated_at` timestamp (or current time as fallback)
4. Writes the corrected records back to the file

### Example output

```
Fixing issue butlers-2bq.7:
  - Dependency butlers-2bq.7 -> butlers-886 (type: blocks): 0001-01-01T00:00:00Z -> 2026-02-15T02:15:24.686020053+08:00

Summary: scanned 746 issues, modified 9 issues, fixed 9 dependencies
```
