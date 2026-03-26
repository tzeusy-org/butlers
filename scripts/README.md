# Scripts

Utility scripts for repository maintenance and fixes.

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
