"""Require an explicit result for ambiguous QA publication operations (bu-mc01y5.15).

Revision ID: core_290
Revises: core_289
Create Date: 2026-11-01 00:00:00.000000

REQ-qa-publication-authority-002 / -006. Historical core_268 allowed an ambiguous
NULL result that the writer never emits. Canonicalize only that result, through
its existing QA write role under forced RLS, without changing bindings, stages,
reconciliation or lineage holds. Then tighten the existing named CHECK. The
transactional table lock covers both the backfill and constraint replacement.

Only the last schema to leave this revision restores the historical CHECK.
Rollback retains canonicalized results: erasing them would violate set-once
semantics and is unnecessary for compatibility. All core_267/core_268 guards,
policies and grants remain intact in both directions.
"""

from __future__ import annotations

from alembic import context, op

revision = "core_290"
down_revision = "core_289"
branch_labels = None
depends_on = None

_TABLE = "public.qa_publication_operations"
_CONSTRAINT = "ck_qa_publication_operations_stage_result"
# Serialize with the migration that owns the shared CHECK and guard functions.
_LOCK = "SELECT pg_advisory_xact_lock(hashtextextended('butlers:core_268:qa-publication', 0))"


def _replace_check(*, strict: bool) -> None:
    ambiguous = "result = 'ambiguous'" if strict else "(result IS NULL OR result = 'ambiguous')"
    op.execute(f"ALTER TABLE {_TABLE} DROP CONSTRAINT {_CONSTRAINT}")
    op.execute(
        f"""
        ALTER TABLE {_TABLE} ADD CONSTRAINT {_CONSTRAINT} CHECK (
            COALESCE(
                (stage IN ('bound', 'egress_started') AND result IS NULL)
                OR (stage = 'succeeded' AND result = 'success')
                OR (stage = 'failed' AND result = 'failed')
                OR (stage = 'ambiguous' AND {ambiguous})
                OR (stage = 'reconciled' AND (result IS NULL OR result = 'ambiguous')),
                false
            )
        )
        """
    )


def _installed_schema_count() -> int:
    """Use actual ancestry, including descendants with lower numeric revision IDs."""
    script = context.script
    descendants: set[str] = set()
    pending = [revision]
    while pending:
        current = pending.pop()
        if current in descendants:
            continue
        descendants.add(current)
        pending.extend(script.get_revision(current).nextrev)

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
    for schema in list(schemas):
        versions = set(
            bind.exec_driver_sql(f"SELECT version_num FROM {quote(schema)}.alembic_version")
            .scalars()
            .all()
        )
        count += int(bool(versions & descendants))
    return count


def upgrade() -> None:
    op.execute(_LOCK)
    op.execute(f"LOCK TABLE {_TABLE} IN ACCESS EXCLUSIVE MODE")
    bind = op.get_bind()
    if bind.exec_driver_sql(
        f"SELECT EXISTS (SELECT 1 FROM {_TABLE} WHERE stage = 'ambiguous' AND result IS NULL)"
    ).scalar_one():
        # The migration owner is subject to FORCE RLS. Bootstrap already grants SET
        # membership in the QA role; do not disable RLS or bypass the write policy.
        caller = bind.exec_driver_sql("SELECT current_user").scalar_one()
        quote = bind.dialect.identifier_preparer.quote
        op.execute("SET LOCAL ROLE butler_qa_rw")
        op.execute(
            f"UPDATE {_TABLE} SET result = 'ambiguous' WHERE stage = 'ambiguous' AND result IS NULL"
        )
        op.execute(f"SET LOCAL ROLE {quote(caller)}")
    _replace_check(strict=True)


def downgrade() -> None:
    op.execute(_LOCK)
    op.execute(f"LOCK TABLE {_TABLE} IN ACCESS EXCLUSIVE MODE")
    if _installed_schema_count() > 1:
        return
    _replace_check(strict=False)
