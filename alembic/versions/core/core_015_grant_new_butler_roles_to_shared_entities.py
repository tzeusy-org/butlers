"""grant_new_butler_roles_to_shared_entities: add missing butler roles to shared.entities GRANT

Revision ID: core_015
Revises: core_014
Create Date: 2026-03-05 00:00:00.000000

core_014 added shared.entities with GRANTs to the five original butler roles
(general, health, messenger, relationship, switchboard).  Butlers added after
that migration — education, finance, home, and travel — were missing from
_ALL_BUTLER_ROLES in core_014 and therefore never received SELECT/INSERT/UPDATE/
DELETE on shared.entities or USAGE on the shared schema.

This migration grants the missing access idempotently (the helper guards on
table existence and role existence, so it is safe to run against databases that
do not yet have the education/finance/home/travel schemas or roles).
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "core_015"
down_revision = "core_014"
branch_labels = None
depends_on = None

# Butler roles that were missing from the core_014 GRANT.
_NEW_BUTLER_ROLES = (
    "butler_education_rw",
    "butler_finance_rw",
    "butler_home_rw",
    "butler_travel_rw",
)

_ENTITIES_TABLE_PRIVILEGES = "SELECT, INSERT, UPDATE, DELETE"


def _quote_ident(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _grant_if_table_exists(table_fqn: str, privilege: str, role: str) -> None:
    """GRANT privilege ON table TO role only when table and role exist."""
    op.execute(
        f"""
        DO $$
        BEGIN
            IF to_regclass('{table_fqn}') IS NOT NULL
               AND EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}')
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
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.schemata
                WHERE schema_name = '{schema}'
            ) AND EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}')
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


def _revoke_if_table_exists(table_fqn: str, privilege: str, role: str) -> None:
    """REVOKE privilege ON table FROM role only when table and role exist."""
    op.execute(
        f"""
        DO $$
        BEGIN
            IF to_regclass('{table_fqn}') IS NOT NULL
               AND EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}')
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
    for role in _NEW_BUTLER_ROLES:
        _grant_if_table_exists("shared.entities", _ENTITIES_TABLE_PRIVILEGES, role)
        _grant_schema_usage_if_exists("shared", role)


def downgrade() -> None:
    for role in _NEW_BUTLER_ROLES:
        _revoke_if_table_exists("shared.entities", _ENTITIES_TABLE_PRIVILEGES, role)
