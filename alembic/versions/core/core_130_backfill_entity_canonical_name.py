"""Backfill ``public.entities.canonical_name`` from linked ``public.contacts.name``.

Revision ID: core_130
Revises: core_129
Create Date: 2026-06-19 00:00:00.000000

Motivation (bu-jnaa3, Phase 7.3a)
---------------------------------
``public.contacts`` is being retired: a contact IS an entity of type person.
Several read surfaces still join ``public.contacts`` purely to fetch a display
name. Before those reads can move onto ``public.entities.canonical_name``, every
entity that has a linked contact must carry that contact's name.

This migration is **additive and reversible**. It only fills entities whose
``canonical_name`` is *missing* (NULL or blank/whitespace) from the name of a
linked contact — it never overwrites a non-empty canonical name. In practice
most entities already have a canonical name (the column is ``NOT NULL`` and
``create_temp_contact`` seeds it), so this is a safety net that guarantees no
entity is left nameless once name-reads move off ``public.contacts``.

Multiplicity
------------
If more than one contact links to the same entity, the most-recently-updated
contact wins (``DISTINCT ON (entity_id) ... ORDER BY updated_at DESC``), matching
the single-valued convention used elsewhere in the identity chain.

Reversibility
-------------
``upgrade()`` snapshots every entity's prior ``canonical_name`` (only for the
rows it is about to change) into
``public.entities_canonical_name_bak_core_130`` before writing. ``downgrade()``
restores from that snapshot and drops it. Rows whose prior name was NULL cannot
be restored under the ``NOT NULL`` constraint and are left filled (harmless).

Forward-compatibility
---------------------
The whole backfill is guarded by ``to_regclass('public.contacts')`` so it
no-ops cleanly if run after the contacts table has already been dropped
(Phase 7.3b) — e.g. a schema-scoped re-run of the core chain.
"""

from __future__ import annotations

from sqlalchemy import text

from alembic import op

revision = "core_130"
down_revision = "core_129"
branch_labels = None
depends_on = None

_SNAPSHOT_TABLE = "public.entities_canonical_name_bak_core_130"

# Deterministic single contact-name per entity (most recently updated wins),
# restricted to contacts that carry a usable (non-blank) name.
_SRC_SUBQUERY = """
    SELECT DISTINCT ON (c.entity_id) c.entity_id, c.name
    FROM public.contacts c
    WHERE c.entity_id IS NOT NULL
      AND c.name IS NOT NULL
      AND btrim(c.name) <> ''
    ORDER BY c.entity_id, c.updated_at DESC NULLS LAST
"""

# Snapshot the rows we are about to change (only entities with a missing name).
_SNAPSHOT_SQL = text(
    f"""
    INSERT INTO {_SNAPSHOT_TABLE} (entity_id, old_canonical_name)
    SELECT e.id, e.canonical_name
    FROM public.entities e
    JOIN ({_SRC_SUBQUERY}) AS src ON e.id = src.entity_id
    WHERE e.canonical_name IS NULL OR btrim(e.canonical_name) = ''
    ON CONFLICT (entity_id) DO NOTHING
    """
)

# Fill the missing canonical name from the linked contact's name.
_BACKFILL_SQL = text(
    f"""
    UPDATE public.entities e
    SET canonical_name = src.name,
        updated_at = now()
    FROM ({_SRC_SUBQUERY}) AS src
    WHERE e.id = src.entity_id
      AND (e.canonical_name IS NULL OR btrim(e.canonical_name) = '')
    """
)


def upgrade() -> None:
    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_SNAPSHOT_TABLE} (
            entity_id          UUID PRIMARY KEY,
            old_canonical_name VARCHAR
        )
        """
    )
    # Guard the data migration so it is a clean no-op if public.contacts is gone.
    op.execute(
        f"""
        DO $$
        BEGIN
            IF to_regclass('public.contacts') IS NOT NULL THEN
                {_SNAPSHOT_SQL.text}
                ;
                {_BACKFILL_SQL.text}
                ;
            END IF;
        END
        $$;
        """
    )


def downgrade() -> None:
    op.execute(
        f"""
        DO $$
        BEGIN
            IF to_regclass('{_SNAPSHOT_TABLE}') IS NOT NULL THEN
                UPDATE public.entities e
                SET canonical_name = b.old_canonical_name,
                    updated_at = now()
                FROM {_SNAPSHOT_TABLE} b
                WHERE e.id = b.entity_id
                  AND b.old_canonical_name IS NOT NULL
                  AND btrim(b.old_canonical_name) <> '';
            END IF;
        END
        $$;
        """
    )
    op.execute(f"DROP TABLE IF EXISTS {_SNAPSHOT_TABLE}")
