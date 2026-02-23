---
name: butler-db-schema
description: Guide for designing and managing a butler's PostgreSQL database schema. Use when creating tables, writing migrations, adding indexes, or evolving a butler's data model.
---

# Butler Database Schema Design

Use this skill when creating or modifying a butler's database schema — adding tables, writing Alembic migrations, designing indexes, or evolving the data model for a specific butler's needs.

## Hard Constraints

- **Shared database, per-butler schemas.** All butlers share a single PostgreSQL database named `butlers`. Each butler gets its own schema (`general`, `health`, `messenger`, etc.) plus read access to the `shared` schema. Inter-butler data exchange happens only via MCP tools through the Switchboard.
- **Five core tables in every butler schema.** See the Core Tables section below. All five are created by `core_001_target_state_baseline.py` and replicated into each butler's schema via `search_path`.
- **Migrations via Alembic only.** No raw DDL in application code. No "just run this SQL."
- **Raw SQL via `op.execute()`.** Migrations use raw SQL strings, not SQLAlchemy ORM operations. There are no SQLAlchemy models (`target_metadata=None`).
- **Backward compatibility in all migrations.** Every migration must be safe to run while the previous version of the code is still active.

---

## Database Topology

```
PostgreSQL database: "butlers"
├── shared          # Core tables + calendar projection tables (read-only for butlers)
├── general         # General butler's domain tables
├── health          # Health butler's domain tables
├── messenger       # Messenger butler's domain tables
├── relationship    # Relationship butler's domain tables
├── switchboard     # Switchboard butler's domain tables
└── public          # Extensions (pgcrypto, vector, uuid-ossp)
```

Each butler's runtime connection sets:
```sql
SET search_path TO <own_schema>, shared, public
```

This means a butler's tools can query `state`, `sessions`, etc. without schema-qualifying — those tables exist in the butler's own schema. Tables in `shared` (like `calendar_sources`) are also visible without qualification.

### Runtime Roles & ACL

Each butler gets a runtime role (`butler_<name>_rw`) with:
- **Own schema:** SELECT, INSERT, UPDATE, DELETE, TRIGGER, REFERENCES on tables; USAGE, SELECT, UPDATE on sequences
- **Shared schema:** SELECT only on tables; USAGE, SELECT on sequences
- **Other butler schemas:** All access REVOKED

These roles and privileges are managed by `core_001_target_state_baseline.py`. New butlers must be added to the `_BUTLER_SCHEMAS` tuple in that migration (or a subsequent one).

---

## Core Tables (Every Butler Schema Gets These)

Every butler schema contains five core tables created by the `core_001` migration. They are created once in the migration but land in whichever schema `search_path` is pointing to at migration time.

| Table | Purpose | Primary access pattern |
|---|---|---|
| `state` | Key-value JSONB store | Point lookups by key, prefix scans |
| `sessions` | Runtime invocation history & trace metadata | Recent-first, lookup by request_id |
| `scheduled_tasks` | Cron-driven recurring prompts + job dispatch | Query enabled + due tasks |
| `route_inbox` | Accept-then-process inbox for route requests | Filter by lifecycle_state |
| `butler_secrets` | Encrypted secrets store (tokens, API keys) | Lookup by secret_key, filter by category |

### 1. `state` — Key-Value Store

General-purpose persistent storage for structured data. Used by core components and modules to store configuration state, counters, flags, cached results, module-specific KV data.

```sql
CREATE TABLE state (
    key TEXT PRIMARY KEY,
    value JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    version INTEGER NOT NULL DEFAULT 1
);

-- Prefix scans for namespaced keys (e.g., "module:email:%")
CREATE INDEX idx_state_key_prefix ON state (key text_pattern_ops);
```

Keys should be namespaced with colons: `module:email:last_check`, `scheduler:last_tick`, `config:override:timezone`. The `version` column tracks mutation count for optimistic concurrency.

### 2. `sessions` — Runtime Invocation History

Every LLM CLI invocation spawned by this butler is recorded here. Includes trace metadata, token usage, and cost tracking.

