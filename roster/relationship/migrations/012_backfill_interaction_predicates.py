"""backfill_interaction_predicates

Migrate all existing interaction facts from the singular predicate
``'interaction'`` to type-specific predicates ``'interaction_{type}'``
(e.g. ``'interaction_call'``, ``'interaction_meeting'``).

This resolves spec/code drift: the predicate taxonomy spec (predicate-taxonomy.md)
and butler-relationship/spec.md have always documented ``interaction_{type}``
predicates, but the original write path stored ``predicate='interaction'`` with
``type`` buried in ``metadata->>'type'``.  The code is now aligned to the spec.

Migration logic:
  1. UPDATE facts SET predicate = 'interaction_' || (metadata->>'type')
     WHERE predicate = 'interaction'
       AND scope = 'relationship'
       AND metadata->>'type' IS NOT NULL
       AND metadata->>'type' != ''
  2. UPDATE remaining rows (NULL or empty type) to 'interaction_other'
     as a safe fallback.

Downgrade restores all interaction_* facts back to 'interaction' (reversible).

Guard: if the facts table does not exist (memory module not installed),
the migration is skipped.

Revision ID: rel_012
Revises: rel_011
Create Date: 2026-04-30 00:00:00.000000
"""

from __future__ import annotations

from sqlalchemy import text

from alembic import op

# revision identifiers, used by Alembic.
revision = "rel_012"
down_revision = "rel_011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    facts_exists = conn.execute(text("SELECT to_regclass('facts')")).scalar()
    if facts_exists is None:
        return

    # Step 1: Migrate rows with a non-empty type value in metadata
    result = conn.execute(
        text("""
        UPDATE facts
        SET predicate = 'interaction_' || (metadata->>'type')
        WHERE predicate = 'interaction'
          AND scope = 'relationship'
          AND metadata->>'type' IS NOT NULL
          AND metadata->>'type' != ''
        """)
    )
    typed_count = result.rowcount

    # Step 2: Migrate remaining rows with NULL or empty type to 'interaction_other'
    result = conn.execute(
        text("""
        UPDATE facts
        SET predicate = 'interaction_other'
        WHERE predicate = 'interaction'
          AND scope = 'relationship'
        """)
    )
    fallback_count = result.rowcount

    total = typed_count + fallback_count
    if total > 0:
        import logging

        logging.getLogger(__name__).info(
            "rel_012 upgrade: migrated %d interaction facts "
            "(%d typed, %d fallback to interaction_other)",
            total,
            typed_count,
            fallback_count,
        )


def downgrade() -> None:
    conn = op.get_bind()
    facts_exists = conn.execute(text("SELECT to_regclass('facts')")).scalar()
    if facts_exists is None:
        return

    # Restore all interaction_* facts back to the singular 'interaction' predicate.
    # The metadata->>'type' field is preserved, so the data is not lost.
    result = conn.execute(
        text("""
        UPDATE facts
        SET predicate = 'interaction'
        WHERE predicate LIKE 'interaction_%'
          AND scope = 'relationship'
        """)
    )
    count = result.rowcount
    if count > 0:
        import logging

        logging.getLogger(__name__).info(
            "rel_012 downgrade: restored %d interaction facts to predicate='interaction'",
            count,
        )
