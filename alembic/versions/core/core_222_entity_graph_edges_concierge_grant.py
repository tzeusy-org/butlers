"""entity_graph_edges: backfill the missing butler_concierge_rw grant

Revision ID: core_222
Revises: core_221
Create Date: 2026-09-06 00:00:00.000000

bu-8cdl1.8 Slice 3 PR review (RFC 0031): ``core_215_entity_graph_edges.py``'s
``_ALL_BUTLER_ROLES`` was described as "mirrors the current
``_ALL_BUTLER_ROLES`` list from ``core_210_expected_signals.py``", but that
list itself omitted ``butler_concierge_rw`` -- a role added to the roster
after ``core_006_dashboard.py``'s original grant list and never backfilled
onto every cross-butler table since (the same class of gap
``core_221_dashboard_messages_search_index.py`` closed for
``dashboard_conversations``/``dashboard_messages``). Concierge is RFC 0031's
own motivating consumer (its fleet dossier reads the projected graph), so
without this grant the Slice-3 ``entity_graph_walk``/``entity_graph_path``
core tools -- registered on every butler via
``UNIVERSAL_CORE_TOOL_NAMES`` -- would fail closed with a Postgres
permission-denied the first time concierge called either tool.

This migration closes that gap with the same guarded grant pattern
``core_215``/``core_221`` use. ``downgrade()`` only revokes the grant this
migration added.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "core_222"
down_revision = "core_221"
branch_labels = None
depends_on = None

_TABLE_FQN = "public.entity_graph_edges"
_TABLE_PRIVILEGES = "SELECT, INSERT, UPDATE, DELETE"
_ROLE = "butler_concierge_rw"


def _quote_ident(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _grant_if_role_exists(table_fqn: str, privilege: str, role: str) -> None:
    """GRANT privilege ON table TO role only when table and role exist."""
    safe_table_fqn = table_fqn.replace("'", "''")
    safe_role = role.replace("'", "''")
    op.execute(
        f"""
        DO $$
        BEGIN
            IF to_regclass('{safe_table_fqn}') IS NOT NULL
               AND EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{safe_role}')
            THEN
                EXECUTE 'GRANT {privilege} ON TABLE {table_fqn} TO {_quote_ident(role)}';
            END IF;
        EXCEPTION
            WHEN insufficient_privilege THEN NULL;
            WHEN undefined_object THEN NULL;
            WHEN undefined_table THEN NULL;
            WHEN invalid_schema_name THEN NULL;
        END
        $$;
        """
    )


def _grant_schema_usage_if_exists(schema: str, role: str) -> None:
    """GRANT USAGE ON SCHEMA only when schema and role exist."""
    safe_schema = schema.replace("'", "''")
    safe_role = role.replace("'", "''")
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.schemata
                WHERE schema_name = '{safe_schema}'
            ) AND EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{safe_role}')
            THEN
                EXECUTE 'GRANT USAGE ON SCHEMA {_quote_ident(schema)} TO {_quote_ident(role)}';
            END IF;
        EXCEPTION
            WHEN insufficient_privilege THEN NULL;
            WHEN undefined_object THEN NULL;
            WHEN invalid_schema_name THEN NULL;
        END
        $$;
        """
    )


def _revoke_if_role_exists(table_fqn: str, privilege: str, role: str) -> None:
    """REVOKE privilege ON table FROM role only when table and role exist."""
    safe_table_fqn = table_fqn.replace("'", "''")
    safe_role = role.replace("'", "''")
    op.execute(
        f"""
        DO $$
        BEGIN
            IF to_regclass('{safe_table_fqn}') IS NOT NULL
               AND EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{safe_role}')
            THEN
                EXECUTE 'REVOKE {privilege} ON TABLE {table_fqn} FROM {_quote_ident(role)}';
            END IF;
        EXCEPTION
            WHEN insufficient_privilege THEN NULL;
            WHEN undefined_object THEN NULL;
            WHEN undefined_table THEN NULL;
            WHEN invalid_schema_name THEN NULL;
        END
        $$;
        """
    )


def upgrade() -> None:
    _grant_if_role_exists(_TABLE_FQN, _TABLE_PRIVILEGES, _ROLE)
    _grant_schema_usage_if_exists("public", _ROLE)


def downgrade() -> None:
    _revoke_if_role_exists(_TABLE_FQN, _TABLE_PRIVILEGES, _ROLE)
