"""grant Chronicler read access to the calendar projection surface

Revision ID: chronicler_026
Revises: chronicler_025
Create Date: 2026-08-31 00:00:00.000000

``CalendarCompletedAdapter`` reads the completed-instance table together with
its ``calendar_events`` and ``calendar_sources`` join companions.  When the
participant join table exists, it also reads ``calendar_event_entities``.
It also performs optional owner-entity resolution through
``public.google_accounts``.
Migration ``chronicler_003`` granted only ``calendar_event_instances``, so a
restricted ``butler_chronicler_rw`` role could see the first table but not the
complete projection surface.  Grant each table explicitly and only when it is
present; the optional calendar module remains safe on deployments without it.

The grants are intentionally separate from the broad own-schema permissions
used by normal butler roles.  Chronicler's cross-schema role remains
least-privilege and migration-tracked.
"""

from __future__ import annotations

from alembic import op

revision = "chronicler_026"
down_revision = "chronicler_025"
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
_NEW_CALENDAR_GRANTS = (
    "calendar_events",
    "calendar_sources",
    "calendar_event_entities",
)
_PUBLIC_READ_SURFACE_TABLES = ("google_accounts",)
_ROLE = "butler_chronicler_rw"


def _q(name: str) -> str:
    """Double-quote a PostgreSQL identifier from the static allowlist."""
    if not name.replace("_", "").isalnum():
        raise ValueError(f"Unsafe identifier: {name!r}")
    return f'"{name}"'


def _lit(value: str) -> str:
    """Produce a single-quoted SQL literal from the static allowlist."""
    if not all(character.isalnum() or character == "_" for character in value):
        raise ValueError(f"Unsafe literal: {value!r}")
    return f"'{value}'"


def _role() -> str:
    return _q(_ROLE)


def _role_exists_guard(body: str) -> str:
    """Run a grant/revoke only when the runtime role exists."""
    return f"""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = {_lit(_ROLE)}) THEN
                RETURN;
            END IF;
            {body}
        END
        $$
    """


def upgrade() -> None:
    for schema in _BUTLER_SCHEMAS:
        op.execute(
            _role_exists_guard(
                f"""
                IF EXISTS (
                    SELECT 1 FROM pg_namespace WHERE nspname = {_lit(schema)}
                ) THEN
                    EXECUTE 'GRANT USAGE ON SCHEMA {_q(schema)} TO {_role()}';
                END IF;
                """
            )
        )
        for table in _CALENDAR_READ_SURFACE_TABLES:
            op.execute(
                _role_exists_guard(
                    f"""
                    IF EXISTS (
                        SELECT 1 FROM information_schema.tables
                        WHERE table_schema = {_lit(schema)}
                          AND table_name = {_lit(table)}
                    ) THEN
                        EXECUTE 'GRANT SELECT ON TABLE {_q(schema)}.{_q(table)} '
                                'TO {_role()}';
                    END IF;
                    """
                )
            )

    for table in _PUBLIC_READ_SURFACE_TABLES:
        op.execute(
            _role_exists_guard(
                f"""
                IF EXISTS (
                    SELECT 1 FROM information_schema.tables
                    WHERE table_schema = 'public'
                      AND table_name = {_lit(table)}
                ) THEN
                    EXECUTE 'GRANT SELECT ON TABLE public.{_q(table)} '
                            'TO {_role()}';
                END IF;
                """
            )
        )


def downgrade() -> None:
    for schema in _BUTLER_SCHEMAS:
        for table in _NEW_CALENDAR_GRANTS:
            op.execute(
                _role_exists_guard(
                    f"""
                    IF EXISTS (
                        SELECT 1 FROM information_schema.tables
                        WHERE table_schema = {_lit(schema)}
                          AND table_name = {_lit(table)}
                    ) THEN
                        EXECUTE 'REVOKE SELECT ON TABLE {_q(schema)}.{_q(table)} '
                                'FROM {_role()}';
                    END IF;
                    """
                )
            )

    # ``init-db.sql`` has historically granted all runtime roles access to
    # shared public lookup tables.  Preserve that baseline on downgrade; ACLs
    # do not retain which grant statement originally supplied the privilege.
