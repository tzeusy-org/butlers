"""briefing_contributions_view

Revision ID: gen_004
Revises: gen_003
Create Date: 2026-03-25 00:00:00.000000

Creates the ``general.v_briefing_contributions`` view that aggregates specialist
briefing contribution state entries across all specialist schemas.

Background:
    The General butler's ``collect_briefing_contributions`` deterministic job
    must read briefing contributions written by each specialist butler
    (education, finance, health, home, relationship, travel) from their own
    ``state`` tables.  Normal schema isolation (RFC 0006) prevents cross-schema
    reads, but the briefing view is a sanctioned exception: it is read-only,
    uses an explicit ``butler`` source column for auditability, and access is
    granted exclusively through this migration.

Design:
    - Creates the view ``general.v_briefing_contributions`` in the general schema,
      UNIONing ``butler``, ``key``, ``value`` from each specialist's ``state``
      table, filtered to ``key LIKE 'briefing/daily/%'``.
    - Each UNION term uses a string literal for the ``butler`` column
      (e.g. ``'health' AS butler``) so source identity is unambiguous.
    - Grants SELECT on each specialist schema's ``state`` table to the General
      butler's database role (``butler_general_rw``).
    - Grants USAGE on each specialist schema to ``butler_general_rw`` so the
      view's cross-schema queries execute without permission errors.
    - All grants are conditional on role and table existence for safe migration
      across environments where roles may not be pre-created.

Reversible:
    Downgrade drops the view and revokes the SELECT grants and schema USAGE
    from ``butler_general_rw``.

Reference:
    openspec/specs/cross-butler-briefing-aggregation/spec.md
    openspec/changes/cross-butler-daily-briefing/tasks.md § 1 (tasks 1.1–1.4)
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "gen_004"
down_revision = "gen_003"
branch_labels = None
depends_on = None

# The General butler's DB role that needs cross-schema SELECT.
_GENERAL_ROLE = "butler_general_rw"

# Specialist schemas whose state tables the view UNIONs.
_SPECIALIST_SCHEMAS: tuple[str, ...] = (
    "education",
    "finance",
    "health",
    "home",
    "relationship",
    "travel",
)


def _quote_ident(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _quote_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _grant_select_on_state_if_exists(schema: str, role: str) -> None:
    """GRANT SELECT on <schema>.state TO role, only when both table and role exist."""
    table_fqn = f"{schema}.state"
    quoted_table = f"{_quote_ident(schema)}.{_quote_ident('state')}"
    quoted_role = _quote_ident(role)
    op.execute(
        f"""
        DO $$
        BEGIN
            IF to_regclass({_quote_literal(table_fqn)}) IS NOT NULL
               AND EXISTS (SELECT 1 FROM pg_roles WHERE rolname = {_quote_literal(role)})
            THEN
                EXECUTE 'GRANT SELECT ON TABLE {quoted_table} TO {quoted_role}';
            END IF;
        EXCEPTION
            WHEN insufficient_privilege THEN NULL;
            WHEN undefined_object      THEN NULL;
            WHEN undefined_table       THEN NULL;
            WHEN invalid_schema_name   THEN NULL;
        END
        $$;
        """
    )


def _grant_schema_usage_if_exists(schema: str, role: str) -> None:
    """GRANT USAGE ON SCHEMA <schema> TO role, only when both exist."""
    quoted_schema = _quote_ident(schema)
    quoted_role = _quote_ident(role)
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.schemata
                WHERE schema_name = {_quote_literal(schema)}
            ) AND EXISTS (SELECT 1 FROM pg_roles WHERE rolname = {_quote_literal(role)})
            THEN
                EXECUTE 'GRANT USAGE ON SCHEMA {quoted_schema} TO {quoted_role}';
            END IF;
        EXCEPTION
            WHEN insufficient_privilege THEN NULL;
            WHEN undefined_object      THEN NULL;
            WHEN invalid_schema_name   THEN NULL;
        END
        $$;
        """
    )


