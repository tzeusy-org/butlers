"""calendar_sources: whose calendar a household source is (bu-5ii4gj.1 S3, REQ-module-calendar-038).

Revision ID: core_365
Revises: core_362
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
down_revision = "core_362"
branch_labels = None
depends_on = None

# The foreign key takes SHARE ROW EXCLUSIVE on the shared public.entities in every schema's run, so
# the whole upgrade (catalog read and DDL) is serialized across schemas (core_243, core_318).
_LOCK = (
    "SELECT pg_advisory_xact_lock(hashtextextended('butlers:core_365:calendar-source-subject', 0))"
)
_PINNED_FUNCTION = "public.calendar_role_subject_entity_pinned(uuid)"
# An owner-confirmed household subject must keep its entity, so the Relationship enrichment reap
# must never archive a placeholder a calendar source's role names. The reap's role cannot read
# other butlers' ``calendar_sources`` (one table per schema), so this definer answers one boolean
# over every schema that carries the column (the vendor_door_entity_pinned precedent, core_336).
_PINNED_DDL = """
CREATE OR REPLACE FUNCTION public.calendar_role_subject_entity_pinned(p_entity_id uuid)
RETURNS boolean
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
DECLARE
    _schema name;
    _hit boolean;
BEGIN
    FOR _schema IN
        SELECT n.nspname
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        JOIN pg_attribute a ON a.attrelid = c.oid AND a.attname = 'role_subject_entity_id'
                           AND NOT a.attisdropped
        WHERE c.relname = 'calendar_sources' AND c.relkind IN ('r', 'p')
    LOOP
        EXECUTE format(
            'SELECT EXISTS (SELECT 1 FROM %I.calendar_sources WHERE role_subject_entity_id = $1)',
            _schema
        ) INTO _hit USING p_entity_id;
        IF _hit THEN
            RETURN true;
        END IF;
    END LOOP;
    RETURN false;
END $$
"""
_PINNED_GRANTS = (
    "REVOKE ALL PRIVILEGES ON FUNCTION public.calendar_role_subject_entity_pinned(uuid) "
    "FROM PUBLIC",
    """
    DO $$
    BEGIN
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'butler_relationship_rw') THEN
            GRANT EXECUTE ON FUNCTION public.calendar_role_subject_entity_pinned(uuid)
                TO butler_relationship_rw;
        END IF;
    END $$
    """,
)
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
    op.execute(_LOCK)
    # Idempotent and table-lock-free, so every schema's run may replace it.
    op.execute(_PINNED_DDL)
    for statement in _PINNED_GRANTS:
        op.execute(statement)
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
