---
name: butler-db-schema
description: Guide for designing and managing a butler's PostgreSQL database schema. Use when creating tables, writing migrations, adding indexes, or evolving a butler's data model.
---

# Butler Database Schema Design

Use this skill when creating or modifying a butler's database schema — adding tables, writing Alembic migrations, designing indexes, or evolving the data model for a specific butler's needs.

## Hard Constraints

- **One database per butler.** Each butler owns a dedicated PostgreSQL database (`butler_<name>`). Butlers never share databases. Inter-butler data exchange happens only via MCP tools through the Switchboard.
- **Six core tables in every butler database.** See the Core Tables section below. All six are created by the initial migration.
- **Migrations via Alembic only.** No raw DDL in application code. No "just run this SQL."
- **Backward compatibility in all migrations.** Every migration must be safe to run while the previous version of the code is still active.

---

## Core Tables (Every Butler Gets These)

Every butler database has exactly six tables that form the shared infrastructure. These are created by the `0001_core_schema` Alembic migration and must never be removed.

| Table | Purpose | Primary access pattern |
|---|---|---|
| `log` | Audit trail — everything in/out | Recent-first, filter by category/level |
| `state` | Key-value JSONB store | Point lookups by key, prefix scans |
| `sessions` | runtime invocation history | Recent-first, lookup by ID |
| `scheduled_tasks` | Recurring cron-driven prompts | Query enabled + due tasks |
| `memories` | Tiered memory (Eden/Mid-Term/Long-Term) | Search by tier, tag, recency |
| `pending_actions` | One-off deferred work + approval queue | Query pending + due items |

### 1. `log` — Audit Trail

The single audit trail for everything flowing in and out of the butler — runtime sessions, tool calls, inbound triggers, outbound actions, errors, module events. This is the most important core table.

```sql
CREATE TABLE log (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ts TIMESTAMPTZ NOT NULL DEFAULT now(),
    level TEXT NOT NULL DEFAULT 'info',         -- 'debug', 'info', 'warn', 'error'
    category TEXT NOT NULL,                     -- 'session', 'tool_call', 'trigger', 'module:<name>', 'scheduler', 'error', etc.
    summary TEXT NOT NULL,                      -- human-readable one-liner
    detail JSONB NOT NULL DEFAULT '{}',         -- structured payload (request, response, metadata, whatever fits)
    session_id UUID,                            -- FK to sessions(id) if this log entry belongs to a runtime session, NULL otherwise
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Most queries are "show me recent logs"
CREATE INDEX idx_log_ts ON log (ts DESC);

-- Filter by category (e.g., "show me all tool_call logs")
CREATE INDEX idx_log_category ON log (category, ts DESC);

-- Filter by level (e.g., "show me errors in the last hour")
CREATE INDEX idx_log_level_ts ON log (level, ts DESC) WHERE level IN ('warn', 'error');

-- Look up logs for a specific runtime session
CREATE INDEX idx_log_session_id ON log (session_id) WHERE session_id IS NOT NULL;

-- Search inside the JSONB detail payload
CREATE INDEX idx_log_detail ON log USING GIN (detail jsonb_path_ops);
```

#### What to log

Log generously. Storage is cheap; missing audit data is not recoverable.

| category | When to log | What goes in `detail` |
|---|---|---|
| `session` | runtime session starts/completes | `{prompt, trigger_source, duration_ms, success, tool_count}` |
| `tool_call` | Every MCP tool invocation | `{tool, args, result_summary, duration_ms}` |
| `trigger` | Inbound trigger arrives | `{source, prompt, caller}` |
| `scheduler` | Scheduled task fires | `{task_name, cron, next_run_at}` |
| `module:<name>` | Module-specific events | Module-defined payload |
| `error` | Anything fails | `{error, traceback, context}` |
| `state` | State store writes | `{key, old_value_hash, new_value_hash}` |
| `pending` | Pending action created/resolved | `{action_id, kind, status, summary}` |
| `memory` | Memory stored/promoted/evicted | `{memory_id, tier, action, tags}` |

