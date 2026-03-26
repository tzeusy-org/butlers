# Shared → Public Schema Migration

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Eliminate the `shared` PostgreSQL schema by moving all its tables to `public`, so that default-initialized pools (which resolve to `public`) can reach cross-butler tables without explicit `search_path` configuration.

**Architecture:** Forward migration (option 2). A single new Alembic migration moves all 15 `shared.*` tables to `public` via `ALTER TABLE ... SET SCHEMA public`. Runtime code and tests are updated to use `public.` prefixes instead of `shared.`. Historical migrations are left untouched — they were already applied.

**Tech Stack:** PostgreSQL, asyncpg, Alembic, Python 3.12+, pytest

**Critical invariant:** The `general` butler has a local `general.entities` table (collection items) that shadows `shared.entities` (identity graph) via search_path ordering. After migration, `public.entities` replaces `shared.entities`. All references to what was `shared.entities` MUST use the explicit `public.` prefix — never unqualified — to avoid resolving to `general.entities` in the general butler context. The same `public.` prefix rule applies to ALL tables for consistency and safety.

---

### Task 1: Write the Alembic Forward Migration

Move all 15 tables from `shared` to `public`, preserving indexes, constraints, FKs, and grants.

**Files:**
- Create: `alembic/versions/core/core_041_shared_to_public_schema.py`

**Step 1: Create the migration file**

The migration must handle all 15 tables. `ALTER TABLE ... SET SCHEMA` automatically moves the table's indexes, constraints, sequences, and triggers. Cross-schema FK references from other schemas (e.g., `relationship.*` → `shared.contacts`) are updated automatically by PostgreSQL.

