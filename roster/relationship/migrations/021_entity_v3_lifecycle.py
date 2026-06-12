"""entity v3 lifecycle: observed_at/metadata + cardinality + supporting tables.

Revision ID: rel_021
Revises: rel_020
Create Date: 2026-06-13 00:00:00.000000

Phase: entity-v3-lifecycle-and-depth (bu-mxxjy, epic bu-89993).

Implements the additive schema delta for the entity v3 lifecycle per
``openspec/changes/entity-v3-lifecycle-and-depth/specs/relationship-facts/spec.md``:

1. ``relationship.entity_facts`` gains two nullable columns:
   - ``observed_at TIMESTAMPTZ NULL`` — when the fact was actually observed
     (distinct from ``created_at`` assertion time).
   - ``metadata JSONB NULL`` — structured provenance (e.g. correction lineage
     ``{correction_source, corrected_from}``).
   Both are additive-only — no table rewrite, no in-DDL default backfill. A
   separate operator-run backfill (``scripts/backfill_entity_fact_observed_at.py``)
   stamps existing rows with ``COALESCE(last_seen, created_at)``.

2. ``relationship.entity_predicate_registry`` gains a ``cardinality`` column:
   ``cardinality TEXT NOT NULL DEFAULT 'multi' CHECK (cardinality IN ('single','multi'))``.
   Seeded ``single`` for ``has-birthday`` and ``dunbar_tier_override``; ``multi``
   for every other predicate. Cardinality is the registry-sourced answer to
   "can an entity legitimately hold two active values for this predicate" — it
   drives merge-review divergence classification. No cardinality is hardcoded
   outside the registry.

3. ``relationship.entity_view_marks`` — one "last viewed" mark per entity
   (owner-only system); supports the delta-since-last-visit dashboard feature.

4. ``relationship.merge_reviews`` — audit log of merge-review outcomes. FKs to
   ``public.entities`` MUST NOT cascade-delete: audit rows survive entity
   tombstoning (post-merge, ``entity_b`` is tombstoned but the review row is
   retained as history).

Cross-chain / cross-schema safety
----------------------------------
``public.entities`` is owned by a sibling migration chain. All references to it
are guarded: this migration raises a clear error if ``public.entities`` is
absent at upgrade time (the FKs cannot be created without it), and column/table
creation uses ``IF NOT EXISTS`` / ``ADD COLUMN IF NOT EXISTS`` so the upgrade is
a safe no-op on re-run. ``downgrade()`` drops only what this migration created.

Grants
------
The new tables grant SELECT, INSERT, UPDATE, DELETE to ``butler_relationship_rw``
only (RFC 0006 schema isolation), best-effort against older DBs.
"""

from __future__ import annotations

from alembic import op

revision = "rel_021"
down_revision = "rel_020"
branch_labels = None
depends_on = None

_RELATIONSHIP_ROLE = "butler_relationship_rw"
_TABLE_PRIVILEGES = "SELECT, INSERT, UPDATE, DELETE"

# Predicates whose cardinality is 'single' — an entity may hold at most one
# active value (a divergence requires resolution at merge time). Everything
# else defaults to 'multi' (union-on-merge, never a conflict).
_SINGLE_CARDINALITY_PREDICATES: tuple[str, ...] = (
    "has-birthday",
    "dunbar_tier_override",
)


def _grant_best_effort(table_fqn: str, privilege: str, role: str) -> None:
    """GRANT privilege ON table TO role; tolerate older DBs missing roles."""
    op.execute(
        f"""
        DO $$
        BEGIN
            IF to_regclass('{table_fqn}') IS NOT NULL
               AND EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}')
            THEN
                EXECUTE 'GRANT {privilege} ON TABLE {table_fqn} TO "{role}"';
            END IF;
        EXCEPTION
            WHEN insufficient_privilege THEN NULL;
            WHEN undefined_object      THEN NULL;
            WHEN undefined_table       THEN NULL;
            WHEN invalid_schema_name   THEN NULL;
        END
        $$;
        """
    )


