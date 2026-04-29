"""life_events_and_tasks

Add life_events and tasks tables for the relationship butler.

Revision ID: rel_009
Revises: rel_008
Create Date: 2026-04-30 00:00:00.000000

Tables created:
  - life_events: records of significant life events linked to a contact and a
    life_event_type from the rel_008 taxonomy
  - tasks: action items linked to a contact with completion tracking

Indexes created:
  - idx_life_events_contact_happened: composite on (contact_id, happened_at)
  - idx_life_events_type: on life_event_type_id
  - idx_tasks_contact_id: on tasks.contact_id
  - idx_tasks_completed: on tasks.completed
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "rel_009"
down_revision = "rel_008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # life_events: significant events linked to a contact and event type
    # ------------------------------------------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS life_events (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            contact_id UUID NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
            life_event_type_id UUID NOT NULL REFERENCES life_event_types(id),
            summary TEXT NOT NULL,
            description TEXT,
            happened_at DATE,
            created_at TIMESTAMPTZ DEFAULT now()
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_life_events_contact_happened
            ON life_events (contact_id, happened_at)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_life_events_type
            ON life_events (life_event_type_id)
    """)

    # ------------------------------------------------------------------
    # tasks: action items linked to a contact with completion tracking
    # ------------------------------------------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS tasks (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            contact_id UUID NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
            title VARCHAR NOT NULL,
            description TEXT,
            completed BOOLEAN DEFAULT false,
            completed_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ DEFAULT now()
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_tasks_contact_id ON tasks (contact_id)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_tasks_completed ON tasks (completed)
    """)


def downgrade() -> None:
    # Drop in reverse FK order: tasks and life_events both reference contacts,
    # but life_events also references life_event_types — no cross-dependency
    # between the two new tables, so either order works; drop tasks first.
    op.execute("DROP TABLE IF EXISTS tasks")
    op.execute("DROP INDEX IF EXISTS idx_tasks_completed")
    op.execute("DROP INDEX IF EXISTS idx_tasks_contact_id")
    op.execute("DROP TABLE IF EXISTS life_events")
    op.execute("DROP INDEX IF EXISTS idx_life_events_type")
    op.execute("DROP INDEX IF EXISTS idx_life_events_contact_happened")