#### Partitioning the log table

For butlers that generate high log volume, partition by time range:

```sql
CREATE TABLE log (
    id BIGINT GENERATED ALWAYS AS IDENTITY,
    ts TIMESTAMPTZ NOT NULL DEFAULT now(),
    level TEXT NOT NULL DEFAULT 'info',
    category TEXT NOT NULL,
    summary TEXT NOT NULL,
    detail JSONB NOT NULL DEFAULT '{}',
    session_id UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (id, ts)
) PARTITION BY RANGE (ts);

-- Create monthly partitions. Old partitions can be detached and archived.
CREATE TABLE log_y2026m01 PARTITION OF log
    FOR VALUES FROM ('2026-01-01') TO ('2026-02-01');
CREATE TABLE log_y2026m02 PARTITION OF log
    FOR VALUES FROM ('2026-02-01') TO ('2026-03-01');
-- ... generate ahead as needed
```

When partitioned, always include `ts` in queries so PostgreSQL can prune partitions. A scheduled task should create future partitions and optionally detach/drop old ones (e.g., older than 6 months).

**When to partition:** If a butler is expected to generate more than ~1M log rows per month, use partitioning. For low-volume butlers (e.g., a personal assistant ticking every 10 minutes), a single unpartitioned table with the indexes above is fine.

### 2. `state` — Key-Value Store

General-purpose persistent storage for structured data. Used by core components and modules to store anything that doesn't fit a dedicated table — configuration state, counters, flags, cached results, module-specific KV data.

```sql
CREATE TABLE state (
    key TEXT PRIMARY KEY,
    value JSONB NOT NULL DEFAULT '{}',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Prefix scans for namespaced keys (e.g., "module:email:%")
CREATE INDEX idx_state_key_prefix ON state (key text_pattern_ops);
```

Keys should be namespaced with colons: `module:email:last_check`, `scheduler:last_tick`, `config:override:timezone`. This makes prefix queries natural (`WHERE key LIKE 'module:email:%'`).

### 3. `sessions` — CC Invocation History

Every LLM CLI invocation spawned by this butler is recorded here. The `log` table references sessions via `session_id` FK.

```sql
CREATE TABLE sessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    trigger_source TEXT NOT NULL,              -- 'schedule:<task-name>', 'tick', 'external', 'trigger', 'pending:<action-id>'
    prompt TEXT NOT NULL,
    result TEXT,
    tool_calls JSONB NOT NULL DEFAULT '[]',
    success BOOLEAN,
    error TEXT,
    duration_ms INT,
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at TIMESTAMPTZ
);

-- Recent sessions first
CREATE INDEX idx_sessions_started ON sessions (started_at DESC);

-- Find sessions by trigger source (e.g., all runs of a specific scheduled task)
CREATE INDEX idx_sessions_trigger ON sessions (trigger_source, started_at DESC);

-- Find failed sessions
CREATE INDEX idx_sessions_failed ON sessions (started_at DESC) WHERE success = false;
```

### 4. `scheduled_tasks` — Cron-Driven Recurring Prompts

Stores both TOML-defined (bootstrap) and runtime-created scheduled tasks. The scheduler checks this table on every `tick()` to find due tasks.

```sql
CREATE TABLE scheduled_tasks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT UNIQUE NOT NULL,
    cron TEXT NOT NULL,
    prompt TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'db',          -- 'toml' or 'db'
    enabled BOOLEAN NOT NULL DEFAULT true,
    last_run_at TIMESTAMPTZ,
    next_run_at TIMESTAMPTZ,
    last_result JSONB,                          -- summary of last runtime session
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- The scheduler query: find enabled tasks that are due
CREATE INDEX idx_tasks_due ON scheduled_tasks (next_run_at ASC) WHERE enabled = true;
```

### 5. `memories` — Tiered Memory System

