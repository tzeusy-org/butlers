"""Stamp healing_attempts.closed_at from database time (bu-mc01y5.36).

Revision ID: core_305
Revises: core_303
Create Date: 2026-11-02 00:00:00.000000

core_301's QA export prune guard admits a prune only once the attempt closed at least 30
days ago, measured from ``public.healing_attempts.closed_at``.  That table has no
row-security fence (runtime roles hold public DML), so any writer, the QA role included,
could backdate ``closed_at`` and make a fresh export prunable.

``healing_attempts_closed_at_stamp`` (BEFORE INSERT OR UPDATE, every role, a privileged
login included) makes the column a database fact, whatever value the statement supplies:

* entering a terminal status (an INSERT, or an UPDATE from a non-terminal status) stamps
  ``closed_at := now()``;
* a terminal row keeps its existing ``closed_at`` on any later UPDATE, so a close is never
  moved, earlier or later;
* a non-terminal row carries no ``closed_at``.

Reopening a terminal row (an operator repair; the runtime never does it) clears the stamp,
and the next close restamps it later: that can only lengthen retention, never shorten it.
Only the table owner can disable the trigger, and that is DDL, not runtime DML.

The terminal set mirrors ``butlers.core.healing.tracking.TERMINAL_STATUSES`` and the core_301
guard; change them together.  Rows closed before this revision keep the value they had.

The core chain runs once per butler schema over one shared table, so only the last schema
to leave core_305 removes the trigger (core_246 pattern).
"""

from __future__ import annotations

from alembic import op

revision = "core_305"
down_revision = "core_303"
branch_labels = None
depends_on = None

_TABLE = "public.healing_attempts"
_FUNCTION = "public.healing_attempts_closed_at_stamp"
_TRIGGER = "healing_attempts_closed_at_stamp"
_LOCK = "SELECT pg_advisory_xact_lock(hashtextextended('butlers:core_305:closed-at', 0))"

_STAMP_FUNCTION = f"""
        CREATE OR REPLACE FUNCTION {_FUNCTION}() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE
            terminal CONSTANT text[] := ARRAY[
                'pr_merged', 'failed', 'unfixable', 'anonymization_failed', 'timeout'
            ];
        BEGIN
            IF NOT (NEW.status = ANY (terminal)) THEN
                NEW.closed_at := NULL;
            ELSIF TG_OP = 'UPDATE' AND OLD.status = ANY (terminal) THEN
                NEW.closed_at := OLD.closed_at;
            ELSE
                NEW.closed_at := now();
            END IF;
            RETURN NEW;
        END
        $$
        """


def _core_305_installation_count() -> int:
    """Count schema-local core chains that still depend on the stamp (core_246)."""
    bind = op.get_bind()
    schemas = bind.exec_driver_sql(
        """
        SELECT namespace.nspname
        FROM pg_class version_table
        JOIN pg_namespace namespace ON namespace.oid = version_table.relnamespace
        WHERE version_table.relname = 'alembic_version'
          AND version_table.relkind IN ('r', 'p')
          AND namespace.nspname NOT LIKE 'pg_%%'
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
                    THEN substring(version_num FROM 6)::integer >= 305
                    ELSE false END
            )
            """
        ).scalar()
        count += int(bool(installed))
    return count


def upgrade() -> None:
    op.execute(_LOCK)
    op.execute(_STAMP_FUNCTION)
    op.execute(f"DROP TRIGGER IF EXISTS {_TRIGGER} ON {_TABLE}")
    op.execute(
        f"""
        CREATE TRIGGER {_TRIGGER}
            BEFORE INSERT OR UPDATE ON {_TABLE}
            FOR EACH ROW EXECUTE FUNCTION {_FUNCTION}()
        """
    )


def downgrade() -> None:
    op.execute(_LOCK)
    # One shared table serves every schema still at core_305; only the last one removes it.
    if _core_305_installation_count() > 1:
        return
    op.execute(f"DROP TRIGGER IF EXISTS {_TRIGGER} ON {_TABLE}")
    op.execute(f"DROP FUNCTION IF EXISTS {_FUNCTION}()")
