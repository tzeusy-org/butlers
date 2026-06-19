"""rel_030 — dedup duplicate-entity contacts + drop relationship→public.contacts FKs (bu-vcfyg).

Revision ID: rel_030
Revises: rel_029
Create Date: 2026-06-20 00:00:00.000000

Context (Phase 7.3a-3b — DROP precondition for bu-y6o7q)
--------------------------------------------------------
``public.contacts`` cannot be dropped (bu-y6o7q) while live FK constraints
reference ``public.contacts(id)``.  Eight such constraints live on
relationship-schema child tables::

    relationship.addresses.contact_id          -> contacts(id)
    relationship.contact_labels.contact_id     -> contacts(id)
    relationship.group_members.contact_id      -> contacts(id)
    relationship.important_dates.contact_id    -> contacts(id)
    relationship.life_events.contact_id        -> contacts(id)
    relationship.relationships.contact_a       -> contacts(id)
    relationship.relationships.contact_b       -> contacts(id)
    relationship.tasks.contact_id              -> contacts(id)

(The ninth, ``public.contacts_source_links.local_contact_id``, plus the
public/connectors dedup, is handled by the sibling core migration ``core_133``;
the two form one logical change split by chain ownership — relationship-schema
objects here, public/connectors objects in core.)

What this migration does
------------------------
1. **Dedup collision** (owner-approved policy, bu-oluyt): where 2+ ``contacts``
   rows share one ``entity_id``, all dependent relationship rows are merged onto
   a single **canonical** contact (the OLDEST by ``created_at``, ``id`` tie-break).
   ``public.contacts`` carries no ``roles`` column — owner-role is an *entity*
   property and is therefore uniform across a single entity's contact set, so it
   cannot tie-break here; ``created_at`` governs (matching the bead's "keep the
   oldest" rule). Non-canonical ``contact_entity_map`` rows are deleted so the
   map becomes 1:1 (entity -> one contact).
2. **Repoint** the duplicate contacts' dependent rows onto the canonical contact,
   deleting rows that would collide on a contact_id-bearing PRIMARY KEY
   (``contact_labels`` PK (label_id, contact_id); ``group_members`` PK
   (group_id, contact_id)) and self-referential ``relationships`` rows
   (``contact_a = contact_b``) produced by merging two related contacts.
3. **Snapshot** every mutated table to ``<table>_dedup_bak_rel_030`` BEFORE
   mutating (reversibility + audit; migration doctrine).
4. **Parity guard** — RAISE (abort) unless: (a) no dependent row still references
   a non-canonical (source) contact after the repoint, (b) the map is 1:1, and
   (c) per-table row counts reconcile with the explicitly-counted deletions.
5. **Drop the 8 FK constraints** so ``public.contacts`` becomes droppable.

Safety / doctrine (cross-chain-migration-drop-hazard)
-----------------------------------------------------
- ``to_regclass`` guards every cross-schema reference; the whole dedup no-ops
  cleanly if ``public.contacts`` is already gone (post-DROP re-run / fresh DB).
- Snapshot-before-mutate + row-count parity raise.
- Idempotent: snapshots are taken only when absent; the repoint/deletes are
  no-ops on re-run (no duplicate contacts remain); FK drops are IF EXISTS.
- NO ``public.contacts`` row is deleted here (bu-y6o7q drops the whole table).
- Reversible: ``downgrade`` restores each table from its snapshot and re-adds the
  FK constraints (best-effort, skipped if ``public.contacts`` is gone).

Schema qualification
--------------------
Relationship tables are referenced UNQUALIFIED — ``search_path`` resolves them to
the ``relationship`` schema in production and to ``public`` in schema-less test
runs (mirrors every other relationship migration + ``_entity_resolve`` doctrine).
``public.contacts`` is fully qualified (unambiguous; ``public`` is always on the
search_path).
"""

from __future__ import annotations

from alembic import op

revision = "rel_030"
down_revision = "rel_029"
branch_labels = None
depends_on = None

# Relationship child tables whose contact column FKs public.contacts(id).
# (table, contact_column, fk_constraint_name, on_delete_for_downgrade)
_FK_TABLES: list[tuple[str, str, str, str]] = [
    ("addresses", "contact_id", "addresses_contact_id_fkey", "CASCADE"),
    ("contact_labels", "contact_id", "contact_labels_contact_id_fkey", "CASCADE"),
    ("group_members", "contact_id", "group_members_contact_id_fkey", "CASCADE"),
    ("important_dates", "contact_id", "important_dates_contact_id_fkey", "CASCADE"),
    ("life_events", "contact_id", "life_events_contact_id_fkey", "CASCADE"),
    ("relationships", "contact_a", "relationships_contact_a_fkey", "CASCADE"),
    ("relationships", "contact_b", "relationships_contact_b_fkey", "CASCADE"),
    ("tasks", "contact_id", "tasks_contact_id_fkey", "CASCADE"),
]

