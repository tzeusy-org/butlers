"""Grant messenger/travel runtime read access to switchboard tables.

Revision ID: core_143
Revises: core_142
Create Date: 2026-06-22 00:00:00.000000

The email/message-owning butlers (messenger, travel) run a deterministic,
zero-LLM ``calendar_prep_contribution`` job that precomputes the meeting-prep
``message_context`` panel (recent email threads per attendee) from the persisted
inbound-message store ``switchboard.message_inbox``. That precompute is a
scheduled-job read of the message store — NOT a request-time cross-schema read —
but it still needs cross-schema SELECT on the ``switchboard`` schema, which these
runtime roles lack by default.

This mirrors ``core_077`` (which granted the relationship runtime role the same
read access for its interaction-sync job). Default privileges ensure future
switchboard tables created by the migration user remain readable. All statements
are best-effort: missing roles/tables/schema are tolerated so the migration is
safe on partially-provisioned databases.
"""

from __future__ import annotations

from alembic import op

revision = "core_143"
down_revision = "core_142"
branch_labels = None
depends_on = None

_ROLE_NAMES = ("butler_messenger_rw", "butler_travel_rw")
_SWITCHBOARD_SCHEMA = "switchboard"


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
            WHEN invalid_schema_name THEN NULL;
        END
        $$;
        """
    )


def upgrade() -> None:
    quoted_schema = _quote_ident(_SWITCHBOARD_SCHEMA)
    for role_name in _ROLE_NAMES:
        quoted_role = _quote_ident(role_name)
        _execute_best_effort(
            f"GRANT USAGE ON SCHEMA {quoted_schema} TO {quoted_role}",
            role_name=role_name,
        )
        _execute_best_effort(
            f"GRANT SELECT ON ALL TABLES IN SCHEMA {quoted_schema} TO {quoted_role}",
            role_name=role_name,
        )
        _execute_best_effort(
            f"ALTER DEFAULT PRIVILEGES IN SCHEMA {quoted_schema} "
            f"GRANT SELECT ON TABLES TO {quoted_role}",
            role_name=role_name,
        )


def downgrade() -> None:
    quoted_schema = _quote_ident(_SWITCHBOARD_SCHEMA)
    for role_name in _ROLE_NAMES:
        quoted_role = _quote_ident(role_name)
        _execute_best_effort(
            f"ALTER DEFAULT PRIVILEGES IN SCHEMA {quoted_schema} "
            f"REVOKE SELECT ON TABLES FROM {quoted_role}",
            role_name=role_name,
        )
        _execute_best_effort(
            f"REVOKE SELECT ON ALL TABLES IN SCHEMA {quoted_schema} FROM {quoted_role}",
            role_name=role_name,
        )
        _execute_best_effort(
            f"REVOKE USAGE ON SCHEMA {quoted_schema} FROM {quoted_role}",
            role_name=role_name,
        )
