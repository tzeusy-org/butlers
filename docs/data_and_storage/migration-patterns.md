# Migration Patterns

> **Purpose:** Explain how Alembic migrations are organized, discovered, and executed across core, module, and butler-specific chains.
> **Audience:** Developers adding new tables or modifying schema.
> **Prerequisites:** [Schema Topology](schema-topology.md), basic Alembic knowledge.

## Overview

Butlers uses Alembic for schema migrations with a **multi-chain branching model**. Instead of a single linear migration history, each migration domain (core infrastructure, individual modules, individual butlers) maintains its own independent revision chain. The daemon runs all applicable chains at startup via a programmatic API -- no CLI invocation required.

## Chain Types

### Core Chain

Location: `alembic/versions/core/`

The core chain manages shared infrastructure tables used by all butlers:

- `state` -- KV state store
- `scheduled_tasks` -- Cron scheduler
- `sessions` -- Session log
- `butler_secrets` -- Credential store
- `public.entities`, `public.entity_info` -- Entity graph
- `public.google_accounts` -- Google OAuth registry
- `ingestion_events` -- Switchboard ingestion log
- `model_catalog` -- LLM model definitions

Core migrations use the branch label `"core"` and revision IDs like `core_001`, `core_002`, etc.

### Module Chains

Location: `src/butlers/modules/<module_name>/migrations/`

Each module that needs its own tables maintains a migration chain within its source directory. The migration runner discovers these automatically by scanning `src/butlers/modules/*/migrations/` for directories containing `.py` files.

Example: the memory module at `src/butlers/modules/memory/migrations/` has 25+ revisions (branch label `"memory"`) creating tables like `episodes`, `facts`, `rules`, `entities`, `predicate_registry`, etc.

A module migration file follows this structure:

```python
"""memory_baseline"""
revision = "mem_001"
down_revision = None
branch_labels = ("memory",)
depends_on = None

def upgrade() -> None:
    op.execute("CREATE TABLE IF NOT EXISTS episodes (...)")

def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS episodes CASCADE")
```

Key conventions:
- `branch_labels` is set on the first revision in the chain (the root).
- `down_revision` chains within the module (e.g., `mem_002` has `down_revision = "mem_001"`).
- Migrations use raw SQL via `op.execute()` rather than Alembic's ORM-based operations.

### Butler-Specific Chains

Location: `roster/<butler_name>/migrations/`

Individual butlers can have their own migration chains for butler-specific tables. These are discovered by scanning `roster/*/migrations/` directories. Module chains take precedence if both exist for the same name.

## Discovery and Resolution

The `migrations.py` module (`src/butlers/migrations.py`) handles chain discovery:

1. **`_discover_module_chains()`** -- Scans `src/butlers/modules/*/migrations/` for Python files.
2. **`_discover_butler_chains()`** -- Scans `roster/*/migrations/` for Python files.
3. **`get_all_chains()`** -- Returns all chains in order: shared chains (`["core"]`) first, then module chains, then butler-specific chains. Duplicates are excluded.

The `_resolve_chain_dir()` function maps a chain name to its filesystem path:
- `"core"` resolves to `alembic/versions/core/`
- Module names resolve to `src/butlers/modules/<name>/migrations/`
- Butler names resolve to `roster/<name>/migrations/`

## Schema-Scoped Migration Execution

When running migrations, the target schema can be specified. This is critical for the multi-schema topology:

```python
await run_migrations(db_url, chain="all", schema="general")
```

When a schema is specified:
- Alembic's `version_table_schema` option is set so `alembic_version` tracking lives within the target schema.
- A custom `butlers.target_schema` option is passed through for migrations that need to create schema-qualified objects.

The `run_migrations()` function iterates through resolved chains and calls `command.upgrade(config, f"{chain}@head")` for each.

## Version Location Configuration

Alembic's `version_locations` setting is always configured with ALL known chain directories, regardless of which chain is being upgraded. This ensures Alembic can resolve every revision in `alembic_version` even when upgrading a single branch. Without this, cross-chain references would fail resolution.

## Migration Ordering at Startup

The butler daemon startup sequence runs migrations in this order:

1. Core migrations (`chain="core"`)
2. Module migrations (for each enabled module that returns a non-None `migration_revisions()`)
3. Butler-specific migrations (if `has_butler_chain(butler_name)` returns True)

Each chain is upgraded to its head independently. Alembic tracks which revisions have been applied per-chain in the `alembic_version` table within the target schema.

## Writing New Migrations

To add a migration to an existing module chain:

1. Create a new Python file in the module's `migrations/` directory.
2. Set `revision` to a unique ID (convention: `<prefix>_<number>`).
3. Set `down_revision` to the previous revision in the chain.
4. Do NOT set `branch_labels` (only the root revision has this).
5. Implement `upgrade()` and `downgrade()` using `op.execute()` with raw SQL.

To create a new module migration chain:

1. Create `src/butlers/modules/<name>/migrations/` directory.
2. Create `__init__.py` (empty).
3. Create the first migration with `branch_labels = ("<name>",)` and `down_revision = None`.
4. Return the branch label from the module's `migration_revisions()` method.

### Database-global protected public boundaries

Core migrations may be replayed once for each target schema even when they
create a database-global `public` object. Such a migration must be guarded and
idempotent: the first target installs the object and every later target proves
the finalized catalog shape before it no-ops. Do not add an accidental
dependency on a specialist schema merely because a Switchboard consumer will
arrive later.

When the object needs a constrained `SECURITY DEFINER` interface, keep the
ordinary migration login out of its ownership. Stage a fixed, privileged
bootstrap installer in `scripts/init-db.sql`, transfer ownership to a
membership-free NOLOGIN definer role, pin the function search path, revoke
`PUBLIC` before specific grants, and verify the final catalog in the migration.
The bootstrap must also run a post-baseline ACL finalizer on each init-db rerun:
the repository's legacy broad public-table/default grants otherwise reappear.

### SECURITY DEFINER search path

Every `SECURITY DEFINER` function sets `search_path = pg_catalog, pg_temp`,
exactly. Any other value is a hijack surface:

- A schema on the path that a less-privileged role can `CREATE` in lets that
  role plant a better-matching overload. `public` is `CREATE`-able by the
  migration login, and a butler schema is `CREATE`-able by its runtime role. An
  exact `format(text, text)` beats the catalog's variadic `format`, and it then
  runs as the definer's owner.
- Leaving `pg_temp` implicit, as in `pg_catalog` alone, is no better. An
  implicit `pg_temp` is searched first for relation and type names.

The body must therefore schema-qualify every relation and every non-catalog
function. A body that needs its own schema resolves it once, at migration time,
and bakes it in as a literal. `current_schema()` inside a pinned definer
returns `pg_catalog`. See `roster/switchboard/migrations/039_pin_definer_search_paths.py`.
Other `proconfig` entries, such as `row_security=on`, are fine.

`butlers.core.definer_search_path.is_pinned()` is the one predicate for this
rule. `tests/migrations/test_definer_search_path_pins.py` applies it to every
definer in `pg_proc` after the bootstrap and every definer-bearing chain, with
an empty allowlist. A chain that gains its first definer must be added to that
guard's bootstrap, and the guard fails until it is.

Durable evidence is not a disposable fixture. A downgrade may remove a
protected boundary only when it is empty and has no active consumer; a nonempty
outbox must refuse rollback with forward-remediation guidance. Avoid foreign
keys/cascades to mutable source records when the evidence must survive source
catalog or attempt deletion.

A string literal inside a stored function body is not reachable the way a schema
object is. `ALTER ... RENAME` is expressible against an existing object, so a
rename converges through a finalizer that re-runs; a literal baked into the text
a one-shot upgrader emits does not, and editing that upgrader reaches fresh
bootstraps only. Define such a body once, in a bootstrap-owned installer both the
one-shot upgrader and the re-runnable finalizer call — see
`runtime_attention_admin.install_legacy_debounce_marker()` — so the two paths
cannot drift. `CREATE OR REPLACE FUNCTION` preserves the OID, owner, and ACL, so
triggers stay bound across the rewrite. Adopt the body *after* the finalizer's
rename step has proven the object exists; adopting it first would let a missing
rename create a second function under the new name while the trigger still points
at the old OID, which looks like convergence and is not. And rewrite the body, not
the rows: a backfill corrects history and then drifts again on the next insert, so
assert on a row written *after* the change, never on historical rows alone.

