# Switchboard Schema Migration Guide

This document provides guidance for evolving Switchboard's PostgreSQL schema safely.

## Migration Principles

1. **Backwards Compatibility**: New migrations must not break existing code
2. **Zero-Downtime**: Schema changes should support live systems
3. **Audit Trail**: All schema changes tracked via Alembic revisions
4. **Data Preservation**: Downgrades must preserve data where possible

---

## Current Schema State

### Tables

| Table | Purpose | Partitioned | Retention |
|-------|---------|-------------|-----------|
| `message_inbox` | Lifecycle store for all requests | Yes (monthly) | 1 month default |
| `butler_registry` | Target butler registration | No | Infinite |
| `routing_log` | Route decisions | No | 90 days |
| `fanout_execution_log` | Fanout execution records | No | 30 days |
| `dead_letter_queue` | Failed/exhausted requests | No | Manual cleanup |
| `operator_audit_log` | Manual intervention log | No | 1 year |
| `dashboard_audit_log` | Dashboard actions | No | 90 days |

### Migration Chain

```
sw_001 (initial tables)
  ↓
sw_002 (extraction tables)
  ↓
sw_003 (notifications)
  ↓
sw_004 (audit log)
  ↓
sw_005 (message_inbox v1)
  ↓
sw_006 (dedupe columns)
  ↓
sw_007 (fanout execution log)
  ↓
sw_008 (partition message_inbox)
  ↓
sw_009 (registry liveness)
  ↓
sw_010 (dedupe unique index)
  ↓
sw_011 (dead_letter_queue)  ← NEW
  ↓
sw_012 (operator_audit_log)  ← NEW
```

---

## Adding a New Table

### Step 1: Create Migration File

```bash
cd roster/switchboard/migrations
touch 013_create_new_table.py
```

### Step 2: Define Migration

```python
"""Add new_table for XYZ functionality.

Revision ID: sw_013
Revises: sw_012
Create Date: 2026-02-XX 00:00:00.000000
"""

from __future__ import annotations
from alembic import op

revision = "sw_013"
down_revision = "sw_012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE new_table (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            -- Add columns here
        )
    """)
    
    # Add indexes
    op.execute("""
        CREATE INDEX ix_new_table_created_at
        ON new_table (created_at DESC)
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS new_table")
```

### Step 3: Test Migration

```bash
# Apply migration
uv run alembic -c roster/switchboard/alembic.ini upgrade head

# Test downgrade
uv run alembic -c roster/switchboard/alembic.ini downgrade -1

# Re-apply
uv run alembic -c roster/switchboard/alembic.ini upgrade head
```

---

## Adding Columns to Existing Tables

### Safe Column Addition

```python
def upgrade() -> None:
    # Add nullable column with default
    op.execute("""
        ALTER TABLE message_inbox
        ADD COLUMN new_field TEXT DEFAULT NULL
    """)
    
    # Optionally backfill (if safe)
    op.execute("""
        UPDATE message_inbox
        SET new_field = 'default_value'
        WHERE new_field IS NULL
    """)
    
    # Make NOT NULL after backfill (if needed)
    op.execute("""
        ALTER TABLE message_inbox
        ALTER COLUMN new_field SET NOT NULL
    """)
```

### Unsafe Patterns (Avoid)

```python
# DON'T: Add NOT NULL column without default
ALTER TABLE message_inbox ADD COLUMN required_field TEXT NOT NULL

# DON'T: Drop columns without grace period
ALTER TABLE message_inbox DROP COLUMN old_field

# DON'T: Change column types without compatibility layer
ALTER TABLE message_inbox ALTER COLUMN id TYPE TEXT
```

---

## Modifying Enums/Constraints

### Adding Enum Values

**Current constraint** (from `sw_011`):

```sql
CONSTRAINT valid_failure_category CHECK (
    failure_category IN (
        'timeout',
        'retry_exhausted',
        'circuit_open',
        'policy_violation',
        'validation_error',
        'downstream_failure',
        'unknown'
    )
)
```

**Adding new value:**

