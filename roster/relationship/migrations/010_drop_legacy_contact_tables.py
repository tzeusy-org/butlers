"""drop_legacy_contact_tables

Drop the five legacy contact-keyed tables (notes, interactions, gifts, loans,
activity_feed) that have been superseded by the SPO facts layer.

Revision ID: rel_010
Revises: rel_009
Create Date: 2026-04-30 00:00:00.000000

Migration steps:
  Step A — Backfill orphan gift/loan facts: ensure every active gift/loan fact
    with entity_id IS NULL has its entity_id resolved from the contact's
    public.contacts.entity_id using the subject regex. Log counts; abort if any
    subject did not match the regex (skipped > 0) unless FORCE_SKIP_ORPHANS env
    var is set to "1".
  Step B — Sanity check: abort if any of the five legacy tables still contain
    rows. Operator must investigate before proceeding.
  Step C — Drop: DROP TABLE relationship.{notes, interactions, gifts, loans,
    activity_feed}.

downgrade(): Recreate the empty tables using the original schemas from
  rel_001. Backfill is not reversed (entity_id corrections are always correct).
"""

from __future__ import annotations

import logging
import os
import re

from sqlalchemy import text

from alembic import op

# revision identifiers, used by Alembic.
revision = "rel_010"
down_revision = "rel_009"
branch_labels = None
depends_on = None

logger = logging.getLogger(__name__)

# Subject pattern: contact:{uuid}:... — captures the contact UUID.
_CONTACT_ID_RE = re.compile(r"^contact:([0-9a-f-]{36}):")


def upgrade() -> None:
    conn = op.get_bind()

    # ------------------------------------------------------------------
    # Step A: Backfill orphan gift/loan facts that have entity_id IS NULL.
    #
    # These arise when a gift/loan fact was written before the contact had
    # an entity linked.  We resolve entity_id from public.contacts.entity_id
    # using the fact subject (contact:{contact_id}:...).
    # ------------------------------------------------------------------
    _step_a_backfill_orphan_facts(conn)

    # ------------------------------------------------------------------
    # Step B: Sanity check — abort if any legacy table still has rows.
    # ------------------------------------------------------------------
    _step_b_sanity_check(conn)

    # ------------------------------------------------------------------
    # Step C: Drop the five legacy tables.
    # ------------------------------------------------------------------
    _step_c_drop_tables(conn)


