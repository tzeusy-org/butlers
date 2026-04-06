# Beads 1.0 Migration Prompt

> Give this prompt to an agent session working in any rig project that needs
> migration. The agent should `cd` into the project root before starting.

---

## Context

Beads (bd) was upgraded from v0.58.0 to v1.0.0 across all Gas Town rigs. The
upgrade changed how bd discovers its Dolt database server:

- **Old behavior (0.58.0):** bd walked up parent directories to find
  `~/gt/.beads/dolt-server.port` (the Gas Town shared Dolt server on port 3307).
- **New behavior (1.0.0):** bd looks only in the project's own `.beads/` directory
  for `dolt-server.port`. If missing, it either shows port 0 (broken) or
  auto-starts a local Dolt server with an empty database.

The Gas Town shared Dolt server runs on **port 3307** at `127.0.0.1`, managed by
`gt dolt`, with data in `~/gt/.dolt-data/`. Each rig has its own database on this
shared server (e.g., rig "al" uses database "al", rig "butlers" uses "butlers").

Additionally, the database schemas on the shared server are still at 0.58.0 for
most rigs and need to be migrated to 1.0.0 (new columns like `no_history`, UUID
conversions for event/comment IDs).

## What you need to do

Migrate this project's beads configuration so that:
1. bd connects to the shared GT Dolt server (port 3307) instead of a local one
2. The database schema is migrated from 0.58.0 to 1.0.0
3. No local Dolt server or data directory remains
4. `bd` commands work correctly (list, export, ready, doctor)

## Step-by-step migration

### Phase 1: Kill any local Dolt server

```bash
# Check if a local server is running
if [ -f .beads/dolt-server.pid ]; then
  pid=$(cat .beads/dolt-server.pid)
  if ps -p "$pid" > /dev/null 2>&1; then
    echo "Killing local Dolt server PID $pid"
    kill "$pid"
    sleep 1
    # Verify it's dead
    ps -p "$pid" > /dev/null 2>&1 && echo "WARN: still alive, try kill -9" || echo "OK: dead"
  else
    echo "PID file exists but process $pid is not running (stale)"
  fi
else
  echo "No local server PID file found (good)"
fi
```

### Phase 2: Clean up local server artifacts

Remove local Dolt server files. These are all gitignored, so this is safe:

```bash
rm -f .beads/dolt-server.port
rm -f .beads/dolt-server.pid
rm -f .beads/dolt-server.lock
rm -f .beads/dolt-server.activity
rm -f .beads/dolt-server.log
rm -f .beads/beads.db          # Stale SQLite artifact
rm -rf .beads/dolt/             # Local Dolt data directory (empty or stale)
```

### Phase 3: Point at the shared GT Dolt server

Write the port file that tells bd where to connect:

```bash
echo -n "3307" > .beads/dolt-server.port
```

### Phase 4: Verify metadata.json

