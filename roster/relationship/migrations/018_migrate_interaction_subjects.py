"""migrate_interaction_subjects: rewrite interaction fact subjects from contact: to entity:.

Revision ID: rel_018
Revises: rel_017
Create Date: 2026-05-31 00:00:00.000000

Background
----------
Prior to this migration, ``interaction_log()`` stored facts with::

    subject = 'contact:{contact_id}'

This bead (bu-ro4i3) migrates the writer to use::

    subject = 'entity:{entity_id}'

This migration rewrites existing rows to match the new subject key format.

Migration logic
---------------
For each interaction fact whose subject starts with ``contact:``:

1. Extract the contact_id from the subject string.
2. Look up the contact's entity_id from public.contacts.
3. If entity_id IS NOT NULL: rewrite subject to ``entity:{entity_id}``.
4. If entity_id IS NULL (contact has no linked entity — data integrity gap):
   LEAVE the row unchanged and log a warning with the count.

NULL entity_id handling
-----------------------
Contacts without a linked entity cannot be migrated cleanly.  Minting new
entities in a data migration is out of scope and would create orphaned entities.
These rows are left with their old ``contact:{contact_id}`` subjects, which
means they will not be matched by the new reader queries that join on
``f.entity_id = c.entity_id``.  This is the safe choice: they were effectively
invisible to Dunbar scoring anyway because ``compute_dunbar_scores`` already
filtered on ``c.entity_id IS NOT NULL``.

Downgrade
---------
The downgrade rewrites ``entity:`` subjects back to ``contact:`` using the same
contacts table mapping.  Rows whose entity_id has no matching contact are left
unchanged (they were unmappable in the upgrade direction too, so this is
consistent).  The downgrade is feasible because entity_id is still stored on
the fact row itself (``facts.entity_id`` column), enabling the reverse lookup.

Guard
-----
If the facts table does not exist (memory module not installed), the migration
is a no-op.
"""

from __future__ import annotations

import logging

from sqlalchemy import text

from alembic import op

revision = "rel_018"
down_revision = "rel_017"
branch_labels = None
depends_on = None

logger = logging.getLogger(__name__)


def upgrade() -> None:
    conn = op.get_bind()

    facts_exists = conn.execute(text("SELECT to_regclass('facts')")).scalar()
    if facts_exists is None:
        logger.info("rel_018 upgrade: facts table not found — skipping")
        return

    # Count how many interaction facts have contact: subjects.
    total_contact_subjects = (
        conn.execute(
            text("""
        SELECT COUNT(*) FROM facts
        WHERE subject LIKE 'contact:%'
          AND predicate LIKE 'interaction_%'
          AND scope = 'relationship'
        """)
        ).scalar()
        or 0
    )

    if total_contact_subjects == 0:
        logger.info("rel_018 upgrade: no interaction facts with contact: subjects — nothing to do")
        return

    logger.info(
        "rel_018 upgrade: found %d interaction fact(s) with contact: subjects to migrate",
        total_contact_subjects,
    )

    # Step 1: Migrate rows where the contact has a linked entity_id.
    result = conn.execute(
        text("""
        UPDATE facts f
        SET subject = 'entity:' || c.entity_id::text
        FROM public.contacts c
        WHERE f.subject = 'contact:' || c.id::text
          AND f.predicate LIKE 'interaction_%'
          AND f.scope = 'relationship'
          AND c.entity_id IS NOT NULL
        """)
    )
    migrated_count = result.rowcount

    # Step 2: Count rows left behind (contact has no entity_id).
    skipped_count = (
        conn.execute(
            text("""
        SELECT COUNT(*) FROM facts
        WHERE subject LIKE 'contact:%'
          AND predicate LIKE 'interaction_%'
          AND scope = 'relationship'
        """)
        ).scalar()
        or 0
    )

    if skipped_count > 0:
        logger.warning(
            "rel_018 upgrade: %d interaction fact(s) left with contact: subject "
            "because their contact has no linked entity_id. "
            "These facts are orphaned and will not be matched by entity-keyed reader queries. "
            "Investigate contacts missing entity_id to resolve.",
            skipped_count,
        )

    logger.info(
        "rel_018 upgrade: migrated %d interaction fact(s) from contact: → entity:; "
        "%d left unchanged (no entity_id on contact)",
        migrated_count,
        skipped_count,
    )


def downgrade() -> None:
    conn = op.get_bind()

    facts_exists = conn.execute(text("SELECT to_regclass('facts')")).scalar()
    if facts_exists is None:
        logger.info("rel_018 downgrade: facts table not found — skipping")
        return

    # Count how many interaction facts have entity: subjects.
    total_entity_subjects = (
        conn.execute(
            text("""
        SELECT COUNT(*) FROM facts
        WHERE subject LIKE 'entity:%'
          AND predicate LIKE 'interaction_%'
          AND scope = 'relationship'
        """)
        ).scalar()
        or 0
    )

    if total_entity_subjects == 0:
        logger.info("rel_018 downgrade: no interaction facts with entity: subjects — nothing to do")
        return

    logger.info(
        "rel_018 downgrade: found %d interaction fact(s) with entity: subjects to revert",
        total_entity_subjects,
    )

    # Reverse: rewrite entity:{entity_id} → contact:{contact_id} using facts.entity_id column.
    # The entity_id is stored on the fact row itself, enabling the reverse lookup
    # without parsing the subject string.
    result = conn.execute(
        text("""
        UPDATE facts f
        SET subject = 'contact:' || c.id::text
        FROM public.contacts c
        WHERE f.entity_id = c.entity_id
          AND f.subject   = 'entity:' || c.entity_id::text
          AND f.predicate LIKE 'interaction_%'
          AND f.scope     = 'relationship'
          AND c.entity_id IS NOT NULL
        """)
    )
    reverted_count = result.rowcount

    # Count any rows we could not revert (entity has no contact, or subject mismatch).
    remaining_count = (
        conn.execute(
            text("""
        SELECT COUNT(*) FROM facts
        WHERE subject LIKE 'entity:%'
          AND predicate LIKE 'interaction_%'
          AND scope = 'relationship'
        """)
        ).scalar()
        or 0
    )

    if remaining_count > 0:
        logger.warning(
            "rel_018 downgrade: %d interaction fact(s) could not be reverted to contact: "
            "subject (entity has no matching contact row). These rows retain entity: subjects.",
            remaining_count,
        )

    logger.info(
        "rel_018 downgrade: reverted %d interaction fact(s) from entity: → contact:; "
        "%d left unchanged (no matching contact for entity_id)",
        reverted_count,
        remaining_count,
    )
