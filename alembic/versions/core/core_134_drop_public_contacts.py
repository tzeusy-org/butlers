"""core_134 — guarded DROP of public.contacts (bu-y6o7q).

Revision ID: core_134
Revises: core_133
Create Date: 2026-06-20 00:00:00.000000

Context (Phase 7.3a-3b — the FINAL, IRREVERSIBLE step)
------------------------------------------------------
``public.contacts`` is the vestigial cross-butler contact registry. Identity
resolution and every live request path were re-pointed off it onto the entity
graph / ``relationship.entity_facts`` (Phase 7 cutover, bu-irphu and siblings).
This migration removes the table itself.

DROP preconditions (all already satisfied by sibling migrations) — every inbound
FK to ``public.contacts(id)`` has been dropped:

- ``core_131`` / ``core_132`` — priority_contacts / home_assistant_persons FKs.
- ``rel_030``                 — the eight relationship-schema child-table FKs.
- ``contacts_005``            — public.contacts_source_links.local_contact_id FK.

This migration is defensive: even if some FK above was missed, step 4 sweeps and
drops *every* remaining inbound FK before the table is dropped.

upgrade() sequence (idempotent, self-guarding, single transaction)
------------------------------------------------------------------
1. **Guard** the whole body in ``IF to_regclass('public.contacts') IS NOT NULL``
   — a re-run (or a fresh/scoped provision where the table never existed) skips
   cleanly with a NOTICE.
2. **SNAPSHOT** — ``DROP TABLE IF EXISTS public.contacts_dropbak`` then
   ``CREATE TABLE public.contacts_dropbak AS TABLE public.contacts``. This is the
   PERMANENT recovery artifact (NOT temp); it persists after the drop so the data
   is recoverable. The snapshot is ALWAYS refreshed (not skipped when a prior
   dropbak exists) so it captures the exact rows being dropped — otherwise a
   downgrade -> in-place-edit -> re-upgrade cycle could leave a stale dropbak that
   passes the count-only parity check while losing those edits.
3. **PARITY RAISE** — assert ``count(public.contacts) = count(public.contacts_dropbak)``;
   RAISE EXCEPTION (abort, drop nothing) if they differ. Proves the snapshot
   copied every row before we destroy the original.
4. **FK SWEEP** — query ``pg_constraint`` for any remaining inbound FK
   (``contype='f'`` AND ``confrelid = 'public.contacts'::regclass``) and
   ``ALTER TABLE ... DROP CONSTRAINT`` each via a dynamic-SQL loop. RAISE NOTICE
   with the count dropped. This protects against any FK the sibling migrations
   did not cover.
5. **DROP** — ``DROP TABLE public.contacts`` with RESTRICT semantics (the
   default). Inbound FKs are already gone (step 4); if a *non-FK* dependent (e.g.
   a view) still references the table, we detect it first and RAISE a clear error
   rather than silently CASCADE-dropping it.

Cross-chain-migration-drop hazard (cross-chain-migration-drop-hazard)
---------------------------------------------------------------------
``public.contacts`` is *created* by ``core_002`` (this chain) and historically
*written* by other chains' migrations that reach across schemas — notably the
relationship chain's ``rel_003`` (``INSERT INTO public.contacts`` during the
relationship→public consolidation) and its FK (re)creation steps. The full
migration replay (tests/config/test_migrations.py) runs each chain start-to-finish
in sequence, so the *core* chain reaches head — and this DROP runs — BEFORE the
relationship chain runs. Those cross-chain writers are already guarded with
``to_regclass('public.contacts')`` existence checks, so once this DROP has run
they no-op cleanly (the contacts→triples migration is complete and there is
nothing to consolidate). This migration deliberately does NOT recreate the table;
the guards on the writers are what keep the replay order-independent.

Reversibility (data only — structural change is one-way)
--------------------------------------------------------
``downgrade()`` best-effort restores ``public.contacts`` from
``public.contacts_dropbak`` when the backup exists and the table does not
(``CREATE TABLE public.contacts AS TABLE public.contacts_dropbak``). It restores
*row data only* — the dropped inbound FK constraints, indexes, defaults, and
column constraints are NOT recreated (this is a one-way structural change). The
backup table is retained as the durable recovery artifact.
"""

from __future__ import annotations

from alembic import op

revision = "core_134"
down_revision = "core_133"
branch_labels = None
depends_on = None