The `metadata.json` file should look like this (the `dolt_database` value must
match this rig's database name on the GT server):

```json
{
  "backend": "dolt",
  "database": "dolt",
  "dolt_database": "<rig-db-name>",
  "dolt_mode": "server"
}
```

**Check and fix:**
- Remove `dolt_server_port` if present (deprecated; the port file is canonical)
- Remove `project_id` if present (bd doctor --fix will regenerate it correctly)
- Ensure `dolt_database` matches the rig's database name on the GT server
  (check with: `mysql -h 127.0.0.1 -P 3307 -u root -e "SHOW DATABASES;"`)

### Phase 5: Test connection

```bash
bd dolt test
```

Expected output:
```
Testing connection to 127.0.0.1:3307...
✓ Connection successful
```

If you see "Server not reachable" or "port 0", the port file wasn't written
correctly. Verify: `cat .beads/dolt-server.port` should output exactly `3307`.

### Phase 6: Run schema migration + doctor

```bash
bd doctor --fix --yes
```

This will:
- Auto-migrate the database schema from 0.58.0 → 1.0.0 (UUID conversions, new columns)
- Backfill `project_id` into metadata.json and the database
- Report any remaining issues

**IMPORTANT:** The schema migration runs on the **shared GT Dolt server database**.
This is correct and expected — the shared server is the source of truth. The
migration is idempotent; if another rig already migrated this database, it's a
no-op.

**WARNING about bd doctor --fix "Database" fixer:** If doctor reports "No database
found", it may try to create a new local Dolt store and import from issues.jsonl.
This is WRONG for our setup — we want the shared server database. If this happens:
1. The shared database is the one that matters (it already has all the issues)
2. The local store creation is harmless but unnecessary
3. Re-verify with `bd dolt test` that you're still on port 3307

### Phase 7: Commit Dolt changes

```bash
bd vc commit -m "schema: upgrade to bd 1.0.0"
```

### Phase 8: Verify with export

```bash
bd export -o .beads/issues.jsonl
```

The exported count should match the issue count in the database:
```bash
# Compare: these two numbers should be close
# (export includes wisps, so may be slightly higher than issues alone)
mysql -h 127.0.0.1 -P 3307 -u root -D <rig-db-name> -N -e "SELECT COUNT(*) FROM issues;"
wc -l .beads/issues.jsonl
```

## Verification checklist

Run each check and confirm the expected result:

| # | Check | Command | Expected |
|---|-------|---------|----------|
| 1 | No local Dolt server running | `ls .beads/dolt-server.pid && ps -p $(cat .beads/dolt-server.pid) 2>/dev/null; echo $?` | File missing OR process not found (exit 1) |
| 2 | No local Dolt data directory | `ls -d .beads/dolt/ 2>/dev/null; echo $?` | Exit 1 (not found), OR dir exists but is empty/has only `.beads-credential-key` |
| 3 | Port file points to 3307 | `cat .beads/dolt-server.port` | `3307` |
| 4 | metadata.json has no deprecated keys | `python3 -c "import json; m=json.load(open('.beads/metadata.json')); assert 'dolt_server_port' not in m; print('OK')"` | `OK` |
| 5 | metadata.json dolt_database is correct | `python3 -c "import json; print(json.load(open('.beads/metadata.json'))['dolt_database'])"` | Matches the rig's DB name on GT server |
| 6 | Connection works | `bd dolt test` | `✓ Connection successful` |
| 7 | Schema is 1.0.0 | `mysql -h 127.0.0.1 -P 3307 -u root -D <db> -N -e "SELECT value FROM metadata WHERE \`key\`='bd_version';"` | `1.0.0` |
| 8 | bd doctor passes | `bd doctor 2>&1 \| grep -E '✖.*error'` | No errors (0 lines) |
| 9 | bd list works | `bd list --json \| python3 -c "import sys,json; print(len(json.load(sys.stdin)))"` | Number > 0 (or 0 if rig genuinely has no open issues) |
| 10 | bd list --all works | `bd list --all --json \| python3 -c "import sys,json; print(len(json.load(sys.stdin)))"` | No errors; returns total issue count |
| 11 | bd export works | `bd export -o /dev/null 2>&1` | `Exported N issues` where N > 0 |
| 12 | No deprecation warnings | `bd dolt test 2>&1 \| grep -i deprecated` | No output (0 lines) |

## Known edge cases

### Rig has a `.beads/redirect` file (e.g., butlers → mayor/rig)
Some projects use a redirect file to delegate beads to a subdirectory. The
migration should be done in the **target** directory (where the redirect points),
not the project root. Check: `cat .beads/redirect`

### Rig has no metadata.json (e.g., homelab, property_agent)
These rigs may not have been initialized with beads. Run `bd init` after setting
up the port file, or `bd bootstrap` if there's an existing issues.jsonl.

### Database doesn't exist on GT server
If `mysql -h 127.0.0.1 -P 3307 -u root -D <name> -e "SELECT 1;"` fails with
"Unknown database", the rig was never registered on the shared server. This is
outside the scope of this migration — escalate to the mayor.

### tze_hud / viz_on_shenton: no bd_version in metadata table
These databases exist on the GT server but have no `bd_version` row. The schema
migration should still work — bd doctor detects the missing version and runs all
migrations from the beginning.

### Multiple rigs share a database name
The GT server has both `hud` and `tze_hud`, and both `viz` and `viz_on_shenton`.
Verify which database name matches your rig's `metadata.json` `dolt_database`
field. Do not change it unless you know which is canonical.

## Databases on GT server and their current status

As of 2026-04-06:

| Database | bd_version | Notes |
|----------|-----------|-------|
| al | 0.58.0 | needs migration |
| beads | 0.58.0 | needs migration |
| butlers | 1.0.0 | **already migrated** |
| gt | 1.0.0 | **already migrated** |
| homelab | 0.58.0 | needs migration |
| hq | 0.58.0 | needs migration (town beads) |
| hud | 0.58.0 | needs migration |
| property_agent | 0.58.0 | needs migration |
| repo | 0.58.0 | needs migration |
| tui | 0.58.0 | needs migration |
| tze_hud | unknown | needs migration (no bd_version row) |
| viz | 0.58.0 | needs migration |
| viz_on_shenton | unknown | needs migration (no bd_version row) |
| zq | 0.58.0 | needs migration |
| zz | 0.58.0 | needs migration |
| zzlab | 0.58.0 | needs migration |
| zztest | 0.58.0 | needs migration |
| zztime | 0.58.0 | needs migration |
