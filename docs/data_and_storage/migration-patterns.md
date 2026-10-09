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

## Metadata visibility and evidence-preserving rollback

The core smoke comparison reads `information_schema.columns` through the ordinary
migration login. `Tables only in fresh DB` is a privilege-filtered inventory
observation. A missing entry can mean that the object exists under another creator
without effective table privileges; it does not establish that downgrade failed to
recreate the table. Diagnose this in a disposable database with a positive qualified
administrative catalog witness. Record the session and effective role, table owner,
effective and inherited privileges, database/schema ownership, per-creator default ACLs, enabled/forced RLS,
and the function owner, EXECUTE grants and pinned search path separately.

The initial core_255 correction in PR #4347 changed two independent behaviors at
once: it added a targeted Switchboard table grant and retained `insight_amendments`
on downgrade. The historical initial source dropped an empty amendment table;
managed bootstrap then recreated it under its own owner. The ordinary migration
login's default privileges applied to its own creations, not the bootstrap creator.
Both a grant-only control and a keep-only control must therefore accompany the
initial-source reproduction. Retention can mask the inventory failure by preserving
ownership and ACLs. That does not make retention the cause of correct grants.

The documented decision is to keep the current core_255 migration unchanged. Its
narrow grant already exposes bootstrap-created amendment metadata through the
ordinary login's configured inherited Switchboard membership. Actual runtime
`SET ROLE` identities still face the Switchboard row policy, including after two
production `init-db.sql` replays re-widen public DML. Metadata equality, row visibility,
DDL ownership and SECURITY DEFINER execution are four separate checks. Runtime table
grants do not confer ownership needed to ALTER a bootstrap-created table. DROP has
an additional schema-owner authority: in the fresh PostgreSQL 17 fixture, the
ordinary login owns its database and implicitly owns `public` through
`pg_database_owner`. It can drop bootstrap-owned contained objects without being
able to ALTER the table or REPLACE the functions. These are separate operations,
not a reason to destroy correction rows. See PostgreSQL's
[database-owner role](https://www.postgresql.org/docs/17/predefined-roles.html),
[DROP TABLE contract](https://www.postgresql.org/docs/17/sql-droptable.html) and
[generic DROP ownership check](https://github.com/postgres/postgres/blob/REL_17_STABLE/src/backend/commands/dropcmds.c).
The core_258 grant convergence and a dynamic-head round-trip remain separate smoke
checks; a passing current head comparison cannot identify which historical change
fixed a core_255 comparison.

Ordinary migration traversal must be tested while the table is still bootstrap
owned, before any ordinary DROP/recreate changes the owner. The current empty
DROP diagnostics with and without the targeted grant both exercise the canonical
`run_migrations(..., chain="core", schema="health")` entrypoint. The exact current
bootstrap-created install also exercises that entrypoint after its complete role
matrix and two `init-db.sql` replays, with all 43 planted rows still present. Only
the independent health version table is positioned at core_254; the already-run
shared predecessors remain intact and core_255 is actually traversed. The tests
record the reached upgrade frame, rejected SQL statement and `42501`, then read
the catalog, rows and version tables through separate connections to prove that
ownership and evidence survived and health did not advance past core_254.
Metadata PASS therefore does not mean ordinary replay succeeds in this current
bootstrap-owned state. The hypothetical empty-DROP controls are diagnostics; the
exact current bootstrap-created replay is an existing failure, not a repaired
supported path. A later ordinary replay over an ordinarily owned retained table
is a separate positive control and does not discharge this failure.

The concrete forward proposal is scoped bootstrap convergence before ordinary
core replay: within the existing privileged bootstrap boundary, resolve the
configured migration identity from `butlers.connecting_user`, guard the qualified
amendment table's existence and converge only its ownership to that trusted
migration identity. Preserve every row, runtime ACL entry, policy and ENABLE/FORCE setting;
retain the existing runtime role matrix and test rollback/durable version state.
This follows the bootstrap's existing contract that Alembic objects belong to the
normal migration user for future ALTER. It requires a separately reviewed
bootstrap change and disposable-PG validation of first install, retained-table
replay, repeated convergence and the same positive/negative role controls.
It is a proposal, not an adopted owner change or a verified repair. No applied
core_255 rewrite, new core allocation, broad grant, DROP or runtime owner privilege
is authorized here. Function-owner convergence remains with `bu-q7vx1q.33`.

A bounded core_255-to-core_254 rollback intentionally retains complete amendment
rows in all four states, while their referenced candidates remain. It removes
candidate `premise` and `delivery_ref`, folds candidate `withdrawn` to `filtered`, and
folds ledger `withdrawn` to `suppressed` and `amended` to `delivered`. Reupgrade cannot
recover the removed candidate fields. The surviving table does not mean that older
code can deliver corrections. Its existing candidate FK still cascades on deletion;
this decision is not a promise of indefinite evidence retention.

Retaining a table also does not converge function ownership. A managed-bootstrap
reupgrade can recreate the two definers under the bootstrap owner. An ordinary
schema replay cannot REPLACE those functions and its best-effort DDL can leave the
owner unchanged. In the fresh database-owner fixture, ordinary rollback can DROP
them through schema ownership; the function is then unavailable (`42883`) until
ordinary reupgrade recreates it under the ordinary owner. Record the actual
database/schema authority and function absence or ownership at each step. Do not
extrapolate this DROP authority to a migration login that lacks schema ownership.
Broader caller/source validation, FK lifecycle, FORCE RLS and
definer ownership hardening remain with `bu-q7vx1q.33`; this investigation adds no
authority or waiver for those choices.

The real-PG controls live in
`tests/migrations/test_core_255_insight_premise_binding_migration.py`. They use the
complete canonical predecessor chain and bootstrap, a checksum-verified historical
test fixture, isolated empty destructive variants, planted positive row witnesses,
and actual role execution. Source inspection, mock results and collection are not
SQL proof. See the new `Premise Amendment Migration Evidence` requirement in
`openspec/specs/proactive-insight-engine/spec.md` for the bounded contract.

## Implementation Notes

- Supported online entrypoints first verify the reviewed bootstrap profile before
  extension, target-schema or version/revision mutations. Ordinary dev is included;
  direct runtime fallback does not waive migration admission. See
  [reviewed bootstrap](reviewed-bootstrap.md) for separate privileged/normal
  identities, genuine core195 repair and finalized repeat/downgrade boundaries.

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
  replacement in a historical revision must carry the cumulative vocabulary. For
  `attention_ledger`, core168/core241/core255 installers accept all eight adopted outcomes on
  upgrade and downgrade: delivered, coalesced, deferred, suppressed, failed, expired, withdrawn,
  amended. A later forward revision cannot repair an earlier `ADD CHECK` that already fails on
  shared rows. Correct that historical installer; do not stamp past it, drop the CHECK, delete rows,
  or map newer outcomes to make an upgrade pass. Downgrades retain their documented own-value
  folds (168 failed -> deferred; 241 expired -> suppressed; 255 withdrawn -> suppressed and
  amended -> delivered), but their CHECK remains cumulative for other schemas. Prove replay with
  a real-Postgres test that migrates a second schema from base after seeding every current outcome,
  reads ids/count/provenance through a separate connection, and compares the final schema and
  vocabulary with a fresh dynamic-head database. Test downgrade/reupgrade separately at each
  bounded revision, preserving every row and all provenance outside the documented outcome fold.
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


### OwnTracks retention history

`core_264` follows protected `core_265` (which descends `core_261`) and adds connector-owned immutable source birth,
lineage, tombstones and committed receipts plus per-owning-schema source-copy
history. `chronicler_027` follows `chronicler_026` and adds owning policy,
projection coverage, decisions and floors. These revisions create no principal
or LOGIN. Receipt SELECT is limited to Chronicler; raw DELETE stays with the
existing connector writer. Source-copy ledgers live in each owning schema and
do not grant peer SQL writes. Trigger bodies use `pg_catalog,pg_temp` and
qualified installed functions. A populated history/floor downgrade refuses;
never erase floors to repair a revision. Actual migration/role controls must
execute before reporting installed authority or recovery proof.

Empty core264 rollback retains inert own ledgers. Its installer accepts only the
exact own table columns/constraints and the established resolved core
writer owner before
converging functions/triggers; populated history still requires roll-forward.
Chronicler privacy preparation preserves every contributor's original output
generation and records monotone previous-to-reduced transitions in the same
transaction, including contributors outside the current bounded plan.

Retained core264 local ledgers use the actual core foundation `state` relation's
established migration-writer owner as the catalog identity anchor. A local state
relation takes precedence; its wrong kind or identity refuses. Only when local
state is absent may the fixed shared `public.state` anchor be used: the adopted
shared-predecessor replay can independently position a schema version after
public core predecessors without having run its own local foundation. The target namespace must
exist, and the selected state must be a regular table with a stored owner. No
invocation identity, namespace owner, inferred runtime role or peer table can
substitute. A managed replay's invoking login need not be the retained owner.
Only newly created ledgers receive that existing owner; retained wrong-owner,
kind, columns and constraints still fail closed. Replay does not transfer
existing objects, grant membership, erase floors or bypass populated refusal.


Retention core264 is an additive successor of the actually protected core265
conversation-identity split (which descends261). Revision numbers are identifiers,
not a topological-order requirement: this reviewed chain has one core head264.
Its empty replay controls stop at265 and preserve the governed foreign split;
no private custody/calendar revision or held capture259 is incorporated. Online
migration still requires the current reviewed-bootstrap admission and its
independent point-of-use guards.