```python
"""shared_to_public_schema: move all shared-schema tables to public.

Revision ID: core_041
Revises: core_040
Create Date: 2026-03-26 00:00:00.000000

Eliminates the ``shared`` schema by moving all cross-butler tables to
``public``.  Pools that default-initialise without explicit search_path
now resolve these tables automatically.

ALTER TABLE ... SET SCHEMA moves the table together with its indexes,
constraints, sequences, and triggers.  Cross-schema FK references from
other schemas are updated automatically by PostgreSQL.

Grants are table-level (not schema-level), so they survive the move.
We re-grant USAGE ON SCHEMA public for completeness (public already has
default USAGE for all roles, but being explicit is cheap insurance).
"""
from alembic import op

revision = "core_041"
down_revision = "core_040"
branch_labels = None
depends_on = None

# Every table currently living in the ``shared`` schema, ordered so that
# parent tables move before children (FK targets before FK sources).
# ALTER TABLE SET SCHEMA updates cross-schema FKs automatically, but
# moving parents first avoids transient FK-target-not-found errors if
# the DDL is auto-committed per statement on some PG configs.
_TABLES_ORDERED = [
    # --- independent / parent tables first ---
    "contacts",
    "entities",
    "model_catalog",
    "provider_config",
    "ingestion_events",
    # --- first-level children ---
    "contact_info",         # FK → contacts
    "entity_info",          # FK → entities
    "google_accounts",      # FK → entities
    "memory_catalog",       # FK → entities (nullable)
    "butler_model_overrides",  # FK → model_catalog
    "token_limits",         # FK → model_catalog
    "token_usage_ledger",   # FK → model_catalog (partitioned)
    # --- second-level children ---
    "dashboard_conversations",  # standalone
    "dashboard_messages",       # FK → dashboard_conversations
    # --- standalone ---
    "healing_attempts",
]

_ALL_BUTLER_ROLES = (
    "butler_switchboard_rw",
    "butler_general_rw",
    "butler_health_rw",
    "butler_relationship_rw",
    "butler_messenger_rw",
    "butler_education_rw",
    "butler_finance_rw",
    "butler_home_rw",
    "butler_travel_rw",
)


def upgrade() -> None:
    conn = op.get_bind()

    # Guard: bail out if shared schema doesn't exist (fresh install).
    row = conn.execute(
        "SELECT 1 FROM information_schema.schemata WHERE schema_name = 'shared'"
    ).fetchone()
    if row is None:
        return

    for table in _TABLES_ORDERED:
        # Guard: skip tables that don't exist (partial installs).
        exists = conn.execute(
            "SELECT to_regclass(%s)", (f"shared.{table}",)
        ).scalar()
        if exists is None:
            continue

        # token_usage_ledger is range-partitioned; move partitions first.
        if table == "token_usage_ledger":
            partitions = conn.execute(
                """
                SELECT c.relname
                  FROM pg_inherits i
                  JOIN pg_class c ON c.oid = i.inhrelid
                  JOIN pg_namespace n ON n.oid = c.relnamespace
                 WHERE i.inhparent = 'shared.token_usage_ledger'::regclass
                   AND n.nspname = 'shared'
                """
            ).fetchall()
            for (part_name,) in partitions:
                conn.execute(f'ALTER TABLE shared."{part_name}" SET SCHEMA public')

        conn.execute(f"ALTER TABLE shared.{table} SET SCHEMA public")

    # Drop the now-empty shared schema.
    conn.execute("DROP SCHEMA IF EXISTS shared CASCADE")


def downgrade() -> None:
    conn = op.get_bind()

    # Recreate shared schema.
    conn.execute("CREATE SCHEMA IF NOT EXISTS shared")

    for table in reversed(_TABLES_ORDERED):
        exists = conn.execute(
            "SELECT to_regclass(%s)", (f"public.{table}",)
        ).scalar()
        if exists is None:
            continue

        if table == "token_usage_ledger":
            partitions = conn.execute(
                """
                SELECT c.relname
                  FROM pg_inherits i
                  JOIN pg_class c ON c.oid = i.inhrelid
                  JOIN pg_namespace n ON n.oid = c.relnamespace
                 WHERE i.inhparent = 'public.token_usage_ledger'::regclass
                   AND n.nspname = 'public'
                """
            ).fetchall()
            for (part_name,) in partitions:
                conn.execute(f'ALTER TABLE public."{part_name}" SET SCHEMA shared')

        conn.execute(f"ALTER TABLE public.{table} SET SCHEMA shared")

    # Re-grant schema USAGE for all butler roles.
    for role in _ALL_BUTLER_ROLES:
        has_role = conn.execute(
            "SELECT 1 FROM pg_roles WHERE rolname = %s", (role,)
        ).scalar()
        if has_role:
            conn.execute(f"GRANT USAGE ON SCHEMA shared TO {role}")
```

**Step 2: Verify the migration file syntax**

Run: `cd /home/tze/gt/butlers/mayor/rig && python -c "import ast; ast.parse(open('alembic/versions/core/core_041_shared_to_public_schema.py').read()); print('OK')"`

Expected: `OK`

**Step 3: Commit**

```bash
git add alembic/versions/core/core_041_shared_to_public_schema.py
git commit -m "feat: add core_041 migration — move shared schema tables to public"
```

---

### Task 2: Update Search Path and Alembic Env

Remove `shared` from the runtime search path and stop creating the shared schema in migrations.

**Files:**
- Modify: `src/butlers/db.py:58-67` (search path function)
- Modify: `alembic/env.py:166-175` (schema creation + search_path SET)

**Step 1: Update `schema_search_path()` in `db.py`**

Change the search path from `(<butler>, shared, public)` to `(<butler>, public)`:

```python
# BEFORE (db.py:64)
    for part in (normalized, "shared", "public"):

# AFTER
    for part in (normalized, "public"):
```

**Step 2: Update `alembic/env.py`**

Remove the shared schema reference from the migration search_path:

```python
# BEFORE (env.py:167-172)
        if target_schema is not None:
            own_schema = _quote_ident(target_schema)
            shared_schema = _quote_ident("shared")
            connection.exec_driver_sql(f"CREATE SCHEMA IF NOT EXISTS {own_schema}")
            connection.exec_driver_sql(f"SET search_path TO {own_schema}, {shared_schema}, public")

# AFTER
        if target_schema is not None:
            own_schema = _quote_ident(target_schema)
            connection.exec_driver_sql(f"CREATE SCHEMA IF NOT EXISTS {own_schema}")
            connection.exec_driver_sql(f"SET search_path TO {own_schema}, public")
```

**Step 3: Update the search_path unit test in `tests/core/test_db_ssl.py`**

The test at line ~81 asserts `schema_search_path("shared") == "shared,public"`. After removing "shared" from the hardcoded list, the function now just dedupes `(normalized, "public")`. When called with `"shared"`, it should return `"shared,public"` — which it still will, since `"shared"` is treated as a normal butler schema name. **Verify this is still true; no change should be needed.**

Run: `cd /home/tze/gt/butlers/mayor/rig && uv run pytest tests/core/test_db_ssl.py -q --tb=short -k search_path`

Expected: PASS (the function still produces `"shared,public"` when called with `"shared"` as the schema name, it just no longer injects "shared" for _every_ schema)

**Step 4: Commit**

```bash
git add src/butlers/db.py alembic/env.py
git commit -m "refactor: remove shared schema from search_path and alembic env"
```

---

### Task 3: Update Runtime SQL — Identity and Contacts

Replace all `shared.` schema prefixes with `public.` in identity, contacts, and credential management code.

**Files:**
- Modify: `src/butlers/identity.py` (~24 occurrences of `shared.`)
- Modify: `src/butlers/credential_store.py` (~12 occurrences)
- Modify: `src/butlers/modules/contacts/__init__.py` (~7 occurrences)
- Modify: `src/butlers/modules/contacts/backfill.py` (~10 occurrences)
- Modify: `src/butlers/modules/contacts/telegram_provider.py` (~1 occurrence)

**Step 1: Bulk replace `shared.` → `public.` in each file**

For every file listed above, replace all SQL schema qualifiers. The replacement is mechanical: every occurrence of `shared.contacts`, `shared.contact_info`, `shared.entities`, `shared.entity_info` becomes `public.contacts`, `public.contact_info`, `public.entities`, `public.entity_info`.

Use this command to verify the scope first:

```bash
cd /home/tze/gt/butlers/mayor/rig
grep -n 'shared\.' src/butlers/identity.py src/butlers/credential_store.py src/butlers/modules/contacts/__init__.py src/butlers/modules/contacts/backfill.py src/butlers/modules/contacts/telegram_provider.py
```

Then apply:

```bash
sed -i 's/shared\.\(contacts\|contact_info\|entities\|entity_info\)/public.\1/g' \
  src/butlers/identity.py \
  src/butlers/credential_store.py \
  src/butlers/modules/contacts/__init__.py \
  src/butlers/modules/contacts/backfill.py \
  src/butlers/modules/contacts/telegram_provider.py
```

**Step 2: Manually verify no `shared.` SQL references remain**

```bash
grep -n 'shared\.' src/butlers/identity.py src/butlers/credential_store.py src/butlers/modules/contacts/__init__.py src/butlers/modules/contacts/backfill.py src/butlers/modules/contacts/telegram_provider.py
```

Expected: no output (or only non-SQL references like comments/docstrings — review any hits)

**Step 3: Run targeted tests**

```bash
uv run pytest tests/daemon/test_owner_bootstrap.py tests/daemon/test_notify_contact_id.py tests/modules/test_contacts_backfill.py -q --tb=short
```

Expected: These tests will FAIL because they still assert `shared.` in queries. That's expected — tests are updated in Task 6.

**Step 4: Commit**

```bash
git add src/butlers/identity.py src/butlers/credential_store.py src/butlers/modules/contacts/
git commit -m "refactor: identity and contacts — shared.* → public.* schema prefix"
```

---

### Task 4: Update Runtime SQL — Memory, Models, Healing, Core

