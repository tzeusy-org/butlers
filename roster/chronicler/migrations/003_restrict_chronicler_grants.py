"""restrict_butler_chronicler_rw_cross_schema_access

Revision ID: chronicler_003
Revises: chronicler_002
Create Date: 2026-04-24 00:00:00.000000

Replaces the broad ``GRANT SELECT ON ALL TABLES IN SCHEMA <butler_schema>``
grants for ``butler_chronicler_rw`` (added by scripts/init-db.sql in
PR #1106) with specific grants on the evidence surfaces declared in
RFC 0014 source compatibility contracts.

Approved evidence surfaces (v1):
  {schema}.sessions                  — CoreSessionsAdapter (all schemas)
  {schema}.calendar_event_instances  — CalendarCompletedAdapter (optional)
  connectors.steam_play_history      — PLANNED
  connectors.owntracks_points        — PLANNED
  connectors.home_assistant_history  — PLANNED

The migration is idempotent: IF EXISTS guards prevent errors if a table
is absent (calendar module not enabled, PLANNED tables not yet created).
Tables that do not exist yet will receive their grant when init-db.sql
is re-run after they are created (init-db.sql is now specific too).

Downgrade restores the blanket grants so the migration can be reversed.

NOTE: REVOKE / GRANT here operates on tables owned by the migration user
(``butlers``). Superuser-issued grants can be revoked by the table owner.
If the migration user does not own a schema's tables, the REVOKE may be
a no-op (the specific grant will still be added). A full cleanup requires
re-running scripts/init-db.sql under superuser after downgrade.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "chronicler_003"
down_revision = "chronicler_002"
branch_labels = None
depends_on = None

# Butler schemas that Chronicler's adapters fan out across.
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

# Evidence tables per schema (CoreSessionsAdapter + CalendarCompletedAdapter).
_PER_SCHEMA_EVIDENCE_TABLES = (
    "sessions",
    "calendar_event_instances",
)

# Connector-schema evidence surfaces for PLANNED adapters.
_CONNECTOR_EVIDENCE_TABLES = (
    "steam_play_history",
    "owntracks_points",
    "home_assistant_history",
)

_ROLE = "butler_chronicler_rw"
_CONNECTOR_SCHEMA = "connectors"


def _role_exists_guard(body: str) -> str:
    """Wrap ``body`` SQL in a DO block that runs only if ``_ROLE`` exists.

    ``scripts/init-db.sql`` is responsible for creating ``butler_chronicler_rw``.
    When that script has not been re-run since chronicler was added, the role is
    absent and plain REVOKE/GRANT statements raise UndefinedObject. Guarding
    each statement keeps the migration idempotent: with no role there are no
    grants to narrow, and a subsequent init-db.sql run will issue the correct
    (narrow) grants directly.
    """
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
    # ── 1. Revoke blanket cross-schema SELECT from butler schemas ──────────
    # Replaces the loop in PR #1106 that granted SELECT ON ALL TABLES.
    # This narrows the footprint to RFC 0014-declared evidence surfaces only.
    for schema in _BUTLER_SCHEMAS:
        op.execute(
            _role_exists_guard(
                f"EXECUTE 'REVOKE SELECT ON ALL TABLES IN SCHEMA {_q(schema)} FROM {_role()}';"
            )
        )

    # ── 2. Grant specific evidence tables per butler schema ────────────────
    for schema in _BUTLER_SCHEMAS:
        for table in _PER_SCHEMA_EVIDENCE_TABLES:
            op.execute(
                _role_exists_guard(
                    f"""
                    IF EXISTS (
                        SELECT 1 FROM information_schema.tables
                        WHERE table_schema = {_lit(schema)}
                          AND table_name   = {_lit(table)}
                    ) THEN
                        EXECUTE 'GRANT SELECT ON TABLE {_q(schema)}.{_q(table)} '
                                'TO {_role()}';
                    END IF;
                    """
                )
            )

    # ── 3. Grant specific connector evidence surfaces (PLANNED) ───────────
    for table in _CONNECTOR_EVIDENCE_TABLES:
        op.execute(
            _role_exists_guard(
                f"""
                IF EXISTS (
                    SELECT 1 FROM information_schema.tables
                    WHERE table_schema = {_lit(_CONNECTOR_SCHEMA)}
                      AND table_name   = {_lit(table)}
                ) THEN
                    EXECUTE 'GRANT SELECT ON TABLE {_q(_CONNECTOR_SCHEMA)}.{_q(table)} '
                            'TO {_role()}';
                END IF;
                """
            )
        )


def downgrade() -> None:
    # Restore blanket SELECT grants so adapters continue working on rollback.
    # A full privilege repair also requires re-running scripts/init-db.sql.
    # Use current_user (via DO/EXECUTE) for the ALTER DEFAULT PRIVILEGES grantor
    # so this works regardless of whether the migration user is named "butlers"
    # or something else (see scripts/init-db.sql _migration_user detection).
    for schema in _BUTLER_SCHEMAS:
        op.execute(
            _role_exists_guard(
                f"EXECUTE 'GRANT SELECT ON ALL TABLES IN SCHEMA {_q(schema)} TO {_role()}';"
            )
        )
        op.execute(
            _role_exists_guard(
                f"""
                EXECUTE format(
                    'ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA {_q(schema)} '
                    'GRANT SELECT ON TABLES TO {_role()}',
                    current_user
                );
                """
            )
        )


# ── helpers ────────────────────────────────────────────────────────────────


def _q(name: str) -> str:
    """Double-quote a PostgreSQL identifier (simple alphanum/underscore only)."""
    if not name.replace("_", "").isalnum():
        raise ValueError(f"Unsafe identifier: {name!r}")
    return f'"{name}"'


def _lit(value: str) -> str:
    """Produce a single-quoted SQL string literal (no special chars allowed)."""
    if not all(c.isalnum() or c == "_" for c in value):
        raise ValueError(f"Unsafe literal: {value!r}")
    return f"'{value}'"


def _role() -> str:
    return _q(_ROLE)
