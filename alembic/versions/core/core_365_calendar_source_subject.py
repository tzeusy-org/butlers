"""calendar_sources: whose calendar a household source is (bu-5ii4gj.1 S3, REQ-module-calendar-038).

Revision ID: core_365
Revises: core_360
Create Date: 2026-11-23 00:00:00.000000

Adds ``role_subject_entity_id UUID NULL`` to the schema's own ``calendar_sources``: a reference to
``public.entities`` (``ON DELETE SET NULL``) naming the person whose calendar a
``household_member``, ``other_person`` or ``team`` source is. A check keeps it null for every other
role. The owner sets it with the role, so it only ever accompanies ``owner_confirmed``; discovery
never writes it.

The core chain runs once per butler schema and this revision only touches the current schema's
table. A run that finds the column, its foreign key and its check already in place is a
catalog-only no-op; a column present with another shape stops the migration. Downgrade keeps the
column and its values (evidence-preserving), as core_360 does.
"""

from __future__ import annotations

from sqlalchemy import text

from alembic import op

revision = "core_365"
down_revision = "core_360"
branch_labels = None
depends_on = None

_FK = "calendar_sources_role_subject_entity_fk"
_CHECK = "calendar_sources_role_subject_role_check"
_SUBJECT_ROLES = "'household_member', 'other_person', 'team'"


def _column() -> tuple[str, str] | None:
    row = (
        op.get_bind()
        .execute(
            text(
                "SELECT data_type, is_nullable FROM information_schema.columns "
                "WHERE table_schema = current_schema() AND table_name = 'calendar_sources' "
                "AND column_name = 'role_subject_entity_id'"
            )
        )
        .fetchone()
    )
    return None if row is None else (row[0], row[1])


def _constraints() -> set[str]:
    rows = (
        op.get_bind()
        .execute(
            text(
                "SELECT conname FROM pg_constraint "
                "WHERE conrelid = to_regclass('calendar_sources') AND conname IN (:fk, :ck)"
            ),
            {"fk": _FK, "ck": _CHECK},
        )
        .fetchall()
    )
    return {row[0] for row in rows}


def upgrade() -> None:
    if (
        not op.get_bind()
        .execute(text("SELECT to_regclass('calendar_sources') IS NOT NULL"))
        .scalar_one()
    ):
        return
    column = _column()
    if column is not None and column != ("uuid", "YES"):
        raise RuntimeError(
            f"calendar_sources.role_subject_entity_id exists with an unexpected shape {column!r}; "
            "expected ('uuid', 'YES')"
        )
    present = _constraints()
    if column is not None and present == {_FK, _CHECK}:
        return
    clauses = []
    if column is None:
        clauses.append("ADD COLUMN role_subject_entity_id UUID")
    if _FK not in present:
        clauses.append(
            f"ADD CONSTRAINT {_FK} FOREIGN KEY (role_subject_entity_id) "
            "REFERENCES public.entities(id) ON DELETE SET NULL"
        )
    if _CHECK not in present:
        clauses.append(
            f"ADD CONSTRAINT {_CHECK} CHECK "
            f"(role_subject_entity_id IS NULL OR time_role IN ({_SUBJECT_ROLES}))"
        )
    op.execute("ALTER TABLE calendar_sources " + ", ".join(clauses))


def downgrade() -> None:
    # Evidence-preserving: the column and its values stay (see the module docstring).
    pass