Replace `shared.` with `public.` in memory module, model routing, healing, ingestion, spawner, and remaining core modules.

**Files:**
- Modify: `src/butlers/modules/memory/storage.py` (~8 occurrences)
- Modify: `src/butlers/modules/memory/search.py` (~5 occurrences)
- Modify: `src/butlers/modules/memory/tools/entities.py` (~16 occurrences)
- Modify: `src/butlers/modules/memory/tools/preferences.py` (~5 occurrences)
- Modify: `src/butlers/modules/memory/tools/context.py` (~2 occurrences)
- Modify: `src/butlers/modules/memory/tools/writing.py` (~1 occurrence)
- Modify: `src/butlers/modules/memory/__init__.py` (~4 occurrences)
- Modify: `src/butlers/core/model_routing.py` (~12 occurrences)
- Modify: `src/butlers/core/ingestion_events.py` (~18 occurrences)
- Modify: `src/butlers/core/healing/tracking.py` (~21 occurrences)
- Modify: `src/butlers/core/healing/worktree.py` (~3 occurrences)
- Modify: `src/butlers/core/healing/__init__.py` (~1 occurrence)
- Modify: `src/butlers/core/healing/dispatch.py` (~2 occurrences)
- Modify: `src/butlers/core/spawner.py` (~3 occurrences)
- Modify: `src/butlers/modules/self_healing/__init__.py` (~1 occurrence)

**Step 1: Bulk replace**

```bash
cd /home/tze/gt/butlers/mayor/rig
# All shared table names that appear in these files:
sed -i 's/shared\.\(entities\|entity_info\|memory_catalog\|model_catalog\|butler_model_overrides\|token_usage_ledger\|token_limits\|healing_attempts\|ingestion_events\|provider_config\|contacts\|contact_info\)/public.\1/g' \
  src/butlers/modules/memory/storage.py \
  src/butlers/modules/memory/search.py \
  src/butlers/modules/memory/tools/entities.py \
  src/butlers/modules/memory/tools/preferences.py \
  src/butlers/modules/memory/tools/context.py \
  src/butlers/modules/memory/tools/writing.py \
  src/butlers/modules/memory/__init__.py \
  src/butlers/core/model_routing.py \
  src/butlers/core/ingestion_events.py \
  src/butlers/core/healing/tracking.py \
  src/butlers/core/healing/worktree.py \
  src/butlers/core/healing/__init__.py \
  src/butlers/core/healing/dispatch.py \
  src/butlers/core/spawner.py \
  src/butlers/modules/self_healing/__init__.py
```

**Step 2: Verify no `shared.` SQL references remain**

```bash
grep -rn 'shared\.' src/butlers/modules/memory/ src/butlers/core/ src/butlers/modules/self_healing/
```

Review any remaining hits — should be only non-SQL references (comments, docstrings, `load_shared`/`store_shared` method names which are unrelated).

**Step 3: Commit**

```bash
git add src/butlers/modules/memory/ src/butlers/core/ src/butlers/modules/self_healing/
git commit -m "refactor: memory, core, and healing — shared.* → public.* schema prefix"
```

---

### Task 5: Update Runtime SQL — API Routers, Daemon, Connectors

Replace `shared.` with `public.` in API layer, daemon, Google account code, and connectors.