```sql
CREATE TABLE sessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    prompt TEXT NOT NULL,
    trigger_source TEXT NOT NULL,          -- 'schedule:<task-name>', 'tick', 'external', 'trigger'
    model TEXT,
    success BOOLEAN,
    error TEXT,
    result TEXT,
    tool_calls JSONB NOT NULL DEFAULT '[]'::jsonb,
    duration_ms INTEGER,
    trace_id TEXT,
    request_id TEXT,
    cost JSONB,
    input_tokens INTEGER,
    output_tokens INTEGER,
    parent_session_id UUID,
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at TIMESTAMPTZ
);

CREATE INDEX idx_sessions_request_id ON sessions (request_id);
```

### 3. `scheduled_tasks` — Cron-Driven Scheduler

Stores both TOML-defined (bootstrap) and runtime-created scheduled tasks. Supports two dispatch modes: `prompt` (spawns an LLM session) and `job` (calls a Python function directly).

```sql
CREATE TABLE scheduled_tasks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL UNIQUE,
    cron TEXT NOT NULL,
    prompt TEXT,                                -- Required for prompt mode, NULL for job mode
    dispatch_mode TEXT NOT NULL DEFAULT 'prompt',
    job_name TEXT,                              -- Required for job mode
    job_args JSONB,
    timezone TEXT NOT NULL DEFAULT 'UTC',
    start_at TIMESTAMPTZ,                      -- Window start (optional)
    end_at TIMESTAMPTZ,                        -- Window end (optional)
    until_at TIMESTAMPTZ,                      -- Expiry date (optional)
    display_title TEXT,
    calendar_event_id UUID,                    -- FK to calendar_events for linked events
    source TEXT NOT NULL DEFAULT 'db',         -- 'toml' or 'db'
    enabled BOOLEAN NOT NULL DEFAULT true,
    next_run_at TIMESTAMPTZ,
    last_run_at TIMESTAMPTZ,
    last_result JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT scheduled_tasks_dispatch_mode_check
        CHECK (dispatch_mode IN ('prompt', 'job')),
    CONSTRAINT scheduled_tasks_dispatch_payload_check
        CHECK (
            (dispatch_mode = 'prompt' AND prompt IS NOT NULL AND job_name IS NULL)
            OR (dispatch_mode = 'job' AND job_name IS NOT NULL)
        ),
    CONSTRAINT scheduled_tasks_window_bounds_check
        CHECK (start_at IS NULL OR end_at IS NULL OR end_at > start_at),
    CONSTRAINT scheduled_tasks_until_bounds_check
        CHECK (until_at IS NULL OR start_at IS NULL OR until_at >= start_at)
);

CREATE UNIQUE INDEX ix_scheduled_tasks_calendar_event_id
    ON scheduled_tasks (calendar_event_id)
    WHERE calendar_event_id IS NOT NULL;
```

### 4. `route_inbox` — Accept-Then-Process Inbox

Incoming route requests are accepted immediately (returning an ID) then processed asynchronously.

```sql
CREATE TABLE route_inbox (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    received_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    route_envelope JSONB NOT NULL,
    lifecycle_state TEXT NOT NULL DEFAULT 'accepted',
    processed_at TIMESTAMPTZ,
    session_id UUID,
    error TEXT
);

CREATE INDEX idx_route_inbox_lifecycle_state
    ON route_inbox (lifecycle_state, received_at);
```

### 5. `butler_secrets` — Secrets Store

Generic secrets store for tokens, API keys, and sensitive configuration. Secrets are stored per-butler in the butler's own schema.

```sql
CREATE TABLE butler_secrets (
    secret_key TEXT PRIMARY KEY,
    secret_value TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT 'general',
    description TEXT,
    is_sensitive BOOLEAN NOT NULL DEFAULT true,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at TIMESTAMPTZ
);

CREATE INDEX ix_butler_secrets_category ON butler_secrets (category);
```

---

## Shared Schema Tables