```python
def upgrade() -> None:
    # Drop constraint
    op.execute("""
        ALTER TABLE dead_letter_queue
        DROP CONSTRAINT valid_failure_category
    """)
    
    # Re-add with new value
    op.execute("""
        ALTER TABLE dead_letter_queue
        ADD CONSTRAINT valid_failure_category CHECK (
            failure_category IN (
                'timeout',
                'retry_exhausted',
                'circuit_open',
                'policy_violation',
                'validation_error',
                'downstream_failure',
                'rate_limited',  -- NEW
                'unknown'
            )
        )
    """)


def downgrade() -> None:
    # Migrate data using new value (if any)
    op.execute("""
        UPDATE dead_letter_queue
        SET failure_category = 'unknown'
        WHERE failure_category = 'rate_limited'
    """)
    
    # Restore old constraint
    op.execute("""
        ALTER TABLE dead_letter_queue
        DROP CONSTRAINT valid_failure_category
    """)
    
    op.execute("""
        ALTER TABLE dead_letter_queue
        ADD CONSTRAINT valid_failure_category CHECK (
            failure_category IN (
                'timeout',
                'retry_exhausted',
                'circuit_open',
                'policy_violation',
                'validation_error',
                'downstream_failure',
                'unknown'
            )
        )
    """)
```

---

## Index Management

### Adding Indexes

```python
def upgrade() -> None:
    # Non-blocking index creation (PostgreSQL 11+)
    op.execute("""
        CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_dead_letter_queue_source_table
        ON dead_letter_queue (source_table, created_at DESC)
    """)
```

**Note:** `CONCURRENTLY` prevents table locks but requires:
- Run outside transaction (use `op.execute(..., execution_options={"isolation_level": "AUTOCOMMIT"})` if needed)
- Cannot be rolled back automatically

### Removing Unused Indexes

```python
def upgrade() -> None:
    # Drop if index is truly unused (verify with query planner first)
    op.execute("DROP INDEX IF EXISTS ix_old_unused_index")
```

**Verification:**

```sql
-- Check index usage
SELECT
    schemaname,
    tablename,
    indexname,
    idx_scan  -- Number of scans (0 = unused)
FROM pg_stat_user_indexes
WHERE indexname = 'ix_old_unused_index';
```

---

## Partition Management

### Message Inbox (Partitioned by `received_at`)

**Current partitioning:** Monthly partitions via `sw_008`

**Helper functions:**

```sql
-- Ensure partition exists for a given month
SELECT switchboard_message_inbox_ensure_partition('2026-03-01'::TIMESTAMPTZ);

-- Drop partitions older than retention period
SELECT switchboard_message_inbox_drop_expired_partitions(INTERVAL '1 month');
```

### Adding New Partitioned Table

```python
def upgrade() -> None:
    # Create parent table with PARTITION BY
    op.execute("""
        CREATE TABLE partitioned_table (
            id UUID NOT NULL,
            partition_key TIMESTAMPTZ NOT NULL,
            data JSONB,
            PRIMARY KEY (partition_key, id)
        ) PARTITION BY RANGE (partition_key)
    """)
    
    # Create partition helper function
    op.execute("""
        CREATE OR REPLACE FUNCTION ensure_partitioned_table_partition(
            reference_ts TIMESTAMPTZ DEFAULT now()
        ) RETURNS TEXT
        LANGUAGE plpgsql
        AS $$
        DECLARE
            month_start TIMESTAMPTZ;
            month_end TIMESTAMPTZ;
            partition_name TEXT;
        BEGIN
            month_start := date_trunc('month', reference_ts);
            month_end := month_start + INTERVAL '1 month';
            partition_name := format('partitioned_table_p%s', to_char(month_start, 'YYYYMM'));

            EXECUTE format(
                'CREATE TABLE IF NOT EXISTS %I PARTITION OF partitioned_table '
                'FOR VALUES FROM (%L) TO (%L)',
                partition_name, month_start, month_end
            );

            RETURN partition_name;
        END;
        $$
    """)
    
    # Create initial partitions
    op.execute("SELECT ensure_partitioned_table_partition(now())")
    op.execute("SELECT ensure_partitioned_table_partition(now() + INTERVAL '1 month')")
```

---

## Data Migration Patterns

### Backfilling Derived Data

```python
def upgrade() -> None:
    # Add new column
    op.execute("""
        ALTER TABLE dead_letter_queue
        ADD COLUMN retry_hours_elapsed NUMERIC
    """)
    
    # Backfill calculation
    op.execute("""
        UPDATE dead_letter_queue
        SET retry_hours_elapsed = EXTRACT(EPOCH FROM (last_retry_at - created_at)) / 3600
        WHERE last_retry_at IS NOT NULL
    """)
```

### Large Table Migrations

For tables with millions of rows:

```python
def upgrade() -> None:
    # Add column without backfill
    op.execute("""
        ALTER TABLE large_table
        ADD COLUMN new_field TEXT DEFAULT NULL
    """)
    
    # Batch backfill (run separately if needed)
    # DO NOT run in migration for very large tables
    # Instead, use a separate background job
```