**Files:**
- Modify: `src/butlers/api/routers/memory.py` (~22 occurrences)
- Modify: `src/butlers/api/routers/model_settings.py` (~30 occurrences)
- Modify: `src/butlers/api/routers/healing.py` (~6 occurrences)
- Modify: `src/butlers/api/routers/search.py` (~7 occurrences)
- Modify: `src/butlers/api/routers/approvals.py` (~3 occurrences)
- Modify: `src/butlers/api/routers/oauth.py` (~4 occurrences)
- Modify: `src/butlers/api/routers/provider_settings.py` (~6 occurrences)
- Modify: `src/butlers/api/routers/ingestion_events.py` (~3 occurrences)
- Modify: `src/butlers/api/conversations.py` (~18 occurrences)
- Modify: `src/butlers/api/models/approval.py` (~1 occurrence)
- Modify: `src/butlers/api/models/ingestion_event.py` (~1 occurrence)
- Modify: `src/butlers/daemon.py` (~18 occurrences)
- Modify: `src/butlers/google_account_registry.py` (~28 occurrences)
- Modify: `src/butlers/google_credentials.py` (~21 occurrences)
- Modify: `src/butlers/connectors/owntracks.py` (~5 occurrences)
- Modify: `src/butlers/connectors/gmail.py` (~5 occurrences)
- Modify: `src/butlers/connectors/google_calendar.py` (~4 occurrences)
- Modify: `src/butlers/connectors/telegram_user_client.py` (~1 occurrence)
- Modify: `src/butlers/connectors/discretion.py` (~4 occurrences)
- Modify: `src/butlers/connectors/discretion_dispatcher.py` (~8 occurrences)
- Modify: `src/butlers/scripts/backfill_facts.py` (~7 occurrences)
- Modify: `src/butlers/modules/approvals/gate.py` (~4 occurrences)
- Modify: `src/butlers/modules/calendar.py` (~4 occurrences)
- Modify: `src/butlers/modules/email.py` (~1 occurrence)

**Step 1: Bulk replace across all remaining src/ files**

The safest approach: run the replacement across ALL Python files in `src/butlers/`, capturing every table name that lives in `shared`:

```bash
cd /home/tze/gt/butlers/mayor/rig
find src/butlers -name '*.py' -exec \
  sed -i 's/shared\.\(contacts\|contact_info\|entities\|entity_info\|memory_catalog\|model_catalog\|butler_model_overrides\|token_usage_ledger\|token_limits\|healing_attempts\|ingestion_events\|google_accounts\|provider_config\|dashboard_conversations\|dashboard_messages\)/public.\1/g' {} +
```

**Step 2: Verify zero `shared.<table>` references remain in src/**

```bash
grep -rn 'shared\.\(contacts\|contact_info\|entities\|entity_info\|memory_catalog\|model_catalog\|butler_model_overrides\|token_usage_ledger\|token_limits\|healing_attempts\|ingestion_events\|google_accounts\|provider_config\|dashboard_conversations\|dashboard_messages\)' src/butlers/
```

Expected: no output

**Step 3: Check for any remaining `shared` references that are schema-related but not table-qualified**

```bash
grep -rn '"shared"' src/butlers/ | grep -v test | grep -v __pycache__ | grep -v '.pyc'
```

Review each hit. Expected survivors:
- `db.py` — already updated in Task 2
- `config.py` — may have schema name references (verify they're not hardcoded)
- Module migration files under `src/butlers/modules/*/migrations/` — leave these alone (historical, already applied)
- `api/db.py` or `api/deps.py` — check if they reference `"shared"` as a schema name

Any `"shared"` in connection setup or pool creation must be removed or updated.

**Step 4: Lint check**

```bash
uv run ruff check src/butlers/ --output-format concise
```

Expected: PASS (or only pre-existing issues unrelated to this change)

**Step 5: Commit**

```bash
git add src/butlers/
git commit -m "refactor: API, daemon, connectors — shared.* → public.* schema prefix"
```

---

### Task 6: Update Tests — Bulk Replace

Replace all `shared.` schema references in test files. This is the largest mechanical step.

**Files:** ~40+ test files across `tests/`

**Step 1: Bulk replace across all test files**

```bash
cd /home/tze/gt/butlers/mayor/rig
find tests -name '*.py' -exec \
  sed -i 's/shared\.\(contacts\|contact_info\|entities\|entity_info\|memory_catalog\|model_catalog\|butler_model_overrides\|token_usage_ledger\|token_limits\|healing_attempts\|ingestion_events\|google_accounts\|provider_config\|dashboard_conversations\|dashboard_messages\|acl_probe_shared\)/public.\1/g' {} +
