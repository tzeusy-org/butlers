"""Record when a QA publication operation's egress began (bu-mc01y5.30).

Revision ID: core_300
Revises: core_299
Create Date: 2026-11-02 00:00:00.000000

REQ-qa-publication-authority-006 (change confine-qa-publication-authority).
The read-only reconciler believes ``resource_absent`` only once a held operation has
settled, but the ledger had no egress timestamp: ``started_at`` is the bind time
(immutable since core_267) and ``bound -> egress_started`` wrote none.  A bind resumed
long after it was written entered ``egress_started`` already looking settled, so a sweep
during the push or create could free the lineage while the egress was in flight.

* ``egress_started_at`` (nullable ``timestamptz``) is stamped with database time by
  ``state.mark_egress_started``.  ``record_reconciliation`` measures the settle from
  ``COALESCE(ended_at, egress_started_at)`` inside its guarded UPDATE.
* ``qa_publication_operations_guard`` is extended, keeping every core_267/core_268 rule:
  a row leaving ``bound`` without the stamp gets database time (so a writer that predates
  this column still anchors the settle), and the stamp is set-once afterwards.
* ``ck_qa_publication_operations_egress_started_at``: a ``bound`` row carries no stamp.
* Rows already in ``egress_started`` with no stamp are backfilled with the migration's
  time, the latest moment their egress can have begun, so their absence settles from now
  rather than from the bind.  Rows past that stage carry ``ended_at`` or are final.

The core chain runs once per butler schema over the shared table, so only the last
schema to leave core_300 drops the column and CHECK and restores the core_268 guard
(core_246 pattern).
"""

from __future__ import annotations

from alembic import op

revision = "core_300"
down_revision = "core_299"
branch_labels = None
depends_on = None

_TABLE = "public.qa_publication_operations"
_FUNCTION = "public.qa_publication_operations_guard"
_COLUMN = "egress_started_at"
# Named to sort after ck_qa_publication_operations_stage: Postgres evaluates CHECKs by name, and
# the stage/order refusal must keep reporting first.
_CONSTRAINT = "ck_qa_publication_operations_stage_stamp"
_LOCK = "SELECT pg_advisory_xact_lock(hashtextextended('butlers:core_300:qa-publication', 0))"

# The core_268 guard body, verbatim.
_GUARD_HEAD = """
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
"""
_STAGE_FORWARD = """
            IF NEW.stage_order < OLD.stage_order
                OR (NEW.stage_order = OLD.stage_order AND NEW.stage IS DISTINCT FROM OLD.stage) THEN
                RAISE EXCEPTION 'qa_publication_operations: a stage can only move forward'
                    USING ERRCODE = 'check_violation';
            END IF;
"""
_SET_ONCE = """
            IF OLD.new_object_id IS NOT NULL
                AND NEW.new_object_id IS DISTINCT FROM OLD.new_object_id THEN
                RAISE EXCEPTION 'qa_publication_operations: new_object_id is set-once'
                    USING ERRCODE = 'check_violation';
            END IF;
            IF OLD.result IS NOT NULL AND NEW.result IS DISTINCT FROM OLD.result THEN
                RAISE EXCEPTION 'qa_publication_operations: result is set-once'
                    USING ERRCODE = 'check_violation';
            END IF;
"""
_GUARD_END = """            RETURN NEW;
        END
"""
# core_300: the egress stamp.  Runs after the stage-forward refusal (core_267 contract).
_EGRESS_STAMP = """
            IF OLD.egress_started_at IS NOT NULL
                AND NEW.egress_started_at IS DISTINCT FROM OLD.egress_started_at THEN
                RAISE EXCEPTION 'qa_publication_operations: egress_started_at is set-once'
                    USING ERRCODE = 'check_violation';
            END IF;
            IF OLD.stage = 'bound' AND NEW.stage <> 'bound' AND NEW.egress_started_at IS NULL THEN
                NEW.egress_started_at := now();
            END IF;
"""


def _guard_function(body: str) -> str:
    return f"""
        CREATE OR REPLACE FUNCTION {_FUNCTION}() RETURNS trigger
        LANGUAGE plpgsql
        AS $fn$
        {body}
        $fn$
        """


def _core_300_installation_count() -> int:
    """Count schema-local core chains that still depend on the egress stamp (core_246)."""
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
                    THEN substring(version_num FROM 6)::integer >= 300
                    ELSE false END
            )
            """
        ).scalar()
        count += int(bool(installed))
    return count


def upgrade() -> None:
    op.execute(_LOCK)
    op.execute(f"ALTER TABLE {_TABLE} ADD COLUMN IF NOT EXISTS {_COLUMN} TIMESTAMPTZ")
    # Before the new guard exists: the core_268 guard allows a same-stage update.
    op.execute(
        f"UPDATE {_TABLE} SET {_COLUMN} = now() "
        f"WHERE stage = 'egress_started' AND {_COLUMN} IS NULL"
    )
    op.execute(
        f"""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conrelid = '{_TABLE}'::regclass AND conname = '{_CONSTRAINT}'
            ) THEN
                ALTER TABLE {_TABLE} ADD CONSTRAINT {_CONSTRAINT}
                    CHECK (stage <> 'bound' OR {_COLUMN} IS NULL);
            END IF;
        END
        $$
        """
    )
    op.execute(
        _guard_function(_GUARD_HEAD + _STAGE_FORWARD + _SET_ONCE + _EGRESS_STAMP + _GUARD_END)
    )


def downgrade() -> None:
    op.execute(_LOCK)
    # One shared column serves every schema still at core_300; only the last one removes it.
    if _core_300_installation_count() > 1:
        return
    # Restore the core_268 guard before the column it names goes away.
    op.execute(_guard_function(_GUARD_HEAD + _STAGE_FORWARD + _SET_ONCE + _GUARD_END))
    op.execute(f"ALTER TABLE {_TABLE} DROP CONSTRAINT IF EXISTS {_CONSTRAINT}")
    op.execute(f"ALTER TABLE {_TABLE} DROP COLUMN IF EXISTS {_COLUMN}")