# Tables snapshotted (full copy) before mutation, for reversibility + parity.
_SNAPSHOT_TABLES = [
    "addresses",
    "contact_labels",
    "group_members",
    "important_dates",
    "life_events",
    "relationships",
    "tasks",
    "contact_entity_map",
]

# ---------------------------------------------------------------------------
# Snapshot + dedup + parity — one DO block (single session: TEMP map visible
# throughout, atomic within the migration transaction).
# ---------------------------------------------------------------------------
_SNAPSHOT_AND_DEDUP_SQL = r"""
DO $$
DECLARE
    _src_refs   bigint;
    _map_dupes  bigint;
    _lost       bigint;
BEGIN
    -- No-op when public.contacts is already gone (post-DROP / fresh DB):
    -- the FK constraints cannot exist either, so there is nothing to dedup.
    IF to_regclass('public.contacts') IS NULL THEN
        RAISE NOTICE 'rel_030: public.contacts absent — skipping dedup';
        RETURN;
    END IF;

    -- 1. Snapshot each mutated table (only if not already snapshotted, so a
    --    re-run preserves the original pre-dedup state).
    IF to_regclass('addresses_dedup_bak_rel_030') IS NULL THEN
        CREATE TABLE addresses_dedup_bak_rel_030 AS SELECT * FROM addresses;
    END IF;
    IF to_regclass('contact_labels_dedup_bak_rel_030') IS NULL THEN
        CREATE TABLE contact_labels_dedup_bak_rel_030 AS SELECT * FROM contact_labels;
    END IF;
    IF to_regclass('group_members_dedup_bak_rel_030') IS NULL THEN
        CREATE TABLE group_members_dedup_bak_rel_030 AS SELECT * FROM group_members;
    END IF;
    IF to_regclass('important_dates_dedup_bak_rel_030') IS NULL THEN
        CREATE TABLE important_dates_dedup_bak_rel_030 AS SELECT * FROM important_dates;
    END IF;
    IF to_regclass('life_events_dedup_bak_rel_030') IS NULL THEN
        CREATE TABLE life_events_dedup_bak_rel_030 AS SELECT * FROM life_events;
    END IF;
    IF to_regclass('relationships_dedup_bak_rel_030') IS NULL THEN
        CREATE TABLE relationships_dedup_bak_rel_030 AS SELECT * FROM relationships;
    END IF;
    IF to_regclass('tasks_dedup_bak_rel_030') IS NULL THEN
        CREATE TABLE tasks_dedup_bak_rel_030 AS SELECT * FROM tasks;
    END IF;
    IF to_regclass('contact_entity_map_dedup_bak_rel_030') IS NULL THEN
        CREATE TABLE contact_entity_map_dedup_bak_rel_030 AS SELECT * FROM contact_entity_map;
    END IF;

    -- 2. Build the source -> canonical map for every duplicate contact.
    --    Canonical = oldest contact (created_at, id tie-break) per entity_id.
    DROP TABLE IF EXISTS _rel030_ddm;
    CREATE TEMP TABLE _rel030_ddm ON COMMIT DROP AS
        SELECT id AS source_id, canonical_id
        FROM (
            SELECT id,
                   first_value(id) OVER (
                       PARTITION BY entity_id ORDER BY created_at ASC, id ASC
                   ) AS canonical_id
            FROM public.contacts
            WHERE entity_id IS NOT NULL
        ) ranked
        WHERE id <> canonical_id;

    -- 3a. Collision-delete on contact_id-bearing composite PKs, THEN repoint.
    --     contact_labels PK (label_id, contact_id)
    DELETE FROM contact_labels cl
    USING _rel030_ddm m
    WHERE cl.contact_id = m.source_id
      AND EXISTS (
          SELECT 1 FROM contact_labels x
          WHERE x.contact_id = m.canonical_id AND x.label_id = cl.label_id
      );
    UPDATE contact_labels cl
    SET contact_id = m.canonical_id
    FROM _rel030_ddm m
    WHERE cl.contact_id = m.source_id;

    --     group_members PK (group_id, contact_id)
    DELETE FROM group_members gm
    USING _rel030_ddm m
    WHERE gm.contact_id = m.source_id
      AND EXISTS (
          SELECT 1 FROM group_members x
          WHERE x.contact_id = m.canonical_id AND x.group_id = gm.group_id
      );
    UPDATE group_members gm
    SET contact_id = m.canonical_id
    FROM _rel030_ddm m
    WHERE gm.contact_id = m.source_id;

    -- 3b. Plain repoints (PK is a surrogate id — no contact collision).
    UPDATE addresses a       SET contact_id = m.canonical_id FROM _rel030_ddm m WHERE a.contact_id = m.source_id;
    UPDATE important_dates d  SET contact_id = m.canonical_id FROM _rel030_ddm m WHERE d.contact_id = m.source_id;
    UPDATE life_events le     SET contact_id = m.canonical_id FROM _rel030_ddm m WHERE le.contact_id = m.source_id;
    UPDATE tasks t            SET contact_id = m.canonical_id FROM _rel030_ddm m WHERE t.contact_id = m.source_id;

    -- 3c. relationships: repoint both endpoints, then delete self-loops produced
    --     by merging two contacts that had a relationship with each other.
    UPDATE relationships r SET contact_a = m.canonical_id FROM _rel030_ddm m WHERE r.contact_a = m.source_id;
    UPDATE relationships r SET contact_b = m.canonical_id FROM _rel030_ddm m WHERE r.contact_b = m.source_id;
    DELETE FROM relationships WHERE contact_a = contact_b;

    -- 3d. contact_entity_map PK (contact_id): delete the non-canonical rows so
    --     the map is 1:1 (entity -> one surviving contact).
    DELETE FROM contact_entity_map cem
    USING _rel030_ddm m
    WHERE cem.contact_id = m.source_id;

    -- 4. Parity guards (RAISE aborts the migration).
    -- 4a. Repoint completeness: no dependent row may still reference a source id.
    SELECT count(*) INTO _src_refs FROM (
        SELECT 1 FROM addresses a       JOIN _rel030_ddm m ON a.contact_id  = m.source_id
        UNION ALL SELECT 1 FROM contact_labels cl  JOIN _rel030_ddm m ON cl.contact_id = m.source_id
        UNION ALL SELECT 1 FROM group_members gm   JOIN _rel030_ddm m ON gm.contact_id = m.source_id
        UNION ALL SELECT 1 FROM important_dates d  JOIN _rel030_ddm m ON d.contact_id  = m.source_id
        UNION ALL SELECT 1 FROM life_events le     JOIN _rel030_ddm m ON le.contact_id = m.source_id
        UNION ALL SELECT 1 FROM tasks t            JOIN _rel030_ddm m ON t.contact_id  = m.source_id
        UNION ALL SELECT 1 FROM relationships r    JOIN _rel030_ddm m ON r.contact_a   = m.source_id
        UNION ALL SELECT 1 FROM relationships r    JOIN _rel030_ddm m ON r.contact_b   = m.source_id
        UNION ALL SELECT 1 FROM contact_entity_map c JOIN _rel030_ddm m ON c.contact_id = m.source_id
    ) leftovers;
    IF _src_refs <> 0 THEN
        RAISE EXCEPTION 'rel_030 parity: % dependent rows still reference a non-canonical contact', _src_refs;
    END IF;

    -- 4b. Map must be 1:1 (no entity with >1 surviving contact row).
    SELECT count(*) INTO _map_dupes FROM (
        SELECT entity_id FROM contact_entity_map GROUP BY entity_id HAVING count(*) > 1
    ) d;
    IF _map_dupes <> 0 THEN
        RAISE EXCEPTION 'rel_030 parity: contact_entity_map still has % entity ids mapped to >1 contact', _map_dupes;
    END IF;

    -- 4c. Loss guard: every snapshot logical row must survive on the canonical
    --     contact.  Collapse each snapshot row to its canonical contact and
    --     require a matching live row.  (id-keyed tables: the surrogate id is
    --     preserved by UPDATE, so match by id; composite-PK tables: match by the
    --     canonicalized natural key; relationships self-loops are expected-gone.)
    SELECT
        (SELECT count(*) FROM addresses_dedup_bak_rel_030 s
             WHERE NOT EXISTS (SELECT 1 FROM addresses a WHERE a.id = s.id))
      + (SELECT count(*) FROM important_dates_dedup_bak_rel_030 s
             WHERE NOT EXISTS (SELECT 1 FROM important_dates d WHERE d.id = s.id))
      + (SELECT count(*) FROM life_events_dedup_bak_rel_030 s
             WHERE NOT EXISTS (SELECT 1 FROM life_events le WHERE le.id = s.id))
      + (SELECT count(*) FROM tasks_dedup_bak_rel_030 s
             WHERE NOT EXISTS (SELECT 1 FROM tasks t WHERE t.id = s.id))
      + (SELECT count(*) FROM contact_labels_dedup_bak_rel_030 s
             WHERE NOT EXISTS (
                 SELECT 1 FROM contact_labels cl
                 WHERE cl.label_id = s.label_id
                   AND cl.contact_id = COALESCE(
                       (SELECT canonical_id FROM _rel030_ddm m WHERE m.source_id = s.contact_id),
                       s.contact_id)))
      + (SELECT count(*) FROM group_members_dedup_bak_rel_030 s
             WHERE NOT EXISTS (
                 SELECT 1 FROM group_members gm
                 WHERE gm.group_id = s.group_id
                   AND gm.contact_id = COALESCE(
                       (SELECT canonical_id FROM _rel030_ddm m WHERE m.source_id = s.contact_id),
                       s.contact_id)))
      + (SELECT count(*) FROM relationships_dedup_bak_rel_030 s
             WHERE COALESCE((SELECT canonical_id FROM _rel030_ddm m WHERE m.source_id = s.contact_a), s.contact_a)
                 <> COALESCE((SELECT canonical_id FROM _rel030_ddm m WHERE m.source_id = s.contact_b), s.contact_b)
               AND NOT EXISTS (SELECT 1 FROM relationships r WHERE r.id = s.id))
      INTO _lost;
    IF _lost <> 0 THEN
        RAISE EXCEPTION 'rel_030 parity: % snapshot rows did not survive onto a canonical contact', _lost;
    END IF;
END
$$;
"""

