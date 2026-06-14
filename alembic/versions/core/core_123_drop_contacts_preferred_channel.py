"""drop public.contacts.preferred_channel (write-orphaned) → prefers-channel facts.

Issue: bu-1yihq.  Parent epic: bu-sbdwt (entity-keyed-preferred-channel).
Spec: openspec/changes/entity-keyed-preferred-channel/specs/contacts-identity/spec.md

Context
-------
``public.contacts.preferred_channel`` (VARCHAR, CHECK IN ('telegram','email'),
created by core_002) is now **write-orphaned**: PR #2244 (bu-g0y3m) removed the
last writer, and the entity-keyed single-valued ``prefers-channel`` fact in
``relationship.entity_facts`` is the sole write path. This migration retires the
column.

Self-guarding design (mirrors core_115 / core_118)
--------------------------------------------------
``upgrade()`` refuses to drop the column if doing so would silently lose data:

1. **Idempotency** — if the column is already gone (re-run / fresh DB where the
   create-with-drop has collapsed), no-op.
2. **Snapshot** — copies every non-null ``(id, entity_id, preferred_channel)``
   triple to ``public.contacts_preferred_channel_dropbak_core_123`` so the drop
   is recoverable.
3. **Data migration** — for each contact whose ``preferred_channel`` is non-null
   AND whose ``entity_id`` resolves, assert a single-valued ``prefers-channel``
   fact on the linked entity (replicating ``assert_prefers_channel`` in raw SQL):
   the entity-keyed fact store is authoritative, so entities that ALREADY have an
   active ``prefers-channel`` fact are left untouched (the live preference wins);
   only entities with no active fact are backfilled, one active fact per entity.
   This runs **only** when ``relationship.entity_facts`` exists (cross-chain: on a
   fresh provision the relationship chain may not have run yet — guarded by
   ``to_regclass``).
4. **Parity guard** — after the backfill, every snapshotted, entity-linked,
   reachable-and-non-preexisting row must have a corresponding active
   ``prefers-channel`` fact. A non-zero gap **raises** and drops nothing
   (override: ``PREFERRED_CHANNEL_DROP_FORCE=1`` with owner sign-off; snapshot is
   still taken).
5. **Drop** — ``ALTER TABLE public.contacts DROP COLUMN preferred_channel`` only
   once parity is clean.

Cross-chain hazard (cross-chain-migration-drop-hazard)
------------------------------------------------------
The relationship chain's rel_003 (``003_consolidate_contacts_to_public``) does
``INSERT INTO public.contacts (... preferred_channel ...)``. Multiple alembic
version_locations have NO guaranteed ordering, so on a fresh provision this
core_123 drop may run BEFORE rel_003 and the column would be missing when rel_003
INSERTs. rel_003 is amended (same change) to detect column presence with
``to_regclass``-style ``information_schema`` lookup and omit ``preferred_channel``
from its INSERT when the column is gone — making the two order-independent.

Reversibility
-------------
``downgrade()`` re-adds the column (+ CHECK) and restores values from the
snapshot when present. Best-effort: the canonical store after this migration is
the ``prefers-channel`` fact; the snapshot is a recovery aid, not a sync source.
"""

from __future__ import annotations

import logging
import os

import sqlalchemy as sa

from alembic import op

logger = logging.getLogger("alembic.runtime.migration")

# revision identifiers, used by Alembic.
revision = "core_123"
down_revision = "core_122"
branch_labels = None
depends_on = None

_BACKUP_TABLE = "public.contacts_preferred_channel_dropbak_core_123"
_PREDICATE = "prefers-channel"


def _forced() -> bool:
    return os.environ.get("PREFERRED_CHANNEL_DROP_FORCE") in ("1", "true", "yes")


def _column_exists(bind, schema: str, table: str, column: str) -> bool:
    return bool(
        bind.execute(
            sa.text(
                """
                SELECT 1
                FROM information_schema.columns
                WHERE table_schema = :schema
                  AND table_name   = :table
                  AND column_name  = :column
                """
            ),
            {"schema": schema, "table": table, "column": column},
        ).scalar()
    )