Tables in the `shared` schema are readable by all butlers but writable only by core migrations. These are created by `core_005` and later core migrations.

### Calendar Projection Tables

The calendar module projects Google Calendar data into these shared tables:

| Table | Purpose |
|---|---|
| `calendar_sources` | Calendar provider sources with lane (user/butler) |
| `calendar_events` | Base events with recurrence rules |
| `calendar_event_instances` | Expanded recurring event occurrences |
| `calendar_sync_cursors` | Incremental sync state per source |
| `calendar_action_log` | Idempotent mutation audit trail |

Key design patterns in calendar tables:
- **GiST indexes on time ranges:** `USING GIST (tstzrange(starts_at, ends_at, '[)'))` for efficient overlap queries
- **Idempotency keys:** `idempotency_key TEXT NOT NULL UNIQUE` on action log
- **Lane-based partitioning:** `lane IN ('user', 'butler')` separates read-only user calendars from writable butler calendars

---

## Module Tables

Modules create tables in the butler's own schema via module-specific migration chains.

### Memory Module (`mem_001`)

The memory module uses pgvector for semantic search. Four tables:

| Table | Purpose |
|---|---|
| `episodes` | Session memory snapshots with embeddings |
| `facts` | Persistent structured knowledge (subject/predicate/content) |
| `rules` | Learned behavioral rules with effectiveness tracking |
| `memory_links` | Cross-type relationships between memory entities |

Key design patterns:
- **`vector(384)` embeddings** with IVFFLAT indexes for cosine similarity search
- **`tsvector` columns** with GIN indexes for full-text search
- **Decay model:** `decay_rate`, `reference_count`, `last_referenced_at` for memory aging
- **Permanence levels:** `permanent`, `stable`, `standard`, `volatile`
- **Consolidation pipeline:** `consolidation_status` ('pending', 'done') tracks episode processing

```sql
-- Example: facts table (key columns)
CREATE TABLE facts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    subject TEXT NOT NULL,
    predicate TEXT NOT NULL,
    content TEXT NOT NULL,
    embedding vector(384),
    search_vector tsvector,
    importance FLOAT NOT NULL DEFAULT 5.0,
    confidence FLOAT NOT NULL DEFAULT 1.0,
    decay_rate FLOAT NOT NULL DEFAULT 0.008,
    permanence TEXT NOT NULL DEFAULT 'standard',
    validity TEXT NOT NULL DEFAULT 'active',
    scope TEXT NOT NULL DEFAULT 'global',
    reference_count INTEGER NOT NULL DEFAULT 0,
    tags JSONB DEFAULT '[]'::jsonb,
    metadata JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_referenced_at TIMESTAMPTZ
);

CREATE INDEX idx_facts_subject_predicate ON facts (subject, predicate);
CREATE INDEX idx_facts_scope_validity ON facts (scope, validity) WHERE validity = 'active';
CREATE INDEX idx_facts_search ON facts USING gin(search_vector);
CREATE INDEX idx_facts_tags ON facts USING gin(tags);
CREATE INDEX idx_facts_embedding ON facts USING ivfflat (embedding vector_cosine_ops) WITH (lists = 20);
```

### Approvals Module

Two tables for tool-call approval gating:

| Table | Purpose |
|---|---|
| `approval_rules` | Pre-approval rules (tool + arg constraints) |
| `pending_actions` | Actions awaiting approval/execution |

### Contacts Module

Three tables for external contact sync:

| Table | Purpose |
|---|---|
| `contacts_source_accounts` | Registered sync provider accounts |
| `contacts_sync_state` | Per-account incremental sync cursor |
| `contacts_source_links` | External-to-local contact provenance |

---

## Butler-Specific Tables

Each butler defines its own domain tables via migrations in `roster/<name>/migrations/`. Examples:

**Health butler** — measurements, medications, medication_doses, conditions, meals, symptoms, research

**Relationship butler** — contacts, relationships, important_dates, notes, interactions, reminders, gifts, loans, groups, group_members, labels, contact_labels, quick_facts, activity_feed