# ---------------------------------------------------------------------------
# Drop the 8 FK constraints (idempotent; safe whether or not public.contacts
# still exists — if it was dropped first, the constraints are already gone).
# ---------------------------------------------------------------------------
_DROP_FK_SQL = r"""
DO $$
BEGIN
    IF to_regclass('addresses') IS NOT NULL THEN
        ALTER TABLE addresses        DROP CONSTRAINT IF EXISTS addresses_contact_id_fkey;
    END IF;
    IF to_regclass('contact_labels') IS NOT NULL THEN
        ALTER TABLE contact_labels   DROP CONSTRAINT IF EXISTS contact_labels_contact_id_fkey;
    END IF;
    IF to_regclass('group_members') IS NOT NULL THEN
        ALTER TABLE group_members    DROP CONSTRAINT IF EXISTS group_members_contact_id_fkey;
    END IF;
    IF to_regclass('important_dates') IS NOT NULL THEN
        ALTER TABLE important_dates  DROP CONSTRAINT IF EXISTS important_dates_contact_id_fkey;
    END IF;
    IF to_regclass('life_events') IS NOT NULL THEN
        ALTER TABLE life_events      DROP CONSTRAINT IF EXISTS life_events_contact_id_fkey;
    END IF;
    IF to_regclass('relationships') IS NOT NULL THEN
        ALTER TABLE relationships    DROP CONSTRAINT IF EXISTS relationships_contact_a_fkey;
        ALTER TABLE relationships    DROP CONSTRAINT IF EXISTS relationships_contact_b_fkey;
    END IF;
    IF to_regclass('tasks') IS NOT NULL THEN
        ALTER TABLE tasks            DROP CONSTRAINT IF EXISTS tasks_contact_id_fkey;
    END IF;
END
$$;
"""