```

**Step 2: Replace `CREATE SCHEMA ... shared` patterns in test fixtures**

Many test fixtures create the shared schema for test isolation. These need updating:

```bash
# Replace schema creation statements
sed -i "s/CREATE SCHEMA IF NOT EXISTS shared/-- shared schema no longer needed (tables live in public)/g" \
  $(grep -rl 'CREATE SCHEMA IF NOT EXISTS shared' tests/)

sed -i "s/CREATE SCHEMA shared/-- shared schema no longer needed (tables live in public)/g" \
  $(grep -rl 'CREATE SCHEMA shared' tests/)
```

**Step 3: Replace `"shared"` string literals used as schema names in test code**

These are trickier — they appear in:
- `schema="shared"` arguments to pool/connection setup
- `SET search_path TO ... shared` SQL statements
- Assertions checking schema names

```bash
grep -rn '"shared"' tests/ | grep -v __pycache__ | head -40
```

Review each hit and update manually:
- `schema="shared"` → remove or change to `schema=None` (public is default)
- `SET search_path TO ..., shared, public` → `SET search_path TO ..., public`
- Assertions on schema name → update expected value

**Step 4: Handle schema isolation tests specifically**

File `tests/config/test_schema_acl_isolation.py` tests that butler roles can access shared tables. Update it to verify access to `public.*` tables instead. The ACL model is the same — just the schema name changes.

File `tests/integration/test_schema_isolation.py` tests cross-butler isolation. Update `shared` references to `public`.

**Step 5: Run the full test suite**

```bash
mkdir -p .tmp/test-logs
PYTEST_LOG=".tmp/test-logs/pytest-shared-to-public-$(date +%Y%m%d-%H%M%S).log"
uv run pytest tests/ --ignore=tests/test_db.py --ignore=tests/test_migrations.py -q --maxfail=5 --tb=short >"$PYTEST_LOG" 2>&1 || tail -n 120 "$PYTEST_LOG"
```

Expected: Some failures may remain from missed references. Fix iteratively.

**Step 6: Commit**

```bash
git add tests/
git commit -m "test: update all test files — shared.* → public.* schema prefix"
```

---

### Task 7: Update Module Migration Files (Runtime, Not Historical Core)

Module-level migrations under `src/butlers/modules/*/migrations/` that create or alter `shared.*` tables need updating **only if they can still be run on fresh installs**. The contacts module migration `002_contact_info_shared.py` creates `shared.contact_info` — on a fresh install this would create the table in the wrong schema.

**Files:**
- Modify: `src/butlers/modules/contacts/migrations/002_contact_info_shared.py`
- Review: `src/butlers/modules/memory/migrations/004_object_entity_id.py`
- Review: `src/butlers/modules/memory/migrations/006_drop_shadow_entities.py`
- Review: `src/butlers/modules/memory/migrations/018_partial_unique_entities.py`
- Review: `src/butlers/modules/memory/migrations/021_fix_partial_unique_deleted_at.py`

**Step 1: Update `contacts/migrations/002_contact_info_shared.py`**

This migration creates the `shared` schema and `shared.contact_info` table. Change it to create in `public` instead:

- Replace `CREATE SCHEMA IF NOT EXISTS shared` → remove (public always exists)
- Replace `shared.contact_info` → `public.contact_info`
- Replace `GRANT ... ON shared.contact_info` → `GRANT ... ON public.contact_info`

**Step 2: Review memory module migrations**

These reference `shared.entities` in FK definitions. Since `ALTER TABLE ... SET SCHEMA` in core_041 moves the table, and these migrations run _before_ core_041 in sequence, they should reference `shared.entities` at migration time but `public.entities` at runtime.

**Decision:** Leave memory module migrations unchanged if they are idempotent and guarded. They ran against `shared.entities` when applied. On a fresh install, the core_014 migration creates `shared.entities`, these migrations reference it, then core_041 moves it to `public`. The sequence is correct.

**However:** `contacts/migrations/002_contact_info_shared.py` runs as a module migration which may execute _after_ core_041 on a fresh install (depends on Alembic branch ordering). If it tries to create `shared.contact_info` after `shared` has been dropped by core_041, it will fail.

**Safe fix:** Make the contacts module migration schema-aware — use `public` and guard with `IF NOT EXISTS`. If the table was already moved by core_041, the `IF NOT EXISTS` guard prevents failure.

**Step 3: Verify migration ordering**

```bash
cd /home/tze/gt/butlers/mayor/rig
uv run alembic branches
uv run alembic heads
```

Check that core chain and module chains are independent. If module migrations can run after core_041, they MUST reference `public`, not `shared`.

**Step 4: Commit**

```bash
git add src/butlers/modules/contacts/migrations/ src/butlers/modules/memory/migrations/
git commit -m "refactor: update module migrations for public schema"
```

---

### Task 8: Update Documentation and Specs

Update references to `shared` schema in documentation, specs, and CLAUDE.md.

**Files:**
- Modify: `CLAUDE.md` (this rig's CLAUDE.md references `shared` schema)
- Modify: `openspec/specs/entity-identity/spec.md` (references `shared.entities`)
- Review: `about/` directory for schema references
- Review: `src/butlers/connectors/README.md`

**Step 1: Find all doc references**

```bash
grep -rn 'shared\.' --include='*.md' /home/tze/gt/butlers/mayor/rig/ | grep -v node_modules | grep -v .git
```

**Step 2: Update each doc file**

Replace `shared.` with `public.` in schema references. Update architectural descriptions to reflect that cross-butler tables now live in `public`.

**Step 3: Update the parent CLAUDE.md** (`/home/tze/gt/butlers/mayor/rig/CLAUDE.md`)

The "Database Isolation" section describes:
> The `shared` schema contains cross-butler identity tables

Update to reflect the new `public` schema location.

**Step 4: Commit**

```bash
git add -A '*.md'
git commit -m "docs: update schema references — shared → public"
```

---

### Task 9: Full Validation

Run the complete quality gate to verify nothing is broken.

**Step 1: Lint**

```bash
cd /home/tze/gt/butlers/mayor/rig
uv run ruff check src/ tests/ roster/ conftest.py --output-format concise
uv run ruff format --check src/ tests/ roster/ conftest.py -q
```

Expected: PASS

**Step 2: Full test suite**

```bash
mkdir -p .tmp/test-logs
PYTEST_LOG=".tmp/test-logs/pytest-final-$(date +%Y%m%d-%H%M%S).log"
uv run pytest tests/ --ignore=tests/test_db.py --ignore=tests/test_migrations.py -q --maxfail=3 --tb=short >"$PYTEST_LOG" 2>&1 || tail -n 120 "$PYTEST_LOG"
```

Expected: ALL PASS

**Step 3: Verify zero `shared.` SQL references remain in src/**

```bash
grep -rn 'shared\.\(contacts\|contact_info\|entities\|entity_info\|memory_catalog\|model_catalog\|butler_model_overrides\|token_usage_ledger\|token_limits\|healing_attempts\|ingestion_events\|google_accounts\|provider_config\|dashboard_conversations\|dashboard_messages\)' src/butlers/ tests/
```

Expected: no output (excluding historical core migrations in `alembic/versions/core/`)

**Step 4: Grep for any `"shared"` that might be a schema reference**

```bash
grep -rn '"shared"' src/butlers/ | grep -v __pycache__ | grep -v migrations/
```

Review any hits. The only acceptable survivors are:
- String `"shared"` in non-SQL contexts (e.g., `store_shared`, `load_shared` method names)
- Comments or docstrings

**Step 5: Final commit (if any fixes needed)**

```bash
git add -A
git commit -m "fix: address remaining shared schema references from validation"
```
