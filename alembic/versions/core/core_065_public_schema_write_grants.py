"""public schema targeted write grants for SET ROLE enforcement

Revision ID: core_065
Revises: core_064
Create Date: 2026-04-09 00:00:00.000000

Grants targeted INSERT/UPDATE/DELETE on specific public tables to all
butler runtime roles and the connector_writer role, enabling SET ROLE
enforcement without breaking public table writes.
"""

from __future__ import annotations

from alembic import op

revision = "core_065"
down_revision = "core_064"
branch_labels = None
depends_on = None

_ROLE_SCHEMAS = (
    "education",
    "finance",
    "general",
    "health",
    "home",
    "lifestyle",
    "messenger",
    "relationship",
    "switchboard",
    "travel",
)
_RUNTIME_ROLES = [f"butler_{schema}_rw" for schema in _ROLE_SCHEMAS]
_CONNECTOR_ROLE = "connector_writer"
_ALL_ROLES = [*_RUNTIME_ROLES, _CONNECTOR_ROLE]

# (table_name, granted_operations)
_PUBLIC_WRITE_GRANTS = [
    ("entities", "INSERT, UPDATE, DELETE"),
    ("contacts", "INSERT, UPDATE"),
    ("contact_info", "INSERT, UPDATE, DELETE"),
    ("entity_info", "INSERT, UPDATE, DELETE"),
    ("google_accounts", "INSERT, UPDATE"),
    ("steam_accounts", "INSERT, UPDATE, DELETE"),
    ("user_context", "INSERT, UPDATE"),
    ("model_round_robin_counters", "INSERT, UPDATE"),
    ("token_usage_ledger", "INSERT"),
    ("ingestion_events", "INSERT, UPDATE, DELETE"),
    ("healing_attempts", "INSERT, UPDATE"),
    ("qa_dismissals", "INSERT, UPDATE, DELETE"),
    ("qa_findings", "INSERT, UPDATE"),
    ("qa_repo_config", "UPDATE"),
    ("qa_patrols", "INSERT, UPDATE"),
    ("memory_catalog", "INSERT, UPDATE"),
    ("facts", "INSERT, UPDATE"),
    ("insight_candidates", "INSERT, UPDATE, DELETE"),
    ("insight_cooldowns", "INSERT, DELETE"),
    ("insight_engagement", "INSERT, UPDATE, DELETE"),
    ("insight_settings", "INSERT, UPDATE"),
]


def _quote_ident(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _quote_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _execute_best_effort(statement: str, *, role_name: str | None = None) -> None:
    condition = "TRUE"
    if role_name is not None:
        condition = f"EXISTS (SELECT 1 FROM pg_roles WHERE rolname = {_quote_literal(role_name)})"
    op.execute(
        f"""
        DO $$
        BEGIN
            IF {condition} THEN
                EXECUTE {_quote_literal(statement)};
            END IF;
        EXCEPTION
            WHEN insufficient_privilege THEN NULL;
            WHEN undefined_object THEN NULL;
            WHEN undefined_table THEN NULL;
        END
        $$;
        """
    )


def _grant_role_membership() -> None:
    """Grant SET ROLE capability: connecting user must be member of each role."""
    for role_name in _ALL_ROLES:
        quoted_role = _quote_ident(role_name)
        _execute_best_effort(
            f"GRANT {quoted_role} TO CURRENT_USER",
            role_name=role_name,
        )


def upgrade() -> None:
    # Step 1: Grant public table write permissions
    for role_name in _ALL_ROLES:
        for table_name, operations in _PUBLIC_WRITE_GRANTS:
            quoted_role = _quote_ident(role_name)
            quoted_table = f"public.{_quote_ident(table_name)}"
            _execute_best_effort(
                f"GRANT {operations} ON {quoted_table} TO {quoted_role}",
                role_name=role_name,
            )
    # Step 2: Grant role membership so SET ROLE works
    _grant_role_membership()


def downgrade() -> None:
    # Step 1: Revoke role membership
    for role_name in _ALL_ROLES:
        quoted_role = _quote_ident(role_name)
        _execute_best_effort(
            f"REVOKE {quoted_role} FROM CURRENT_USER",
            role_name=role_name,
        )
    # Step 2: Revoke public table write permissions
    for role_name in _ALL_ROLES:
        for table_name, operations in _PUBLIC_WRITE_GRANTS:
            quoted_role = _quote_ident(role_name)
            quoted_table = f"public.{_quote_ident(table_name)}"
            _execute_best_effort(
                f"REVOKE {operations} ON {quoted_table} FROM {quoted_role}",
                role_name=role_name,
            )
