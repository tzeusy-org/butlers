"""Limit relationship, messenger and travel Switchboard reads to message_inbox (bu-svqpzo.13).

Revision ID: core_283
Revises: core_282
Create Date: 2026-10-18 00:00:00.000000

core_077 (relationship) and core_143 (messenger, travel) granted SELECT on every table in the
``switchboard`` schema, plus default privileges for every future one.  That reached
``switchboard.butler_secrets`` (plaintext ``secret_value``) and would reach any table added
later (RFC 0038 puts the goal ledger in this schema).  The only Switchboard table these
roles read is ``switchboard.message_inbox`` (relationship ``interaction_sync`` and
``calendar_prep_contribution``; the messenger and travel ``calendar_prep_contribution``).

This revision revokes the schema-wide SELECT and the default privileges and grants SELECT on
``switchboard.message_inbox`` only.  ``USAGE`` on the schema stays.  On a fresh database the
table does not exist yet when the core chain runs, so ``sw_054`` grants it once the Switchboard
chain has created it.

The core chain runs once per butler schema over shared roles, and every statement is
idempotent.  Only a role or schema that is absent is skipped.  The revokes are plain REVOKE
statements, which PostgreSQL only warns about when the grantor holds no privilege to remove, so
a revoke that cannot take effect does not fail the migration; the real-PG tests read the
resulting grants back instead.  Downgrade restores the core_077 and
core_143 grants exactly.
"""

from __future__ import annotations

from alembic import op

revision = "core_283"
down_revision = "core_282"
branch_labels = None
depends_on = None

_ROLES = ("butler_relationship_rw", "butler_messenger_rw", "butler_travel_rw")
_SCHEMA = '"switchboard"'
_INBOX = '"switchboard"."message_inbox"'


def _quote_ident(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _quote_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _run(statement: str, role: str, *tolerate: str) -> None:
    """Run *statement* when *role* exists, skipping only the named absent-object cases."""
    handlers = "".join(f"WHEN {condition} THEN NULL;\n" for condition in tolerate)
    exception = f"EXCEPTION\n{handlers}" if handlers else ""
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = {_quote_literal(role)}) THEN
                EXECUTE {_quote_literal(statement)};
            END IF;
        {exception}END
        $$;
        """
    )


def upgrade() -> None:
    for role in _ROLES:
        who = _quote_ident(role)
        _run(
            f"ALTER DEFAULT PRIVILEGES IN SCHEMA {_SCHEMA} REVOKE SELECT ON TABLES FROM {who}",
            role,
            "invalid_schema_name",
        )
        _run(
            f"REVOKE SELECT ON ALL TABLES IN SCHEMA {_SCHEMA} FROM {who}",
            role,
            "invalid_schema_name",
        )
        _run(
            f"GRANT SELECT ON TABLE {_INBOX} TO {who}",
            role,
            "undefined_table",
            "invalid_schema_name",
        )


def downgrade() -> None:
    for role in _ROLES:
        who = _quote_ident(role)
        _run(
            f"GRANT SELECT ON ALL TABLES IN SCHEMA {_SCHEMA} TO {who}",
            role,
            "invalid_schema_name",
        )
        _run(
            f"ALTER DEFAULT PRIVILEGES IN SCHEMA {_SCHEMA} GRANT SELECT ON TABLES TO {who}",
            role,
            "invalid_schema_name",
        )
