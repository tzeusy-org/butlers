"""qa_patrols: a ``halted`` status and the ``halt_reason`` that names the gate (bu-ejow5p.3).

A patrol whose novel findings were held behind a halting gate (open circuit breaker, no
executor model, full concurrency cap) used to be recorded as ``suppressed``, which the QA
manifesto counts as healthy discovery. ``halted`` says remediation stopped, and ``halt_reason``
(``circuit_breaker``, ``no_model`` or ``concurrency_cap``) says why. Discovery still ran, so a
halted patrol keeps counting as coverage.

The core chain runs once per butler schema over this shared table, so every statement is
idempotent and only the last schema to leave core_367 removes the status and the column
(core_246 pattern). Rows already written stay readable as text after a downgrade: ``halted``
reads back as the status it replaced, ``suppressed``.
"""

from __future__ import annotations

from alembic import op

revision = "core_367"
down_revision = "core_362"
branch_labels = None
depends_on = None

_TABLE = "public.qa_patrols"
_CHECK = "ck_qa_patrols_status"
_LOCK = "SELECT pg_advisory_xact_lock(hashtextextended('butlers:core_367:qa-patrol-halted', 0))"
_OLD = ("running", "clean", "findings_dispatched", "error", "skipped_overlap", "suppressed")
_NEW = (*_OLD, "halted")


#: Catalog-only read (no table lock): true when an earlier schema's run left both the column
#: and the widened check in place. DROP/ADD CONSTRAINT locks and rescans the table even as a
#: no-op, so a later schema reads this under the advisory lock and returns. No percent sign,
#: so a driver never treats it as a parameter marker.
_ALREADY_INSTALLED = f"""
    SELECT EXISTS (
        SELECT 1 FROM pg_attribute
        WHERE attrelid = '{_TABLE}'::regclass AND attname = 'halt_reason' AND NOT attisdropped
    ) AND EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = '{_TABLE}'::regclass AND conname = '{_CHECK}'
          AND position('halted' IN pg_get_constraintdef(oid)) > 0
    )
"""


def _check(statuses: tuple[str, ...]) -> str:
    values = ", ".join(f"'{status}'" for status in statuses)
    return f"ALTER TABLE {_TABLE} ADD CONSTRAINT {_CHECK} CHECK (status IN ({values}))"


def _installation_count() -> int:
    """Count schema-local core chains that still depend on this revision (core_246)."""
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
                    THEN substring(version_num FROM 6)::integer >= 367
                    ELSE false END
            )
            """
        ).scalar()
        count += int(bool(installed))
    return count


def upgrade() -> None:
    op.execute(_LOCK)
    if op.get_bind().exec_driver_sql(_ALREADY_INSTALLED).scalar():
        return
    op.execute(f"ALTER TABLE {_TABLE} ADD COLUMN IF NOT EXISTS halt_reason TEXT")
    op.execute(f"ALTER TABLE {_TABLE} DROP CONSTRAINT IF EXISTS {_CHECK}")
    op.execute(_check(_NEW))


def downgrade() -> None:
    op.execute(_LOCK)
    # One shared table serves every schema still at core_367; only the last one removes it.
    if _installation_count() > 1:
        return
    op.execute(f"UPDATE {_TABLE} SET status = 'suppressed' WHERE status = 'halted'")
    op.execute(f"ALTER TABLE {_TABLE} DROP CONSTRAINT IF EXISTS {_CHECK}")
    op.execute(_check(_OLD))
    op.execute(f"ALTER TABLE {_TABLE} DROP COLUMN IF EXISTS halt_reason")
