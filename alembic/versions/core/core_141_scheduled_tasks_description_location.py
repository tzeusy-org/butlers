"""scheduled_tasks_description_location: add description/location columns.

Revision ID: core_141
Revises: core_140
Create Date: 2026-06-21 00:00:00.000000

Adds optional ``description`` and ``location`` TEXT columns to the per-butler
``scheduled_tasks`` table so butler-authored calendar events (created via
``calendar_create_butler_event``) can carry a free-text description and a
location through to the workspace projection and the Butlers Google subcalendar.

Before this change, accepting a calendar proposal that carried a description or
location silently dropped both fields: the create tool had no parameters for
them and the scheduler row had nowhere to store them. These columns close that
gap (bu-cb0ap).

Runs once per butler schema via the per-schema migration runner
(``search_path`` is set to the butler schema), guarded by ``to_regclass`` so
schemas without a ``scheduled_tasks`` table are skipped.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "core_141"
down_revision = "core_140"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        DO $$
        DECLARE
            target_schema text := current_schema();
            table_ref regclass := to_regclass(format('%I.scheduled_tasks', target_schema));
        BEGIN
            IF table_ref IS NULL THEN
                RETURN;
            END IF;

            EXECUTE format(
                'ALTER TABLE %I.scheduled_tasks '
                'ADD COLUMN IF NOT EXISTS description TEXT, '
                'ADD COLUMN IF NOT EXISTS location TEXT',
                target_schema
            );
        END
        $$;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        DECLARE
            target_schema text := current_schema();
            table_ref regclass := to_regclass(format('%I.scheduled_tasks', target_schema));
        BEGIN
            IF table_ref IS NULL THEN
                RETURN;
            END IF;

            EXECUTE format(
                'ALTER TABLE %I.scheduled_tasks '
                'DROP COLUMN IF EXISTS description, '
                'DROP COLUMN IF EXISTS location',
                target_schema
            );
        END
        $$;
        """
    )
