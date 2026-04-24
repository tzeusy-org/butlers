"""add_per_schema_watermarks_to_projection_checkpoints

Revision ID: chronicler_002
Revises: chronicler_001
Create Date: 2026-04-24 00:00:00.000000

Adds per-sub-source watermark tracking to ``projection_checkpoints``.

``CoreSessionsAdapter`` fans out across N butler schemas. Previously, a single
global watermark (max across all schemas) was stored under ``source_name``.
This can stall or skip sessions when schemas have very different activity.

This migration replaces the ``PRIMARY KEY (source_name)`` with a composite
primary key ``(source_name, subsource)`` where:
- ``subsource = ''``  (empty string) — global checkpoint row, compatible with
  all existing adapters that do not use sub-source tracking.
- ``subsource = '<schema_name>'`` — per-schema checkpoint row (used by
  ``CoreSessionsAdapter`` after this change).

Existing rows are migrated with ``subsource = ''`` so no data is lost.

The migration is idempotent — re-running it is safe because each step uses
``IF NOT EXISTS`` / ``IF EXISTS`` guards or ``ALTER … IF NOT EXISTS``.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "chronicler_002"
down_revision = "chronicler_001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Step 1: add subsource column defaulting to '' (global sentinel).
    op.execute("""
        ALTER TABLE projection_checkpoints
        ADD COLUMN IF NOT EXISTS subsource TEXT NOT NULL DEFAULT ''
    """)

    # Step 2: drop the old single-column primary key.
    # The constraint is named by PostgreSQL as 'projection_checkpoints_pkey'.
    op.execute("""
        ALTER TABLE projection_checkpoints
        DROP CONSTRAINT IF EXISTS projection_checkpoints_pkey
    """)

    # Step 3: promote the composite key as the new primary key.
    op.execute("""
        ALTER TABLE projection_checkpoints
        ADD CONSTRAINT projection_checkpoints_pkey
            PRIMARY KEY (source_name, subsource)
    """)


def downgrade() -> None:
    # Reverse is safe only if no per-schema rows exist.
    op.execute("""
        ALTER TABLE projection_checkpoints
        DROP CONSTRAINT IF EXISTS projection_checkpoints_pkey
    """)
    op.execute("""
        ALTER TABLE projection_checkpoints
        ADD CONSTRAINT projection_checkpoints_pkey
            PRIMARY KEY (source_name)
    """)
    op.execute("""
        ALTER TABLE projection_checkpoints
        DROP COLUMN IF EXISTS subsource
    """)
