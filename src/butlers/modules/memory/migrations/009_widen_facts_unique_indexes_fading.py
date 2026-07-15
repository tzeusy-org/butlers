"""Widen the facts partial unique indexes to cover validity='fading'.

Revision ID: mem_009
Revises: mem_008
Create Date: 2026-07-15 00:00:00.000000

bu-agj5a: the three partial unique indexes created in ``001_memory_schema.py``
(``idx_facts_entity_scope_predicate_active`` /
``idx_facts_edge_scope_predicate_active`` /
``idx_facts_no_entity_subject_predicate_active``) enforce one-live-fact-per-key
only among ``validity = 'active'`` rows.

But a ``fading`` fact is still the live/current value for its key (low
confidence, not yet retired). ``store_fact`` (``storage.py``) upholds the real
invariant -- **at most one live (active|fading) property fact per
``(key)``** -- by finding the existing ``validity IN ('active','fading')`` row
and marking it ``superseded`` *before* inserting the new active row (PR #3162
widened that supersession lookup to include fading). That ordering means the
legitimate write path never transiently holds a fading row and a new active row
for the same key, so it cannot trip a widened index.

The gap the active-only index leaves is a **concurrent-write race**: two
interleaved writers (or a write racing a fading/recovery transition) could land
a fresh active fact alongside an un-superseded fading fact for the same key, and
the DB would not object -- an invalid ``fading`` + ``active`` coexistence. This
migration closes that at the storage layer by widening the unique predicate to
``validity IN ('active', 'fading')`` (still property-facts-only,
``valid_at IS NULL``; temporal facts continue to coexist freely).

The index NAMES keep the historical ``_active`` suffix (renaming would churn for
no functional gain); they now cover the live active-or-fading set.

Self-guarding (cross-chain drop hazard + opaque-failure avoidance):
  - ``to_regclass('facts')`` no-op on a fresh/pre-memory schema.
  - PRE-FLIGHT: count any key that already has >1 live (active|fading) property
    fact under each of the three widened predicates and RAISE with per-index
    counts, rather than letting ``CREATE UNIQUE INDEX`` fail mid-build with an
    opaque unique-violation. (Verified 0 such rows across all live schemas on
    2026-07-15; this guard is defense for any that appear before rollout.)

Applied per butler schema like every migration in this module chain
(``src/butlers/migrations.py`` runs the ``memory`` chain once per schema);
unqualified ``facts`` resolves via each schema's search_path.

``downgrade()`` restores the original ``validity = 'active'`` predicates.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "mem_009"
down_revision = "mem_008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        DO $$
        DECLARE
            v_entity  BIGINT;
            v_edge    BIGINT;
            v_subject BIGINT;
        BEGIN
            IF to_regclass('facts') IS NULL THEN
                RETURN;
            END IF;

            -- Pre-flight: any key already holding >1 live (active|fading)
            -- property fact would make CREATE UNIQUE INDEX fail opaquely.
            SELECT COALESCE(sum(cnt - 1), 0) INTO v_entity FROM (
                SELECT count(*) AS cnt FROM facts
                WHERE entity_id IS NOT NULL AND object_entity_id IS NULL
                  AND validity IN ('active', 'fading') AND valid_at IS NULL
                GROUP BY entity_id, scope, predicate HAVING count(*) > 1
            ) e;
            SELECT COALESCE(sum(cnt - 1), 0) INTO v_edge FROM (
                SELECT count(*) AS cnt FROM facts
                WHERE object_entity_id IS NOT NULL
                  AND validity IN ('active', 'fading') AND valid_at IS NULL
                GROUP BY entity_id, object_entity_id, scope, predicate HAVING count(*) > 1
            ) g;
            SELECT COALESCE(sum(cnt - 1), 0) INTO v_subject FROM (
                SELECT count(*) AS cnt FROM facts
                WHERE entity_id IS NULL
                  AND validity IN ('active', 'fading') AND valid_at IS NULL
                GROUP BY scope, subject, predicate HAVING count(*) > 1
            ) s;

            IF (v_entity + v_edge + v_subject) > 0 THEN
                RAISE EXCEPTION
                    'mem_009: cannot widen facts unique indexes -- % pre-existing '
                    'live(active|fading) duplicate rows (entity=%, edge=%, subject=%). '
                    'Supersede or expire the stale coexisting fact before applying.',
                    v_entity + v_edge + v_subject, v_entity, v_edge, v_subject;
            END IF;

            RAISE NOTICE
                'mem_009: no live-duplicate violations; widening facts unique '
                'indexes to validity IN (active, fading)';

            EXECUTE 'DROP INDEX IF EXISTS idx_facts_entity_scope_predicate_active';
            EXECUTE 'DROP INDEX IF EXISTS idx_facts_edge_scope_predicate_active';
            EXECUTE 'DROP INDEX IF EXISTS idx_facts_no_entity_subject_predicate_active';

            EXECUTE $ix$
                CREATE UNIQUE INDEX idx_facts_entity_scope_predicate_active
                ON facts (entity_id, scope, predicate)
                WHERE entity_id IS NOT NULL
                  AND object_entity_id IS NULL
                  AND validity IN ('active', 'fading')
                  AND valid_at IS NULL
            $ix$;
            EXECUTE $ix$
                CREATE UNIQUE INDEX idx_facts_edge_scope_predicate_active
                ON facts (entity_id, object_entity_id, scope, predicate)
                WHERE object_entity_id IS NOT NULL
                  AND validity IN ('active', 'fading')
                  AND valid_at IS NULL
            $ix$;
            EXECUTE $ix$
                CREATE UNIQUE INDEX idx_facts_no_entity_subject_predicate_active
                ON facts (scope, subject, predicate)
                WHERE entity_id IS NULL
                  AND validity IN ('active', 'fading')
                  AND valid_at IS NULL
            $ix$;
        END
        $$;
    """)


def downgrade() -> None:
    op.execute("""
        DO $$
        BEGIN
            IF to_regclass('facts') IS NULL THEN
                RETURN;
            END IF;

            EXECUTE 'DROP INDEX IF EXISTS idx_facts_entity_scope_predicate_active';
            EXECUTE 'DROP INDEX IF EXISTS idx_facts_edge_scope_predicate_active';
            EXECUTE 'DROP INDEX IF EXISTS idx_facts_no_entity_subject_predicate_active';

            EXECUTE $ix$
                CREATE UNIQUE INDEX idx_facts_entity_scope_predicate_active
                ON facts (entity_id, scope, predicate)
                WHERE entity_id IS NOT NULL
                  AND object_entity_id IS NULL
                  AND validity = 'active'
                  AND valid_at IS NULL
            $ix$;
            EXECUTE $ix$
                CREATE UNIQUE INDEX idx_facts_edge_scope_predicate_active
                ON facts (entity_id, object_entity_id, scope, predicate)
                WHERE object_entity_id IS NOT NULL
                  AND validity = 'active'
                  AND valid_at IS NULL
            $ix$;
            EXECUTE $ix$
                CREATE UNIQUE INDEX idx_facts_no_entity_subject_predicate_active
                ON facts (scope, subject, predicate)
                WHERE entity_id IS NULL
                  AND validity = 'active'
                  AND valid_at IS NULL
            $ix$;
        END
        $$;
    """)