def upgrade() -> None:
    op.execute(_SNAPSHOT_AND_DEDUP_SQL)
    op.execute(_DROP_FK_SQL)


def downgrade() -> None:
    # Re-add the FK constraints (best-effort) then restore the pre-dedup table
    # contents from the snapshots. Skipped cleanly if public.contacts is gone.
    restore_stmts = "\n".join(
        f"""
        IF to_regclass('{tbl}_dedup_bak_rel_030') IS NOT NULL THEN
            DELETE FROM {tbl};
            INSERT INTO {tbl} SELECT * FROM {tbl}_dedup_bak_rel_030;
            DROP TABLE {tbl}_dedup_bak_rel_030;
        END IF;"""
        for tbl in _SNAPSHOT_TABLES
    )
    readd_fks = "\n".join(
        f"""
        IF to_regclass('{tbl}') IS NOT NULL
           AND NOT EXISTS (
               SELECT 1 FROM information_schema.table_constraints
               WHERE table_name = '{tbl}' AND constraint_name = '{fk}'
                 AND constraint_type = 'FOREIGN KEY'
           ) THEN
            ALTER TABLE {tbl}
                ADD CONSTRAINT {fk} FOREIGN KEY ({col})
                REFERENCES public.contacts(id) ON DELETE {ondel};
        END IF;"""
        for tbl, col, fk, ondel in _FK_TABLES
    )
    op.execute(
        f"""
        DO $$
        BEGIN
            IF to_regclass('public.contacts') IS NULL THEN
                RAISE NOTICE 'rel_030 downgrade: public.contacts absent — restore only';
            END IF;
            -- 1. Restore snapshotted contents (reverses the dedup).
            {restore_stmts}
            -- 2. Re-add FK constraints (skipped if public.contacts is gone).
            IF to_regclass('public.contacts') IS NOT NULL THEN
                {readd_fks}
            END IF;
        END
        $$;
        """
    )
