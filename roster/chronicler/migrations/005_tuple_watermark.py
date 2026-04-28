"""add_watermark_id_to_projection_checkpoints

Revision ID: chronicler_005
Revises: chronicler_004
Create Date: 2026-04-28 00:00:00.000000

Extends ``projection_checkpoints`` with a ``watermark_id`` column that
stores the source-table ``id`` of the last-projected row alongside the
existing ``watermark`` timestamp.

Together, ``(watermark, watermark_id)`` form a tuple watermark that allows
adapters to use the row-value comparison

    WHERE (ts_col, id) > ($1, $2)  ORDER BY ts_col ASC, id ASC

instead of the single-column

    WHERE ts_col > $1

This eliminates the edge case where multiple rows share the same timestamp
at a batch boundary and the single-column filter would silently skip the
rows whose ``id`` falls after the last-projected ``id`` at that timestamp.

**Backwards compatibility:**
- ``watermark_id`` is nullable (``BIGINT NULL``).
- Existing checkpoint rows keep ``watermark_id = NULL``.
- Adapters treat ``watermark_id = NULL`` as "use single-column ``>``
  semantics" — the legacy path is preserved until the next successful
  projection run, after which all subsequent runs use the tuple path.
- No data migration of existing rows is required or desirable.

The migration is idempotent: ``ADD COLUMN IF NOT EXISTS`` is a no-op when
re-applied.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "chronicler_005"
down_revision = "chronicler_004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE projection_checkpoints
        ADD COLUMN IF NOT EXISTS watermark_id BIGINT
    """)


def downgrade() -> None:
    op.execute("""
        ALTER TABLE projection_checkpoints
        DROP COLUMN IF EXISTS watermark_id
    """)