# Backfill: for entities that have a snapshotted non-null preferred_channel but
# NO active prefers-channel fact yet, insert exactly ONE active fact per entity.
# DISTINCT ON collapses multiple contacts sharing an entity to a single row
# (the entity-keyed preference is single-valued); the chosen value is the most
# recently-updated contact's channel, a stable deterministic tie-break.
_BACKFILL_SQL = sa.text(
    """
    INSERT INTO relationship.entity_facts (
        id, subject, predicate, object, object_kind,
        src, conf, verified, validity, created_at, updated_at
    )
    SELECT
        gen_random_uuid(), src.entity_id, :predicate, src.preferred_channel,
        'literal', 'migration:core_123', 1.0, true, 'active', now(), now()
    FROM (
        SELECT DISTINCT ON (c.entity_id)
            c.entity_id, c.preferred_channel
        FROM public.contacts c
        WHERE c.preferred_channel IS NOT NULL
          AND c.entity_id IS NOT NULL
        ORDER BY c.entity_id, c.updated_at DESC NULLS LAST, c.id
    ) AS src
    WHERE NOT EXISTS (
        SELECT 1 FROM relationship.entity_facts ef
        WHERE ef.subject   = src.entity_id
          AND ef.predicate = :predicate
          AND ef.validity  = 'active'
    )
    """
)

# Parity guard: count entity-linked, non-null preferred_channel rows whose entity
# has NO active prefers-channel fact after the backfill. Zero = clean.
_PARITY_SQL = sa.text(
    """
    SELECT count(*) AS n
    FROM (
        SELECT DISTINCT c.entity_id
        FROM public.contacts c
        WHERE c.preferred_channel IS NOT NULL
          AND c.entity_id IS NOT NULL
    ) AS linked
    WHERE NOT EXISTS (
        SELECT 1 FROM relationship.entity_facts ef
        WHERE ef.subject   = linked.entity_id
          AND ef.predicate = :predicate
          AND ef.validity  = 'active'
    )
    """
)


def upgrade() -> None:
    bind = op.get_bind()

    # 1. Idempotency — column already gone → nothing to do.
    if not _column_exists(bind, "public", "contacts", "preferred_channel"):
        return

    # 2. Snapshot the surviving non-null values for recovery.
    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_BACKUP_TABLE} AS
        SELECT id, entity_id, preferred_channel
        FROM public.contacts
        WHERE preferred_channel IS NOT NULL
        """
    )

    facts_present = (
        bind.execute(sa.text("SELECT to_regclass('relationship.entity_facts')")).scalar()
        is not None
    )

    if facts_present:
        # 3. Data-migrate column → single-valued prefers-channel fact.
        bind.execute(_BACKFILL_SQL, {"predicate": _PREDICATE})

        # 4. Parity guard — every entity-linked preference must now have a fact.
        gap = int(bind.execute(_PARITY_SQL, {"predicate": _PREDICATE}).scalar() or 0)
        if gap > 0:
            msg = (
                f"core_123 ABORTED: {gap} entity-linked contact(s) carry a non-null "
                f"preferred_channel with no active 'prefers-channel' fact after backfill; "
                f"dropping the column would silently lose them. A full snapshot was taken "
                f"at {_BACKUP_TABLE}. Set PREFERRED_CHANNEL_DROP_FORCE=1 with explicit "
                f"owner sign-off to accept the loss."
            )
            if not _forced():
                raise RuntimeError(msg)
            logger.warning("PREFERRED_CHANNEL_DROP_FORCE override: %s", msg)
    else:
        # Relationship chain absent (fresh isolated core provision). We cannot
        # migrate values into a fact store that does not exist, so only proceed
        # when there is nothing to lose; otherwise raise.
        remaining = bind.execute(
            sa.text("SELECT count(*) FROM public.contacts WHERE preferred_channel IS NOT NULL")
        ).scalar()
        if remaining and not _forced():
            raise RuntimeError(
                f"core_123 ABORTED: relationship.entity_facts is absent so the "
                f"preferred_channel values cannot be migrated to facts, but "
                f"public.contacts still holds {remaining} non-null preferred_channel "
                f"row(s). Apply the relationship chain first, or set "
                f"PREFERRED_CHANNEL_DROP_FORCE=1 with owner sign-off. A full snapshot "
                f"was taken at {_BACKUP_TABLE}."
            )

    # 5. Drop the column (CHECK constraint drops with it).
    op.execute("ALTER TABLE public.contacts DROP COLUMN IF EXISTS preferred_channel")


def downgrade() -> None:
    bind = op.get_bind()

    # Re-add the column + CHECK (core_002 shape) if it is missing.
    if not _column_exists(bind, "public", "contacts", "preferred_channel"):
        op.execute(
            """
            ALTER TABLE public.contacts
                ADD COLUMN preferred_channel VARCHAR
                CONSTRAINT contacts_preferred_channel_check
                CHECK (preferred_channel IN ('telegram', 'email'))
            """
        )

    # Restore values from the snapshot if it is still present.
    if bind.execute(sa.text(f"SELECT to_regclass('{_BACKUP_TABLE}')")).scalar() is not None:
        op.execute(
            f"""
            UPDATE public.contacts c
            SET preferred_channel = b.preferred_channel
            FROM {_BACKUP_TABLE} b
            WHERE c.id = b.id
              AND c.preferred_channel IS NULL
            """
        )
