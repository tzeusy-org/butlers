"""Create general.v_briefing_contributions cross-schema view.

Revision ID: core_063
Revises: core_062
Create Date: 2026-04-09 00:00:00.000000

Creates a read-only SQL view ``general.v_briefing_contributions`` that unions
briefing contribution rows from each specialist butler's ``state`` table.
This is a sanctioned exception to schema isolation (RFC 0006) — the view
is read-only, uses an explicit ``butler`` discriminator column, and grants
are migration-based for auditability.

The view is consumed by the ``collect_briefing_contributions`` deterministic
job on the General butler, which aggregates specialist contributions into a
combined daily briefing payload.

Columns exposed:
  butler  TEXT   — string literal identifying the source schema
  key     TEXT   — state key (filtered to ``briefing/daily/%``)
  value   JSONB  — contribution payload
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "core_063"
down_revision: str = "core_062"
branch_labels = None
depends_on = None

# Specialist schemas whose state tables contribute to the briefing view.
# Must match SPECIALIST_BUTLERS in src/butlers/jobs/briefing.py.
_SPECIALIST_SCHEMAS: tuple[str, ...] = (
    "education",
    "finance",
    "health",
    "home",
    "lifestyle",
    "relationship",
    "travel",
)

_GENERAL_ROLE = "butler_general_rw"


def _ensure_role_exists(role_name: str) -> None:
    """Create role if it doesn't exist (best-effort, matches core_001 pattern)."""
    op.execute(f"""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role_name}') THEN
                EXECUTE 'CREATE ROLE {role_name} LOGIN';
            END IF;
        EXCEPTION
            WHEN insufficient_privilege THEN
                NULL;
        END
        $$;
    """)


def _schema_exists(schema_name: str) -> bool:
    bind = op.get_bind()
    return (
        bind.execute(
            sa.text("SELECT 1 FROM information_schema.schemata WHERE schema_name = :schema"),
            {"schema": schema_name},
        ).scalar()
        is not None
    )


def _state_table_exists(schema_name: str) -> bool:
    bind = op.get_bind()
    relname = f"{schema_name}.state"
    return (
        bind.execute(sa.text("SELECT to_regclass(:relname)"), {"relname": relname}).scalar()
        is not None
    )


def upgrade() -> None:
    # -- Step 0: Ensure the general role exists (may be missing on dev) --------
    _ensure_role_exists(_GENERAL_ROLE)

    available_schemas = tuple(
        schema for schema in _SPECIALIST_SCHEMAS if _state_table_exists(schema)
    )

    # -- Step 1: Grant SELECT on each specialist schema's state table ---------
    for schema in available_schemas:
        op.execute(f"""
            DO $$
            BEGIN
                EXECUTE 'GRANT SELECT ON {schema}.state TO {_GENERAL_ROLE}';
            EXCEPTION
                WHEN undefined_object THEN
                    NULL;
            END
            $$;
        """)

    # -- Step 2: Create the cross-schema view ---------------------------------
    if not _schema_exists("general"):
        return

    if available_schemas:
        union_terms = "\n    UNION ALL\n    ".join(
            f"SELECT '{schema}' AS butler, key, value "
            f"FROM {schema}.state "
            f"WHERE key LIKE 'briefing/daily/%'"
            for schema in available_schemas
        )
    else:
        union_terms = (
            "SELECT NULL::text AS butler, NULL::text AS key, NULL::jsonb AS value "
            "WHERE FALSE"
        )
    op.execute(f"""
        CREATE OR REPLACE VIEW general.v_briefing_contributions AS
        {union_terms}
    """)


def downgrade() -> None:
    # -- Step 1: Drop the view ------------------------------------------------
    if _schema_exists("general"):
        op.execute("DROP VIEW IF EXISTS general.v_briefing_contributions")

    # -- Step 2: Revoke cross-schema grants -----------------------------------
    for schema in _SPECIALIST_SCHEMAS:
        if not _state_table_exists(schema):
            continue
        op.execute(f"""
            DO $$
            BEGIN
                EXECUTE 'REVOKE SELECT ON {schema}.state FROM {_GENERAL_ROLE}';
            EXCEPTION
                WHEN undefined_object THEN
                    NULL;
            END
            $$;
        """)