Generational memory inspired by JVM GC. Memories start in Eden (short-term), promote to Mid-Term (working), then Long-Term (identity) based on reference frequency. Unreferenced memories decay and evict.

```sql
CREATE TABLE memories (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tier TEXT NOT NULL DEFAULT 'eden',          -- 'eden', 'mid', 'long'
    content TEXT NOT NULL,                       -- the memory content
    summary TEXT,                                -- summarized version (populated on promotion)
    tags JSONB NOT NULL DEFAULT '[]',            -- searchable tags
    source TEXT NOT NULL,                        -- 'session:<id>', 'promotion:<id>', 'manual'
    metadata JSONB NOT NULL DEFAULT '{}',
    reference_count INT NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_referenced_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    promoted_at TIMESTAMPTZ,                    -- when last promoted to current tier
    expires_at TIMESTAMPTZ                      -- computed from tier thresholds
);

-- Filter by tier (e.g., "all eden memories")
CREATE INDEX idx_memories_tier ON memories (tier);

-- Eviction candidates: stale entries per tier
CREATE INDEX idx_memories_last_ref ON memories (last_referenced_at ASC);

-- Tag-based search
CREATE INDEX idx_memories_tags ON memories USING GIN (tags);

-- Find expired memories for eviction sweep
CREATE INDEX idx_memories_expires ON memories (expires_at ASC) WHERE expires_at IS NOT NULL;
```

Key behavior: `memory_search`, `memory_get`, and `memory_recall` MCP tools all bump `last_referenced_at` and `reference_count`. This is the mechanism that keeps important memories alive and lets unimportant ones decay.

### 6. `pending_actions` — Deferred Work + Approval Queue

One-off actions that need to happen in the future or require human approval before execution. Covers three patterns:

- **Deferred actions** — "send this email tomorrow at 9am." One-shot, carries a specific payload.
- **Human-in-the-loop approvals** — CC wants to do something that requires user sign-off. `due_at=NULL` means "wait for approval."
- **Follow-ups** — "If I don't hear back by Friday, remind me." Fires a runtime session with context when due.

The scheduler's `tick()` checks this table alongside `scheduled_tasks` for due items.

```sql
CREATE TABLE pending_actions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    kind TEXT NOT NULL,                         -- 'deferred', 'approval', 'followup'
    status TEXT NOT NULL DEFAULT 'pending',     -- 'pending', 'approved', 'rejected', 'executed', 'expired'
    summary TEXT NOT NULL,                      -- human-readable: "Send birthday email to Sarah"
    action JSONB NOT NULL,                      -- machine-readable payload: {tool, args, prompt}
    context JSONB NOT NULL DEFAULT '{}',        -- why this was created, originating session, etc.
    created_by_session UUID,                    -- FK to sessions(id)
    due_at TIMESTAMPTZ,                         -- NULL = needs manual approval, non-NULL = execute after this time
    expires_at TIMESTAMPTZ,                     -- auto-expire if not acted on by this time
    resolved_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- The tick() query: find pending actions that are due
CREATE INDEX idx_pending_due ON pending_actions (due_at ASC)
    WHERE status = 'pending' AND due_at IS NOT NULL;

-- Find items awaiting human approval
CREATE INDEX idx_pending_approval ON pending_actions (created_at DESC)
    WHERE status = 'pending' AND due_at IS NULL;

-- Find expired items for cleanup
CREATE INDEX idx_pending_expired ON pending_actions (expires_at ASC)
    WHERE status = 'pending' AND expires_at IS NOT NULL;
```

---

## Butler-Specific Schemas

Beyond the six core tables, each butler will have **wildly different schemas** based on its purpose. There is no universal data model. Design tables for what the butler actually needs.

### Examples

**Relationship butler** — tracks people and interactions:
```sql
CREATE TABLE contacts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    details JSONB NOT NULL DEFAULT '{}',   -- email, phone, birthday, notes, etc.
    tags TEXT[] NOT NULL DEFAULT '{}',
    last_contact_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE interactions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    contact_id UUID NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
    channel TEXT NOT NULL,               -- 'email', 'call', 'meeting', 'text'
    summary TEXT NOT NULL,
    detail JSONB NOT NULL DEFAULT '{}',
    occurred_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_interactions_contact_recent ON interactions (contact_id, occurred_at DESC);
```