def _revoke_select_on_state_if_exists(schema: str, role: str) -> None:
    """REVOKE SELECT on <schema>.state FROM role, only when both exist."""
    table_fqn = f"{schema}.state"
    quoted_table = f"{_quote_ident(schema)}.{_quote_ident('state')}"
    quoted_role = _quote_ident(role)
    op.execute(
        f"""
        DO $$
        BEGIN
            IF to_regclass({_quote_literal(table_fqn)}) IS NOT NULL
               AND EXISTS (SELECT 1 FROM pg_roles WHERE rolname = {_quote_literal(role)})
            THEN
                EXECUTE 'REVOKE SELECT ON TABLE {quoted_table} FROM {quoted_role}';
            END IF;
        EXCEPTION
            WHEN insufficient_privilege THEN NULL;
            WHEN undefined_object      THEN NULL;
            WHEN undefined_table       THEN NULL;
            WHEN invalid_schema_name   THEN NULL;
        END
        $$;
        """
    )


def _revoke_schema_usage_if_exists(schema: str, role: str) -> None:
    """REVOKE USAGE ON SCHEMA <schema> FROM role, only when both exist."""
    quoted_schema = _quote_ident(schema)
    quoted_role = _quote_ident(role)
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.schemata
                WHERE schema_name = {_quote_literal(schema)}
            ) AND EXISTS (SELECT 1 FROM pg_roles WHERE rolname = {_quote_literal(role)})
            THEN
                EXECUTE 'REVOKE USAGE ON SCHEMA {quoted_schema} FROM {quoted_role}';
            END IF;
        EXCEPTION
            WHEN insufficient_privilege THEN NULL;
            WHEN undefined_object      THEN NULL;
            WHEN invalid_schema_name   THEN NULL;
        END
        $$;
        """
    )


def upgrade() -> None:
    # -------------------------------------------------------------------------
    # 1. Grant SELECT on each specialist schema's state table to the General
    #    butler role, and USAGE on each specialist schema.
    #    These grants are required for the view to query across schemas.
    #    All grants are conditional on role + table existence for safety.
    # -------------------------------------------------------------------------
    for schema in _SPECIALIST_SCHEMAS:
        _grant_schema_usage_if_exists(schema, _GENERAL_ROLE)
        _grant_select_on_state_if_exists(schema, _GENERAL_ROLE)

    # -------------------------------------------------------------------------
    # 2. Build the view SQL by UNIONing state entries from each specialist.
    #    Each UNION term adds an explicit ``butler`` string literal column so
    #    the aggregation job can identify the contribution source without
    #    parsing the JSON payload.
    #    The view is created in the ``general`` schema (search_path in Alembic
    #    env is set to the current butler schema; use schema-qualified DDL).
    # -------------------------------------------------------------------------
    union_terms = "\n    UNION ALL\n    ".join(
        f"SELECT {_quote_literal(schema)} AS butler, {_quote_ident('key')}, {_quote_ident('value')} "
        f"FROM {_quote_ident(schema)}.{_quote_ident('state')} "
        f"WHERE {_quote_ident('key')} LIKE 'briefing/daily/%'"
        for schema in _SPECIALIST_SCHEMAS
    )

    op.execute(
        f"""
        CREATE OR REPLACE VIEW general.v_briefing_contributions AS
            {union_terms}
        """
    )


def downgrade() -> None:
    # -------------------------------------------------------------------------
    # 2. Drop the view.
    # -------------------------------------------------------------------------
    op.execute("DROP VIEW IF EXISTS general.v_briefing_contributions")

    # -------------------------------------------------------------------------
    # 1. Revoke the cross-schema grants issued during upgrade.
    # -------------------------------------------------------------------------
    for schema in _SPECIALIST_SCHEMAS:
        _revoke_select_on_state_if_exists(schema, _GENERAL_ROLE)
        _revoke_schema_usage_if_exists(schema, _GENERAL_ROLE)
