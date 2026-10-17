"""Enforce QA publication ledger invariants in the database (bu-ie4wl1).

Revision ID: core_267
Revises: core_266
Create Date: 2026-10-18 00:00:00.000000

REQ-qa-publication-authority-002 / -006 (change confine-qa-publication-authority).
core_266 left forward-only stages, binding immutability and exact reconciliation to
application code.  The writer (bu-mc01y5.6) does not exist yet, so the database
becomes the fence first:

* ``ck_qa_publication_operations_exact_reconciliation`` -- a reconciled row names the
  bound ``expected_object_id`` exactly (``resource_found``) or no object at all
  (``resource_absent``); no other row carries a reconciliation or reconciled object.
* ``qa_publication_operations_guard`` (BEFORE UPDATE) -- a stage can only move forward
  (``stage_order`` strictly increases; a different stage at the same order, such as
  ``ambiguous`` to ``succeeded``, is a rewrite), a reconciled row is final, and the
  attempt binding, kind, ``binding_digest``, ``request_digest`` and
  ``expected_object_id`` can never change once the row exists.

The trigger runs for every role, including the table owner, so it holds even where row
security would not.  It does not guard TRUNCATE or DELETE, which core_266 documents.
The CHECK is added validated: no writer exists, so a violating row is a defect to surface.

The core chain runs once per butler schema over the shared table, so only the last
schema to leave core_267 removes the shared trigger, function and constraint (core_246
pattern).
"""

from __future__ import annotations

from alembic import op

revision = "core_267"
down_revision = "core_266"
branch_labels = None
depends_on = None

_TABLE = "public.qa_publication_operations"
_FUNCTION = "public.qa_publication_operations_guard"
_TRIGGER = "qa_publication_operations_guard"
_CONSTRAINT = "ck_qa_publication_operations_exact_reconciliation"
_LOCK = "SELECT pg_advisory_xact_lock(hashtextextended('butlers:core_267:qa-publication', 0))"


def _core_267_installation_count() -> int:
    """Count schema-local core chains that still depend on the shared guard (core_246 pattern)."""
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
                    THEN substring(version_num FROM 6)::integer >= 267
                    ELSE false END
            )
            """
        ).scalar()
        count += int(bool(installed))
    return count


def upgrade() -> None:
    op.execute(_LOCK)
    op.execute(
        f"""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conrelid = '{_TABLE}'::regclass AND conname = '{_CONSTRAINT}'
            ) THEN
                ALTER TABLE {_TABLE} ADD CONSTRAINT {_CONSTRAINT} CHECK (
                    -- COALESCE: a CHECK passes on NULL, and an unknown must not be exact.
                    COALESCE(
                        CASE
                            WHEN stage = 'reconciled' THEN
                                (reconciliation = 'resource_found'
                                    AND reconciled_object_id IS NOT NULL
                                    AND reconciled_object_id = expected_object_id)
                                OR (reconciliation = 'resource_absent'
                                    AND reconciled_object_id IS NULL)
                            ELSE reconciliation IS NULL AND reconciled_object_id IS NULL
                        END,
                        false
                    )
                );
            END IF;
        END
        $$
        """
    )
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION {_FUNCTION}() RETURNS trigger
        LANGUAGE plpgsql
        AS $fn$
        BEGIN
            IF OLD.stage = 'reconciled' THEN
                RAISE EXCEPTION 'qa_publication_operations: a reconciled row is final'
                    USING ERRCODE = 'check_violation';
            END IF;
            IF NEW.id IS DISTINCT FROM OLD.id
                OR NEW.attempt_id IS DISTINCT FROM OLD.attempt_id
                OR NEW.kind IS DISTINCT FROM OLD.kind
                OR NEW.binding_digest IS DISTINCT FROM OLD.binding_digest
                OR NEW.request_digest IS DISTINCT FROM OLD.request_digest
                OR NEW.expected_object_id IS DISTINCT FROM OLD.expected_object_id
                OR NEW.started_at IS DISTINCT FROM OLD.started_at THEN
                RAISE EXCEPTION 'qa_publication_operations: the operation binding is immutable'
                    USING ERRCODE = 'check_violation';
            END IF;
            IF NEW.stage_order < OLD.stage_order
                OR (NEW.stage_order = OLD.stage_order AND NEW.stage IS DISTINCT FROM OLD.stage) THEN
                RAISE EXCEPTION 'qa_publication_operations: a stage can only move forward'
                    USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END
        $fn$
        """
    )
    op.execute(f"DROP TRIGGER IF EXISTS {_TRIGGER} ON {_TABLE}")
    op.execute(
        f"""
        CREATE TRIGGER {_TRIGGER}
            BEFORE UPDATE ON {_TABLE}
            FOR EACH ROW EXECUTE FUNCTION {_FUNCTION}()
        """
    )


def downgrade() -> None:
    op.execute(_LOCK)
    # One shared trigger serves every schema still at core_267; only the last one removes it.
    if _core_267_installation_count() > 1:
        return
    op.execute(f"DROP TRIGGER IF EXISTS {_TRIGGER} ON {_TABLE}")
    op.execute(f"DROP FUNCTION IF EXISTS {_FUNCTION}()")
    op.execute(f"ALTER TABLE {_TABLE} DROP CONSTRAINT IF EXISTS {_CONSTRAINT}")
