"""visit_briefs

Revision ID: health_005
Revises: health_004
Create Date: 2026-10-18 00:00:00.000000

bu-jv1moa.15 (S1). Records the pre-appointment health brief sent for each appointment on
Health's calendar. ``event_id`` is the calendar event id (``<event id>:<instance ref>`` for
a recurring instance) and is the primary key, so one appointment is briefed once: a
rescheduled event updates ``event_start`` on its existing row. ``delivered_at`` is set only
when the owner notification was delivered, so a suppressed or failed send is retried.
``digest`` keeps every figure in the brief with the fact ids it rests on.

Rollback: drop the table (``downgrade``), together with the ``health_visit_brief`` job.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "health_005"
down_revision = "health_004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS visit_briefs (
            event_id TEXT PRIMARY KEY,
            event_start TIMESTAMPTZ NOT NULL,
            period_start TIMESTAMPTZ NOT NULL,
            digest JSONB NOT NULL DEFAULT '{}'::jsonb,
            delivered_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS visit_briefs")
