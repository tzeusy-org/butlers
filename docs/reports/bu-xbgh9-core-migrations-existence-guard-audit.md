# Audit: Core Migration Existence Guards for Butler-Schema Tables

**Issue:** bu-xbgh9
**Date:** 2026-04-30
**Scope:** `alembic/versions/core/` — all 57 migrations (core_001 through core_084)
**Trigger:** bu-8sbbi review of PR #1303, which found `core_085` directly touching
`chronicler.episodes` without an existence guard, causing `ProgrammingError` in
test DBs that run only the core+memory+relationship chain.

---

## Summary

All 57 core migrations on `main` (core_001 through core_084) that touch
butler-schema tables are **fully guarded**. No fixes required on this branch.

`core_085` (on pending branch `agent/bu-6c5i6`, not yet merged to `main`) was
the only migration with a missing guard. It was fixed in commit `ef94c40e`
(`fix(migration): guard core_085 against missing chronicler schema [bu-8sbbi]`)
before this audit ran.

---

## Methodology

1. Listed all 57 files in `alembic/versions/core/`.
2. Identified candidates with `grep` for:
   - Butler-schema-specific table names: `episodes`, `facts`, `rules`, `point_events`,
     `event_chains`, `scheduled_tasks`.
   - Schema-qualified DML: `UPDATE <schema>.`, `DELETE FROM <schema>.`, `ALTER TABLE <schema>.`
     where `<schema>` is one of the 12 butler schemas (education, finance, general, health,
     home, lifestyle, messenger, relationship, switchboard, travel, chronicler, memory).
   - Dynamic schema iteration: `for schema in _BUTLER_SCHEMAS`.
3. For each candidate, verified the presence of one or more guard mechanisms:
   - Python-layer: `_table_exists()`, `_column_exists()` (core_056 pattern).
   - SQL-layer: `DO $$ IF EXISTS (SELECT 1 FROM information_schema.tables ...) THEN ... END IF END $$` (core_080/core_085 pattern).
   - SQL-layer: `ADD COLUMN IF NOT EXISTS`, `DROP COLUMN IF EXISTS`, `CREATE TABLE IF NOT EXISTS`.
   - SQL-layer: `EXCEPTION WHEN undefined_table THEN NULL` (core_011 pattern).
   - Wrapper helper: `_execute_best_effort()` (core_065/core_081/core_082 pattern).

---

## Full Migration Matrix

Only migrations that reference butler-schema tables are listed. All others
(`core_002`, `core_004`–`core_010`, `core_012`, `core_041`–`core_042`,
`core_045`–`core_049`, `core_051`–`core_055`, `core_057`–`core_062`,
`core_064`, `core_066`–`core_079`, `core_083`) touch only `public` or `connectors`
schemas which are guaranteed to exist in the core chain.