_DROP_SQL = r"""
DO $$
DECLARE
    _live      bigint;
    _backup    bigint;
    _fk_count  bigint := 0;
    _nonfk     bigint := 0;
    _r         record;
BEGIN
    -- 1. Guard: skip cleanly if public.contacts is already gone (re-run, or a
    --    fresh/scoped provision where the table never existed).
    IF to_regclass('public.contacts') IS NULL THEN
        RAISE NOTICE 'core_134: public.contacts absent — nothing to drop';
        RETURN;
    END IF;

    -- 2. Snapshot to a PERMANENT recovery table. ALWAYS refresh so the backup
    --    reflects the exact rows about to be dropped. (A downgrade -> in-place
    --    edit -> re-upgrade cycle could otherwise leave a STALE dropbak that
    --    still passes the count-only parity check below while silently losing
    --    those edits.) In the production single forward run dropbak is absent,
    --    so this is simply a fresh snapshot; the DROP is guarded above so this
    --    only runs while public.contacts still exists.
    DROP TABLE IF EXISTS public.contacts_dropbak;
    CREATE TABLE public.contacts_dropbak AS TABLE public.contacts;

    -- 3. Parity raise: every live row must be present in the backup. Abort the
    --    whole migration (drop nothing) if the snapshot is incomplete.
    SELECT count(*) INTO _live   FROM public.contacts;
    SELECT count(*) INTO _backup FROM public.contacts_dropbak;
    IF _live <> _backup THEN
        RAISE EXCEPTION
            'core_134 parity: public.contacts has % row(s) but snapshot public.contacts_dropbak has % — refusing to DROP',
            _live, _backup;
    END IF;

    -- 4. Defensive inbound-FK sweep: drop every remaining FK that REFERENCES
    --    public.contacts(id), whichever schema/table it lives on.
    FOR _r IN
        SELECT con.conname AS fk_name,
               conrel.relnamespace::regnamespace::text AS fk_schema,
               conrel.relname AS fk_table
        FROM pg_constraint con
        JOIN pg_class conrel ON conrel.oid = con.conrelid
        WHERE con.contype = 'f'
          AND con.confrelid = 'public.contacts'::regclass
    LOOP
        EXECUTE format(
            'ALTER TABLE %I.%I DROP CONSTRAINT IF EXISTS %I',
            _r.fk_schema, _r.fk_table, _r.fk_name
        );
        _fk_count := _fk_count + 1;
    END LOOP;
    RAISE NOTICE 'core_134: dropped % remaining inbound FK constraint(s) to public.contacts', _fk_count;

    -- 5. Detect any NON-FK dependent (e.g. a view/rule) so we fail loudly with a
    --    clear message instead of silently CASCADE-dropping it.
    SELECT count(*) INTO _nonfk
    FROM pg_depend d
    JOIN pg_rewrite rw ON rw.oid = d.objid
    JOIN pg_class dep ON dep.oid = rw.ev_class
    WHERE d.refobjid = 'public.contacts'::regclass
      AND d.deptype = 'n'
      AND dep.oid <> 'public.contacts'::regclass;
    IF _nonfk <> 0 THEN
        RAISE EXCEPTION
            'core_134: % non-FK dependent object(s) (e.g. view/rule) still depend on public.contacts; '
            'resolve them before the DROP rather than CASCADE-dropping silently',
            _nonfk;
    END IF;

    -- 6. Drop the table. RESTRICT (default) — inbound FKs are already gone; any
    --    unexpected remaining dependency raises loudly here.
    DROP TABLE public.contacts;
    RAISE NOTICE 'core_134: dropped public.contacts (backup retained at public.contacts_dropbak)';
END
$$;
"""


def upgrade() -> None:
    op.execute(_DROP_SQL)


def downgrade() -> None:
    # Best-effort, data-only restore. Does NOT recreate the dropped inbound FKs,
    # indexes, or column constraints (one-way structural change). The backup
    # table is retained as the durable recovery artifact.
    op.execute(
        """
        DO $$
        BEGIN
            IF to_regclass('public.contacts') IS NULL
               AND to_regclass('public.contacts_dropbak') IS NOT NULL THEN
                CREATE TABLE public.contacts AS TABLE public.contacts_dropbak;
                RAISE NOTICE 'core_134 downgrade: restored public.contacts data from public.contacts_dropbak (FKs/constraints NOT recreated)';
            ELSE
                RAISE NOTICE 'core_134 downgrade: public.contacts present or no backup — nothing to restore';
            END IF;
        END
        $$;
        """
    )
