# Heartbeat Endpoint Coverage Verification — bu-hb7dh.2

**Verdict: PATH_B — GAP FOUND**

## What Was Checked

### 1. Source-of-truth comparison

`/api/butlers` (list endpoint) derives its butler set from `get_butler_configs()`,
which scans the roster filesystem (`roster/*/butler.toml`).

`/api/system/butlers/heartbeat` derives its butler set from:
```python
all_butler_names = sorted(db.butler_names)   # = DatabaseManager._pools.keys()
all_names = sorted(set(all_butler_names) | set(registry.keys()))
```

These sources differ. `db.butler_names` is populated by `init_db_manager()`, which
iterates the same `butler_configs` list but silently skips butlers whose DB pool
creation raises an exception (see `deps.py:397-398`).

### 2. The gap

If a butler's DB pool fails to initialize AND the butler has never sent a heartbeat
to the switchboard (`butler_registry` has no row for it), the butler:
- **Appears** in `/api/butlers` (roster-based scan always includes it)
- **Does NOT appear** in `/api/system/butlers/heartbeat` (omitted entirely — not
  even an `error='schema_unreachable'` entry)

The `error='schema_unreachable'` path at `system.py:726-728` only fires when
the butler IS in `db.butler_names` but its session queries fail — it does not
cover the case where the butler was never registered with the DB manager at all.

### 3. Verified behavior (code inspection)

- Butlers in `db.butler_names` but absent from registry: appear with
  `last_heartbeat_at=null`, `heartbeat_age_seconds=null`, `error=null`.
  Session facts (last_session_at, active_session_count) are populated normally.
  **This is correct behavior.**

- Butlers in `db.butler_names` whose session queries fail: appear with
  `error='schema_unreachable'`. **This is correct behavior.**

- Butlers in the registry but NOT in `db.butler_names`: appear via the union at
  `system.py:681`. Session facts are skipped (guarded by `if name in all_butler_names`).
  **This is correct behavior.**

- Butlers in neither `db.butler_names` nor the registry: **absent from results**.
  This is the gap. The heartbeat should use `get_butler_configs()` as the canonical
  source so these butlers appear with `error='schema_unreachable'`.

### 4. Existing test coverage

Three existing tests cover heartbeat scenarios:
- `test_heartbeat_happy_path_fields` — online butler, all fields populated
- `test_heartbeat_schema_unreachable_sets_error` — session query fails -> error
- `test_heartbeat_503_when_registry_fails` — switchboard query fails -> 503

None asserted parity between heartbeat and the list endpoint's butler set.

### 5. Regression tests added

Two new tests added to `tests/api/test_system.py`:

- `test_heartbeat_null_last_heartbeat_when_not_in_registry` — butler in
  `db.butler_names` but absent from registry appears with null heartbeat fields.
  Verifies the happy-path for butlers that have never pinged the switchboard.

- `test_heartbeat_omits_butler_absent_from_db_manager_and_registry` — documents
  the gap in two assertions:
  1. A butler reachable only via the registry union path (failed pool + has pinged)
     correctly appears in results.
  2. A butler that failed pool init AND never pinged is silently omitted
     (gap confirmed with a `assert "ghost" not in names_no_registry` that passes,
     documenting the known defect).

## Fix Required

The heartbeat endpoint should use `get_butler_configs()` as its canonical butler
source (matching the list endpoint) and produce `error='schema_unreachable'` for
any butler name not in `db.butler_names`. This requires plumbing the butler configs
into the endpoint or exposing the roster-based list via a shared dependency.

## Files Inspected

- `src/butlers/api/routers/system.py:639-741` (heartbeat endpoint)
- `src/butlers/api/routers/butlers.py:179-193` (list endpoint)
- `src/butlers/api/db.py:90-163` (DatabaseManager, add_butler, butler_names)
- `src/butlers/api/deps.py:205-398` (discover_butlers, init_db_manager)
- `tests/api/test_system.py` (existing + new tests)