A later revision can make a teardown one-way. When that happens, record it in the operator
documentation for the subsystem: a rollback that a database can no longer perform is unavailable,
not merely untested. The runtime-attention boundary is the worked example; see the
[Runtime Attention runbook](../operations/runtime-attention.md#stop-paging).

## Verification

To confirm the migration chain structure described here matches the running system:

```bash
# 1. Core migration chain is at head in each butler's schema
psql -h localhost -U butlers -d butlers -c \
  "SELECT version_num FROM general.alembic_version;"
# Expected: the latest core_NNN revision (check alembic/versions/core/ for the highest number)

# 2. Module migration chains are tracked separately
psql -h localhost -U butlers -d butlers -c \
  "SELECT version_num FROM general.alembic_version;"
# Expected: multiple rows if module chains are active (one per chain at head)

# 3. Migration runner applies all chains at startup without error
butlers db migrate --only general 2>&1
# Expected: output for each chain (core, memory, etc.) showing "Already at head" or migration steps

# 4. Discovered module chains match source directories
python3 -c "
from butlers.migrations import get_all_chains
chains = get_all_chains()
print(chains)
"
# Expected: list starting with "core" followed by module names and butler names

# 5. Module migration file follows the branch-label convention
grep -r "branch_labels" src/butlers/modules/memory/migrations/ | head -5
# Expected: only the FIRST (root) migration file contains branch_labels = ("memory",);
# subsequent files have no branch_labels

# 6. New migration does not break existing butler on upgrade
uv run pytest tests/test_migrations.py -q --tb=short 2>&1 | tail -20
# Expected: all migration integrity tests pass
```

## Implementation Notes

- Alembic loads every `*.py` in a versions directory, so a stray file with a duplicate `revision`
  breaks the chain even when chain tests only check expected filenames.
- Revision identifiers are global across chains and branches. Parallel branches collide: two PRs
  once both minted `core_164` and merged green. Before publishing a migration, take the next number
  from every live branch (`git ls-remote --heads origin`, then `git ls-tree -r --name-only
  origin/<branch> -- alembic/versions/core/`), not just `origin/main`, and re-run
  `tests/config/test_migration_chain_head.py` against the merge result. The
  `migration-chain-main.yml` workflow re-runs that guard on every `main` push.
- Head-pinned assertions derive the head: use `butlers.testing.migration.assert_at_chain_head()` or
  `butlers.migrations.get_chain_head(chain)`. An AST guard in `test_migration_chain_head.py` fails a
  literal revision compared with an `alembic_version` read; a deliberate pin carries
  `# pinned-revision: <why>` on the marker line.
- Rollback is not uniform: `core_196` and `core_198` install trusted-bootstrap boundaries whose
  downgrade may refuse. A test that rolls back an old migration bounds its upgrade to the revision it
  owns (`core@<rev>`, or `create_migrated_test_db(..., revisions={"core": "core_NNN"})`), never
  `core@head` then down. `tests/config/test_bounded_revision_downgrade_guard.py` derives the
  boundary set from the migration sources.
- `create_migrated_test_db()` returns the ordinary migration login, which has no privileges on
  `public.runtime_attention_outbox` (FORCE RLS). Read such tables through
  `migration_bootstrap_db_url()` from a module-scoped db-name fixture.
- Core revisions replay against shared `public.*` data whenever a new schema is added, so a CHECK
  replacement in a historical revision must carry the cumulative vocabulary and a downgrade must
  not narrow persisted values. Prove it with a real-Postgres test that migrates a second schema from
  base after seeding current values.
- Table rewrites (rename old, create new) keep the old index names on the backup table; new index
  names must not collide.
- `run_migrations` builds its Alembic config through
  `src/butlers/migrations.py::_build_alembic_config`, which escapes `%` as `%%` in
  `sqlalchemy.url`; percent-encoded libpq options
  otherwise raise `configparser` interpolation errors.
- Compare `timestamptz` with a `DATE` UTC-explicitly: `ts >= (v_month::timestamp AT TIME ZONE
  'UTC')`. A bare `ts >= v_month` promotes through the session `TimeZone`.
- The `test_core_chain_serializes_global_runtime_attention_*` tests contend on a global
  cross-process lock and fail spuriously beside another run on the same Postgres.
- CLI migration entrypoints (`butlers db migrate`, compose's `migrations` service) apply the same
  module schema override as daemon startup: memory chains honour `[modules.memory].memory_schema`
  (e.g. chronicler's `chronicler_mem`), not the owning butler schema.
- A migration that rewrites enum-like `TEXT` values under a `CHECK` must drop or replace the
  constraint before writing; fresh-schema tests pass either way, live upgrades do not.
- Derive the expected core head from `alembic/versions/core/` in tests, never a pinned constant.
- Two open PRs taking the same `core_NNN`: merge the first, then rebase the second, `git mv` it to
  the next number, repoint `down_revision`, update chain-head literals in tests, and push with
  `--force-with-lease`. Renumber before review so the reviewed head is final.

## Related Pages

- [Schema Topology](schema-topology.md) -- Database layout and search path
- [State Store](state-store.md) -- The `state` table created by core migrations