**General butler** — collections, entities (freeform JSONB data)

**Messenger butler** — delivery_requests, delivery_attempts, delivery_receipts, delivery_dead_letter

**Switchboard butler** — butler_registry, routing_log, extraction_queue, extraction_log, message_inbox, and more

### Schema Design Principles

1. **JSONB for flexible/evolving fields.** Use typed columns for things you query on (foreign keys, timestamps, amounts). Use JSONB for metadata, details, and fields that vary across records.
2. **Always include `created_at`.** Every table gets `created_at TIMESTAMPTZ NOT NULL DEFAULT now()`.
3. **Include `updated_at` on mutable tables.** If rows get updated, track when.
4. **Use `UUID` primary keys** for domain tables. Use `BIGINT GENERATED ALWAYS AS IDENTITY` only for high-volume append-only tables.
5. **Use `TEXT` over `VARCHAR`.** PostgreSQL treats them identically. `TEXT` is simpler.
6. **Prefer JSONB arrays for tags** (`JSONB DEFAULT '[]'::jsonb`) over `TEXT[]` — this is the established pattern across the codebase.
7. **Cascade deletes where ownership is clear.** `ON DELETE CASCADE` for child records that have no meaning without their parent.
8. **Use CHECK constraints for enums.** `CHECK (status IN ('pending', 'active', 'done'))` instead of a separate lookup table.

---

## Indexing Strategy

Butler query patterns are heavily biased toward **recent data**. Design indexes accordingly.

### Rules

1. **Every timestamp column used in WHERE or ORDER BY gets a descending index.** Butlers almost always want "most recent first."
   ```sql
   CREATE INDEX idx_<table>_<col> ON <table> (<col> DESC);
   ```

2. **Compound indexes for filtered recency queries.** If you filter by a category and sort by time:
   ```sql
   CREATE INDEX idx_<table>_<filter>_recent ON <table> (<filter_col>, <time_col> DESC);
   ```

3. **GIN indexes for JSONB columns you search inside.** Use `jsonb_path_ops` for containment queries (`@>`), plain `GIN` if you also need key-existence checks (`?`, `?|`):
   ```sql
   CREATE INDEX idx_<table>_<col>_gin ON <table> USING GIN (<col> jsonb_path_ops);
   -- or plain GIN (established pattern in codebase):
   CREATE INDEX idx_<table>_<col>_gin ON <table> USING GIN (<col>);
   ```

4. **GIN indexes for JSONB array columns:**
   ```sql
   CREATE INDEX idx_<table>_tags_gin ON <table> USING GIN (tags);
   ```

5. **Partial indexes for hot subsets.** If you frequently query only active items or pending tasks:
   ```sql
   CREATE INDEX idx_<table>_active ON <table> (<col>) WHERE status = 'active';
   CREATE INDEX idx_tasks_due ON scheduled_tasks (next_run_at) WHERE enabled = true;
   ```

6. **GiST indexes for time-range overlap queries** (used by calendar):
   ```sql
   CREATE INDEX idx_<table>_time_window_gist
       ON <table> USING GIST (tstzrange(starts_at, ends_at, '[)'));
   ```

7. **IVFFLAT indexes for vector embeddings** (used by memory):
   ```sql
   CREATE INDEX idx_<table>_embedding
       ON <table> USING ivfflat (embedding vector_cosine_ops) WITH (lists = 20);
   ```

8. **Don't index columns you never filter or sort on.** No index on `detail` unless you actually run JSONB containment queries against it.

### Naming Convention

The codebase uses both `idx_` and `ix_` prefixes (both are acceptable). Be consistent within a single migration file. Pattern: `idx_<table>_<column(s)>`.

---

## Alembic Migration System

All schema changes go through Alembic. No exceptions.

### Multi-Chain Architecture

Migrations are organized into independent chains that are auto-discovered by `alembic/env.py`:

```
alembic/
  alembic.ini
  env.py                              # Multi-chain discovery + schema-scoped runner
  versions/
    core/                             # Shared core chain (branch_labels=("core",))
      core_001_target_state_baseline.py
      core_002_add_dispatch_mode_columns.py
      core_005_add_calendar_projection_tables.py
      ...

src/butlers/modules/
  memory/migrations/                  # Memory module chain (branch_labels=("memory",))
    001_memory_baseline.py
  approvals/migrations/               # Approvals module chain
    001_create_approvals_tables.py
    002_create_approval_events.py
  contacts/migrations/                # Contacts module chain
    001_contacts_sync_tables.py
  mailbox/migrations/                 # Mailbox module chain
    001_create_mailbox_table.py

roster/
  health/migrations/                  # Health butler chain (branch_labels=("health",))
    001_health_tables.py
  general/migrations/                 # General butler chain (branch_labels=("general",))
    001_general_tables.py
    002_add_entity_tags.py
  relationship/migrations/            # Relationship butler chain
    001_relationship_tables.py
    rel_002a_enrich_interactions.py
    ...
  messenger/migrations/               # Messenger butler chain
    msg_001_create_delivery_tables.py
  switchboard/migrations/             # Switchboard butler chain
    001_switchboard_tables.py
    002_extraction_tables.py
    ...
```

The discovery mechanism in `alembic/env.py`:
1. Shared chains: scans `alembic/versions/core/`
2. Module chains: scans `src/butlers/modules/*/migrations/`
3. Butler chains: scans `roster/*/migrations/`

### Migration Template

```python
"""<Short description of what this migration does>.

Revision ID: <prefix>_<number>
Revises:
Create Date: YYYY-MM-DD HH:MM:SS.000000
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "<prefix>_001"      # e.g., "health_001", "mem_001", "core_005"
down_revision = None            # None for first migration in chain, else previous revision
branch_labels = ("<chain>",)    # Only on first migration in a chain (e.g., ("health",))
depends_on = None               # Cross-chain dependency (e.g., "core_001")


def upgrade() -> None:
    # Raw SQL via op.execute() — NO SQLAlchemy ORM operations
    op.execute("""
        CREATE TABLE IF NOT EXISTS example (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            name TEXT NOT NULL,
            data JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_example_name
        ON example (name)
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_example_name")
    op.execute("DROP TABLE IF EXISTS example")
```

### Key Conventions

1. **Raw SQL only.** Use `op.execute("CREATE TABLE ...")`, not `op.create_table(...)` with SQLAlchemy Column objects. The project has `target_metadata=None`.

2. **`IF NOT EXISTS` / `IF EXISTS`.** All DDL uses idempotent forms because multiple schema-scoped runs execute the same migration file.

3. **Branch labels on first migration only.** The first migration in a chain sets `branch_labels = ("<chain_name>",)`. Subsequent migrations in the chain set `branch_labels = None`.

4. **Revision ID prefixes.** Use a chain prefix for readability:
   - Core: `core_001`, `core_002`, ...
   - Modules: `mem_001`, `approvals_001`, `contacts_001`, ...
   - Butlers: `health_001`, `gen_001`, `rel_001`, `msg_001`, `sw_001`, ...

5. **One logical change per migration.** Don't combine "add contacts table" and "add index on log" in the same migration.

6. **Always write `downgrade()`.** Even if you think you'll never roll back.

7. **No SQLAlchemy imports** beyond `from alembic import op`. Don't import `sa`, `sqlalchemy`, or `postgresql` dialect modules.

### Adding a New Butler's First Migration

1. Create `roster/<butler-name>/migrations/__init__.py` (empty file)
2. Create the migration file: `roster/<butler-name>/migrations/001_<butler>_tables.py`
3. Set `branch_labels = ("<butler-name>",)` on the first migration
4. Use the revision ID pattern: `<butler-name>_001`
5. The migration will be auto-discovered by `alembic/env.py`

Example first migration for a new butler:

