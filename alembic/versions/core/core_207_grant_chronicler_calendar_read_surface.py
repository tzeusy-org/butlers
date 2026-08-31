"""Converge Chronicler calendar grants after the core calendar tables exist.

Revision ID: core_207
Revises: core_206
Create Date: 2026-08-31 00:00:00.000000

The bootstrap script and ``chronicler_026`` are intentionally guarded because
they may run before a later core migration creates every calendar table.  In
that order, an existence-guarded grant is a no-op and is not replayed when
``core_076`` creates ``calendar_event_entities``.

This post-calendar core revision is the durable convergence point.  It runs
after the current core chain has materialized the calendar surface and grants
only the tables named by RFC 0014.  It does not use default privileges or a
blanket schema grant, so future unrelated tables remain outside Chronicler's
read boundary.
"""

from __future__ import annotations

from alembic import op

revision = "core_207"
down_revision = "core_206"
branch_labels = None
depends_on = None

_BUTLER_SCHEMAS = (
    "education",
    "finance",
    "general",
    "health",
    "home",
    "lifestyle",
    "messenger",
    "qa",
    "relationship",
    "switchboard",
    "travel",
)
_CALENDAR_READ_SURFACE_TABLES = (
    "calendar_event_instances",
    "calendar_events",
    "calendar_sources",
    "calendar_event_entities",
)
_PUBLIC_READ_SURFACE_TABLES = ("google_accounts",)
_ROLE = "butler_chronicler_rw"


def _quote_ident(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _quote_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _grant_if_present(schema: str, table: str) -> None:
    """Grant one allowlisted table privilege when the relation is present."""
    qualified_table = f"{_quote_ident(schema)}.{_quote_ident(table)}"
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM pg_roles
                WHERE rolname = {_quote_literal(_ROLE)}
            ) AND EXISTS (
                SELECT 1
                FROM pg_class AS relation
                JOIN pg_namespace AS namespace
                  ON namespace.oid = relation.relnamespace
                WHERE namespace.nspname = {_quote_literal(schema)}
                  AND relation.relname = {_quote_literal(table)}
                  AND relation.relkind IN ('r', 'p')
            ) THEN
                EXECUTE 'GRANT SELECT ON TABLE {qualified_table} '
                        'TO {_quote_ident(_ROLE)}';
            END IF;
        END
        $$;
        """
    )


def upgrade() -> None:
    for schema in _BUTLER_SCHEMAS:
        for table in _CALENDAR_READ_SURFACE_TABLES:
            _grant_if_present(schema, table)
    for table in _PUBLIC_READ_SURFACE_TABLES:
        _grant_if_present("public", table)


def downgrade() -> None:
    # calendar_event_instances was granted by chronicler_003 and
    # public.google_accounts is part of the shared bootstrap baseline.  Leave
    # those privileges intact; only remove the companion grants this
    # convergence revision adds for deployments that have not applied
    # chronicler_026.
    for schema in _BUTLER_SCHEMAS:
        for table in (
            "calendar_events",
            "calendar_sources",
            "calendar_event_entities",
        ):
            qualified_table = f"{_quote_ident(schema)}.{_quote_ident(table)}"
            op.execute(
                f"""
                DO $$
                BEGIN
                    IF EXISTS (
                        SELECT 1 FROM pg_roles
                        WHERE rolname = {_quote_literal(_ROLE)}
                    ) AND EXISTS (
                        SELECT 1
                        FROM pg_class AS relation
                        JOIN pg_namespace AS namespace
                          ON namespace.oid = relation.relnamespace
                        WHERE namespace.nspname = {_quote_literal(schema)}
                          AND relation.relname = {_quote_literal(table)}
                          AND relation.relkind IN ('r', 'p')
                    ) THEN
                        EXECUTE 'REVOKE SELECT ON TABLE {qualified_table} '
                                'FROM {_quote_ident(_ROLE)}';
                    END IF;
                END
                $$;
                """
            )
