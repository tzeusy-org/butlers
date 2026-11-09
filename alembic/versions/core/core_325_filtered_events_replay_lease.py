"""Replay drain claim lease on connectors.filtered_events (bu-dhloi1.225).

Revision ID: core_325
Revises: core_324
Create Date: 2026-11-09 00:00:00.000000

``drain_replay_pending`` no longer holds a transaction across submission. It claims rows in
one short statement, stamping a per-drain ``claim_token`` and ``claimed_at``; submits with no
transaction held; then records each outcome only while the row still carries its token. A
drain that is cancelled releases its unsubmitted claims, and a drain that dies leaves a claim
that any later drain reclaims once ``claimed_at`` is older than the lease.

Both columns are nullable with no default, so adding them is a metadata-only change on the
partitioned table. The core chain runs once per butler schema over this shared table, so every
statement is idempotent, and only the last schema to leave core_325 drops the columns.
"""

from __future__ import annotations

from alembic import op

revision = "core_325"
down_revision = "core_324"
branch_labels = None
depends_on = None

_LOCK = "SELECT pg_advisory_xact_lock(hashtextextended('butlers:core_325:replay-lease', 0))"


def _installation_count() -> int:
    """Count schema-local core chains that still depend on the lease columns."""
    bind = op.get_bind()
    schemas = bind.exec_driver_sql(
        """
        SELECT namespace.nspname
        FROM pg_class version_table
        JOIN pg_namespace namespace ON namespace.oid = version_table.relnamespace
        WHERE version_table.relname = 'alembic_version'
          AND version_table.relkind IN ('r', 'p')
          AND left(namespace.nspname, 3) <> 'pg_'
          AND namespace.nspname <> 'information_schema'
        """
    ).scalars()
    quote = bind.dialect.identifier_preparer.quote
    count = 0
    for schema in schemas:
        installed = bind.exec_driver_sql(
            f"""
            SELECT EXISTS (
                SELECT 1 FROM {quote(schema)}.alembic_version
                WHERE CASE WHEN version_num ~ '^core_[0-9]+$'
                    THEN substring(version_num FROM 6)::integer >= 325
                    ELSE false END
            )
            """
        ).scalar()
        count += int(bool(installed))
    return count


def upgrade() -> None:
    op.execute(_LOCK)
    # Short lock wait: ingestion writes this table continuously.
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute(
        """
        ALTER TABLE connectors.filtered_events
            ADD COLUMN IF NOT EXISTS claim_token UUID,
            ADD COLUMN IF NOT EXISTS claimed_at TIMESTAMPTZ
        """
    )


def downgrade() -> None:
    op.execute(_LOCK)
    if _installation_count() > 1:
        return
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute(
        """
        ALTER TABLE connectors.filtered_events
            DROP COLUMN IF EXISTS claim_token,
            DROP COLUMN IF EXISTS claimed_at
        """
    )