**Finance butler** — tracks transactions and budgets:
```sql
CREATE TABLE transactions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    amount NUMERIC(12,2) NOT NULL,
    currency TEXT NOT NULL DEFAULT 'USD',
    category TEXT,
    description TEXT,
    source TEXT NOT NULL,               -- 'bank_import', 'manual', 'receipt_scan'
    metadata JSONB NOT NULL DEFAULT '{}',
    occurred_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_transactions_recent ON transactions (occurred_at DESC);
CREATE INDEX idx_transactions_category ON transactions (category, occurred_at DESC);
```

**Research butler** — stores collected artifacts:
```sql
CREATE TABLE artifacts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    kind TEXT NOT NULL,                  -- 'article', 'note', 'bookmark', 'snippet'
    title TEXT NOT NULL,
    content TEXT,
    url TEXT,
    tags TEXT[] NOT NULL DEFAULT '{}',
    metadata JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_artifacts_tags ON artifacts USING GIN (tags);
CREATE INDEX idx_artifacts_kind ON artifacts (kind, created_at DESC);
```

### Schema Design Principles

1. **JSONB for flexible/evolving fields.** Use typed columns for things you query on (foreign keys, timestamps, amounts). Use JSONB for metadata, details, and fields that vary across records or will evolve over time.
2. **Always include `created_at`.** Every table gets `created_at TIMESTAMPTZ NOT NULL DEFAULT now()`.
3. **Include `updated_at` on mutable tables.** If rows get updated, track when.
4. **Use `UUID` primary keys** for domain tables. Use `BIGINT GENERATED ALWAYS AS IDENTITY` for high-volume append-only tables (like `log`).
5. **Use `TEXT` over `VARCHAR`.** PostgreSQL treats them identically. `TEXT` is simpler.
6. **Prefer `TEXT[]` arrays for tags** over a join table — butler queries are simple tag-based filters, not complex relational joins.
7. **Cascade deletes where ownership is clear.** `ON DELETE CASCADE` for child records that have no meaning without their parent (e.g., interactions without a contact).

---

## Indexing Strategy

Butler query patterns are heavily biased toward **recent data**. Design indexes accordingly.

### Rules

1. **Every timestamp column used in WHERE or ORDER BY gets a descending index.** Butlers almost always want "most recent first."
   ```sql
   CREATE INDEX idx_<table>_<col> ON <table> (<col> DESC);
   ```

2. **Compound indexes for filtered recency queries.** If you filter by a category and sort by time, create a compound index:
   ```sql
   CREATE INDEX idx_<table>_<filter>_recent ON <table> (<filter_col>, <time_col> DESC);
   ```

3. **GIN indexes for JSONB columns you search inside.** Use `jsonb_path_ops` for containment queries (`@>`), plain `GIN` if you also need key-existence checks (`?`, `?|`):
   ```sql
   CREATE INDEX idx_<table>_<col> ON <table> USING GIN (<col> jsonb_path_ops);
   ```

4. **GIN indexes for array columns** (`TEXT[]`, `UUID[]`):
   ```sql
   CREATE INDEX idx_<table>_tags ON <table> USING GIN (tags);
   ```

5. **Partial indexes for hot subsets.** If you frequently query only errors, or only enabled tasks, use a partial index:
   ```sql
   CREATE INDEX idx_log_errors ON log (ts DESC) WHERE level = 'error';
   CREATE INDEX idx_tasks_enabled ON scheduled_tasks (next_run_at) WHERE enabled = true;
   ```

6. **Don't index columns you never filter or sort on.** No index on `detail` unless you actually run JSONB containment queries against it.

---

## Alembic Migration Rules

All schema changes go through Alembic. No exceptions.