**Separate backfill script:**

```python
# scripts/backfill_new_field.py
async def backfill_in_batches(conn, batch_size=1000):
    while True:
        updated = await conn.execute("""
            UPDATE large_table
            SET new_field = compute_new_value(old_field)
            WHERE id IN (
                SELECT id FROM large_table
                WHERE new_field IS NULL
                LIMIT $1
            )
        """, batch_size)
        
        if updated == 0:
            break
        
        await asyncio.sleep(0.1)  # Rate limit
```

---

## Rollback Strategies

### Safe Downgrades

```python
def downgrade() -> None:
    # Drop new table (no data loss if recently added)
    op.execute("DROP TABLE IF EXISTS new_table")
    
    # Drop new column (preserve data in backup if needed)
    op.execute("""
        -- Optional: Create backup
        CREATE TABLE dead_letter_queue_backup AS
        SELECT * FROM dead_letter_queue
    """)
    
    op.execute("""
        ALTER TABLE dead_letter_queue
        DROP COLUMN new_field
    """)
```

### Emergency Rollback

If production migration fails mid-apply:

```bash
# Check current revision
uv run alembic -c roster/switchboard/alembic.ini current

# Downgrade to previous
uv run alembic -c roster/switchboard/alembic.ini downgrade -1

# Or downgrade to specific revision
uv run alembic -c roster/switchboard/alembic.ini downgrade sw_011
```

---

## Testing Migrations

### Unit Tests

```python
# tests/test_migrations.py
import pytest

async def test_sw_013_upgrade(test_db_pool):
    async with test_db_pool.acquire() as conn:
        # Apply migration (via Alembic)
        # ...
        
        # Verify table exists
        result = await conn.fetchrow("""
            SELECT EXISTS (
                SELECT FROM pg_tables
                WHERE tablename = 'new_table'
            )
        """)
        assert result["exists"] is True


async def test_sw_013_downgrade(test_db_pool):
    async with test_db_pool.acquire() as conn:
        # Apply and then downgrade
        # ...
        
        # Verify table removed
        result = await conn.fetchrow("""
            SELECT EXISTS (
                SELECT FROM pg_tables
                WHERE tablename = 'new_table'
            )
        """)
        assert result["exists"] is False
```

### Integration Tests

```bash
# Full migration test suite
uv run pytest tests/test_migrations.py -v
```

---

## Production Deployment Checklist

- [ ] Migration tested locally
- [ ] Migration tested in staging environment
- [ ] Downgrade tested and verified
- [ ] Large table backfills planned separately (if applicable)
- [ ] Index creation uses `CONCURRENTLY` for production
- [ ] Monitoring alerts configured for new tables/columns
- [ ] Runbooks updated with new schema changes
- [ ] Code deployment coordinated with migration timing
- [ ] Rollback plan documented and tested

---

## Schema Version Compatibility

### Application Code Compatibility Matrix

| Migration | Min App Version | Notes |
|-----------|----------------|-------|
| sw_011 | v1.14.0 | Requires dead-letter handler code |
| sw_012 | v1.14.0 | Requires operator control tools |

### Deprecation Policy

1. **Announce deprecation** in migration docstring
2. **Grace period** of at least 2 releases
3. **Remove deprecated fields/tables** in subsequent migration

Example:

```python
"""Remove deprecated field (deprecated in sw_010, removed in sw_015).

Grace period: 5 releases
"""
```

---

## Common Pitfalls

### Pitfall 1: NOT NULL Without Default

**Problem:** Adding `NOT NULL` column to existing rows fails.

**Solution:** Add as nullable, backfill, then set `NOT NULL`.

### Pitfall 2: Long-Running Migrations

**Problem:** Large table alterations lock table for minutes.

**Solution:** Use `CONCURRENTLY` or batch operations.

### Pitfall 3: Forgetting Downgrade

**Problem:** Downgrade fails because data migration is missing.

**Solution:** Always test downgrade path.

### Pitfall 4: Enum Constraint Violations

**Problem:** Existing data violates new constraint.

**Solution:** Migrate data first, then add constraint.

---

## References

- Alembic documentation: https://alembic.sqlalchemy.org/
- PostgreSQL ALTER TABLE: https://www.postgresql.org/docs/current/sql-altertable.html
- PostgreSQL partitioning: https://www.postgresql.org/docs/current/ddl-partitioning.html
