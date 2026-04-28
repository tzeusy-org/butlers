"""add_carryover_metadata_to_projection_checkpoints

Revision ID: chronicler_006
Revises: chronicler_005
Create Date: 2026-04-28 00:00:00.000000

Adds a ``carryover`` JSONB column to ``projection_checkpoints`` that batch
adapters can use to record open-episode state across batch boundaries.

Background
----------
Tier-0 batch adapters (home_assistant.history, owntracks.points) project
episodes by scanning a window of source rows.  When a continuous span (a
person staying "home", a movement sequence) crosses a batch boundary, the
adapter sees the first half in batch N and the second half in batch N+1.
Without carryover, two fragmented episodes are written instead of one.

The ``carryover`` column lets adapters persist the minimal open-episode
state they need to continue stitching at the start of the next batch.
The schema is adapter-defined JSONB, but the conventional envelope is:

    {
      "<entity_key>": {
        "source_ref": "<stable ref from prior batch>",
        "start_at": "<ISO 8601 UTC>",
        "end_at": "<ISO 8601 UTC>"
      },
      ...
    }

At batch start the adapter reads ``carryover``, tries to extend any
open episodes, then overwrites ``carryover`` with new open-episode state
(or ``{}`` if all episodes closed within the batch).

Backwards compatibility
-----------------------
- ``carryover`` is nullable; NULL means "no carryover" (legacy row or first run).
- Adapters treat NULL identically to ``{}``.
- No data migration of existing rows is required.
- The migration is idempotent: ``ADD COLUMN IF NOT EXISTS`` is a no-op when
  re-applied.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "chronicler_006"
down_revision = "chronicler_005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE projection_checkpoints
        ADD COLUMN IF NOT EXISTS carryover JSONB
    """)


def downgrade() -> None:
    op.execute("""
        ALTER TABLE projection_checkpoints
        DROP COLUMN IF EXISTS carryover
    """)
