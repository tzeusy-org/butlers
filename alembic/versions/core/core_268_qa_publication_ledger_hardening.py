"""Harden the QA publication ledger rows against rewrite and deletion (bu-mc01y5.14).

Revision ID: core_268
Revises: core_267
Create Date: 2026-10-18 00:00:00.000000

REQ-qa-publication-authority-002 / -006 (change confine-qa-publication-authority).
core_267 fenced stage order, the binding and reconciliation.  Three gaps remained:

* ``new_object_id`` is set-once.  ``record_outcome`` may fill it (NULL to a value) and
  may repeat the same value, but a recorded object can never be replaced.  The
  ``qa_publication_operations_guard`` function is extended, not replaced: every core_267
  rule is kept.
* ``result`` is set-once, like ``new_object_id``: ``record_outcome`` fills it (NULL to a
  value) and nothing may change or clear it afterwards, so a reconciled row really keeps the
  outcome it was reached from.
* ``ck_qa_publication_operations_stage_result`` ties ``result`` to ``stage`` using only the
  values ``state.py`` writes: ``bound`` and ``egress_started`` carry no result, ``succeeded``
  carries ``success``, ``failed`` carries ``failed``, ``ambiguous`` carries ``ambiguous``, and
  a ``reconciled`` row carries none (reached from ``egress_started``) or ``ambiguous``
  (reached from ``ambiguous``).  A ``succeeded`` or ``failed`` row without its result is
  refused.  Residual: an ``ambiguous`` row may carry a NULL result.  The writer never
  produces one and the hold is keyed on stage, not result, so it cannot release a hold;
  bu-mc01y5.15 closes it later.
* ``qa_publication_operations_no_held_delete`` (BEFORE DELETE) refuses deleting a row
  that holds the lineage (``egress_started`` or ``ambiguous``), including from a privileged
  login that bypasses row security.  Other rows may still be deleted.  TRUNCATE is not guarded.

The core chain runs once per butler schema over the shared table, so only the last
schema to leave core_268 removes the shared objects and restores the core_267 guard
(core_246 pattern).
"""

from __future__ import annotations

from alembic import op

revision = "core_268"
down_revision = "core_267"
branch_labels = None
depends_on = None

_TABLE = "public.qa_publication_operations"
_FUNCTION = "public.qa_publication_operations_guard"
_DELETE_FUNCTION = "public.qa_publication_operations_no_held_delete"
_DELETE_TRIGGER = "qa_publication_operations_no_held_delete"
# Named to sort after ck_qa_publication_operations_stage: Postgres evaluates CHECKs by name, and
# the stage/order refusal must keep reporting first.
_CONSTRAINT = "ck_qa_publication_operations_stage_result"
_HOLD_STAGES = "('egress_started', 'ambiguous')"
_LOCK = "SELECT pg_advisory_xact_lock(hashtextextended('butlers:core_268:qa-publication', 0))"

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
_GUARD_END = """            RETURN NEW;
        END
"""
# Order matters: the stage-forward refusal runs before the set-once checks so a stage rewrite that
# also writes a result keeps reporting the stage error (core_267 contract).
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


def _guard_function(body: str) -> str:
    return f"""
        CREATE OR REPLACE FUNCTION {_FUNCTION}() RETURNS trigger
        LANGUAGE plpgsql
        AS $fn$
        {body}
        $fn$
        """


def _core_268_installation_count() -> int:
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
                    THEN substring(version_num FROM 6)::integer >= 268
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
                    -- COALESCE: a CHECK passes on NULL, and an unknown must not match.
                    COALESCE(
                        (stage IN ('bound', 'egress_started') AND result IS NULL)
                        OR (stage = 'succeeded' AND result = 'success')
                        OR (stage = 'failed' AND result = 'failed')
                        OR (stage = 'ambiguous' AND (result IS NULL OR result = 'ambiguous'))
                        OR (stage = 'reconciled' AND (result IS NULL OR result = 'ambiguous')),
                        false
                    )
                );
            END IF;
        END
        $$
        """
    )
    op.execute(_guard_function(_GUARD_HEAD + _STAGE_FORWARD + _SET_ONCE + _GUARD_END))
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION {_DELETE_FUNCTION}() RETURNS trigger
        LANGUAGE plpgsql
        AS $fn$
        BEGIN
            IF OLD.stage IN {_HOLD_STAGES} THEN
                RAISE EXCEPTION 'qa_publication_operations: a held row cannot be deleted'
                    USING ERRCODE = 'check_violation';
            END IF;
            RETURN OLD;
        END
        $fn$
        """
    )
    op.execute(f"DROP TRIGGER IF EXISTS {_DELETE_TRIGGER} ON {_TABLE}")
    op.execute(
        f"""
        CREATE TRIGGER {_DELETE_TRIGGER}
            BEFORE DELETE ON {_TABLE}
            FOR EACH ROW EXECUTE FUNCTION {_DELETE_FUNCTION}()
        """
    )


def downgrade() -> None:
    op.execute(_LOCK)
    # One shared set of objects serves every schema still at core_268; only the last removes it.
    if _core_268_installation_count() > 1:
        return
    op.execute(f"DROP TRIGGER IF EXISTS {_DELETE_TRIGGER} ON {_TABLE}")
    op.execute(f"DROP FUNCTION IF EXISTS {_DELETE_FUNCTION}()")
    op.execute(f"ALTER TABLE {_TABLE} DROP CONSTRAINT IF EXISTS {_CONSTRAINT}")
    # Restore the core_267 guard body (core_267 still owns the trigger itself).
    op.execute(_guard_function(_GUARD_HEAD + _STAGE_FORWARD + _GUARD_END))