def downgrade() -> None:
    conn = op.get_bind()

    # Recreate the five tables with original schemas from rel_001.
    # All tables are created empty; data is not restored.
    conn.execute(
        text("""
        CREATE TABLE IF NOT EXISTS notes (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            contact_id UUID NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
            content TEXT NOT NULL,
            emotion TEXT,
            created_at TIMESTAMPTZ DEFAULT now()
        )
    """)
    )

    conn.execute(
        text("""
        CREATE TABLE IF NOT EXISTS interactions (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            contact_id UUID NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
            type TEXT NOT NULL,
            summary TEXT,
            occurred_at TIMESTAMPTZ DEFAULT now(),
            created_at TIMESTAMPTZ DEFAULT now()
        )
    """)
    )
    conn.execute(
        text("""
        CREATE INDEX IF NOT EXISTS idx_interactions_contact_occurred
            ON interactions (contact_id, occurred_at)
    """)
    )

    conn.execute(
        text("""
        CREATE TABLE IF NOT EXISTS gifts (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            contact_id UUID NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
            description TEXT NOT NULL,
            occasion TEXT,
            status TEXT NOT NULL DEFAULT 'idea'
                CHECK (status IN ('idea', 'purchased', 'wrapped', 'given', 'thanked')),
            created_at TIMESTAMPTZ DEFAULT now(),
            updated_at TIMESTAMPTZ DEFAULT now()
        )
    """)
    )

    conn.execute(
        text("""
        CREATE TABLE IF NOT EXISTS loans (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            contact_id UUID NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
            amount NUMERIC NOT NULL,
            direction TEXT NOT NULL CHECK (direction IN ('lent', 'borrowed')),
            description TEXT,
            settled BOOLEAN DEFAULT false,
            created_at TIMESTAMPTZ DEFAULT now(),
            settled_at TIMESTAMPTZ
        )
    """)
    )

    conn.execute(
        text("""
        CREATE TABLE IF NOT EXISTS activity_feed (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            contact_id UUID NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
            type TEXT NOT NULL,
            description TEXT NOT NULL,
            created_at TIMESTAMPTZ DEFAULT now()
        )
    """)
    )
    conn.execute(
        text("""
        CREATE INDEX IF NOT EXISTS idx_activity_feed_contact_created
            ON activity_feed (contact_id, created_at)
    """)
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _step_a_backfill_orphan_facts(conn) -> None:  # type: ignore[no-untyped-def]
    """Backfill entity_id for orphan gift/loan facts.

    Orphan: active fact with predicate IN ('gift', 'loan') in scope
    'relationship' where entity_id IS NULL.

    Resolution: extract contact_id from subject via regex, look up
    public.contacts.entity_id, and UPDATE the fact.

    Abort if any subject did not match the regex (skipped > 0) unless
    FORCE_SKIP_ORPHANS=1 is set.
    """
    facts_exists = conn.execute(text("SELECT to_regclass('facts')")).scalar()
    if facts_exists is None:
        logger.info("Step A: facts table not present — skipping backfill")
        return

    rows = conn.execute(
        text("""
            SELECT id, subject
            FROM facts
            WHERE predicate IN ('gift', 'loan')
              AND scope = 'relationship'
              AND validity = 'active'
              AND entity_id IS NULL
        """)
    ).fetchall()

    if not rows:
        logger.info("Step A: no orphan gift/loan facts found — nothing to backfill")
        return

    logger.info("Step A: found %d orphan gift/loan fact(s) to backfill", len(rows))

    fixed = 0
    skipped = 0
    for row in rows:
        fact_id = row[0]
        subject = row[1] or ""
        m = _CONTACT_ID_RE.match(subject)
        if not m:
            logger.warning(
                "Step A: fact %s has subject %r — regex did not match; skipping",
                fact_id,
                subject,
            )
            skipped += 1
            continue

        contact_id_str = m.group(1)
        entity_row = conn.execute(
            text("SELECT entity_id FROM public.contacts WHERE id = :cid"),
            {"cid": contact_id_str},
        ).fetchone()
        if entity_row is None or entity_row[0] is None:
            logger.warning(
                "Step A: fact %s — contact %s has no entity_id; skipping",
                fact_id,
                contact_id_str,
            )
            skipped += 1
            continue

        conn.execute(
            text("UPDATE facts SET entity_id = :eid WHERE id = :fid"),
            {"eid": entity_row[0], "fid": fact_id},
        )
        fixed += 1

    logger.info("Step A: backfill complete — fixed=%d skipped=%d", fixed, skipped)

    if skipped > 0:
        force = os.environ.get("FORCE_SKIP_ORPHANS", "0") == "1"
        if not force:
            raise RuntimeError(
                f"Step A: {skipped} orphan gift/loan fact(s) could not be resolved "
                f"(subject regex did not match or contact has no entity_id). "
                f"Investigate and fix, or set FORCE_SKIP_ORPHANS=1 to skip them. "
                f"Fixed {fixed} fact(s) before aborting."
            )
        logger.warning(
            "Step A: FORCE_SKIP_ORPHANS=1 — proceeding despite %d unresolved orphan(s)",
            skipped,
        )


def _step_b_sanity_check(conn) -> None:  # type: ignore[no-untyped-def]
    """Abort if any of the five legacy tables still has rows."""
    _LEGACY_TABLES = ["notes", "interactions", "gifts", "loans", "activity_feed"]
    for table in _LEGACY_TABLES:
        exists = conn.execute(text(f"SELECT to_regclass('{table}')")).scalar()  # noqa: S608
        if exists is None:
            logger.info("Step B: table %s does not exist — skipping count", table)
            continue
        count = conn.execute(text(f"SELECT count(*) FROM {table}")).scalar()  # noqa: S608
        if count and count > 0:
            raise RuntimeError(
                f"Step B: legacy table '{table}' still contains {count} row(s). "
                f"Data must be migrated or deleted before dropping the table. "
                f"The migration has been aborted — no tables have been dropped."
            )
        logger.info("Step B: table %s is empty (count=%d) — ok to drop", table, count)


def _step_c_drop_tables(conn) -> None:  # type: ignore[no-untyped-def]
    """Drop the five legacy tables."""
    conn.execute(text("DROP TABLE IF EXISTS activity_feed"))
    conn.execute(text("DROP TABLE IF EXISTS loans"))
    conn.execute(text("DROP TABLE IF EXISTS gifts"))
    conn.execute(text("DROP TABLE IF EXISTS interactions"))
    conn.execute(text("DROP TABLE IF EXISTS notes"))
    logger.info("Step C: dropped tables activity_feed, loans, gifts, interactions, notes")
