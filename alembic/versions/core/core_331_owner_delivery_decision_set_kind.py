"""Owner-delivery subjects may name a decision set (bu-jv1moa.27 P2).

Revision ID: core_331
Revises: core_330
Create Date: 2026-11-14 00:00:00.000000

``public.owner_delivery_subjects.kind`` gains ``decision_set``: one subject per
``(set id, version)`` of the RFC 0038 M7 decision-set producer, so a Telegram card for a set is
bound by the same single-use ``od1`` tokens as an approval card. This only widens the existing
CHECK on an existing table: no new table, no grant or policy change, so no REVOKE, statement
guard or schema-reach entry is involved (those apply to new tables).

The core chain runs once per butler schema over this shared table. The widening is idempotent
(drop and re-add the one named constraint under an advisory lock), and only the last schema
still at core_331 narrows it again. The downgrade refuses while any ``decision_set`` subject
exists, so no row is ever left violating the older constraint.
"""

from __future__ import annotations

from alembic import op

revision = "core_331"
down_revision = "core_330"
branch_labels = None
depends_on = None

_LOCK = "SELECT pg_advisory_xact_lock(hashtextextended('butlers:core_331:subject-kind', 0))"
_CONSTRAINT = "owner_delivery_subjects_kind_check"
_ROLLBACK_REFUSAL = "core_331 downgrade refused: decision_set owner-delivery subjects exist"


def _set_kinds(kinds: tuple[str, ...]) -> None:
    listed = ", ".join(f"'{kind}'" for kind in kinds)
    op.execute(
        f"""
        DO $$
        BEGIN
            IF to_regclass('public.owner_delivery_subjects') IS NOT NULL THEN
                ALTER TABLE public.owner_delivery_subjects DROP CONSTRAINT IF EXISTS {_CONSTRAINT};
                ALTER TABLE public.owner_delivery_subjects
                    ADD CONSTRAINT {_CONSTRAINT} CHECK (kind IN ({listed}));
            END IF;
        END
        $$
        """
    )


def _installation_count() -> int:
    """Count schema-local core chains that still depend on the widened constraint."""
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
                    THEN substring(version_num FROM 6)::integer >= 331
                    ELSE false END
            )
            """
        ).scalar()
        count += int(bool(installed))
    return count


def upgrade() -> None:
    op.execute(_LOCK)
    _set_kinds(("approval", "goal_answer", "pending_ask", "decision_set"))


def downgrade() -> None:
    op.execute(_LOCK)
    if _installation_count() > 1:
        return
    op.execute(
        f"""
        DO $$
        BEGIN
            IF to_regclass('public.owner_delivery_subjects') IS NOT NULL
               AND EXISTS (
                   SELECT 1 FROM public.owner_delivery_subjects WHERE kind = 'decision_set'
               ) THEN
                RAISE EXCEPTION '{_ROLLBACK_REFUSAL}';
            END IF;
        END
        $$
        """
    )
    _set_kinds(("approval", "goal_answer", "pending_ask"))