| Migration | Tables touched | Guard mechanism | Status |
|---|---|---|---|
| `core_001_foundation.py` | `scheduled_tasks`, `sessions`, etc. (all butlers) | `CREATE TABLE IF NOT EXISTS` + `DO $$ EXCEPTION WHEN` | GUARDED |
| `core_011_steam_play_history_fix.py` | `connectors.steam_play_history` | `ADD/DROP COLUMN IF NOT EXISTS`, `DO $$ EXCEPTION WHEN undefined_table` | GUARDED (connectors is core schema) |
| `core_012_temporal_intelligence.py` | `scheduled_tasks`, `delivery_preferences`, etc. (all butlers) | `CREATE TABLE IF NOT EXISTS` | GUARDED |
| `core_013_event_chains.py` | `event_chains` (all butlers) | `CREATE TABLE IF NOT EXISTS` | GUARDED |
| `core_043_deadline_columns.py` | `scheduled_tasks` (unqualified — search_path schema) | `ADD/DROP COLUMN IF NOT EXISTS` | GUARDED (core_001 creates table first; ADD IF NOT EXISTS handles idempotency) |
| `core_044_event_chains_status.py` | `event_chains` (all butlers, iterated) | `DO $$ IF to_regclass(...) IS NOT NULL` | GUARDED |
| `core_050_schedule_token_budget.py` | `scheduled_tasks` (all butlers, iterated) | `DO $$ IF EXISTS (information_schema.tables) THEN ALTER TABLE ... ADD COLUMN IF NOT EXISTS` | GUARDED |
| `core_056_tenant_id_defaults.py` | `episodes`, `facts`, `rules` (all butlers, iterated) | Python `_table_exists()` + `_column_exists()` checks on every operation | GUARDED |
| `core_063_v_briefing_contributions.py` | `state` (specialist butlers) | Python `_schema_exists()` + `_state_table_exists()` | GUARDED |
| `core_065_public_schema_write_grants.py` | GRANT on `public.facts` and other public tables | `_execute_best_effort()` with role existence check | GUARDED (public schema tables, not butler-schema) |
| `core_080_chronicler_token_budget.py` | `chronicler.scheduled_tasks` | `DO $$ IF EXISTS (information_schema.tables WHERE schema='chronicler')` | GUARDED |
| `core_081_owntracks_points.py` | `connectors.owntracks_points` | `CREATE TABLE IF NOT EXISTS`, `_execute_best_effort()` | GUARDED (connectors is core schema) |
| `core_082_backfill_delivery_tables_current_schema.py` | `delivery_preferences`, `deferred_notifications` (current schema) | `CREATE TABLE IF NOT EXISTS`, `EXCEPTION WHEN` | GUARDED |
| `core_084_home_assistant_history.py` | `connectors.home_assistant_history` | `CREATE TABLE IF NOT EXISTS`, `_execute_best_effort()` | GUARDED (connectors is core schema) |

**`core_085_backfill_spotify_owntracks_privacy.py`** (on `agent/bu-6c5i6`, not yet on `main`):
Fixed by commit `ef94c40e` — wraps `UPDATE chronicler.episodes` in `DO $$ IF EXISTS` guard.

---

## Key Findings

### No unguarded migrations on main

Every core migration that touches butler-schema tables uses one of the established
guard patterns. `core_056` (the migration mentioned specifically in the issue)
has comprehensive Python-layer guards (`_table_exists()` / `_column_exists()`) that
skip each table operation when the target table doesn't exist in the target schema.

### Guard patterns in use (reference)

**Pattern A — Python layer (core_056):**
```python
def _table_exists(schema: str, table: str) -> bool:
    bind = op.get_bind()
    return bind.execute(sa.text("SELECT to_regclass(:relname)"), ...).scalar() is not None

for schema in _SCHEMAS:
    for table in ("episodes", "facts", "rules"):
        if not _table_exists(schema, table) or not _column_exists(schema, table, "tenant_id"):
            continue
        op.execute(f'ALTER TABLE "{schema}"."{table}" ...')
```

**Pattern B — SQL DO block (core_080, core_085):**
```sql
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_schema = 'chronicler' AND table_name = 'episodes'
    ) THEN
        UPDATE chronicler.episodes SET ...;
    END IF;
END
$$;
```

**Pattern C — Best-effort wrapper (core_065, core_081, core_082):**
```python
def _execute_best_effort(statement: str, *, role_name: str | None = None) -> None:
    op.execute("""
        DO $$ BEGIN
            IF <condition> THEN EXECUTE <statement>; END IF;
        EXCEPTION WHEN insufficient_privilege THEN NULL; ...
        END $$;
    """)
```

### `core_043` note

`core_043` uses unqualified `ALTER TABLE scheduled_tasks ADD COLUMN IF NOT EXISTS` — no schema prefix. This is safe because:
1. `core_001` always runs first and creates `scheduled_tasks` in the search_path schema.
2. `ADD COLUMN IF NOT EXISTS` is idempotent.

The unqualified reference is intentional: the core chain runs once per butler schema
with `SET search_path TO {schema}`, so `scheduled_tasks` resolves to the correct
butler-owned table each time.

---

## Conclusion

No code changes are required. All core migrations on `main` (core_001 through core_084)
correctly guard against missing butler-schema tables. The guard convention is well-established
and consistently applied. New core migrations that touch non-core schemas should follow
Pattern B or Pattern C above.

The `core_085` guard (Pattern B) added by `ef94c40e` is the correct template for future
migrations that directly access a named butler schema like `chronicler.episodes`.