### Project layout

```
src/butlers/
  db/
    alembic.ini
    alembic/
      env.py
      versions/
        0001_core_schema.py          # All 6 core tables: log, state, sessions, scheduled_tasks, memories, pending_actions
        0002_<butler_specific>.py    # Butler-specific tables (contacts, transactions, etc.)
        ...
```

Each butler runs `alembic upgrade head` on startup against its own database.

### Writing migrations

**Every migration must be backward-compatible.** Assume the old code is still running when the migration executes. This means:

| Operation | Safe? | How to do it safely |
|---|---|---|
| Add a table | Yes | Just `CREATE TABLE`. Old code ignores it. |
| Add a nullable column | Yes | `ALTER TABLE ADD COLUMN ... DEFAULT NULL`. Old code ignores it. |
| Add a column with a default | Yes | `ALTER TABLE ADD COLUMN ... DEFAULT <value>`. Old code ignores it. |
| Add an index | Yes | Use `CREATE INDEX CONCURRENTLY` to avoid locking. In Alembic, set `op.create_index(..., postgresql_concurrently=True)` and mark the migration with `# non-transactional` (run outside transaction block). |
| Drop a column | **Two-phase.** | Phase 1: Stop reading/writing the column in code. Deploy. Phase 2: Migration drops the column. |
| Rename a column | **Two-phase.** | Phase 1: Add new column, backfill, update code to use new column. Phase 2: Drop old column. |
| Drop a table | **Two-phase.** | Phase 1: Remove all code references. Deploy. Phase 2: Migration drops the table. |
| Change a column type | **Careful.** | Add new column with new type, backfill, migrate code, drop old column. Or use `USING` cast if the conversion is lossless and the table is small. |
| Add a NOT NULL constraint | **Two-phase.** | Phase 1: Backfill NULLs, set default in code. Phase 2: `ALTER TABLE ... SET NOT NULL`. |

### Migration template

```python
"""<Short description of what this migration does>.

Revision ID: <auto>
Revises: <auto>
Create Date: <auto>
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers
revision = "<auto>"
down_revision = "<auto>"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Always include a comment explaining WHY, not just WHAT
    op.create_table(
        "example",
        sa.Column("id", postgresql.UUID(), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("example")
```

### Rules

1. **One logical change per migration.** Don't combine "add contacts table" and "add index on log" in the same migration.
2. **Always write `downgrade()`.** Even if you think you'll never roll back.
3. **Use `CONCURRENTLY` for index creation** on any table that might have data. This requires running outside a transaction — in Alembic, this means the migration file needs:
   ```python
   # At module level, outside upgrade/downgrade:
   # This migration must run outside a transaction block.
   def upgrade() -> None:
       # Disable transaction for this migration
       op.execute("COMMIT")
       op.create_index("idx_name", "table", ["col"], postgresql_concurrently=True)
   ```
4. **Test migrations against a real database.** Don't rely on SQLite or mocks for migration testing.
5. **Name migrations with a sequence prefix** (`0001_`, `0002_`) for readability alongside Alembic's revision chain.

---

## Partitioning Strategy

Use partitioning selectively. Most butler-specific tables will be small enough that good indexes are sufficient.

**Partition when:**
- The table is append-only or append-heavy (logs, events, interactions)
- Row count will exceed ~1M rows
- Queries almost always filter by time range
- You want to cheaply archive or drop old data (detach partition)

**Don't partition when:**
- The table is small (contacts, config, state)
- Queries need full-table access (search across all time)
- The table is heavily updated (partitioning adds overhead to UPDATE)

**Always partition by time range** (`RANGE` on a `TIMESTAMPTZ` column). Hash or list partitioning is unlikely to be useful for butler workloads.

```sql
-- Pattern: monthly partitions with auto-creation
CREATE TABLE <table> (...) PARTITION BY RANGE (ts);

-- Create partitions 3 months ahead, drop/archive partitions older than retention period
-- This should be a scheduled task in the butler itself
```