def upgrade() -> None:
    # 0. Cross-schema guard: public.entities is owned by a sibling chain. The
    #    new supporting tables FK to it, so fail fast with a clear message if it
    #    is absent (rather than emitting a confusing FK error).
    op.execute(
        """
        DO $$
        BEGIN
            IF to_regclass('public.entities') IS NULL THEN
                RAISE EXCEPTION
                    'rel_021 requires public.entities (owned by a sibling migration chain) '
                    'to exist before the relationship chain reaches this revision';
            END IF;
        END
        $$;
        """
    )

    # Schema is created by rel_013; this is defensive and idempotent.
    op.execute("CREATE SCHEMA IF NOT EXISTS relationship")

    # ------------------------------------------------------------------
    # 1. Additive columns on relationship.entity_facts (no table rewrite).
    #    ADD COLUMN ... NULL with no default is a metadata-only change in
    #    Postgres (no row rewrite, no exclusive-lock rewrite).
    # ------------------------------------------------------------------
    op.execute(
        "ALTER TABLE relationship.entity_facts "
        "ADD COLUMN IF NOT EXISTS observed_at TIMESTAMPTZ NULL"
    )
    op.execute("ALTER TABLE relationship.entity_facts ADD COLUMN IF NOT EXISTS metadata JSONB NULL")

    # ------------------------------------------------------------------
    # 2. cardinality column on the predicate registry + seed.
    # ------------------------------------------------------------------
    op.execute(
        """
        ALTER TABLE relationship.entity_predicate_registry
            ADD COLUMN IF NOT EXISTS cardinality TEXT NOT NULL DEFAULT 'multi'
                CHECK (cardinality IN ('single', 'multi'))
        """
    )

    # Seed: explicit 'single' for the listed predicates; everything else is
    # left at the column default 'multi'. UPDATE (not INSERT) — the registry
    # rows already exist (seeded by rel_014). Idempotent: re-running just
    # re-asserts the same values.
    for predicate in _SINGLE_CARDINALITY_PREDICATES:
        op.execute(
            f"""
            UPDATE relationship.entity_predicate_registry
            SET cardinality = 'single'
            WHERE predicate = '{predicate}'
            """
        )
    # Defensive: ensure every other existing row is explicitly 'multi' (the
    # column default already does this for rows present at ADD COLUMN time, but
    # this keeps the seed self-contained and correct if the default ever changes).
    single_list = ", ".join(f"'{p}'" for p in _SINGLE_CARDINALITY_PREDICATES)
    op.execute(
        f"""
        UPDATE relationship.entity_predicate_registry
        SET cardinality = 'multi'
        WHERE predicate NOT IN ({single_list})
        """
    )

    # ------------------------------------------------------------------
    # 3. relationship.entity_view_marks — one mark per entity.
    # ------------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS relationship.entity_view_marks (
            id        UUID        NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
            entity_id UUID        NOT NULL UNIQUE REFERENCES public.entities(id),
            marked_at TIMESTAMPTZ NOT NULL
        )
        """
    )
    _grant_best_effort("relationship.entity_view_marks", _TABLE_PRIVILEGES, _RELATIONSHIP_ROLE)

    # ------------------------------------------------------------------
    # 4. relationship.merge_reviews — audit log. NO cascade delete on either
    #    entity FK: rows survive tombstoning of the merged-away entity.
    # ------------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS relationship.merge_reviews (
            id              UUID        NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
            entity_a        UUID        NOT NULL REFERENCES public.entities(id),
            entity_b        UUID        NOT NULL REFERENCES public.entities(id),
            shared_facts    JSONB       NOT NULL,
            divergent_facts JSONB       NOT NULL,
            outcome         TEXT        NOT NULL CHECK (outcome IN ('merged', 'dismissed')),
            reviewed_at     TIMESTAMPTZ NOT NULL,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    _grant_best_effort("relationship.merge_reviews", _TABLE_PRIVILEGES, _RELATIONSHIP_ROLE)


def downgrade() -> None:
    # Drop only what this migration created, newest-first. The relationship
    # schema, entity_facts, and predicate_registry tables are owned by earlier
    # migrations and survive.
    op.execute("DROP TABLE IF EXISTS relationship.merge_reviews")
    op.execute("DROP TABLE IF EXISTS relationship.entity_view_marks")

    op.execute(
        "ALTER TABLE relationship.entity_predicate_registry DROP COLUMN IF EXISTS cardinality"
    )

    op.execute("ALTER TABLE relationship.entity_facts DROP COLUMN IF EXISTS metadata")
    op.execute("ALTER TABLE relationship.entity_facts DROP COLUMN IF EXISTS observed_at")
