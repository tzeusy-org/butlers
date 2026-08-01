# Scripts

Utility scripts for repository maintenance and fixes.

## init-db.sql

Privileged PostgreSQL bootstrap script to run **before** the first Alembic
migration on a fresh database. Must be executed by a superuser (or the
database owner). It is safe to re-run later if the managed schema/role surface
expands.

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
bootstrap role. After this script runs once, normal `docker compose` /
Alembic flows can continue using the lower-privilege `butlers` user.

### Usage

```bash
# Typical dev: run as superuser, grants to 'butlers' (default connecting user)
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
silently accepting a base-branch advance. GitHub's REST merge endpoint can
condition only on the pull request head SHA; it cannot atomically require the
base SHA that was reviewed. This helper therefore:

1. confirms the currently open PR still has the final reviewed head and base,
2. keeps the supported head-SHA pin on the REST squash request, and
3. verifies that the resulting squash commit has exactly the reviewed base as
   its sole parent.

It is a final merge guard, not a substitute for terminal hosted CI, independent
review, or resolved review threads. Capture `headRefOid` and the *live target
branch tip* from the same final revalidation. Do not use a PR's `baseRefOid` as
the expected base: it can remain stale while the target branch has advanced.

```bash
HEAD_SHA=$(gh pr view "$PR" --json headRefOid --jq .headRefOid)
BASE_REF=$(gh pr view "$PR" --json baseRefName --jq .baseRefName)
BASE_SHA=$(gh api "repos/Tzeusy/butlers/git/ref/heads/$BASE_REF" --jq .object.sha)
```

Then pass both exact values to the helper:

```bash
python3 scripts/merge_pr_exact_base.py \
  --pr "$PR" \
  --expected-head "$HEAD_SHA" \
  --expected-base "$BASE_SHA"
```

Only `merged-exact-base` (exit `0`) permits the coordinator to close the source
Bead. A `premerge-head-drift` or `premerge-base-drift` result sends no merge
request; rebase onto current `origin/main`, then repeat the full exact-head
review and CI gates. `postmerge-base-drift` means GitHub merged the SHA-pinned
head on a newer base during the unavoidable API race: leave the source Bead
open and record/run the required post-merge race audit instead of treating it
as exact-current-base evidence.

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
