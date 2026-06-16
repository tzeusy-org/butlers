"""drop_quick_facts: DROP relationship.quick_facts (self-guarding, gated on zero rows).

quick_facts has been deprecated — its readers/writers were removed and vCard
ORG/TITLE now routes to public.contacts (bu-er779, merged #2404).  This
migration is the final removal.

Revision ID: rel_025
Revises: rel_024
Create Date: 2026-06-16 00:00:00.000000

Safety contract
---------------
* ``to_regclass`` guard: if the table is already absent (e.g. the migration ran
  in another environment or was applied out-of-band), the upgrade is a clean
  no-op — no error is raised.
* Row-count gate: if the table exists but is non-empty the migration REFUSES
  with a RuntimeError naming the row count.  An operator must investigate before
  proceeding; data must not be silently lost.
* Only when the table both exists AND has zero rows does the DROP execute.

downgrade()
-----------
Recreates the empty ``quick_facts`` table with the original DDL from rel_001.
Row data is not restored — the migration is only applied when the table is
empty, so there is nothing to restore.  Schema matches rel_001 exactly
(primary key, FK, unique constraint, timestamps).
"""

from __future__ import annotations

import logging

import sqlalchemy as sa

from alembic import op

logger = logging.getLogger(__name__)

# revision identifiers, used by Alembic.
revision = "rel_025"
down_revision = "rel_024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()

    # Guard: no-op if the table is already absent.
    if bind.execute(sa.text("SELECT to_regclass('quick_facts')")).scalar() is None:
        logger.info("rel_025: quick_facts already absent — skipping")
        return

    # Row-count gate: refuse if the table has any rows.
    count = bind.execute(sa.text("SELECT COUNT(*) FROM quick_facts")).scalar()
    if count and count > 0:
        raise RuntimeError(
            f"rel_025: quick_facts has {count} row(s). "
            f"The table must be empty before it can be dropped. "
            f"Investigate and drain the data, then re-run the migration."
        )

    op.execute("DROP TABLE IF EXISTS quick_facts")
    logger.info("rel_025: dropped relationship.quick_facts")


def downgrade() -> None:
    bind = op.get_bind()

    # Idempotent: skip if the table already exists.
    if bind.execute(sa.text("SELECT to_regclass('quick_facts')")).scalar() is not None:
        logger.info("rel_025 downgrade: quick_facts already present — skipping")
        return

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS quick_facts (
            id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            contact_id UUID NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
            key        TEXT NOT NULL,
            value      TEXT NOT NULL,
            created_at TIMESTAMPTZ DEFAULT now(),
            updated_at TIMESTAMPTZ DEFAULT now(),
            UNIQUE (contact_id, key)
        )
        """
    )
    logger.info("rel_025 downgrade: recreated empty quick_facts table")