```python
"""create_finance_tables

Revision ID: finance_001
Revises:
Create Date: 2026-02-23 00:00:00.000000
"""

from __future__ import annotations

from alembic import op

revision = "finance_001"
down_revision = None
branch_labels = ("finance",)
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS accounts (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            name TEXT NOT NULL UNIQUE,
            account_type TEXT NOT NULL,
            currency TEXT NOT NULL DEFAULT 'USD',
            metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS transactions (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            account_id UUID NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
            amount NUMERIC(12,2) NOT NULL,
            description TEXT,
            category TEXT,
            metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
            occurred_at TIMESTAMPTZ NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_transactions_account_occurred
        ON transactions (account_id, occurred_at DESC)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_transactions_category_occurred
        ON transactions (category, occurred_at DESC)
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS transactions")
    op.execute("DROP TABLE IF EXISTS accounts")
```

### Backward Compatibility Rules

**Every migration must be backward-compatible.** Assume the old code is still running when the migration executes.

| Operation | Safe? | How to do it safely |
|---|---|---|
| Add a table | Yes | `CREATE TABLE IF NOT EXISTS`. Old code ignores it. |
| Add a nullable column | Yes | `ALTER TABLE ADD COLUMN ... DEFAULT NULL`. Old code ignores it. |
| Add a column with a default | Yes | `ALTER TABLE ADD COLUMN ... DEFAULT <value>`. Old code ignores it. |
| Add an index | Yes | Use `CREATE INDEX CONCURRENTLY` for large tables. See note below. |
| Drop a column | **Two-phase.** | Phase 1: Stop reading/writing the column in code. Deploy. Phase 2: Drop column. |
| Rename a column | **Two-phase.** | Phase 1: Add new column, backfill, update code. Phase 2: Drop old column. |
| Drop a table | **Two-phase.** | Phase 1: Remove all code references. Deploy. Phase 2: Drop table. |
| Change a column type | **Careful.** | Add new column, backfill, migrate code, drop old. |
| Add NOT NULL | **Two-phase.** | Phase 1: Backfill NULLs, set default in code. Phase 2: `SET NOT NULL`. |

**CONCURRENTLY note:** `CREATE INDEX CONCURRENTLY` cannot run inside a transaction. If needed, the migration must disable the transaction wrapper:
```python
def upgrade() -> None:
    op.execute("COMMIT")  # Exit Alembic's transaction
    op.execute("CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_name ON table (col)")
```

---

## Schema-Scoped Migration Execution

When the daemon runs migrations for a butler, `alembic/env.py` sets the target schema:

```python
# In env.py run_migrations_online():
if target_schema is not None:
    connection.exec_driver_sql(f"CREATE SCHEMA IF NOT EXISTS {own_schema}")
    connection.exec_driver_sql(f"SET search_path TO {own_schema}, {shared_schema}, public")
```

This means:
- Core tables (`state`, `sessions`, etc.) are created **in the butler's own schema**, not in `shared`
- Butler-specific tables are also created in the butler's own schema
- Each butler has its own copy of core tables (no cross-butler contamination)
- The `shared` schema contains only tables explicitly created there by core migrations (calendar projections, etc.)

### Adding a New Butler to Core ACL

When adding a new butler, `core_001` (or a subsequent core migration) must list the butler in `_BUTLER_SCHEMAS` to create its runtime role and grant privileges. If the butler is added after the initial deployment, write a new core migration that:

1. Creates the schema
2. Creates the runtime role
3. Grants appropriate privileges

---

## What NOT to Do

- **Don't use SQLAlchemy ORM** in migrations — use `op.execute()` with raw SQL
- **Don't create separate databases** — all butlers share `butlers` DB with schema isolation
- **Don't access other butler schemas directly** — use MCP/Switchboard for inter-butler communication
- **Don't skip `IF NOT EXISTS`** — migrations run per-schema, idempotency is required
- **Don't use `datetime.now()` in SQL** — use `now()` for consistency
- **Don't put migrations in `src/butlers/db/`** — they go in `alembic/versions/core/`, `src/butlers/modules/*/migrations/`, or `roster/*/migrations/`
- **Don't import `sqlalchemy` in migrations** — only import `from alembic import op`
