"""activitywatch_events: safe browser-domain evidence projection field.

Revision ID: core_192
Revises: core_191
Create Date: 2026-07-27 00:00:00.000000

Adds the connector-derived ``browser_domain`` hostname for best-effort
ActivityWatch ``aw-watcher-web`` correlation. This field is intentionally
limited to a validated hostname so Chronicler can expose browser time by
domain at normal privacy. Raw URLs and web-watcher tab titles remain only in
the existing sensitive ``raw_payload`` JSONB evidence surface.

The existing table-level connector_writer / chronicler SELECT grants cover
the new column; no privilege expansion is needed.
"""

from __future__ import annotations

from alembic import op

revision = "core_192"
down_revision = "core_191"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE IF EXISTS connectors.activitywatch_events
        ADD COLUMN IF NOT EXISTS browser_domain TEXT
    """)
    # COMMENT needs a separate existence guard because PostgreSQL has no
    # COMMENT ON COLUMN IF EXISTS form. The core chain normally creates this
    # table in core_154, but an optional-schema deployment must still migrate.
    op.execute("""
        DO $$
        BEGIN
            IF to_regclass('connectors.activitywatch_events') IS NOT NULL THEN
                COMMENT ON COLUMN connectors.activitywatch_events.browser_domain IS
                    'Validated hostname only; raw URLs and web titles remain in raw_payload.';
            END IF;
        END
        $$;
    """)


def downgrade() -> None:
    op.execute("""
        ALTER TABLE IF EXISTS connectors.activitywatch_events
        DROP COLUMN IF EXISTS browser_domain
    """)
