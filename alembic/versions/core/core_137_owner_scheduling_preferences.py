"""owner_scheduling_preferences: owner life/meeting-hours availability.

Revision ID: core_137
Revises: core_136
Create Date: 2026-06-21 01:00:00.000000

Creates ``public.owner_scheduling_preferences`` — a single owner-scoped record
describing when a meeting may occupy the owner's time (life/availability), used
by calendar slot ranking (``_build_suggested_slots`` and the future
``calendar_find_free_slots``).

This is DELIBERATELY DISTINCT from the per-butler notification quiet hours in
``{schema}.delivery_preferences`` (core_012): that table answers "when may a
butler ping me?" and is keyed by ``butler_name``; this table answers "when may a
meeting be placed on my day?" and is a single owner-scoped row (``id = TRUE``).
See openspec change ``calendar-availability-find-time`` (design D3).

It lives in ``public`` (not per-butler) so every butler that runs the calendar
module reads the same record. CRUD is granted to all butler runtime roles
(mirroring core_057 ``public.qa_allowed_repositories``); only the messenger
butler exposes the ``scheduling_preferences_set``/``_get`` MCP tools.

Columns
-------
id                    BOOLEAN PK, always TRUE — singleton guard
earliest_meeting_time TIME            — earliest a meeting may start (owner tz)
latest_meeting_time   TIME            — latest a meeting may end (owner tz)
meeting_days          JSONB           — allowed weekday codes, e.g. ["MO","TU"]
timezone              TEXT NOT NULL DEFAULT 'UTC' — owner/residence timezone
no_meeting_blocks     JSONB NOT NULL DEFAULT '[]' — [{"start":"12:00","end":"13:00"}]
created_at            TIMESTAMPTZ NOT NULL DEFAULT now()
updated_at            TIMESTAMPTZ NOT NULL DEFAULT now()
"""

from __future__ import annotations

from alembic import op

revision = "core_137"
down_revision = "core_136"
branch_labels = None
depends_on = None

_TABLE = "public.owner_scheduling_preferences"

_ALL_BUTLER_ROLES = (
    "butler_education_rw",
    "butler_finance_rw",
    "butler_general_rw",
    "butler_health_rw",
    "butler_home_rw",
    "butler_lifestyle_rw",
    "butler_messenger_rw",
    "butler_relationship_rw",
    "butler_switchboard_rw",
    "butler_travel_rw",
    "butler_qa_rw",
    "butler_chronicler_rw",
)

_TABLE_PRIVILEGES = "SELECT, INSERT, UPDATE, DELETE"


def _grant_best_effort(table_fqn: str, privilege: str, role: str) -> None:
    """GRANT privilege ON table TO role; tolerates missing role/table."""
    op.execute(
        f"""
        DO $$
        BEGIN
            IF to_regclass('{table_fqn}') IS NOT NULL
               AND EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}')
            THEN
                EXECUTE 'GRANT {privilege} ON TABLE {table_fqn} TO "{role}"';
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
    op.execute(f"""
        CREATE TABLE IF NOT EXISTS {_TABLE} (
            id                    BOOLEAN PRIMARY KEY DEFAULT TRUE,
            earliest_meeting_time TIME,
            latest_meeting_time   TIME,
            meeting_days          JSONB,
            timezone              TEXT NOT NULL DEFAULT 'UTC',
            no_meeting_blocks     JSONB NOT NULL DEFAULT '[]'::jsonb,
            created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT chk_owner_scheduling_preferences_singleton CHECK (id = TRUE),
            CONSTRAINT chk_owner_scheduling_preferences_window
                CHECK (
                    earliest_meeting_time IS NULL
                    OR latest_meeting_time IS NULL
                    OR latest_meeting_time > earliest_meeting_time
                )
        )
    """)

    for role in _ALL_BUTLER_ROLES:
        _grant_best_effort(_TABLE, _TABLE_PRIVILEGES, role)


def downgrade() -> None:
    op.execute(f"DROP TABLE IF EXISTS {_TABLE}")
