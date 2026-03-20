"""predicate_aliases — mem_025

Add ``aliases TEXT[]`` column to ``predicate_registry`` for deterministic
synonym resolution at write time.

Changes
-------
1. Add ``aliases TEXT[]`` column (DEFAULT ``'{}'``).
2. Replace the ``predicate_registry_search_vector_trigger`` function so that
   alias tokens are included in the tsvector at weight 'A' (same weight as
   the canonical name — aliases are first-class synonyms for search).
3. Recreate the trigger to fire on INSERT or UPDATE OF name, description, aliases.
4. Backfill ``search_vector`` for all existing rows (aliases are currently
   empty, so backfill produces the same result as before — but the trigger
   signature is now correct for future alias additions).

Alias resolution behaviour (enforced in Python, not SQL)
---------------------------------------------------------
When ``store_fact()`` receives a predicate name that matches an alias in the
registry, it resolves to the canonical ``name`` before proceeding.  The
migration only adds the column and updates the search infrastructure; the
Python-layer resolution is in ``storage.py``.

Revision ID: mem_025
Revises: mem_024
Create Date: 2026-03-20 00:00:00.000000
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "mem_025"
down_revision = "mem_024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # -------------------------------------------------------------------------
    # 1. Add aliases column — empty array by default.
    # -------------------------------------------------------------------------
    op.execute("""
        ALTER TABLE predicate_registry
            ADD COLUMN IF NOT EXISTS aliases TEXT[] NOT NULL DEFAULT '{}'
    """)

    # -------------------------------------------------------------------------
    # 2. Replace the search_vector trigger function to include alias tokens.
    #    Aliases are tokenised and added at weight 'A' (same as the canonical
    #    name) because they are direct synonyms, not descriptions.
    #    array_to_string(aliases, ' ') safely handles empty arrays (→ '').
    # -------------------------------------------------------------------------
    op.execute("""
        CREATE OR REPLACE FUNCTION predicate_registry_search_vector_trigger()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            NEW.search_vector :=
                setweight(to_tsvector('english', coalesce(NEW.name, '')), 'A') ||
                setweight(to_tsvector('english',  -- aliases tokenised at weight A
                    coalesce(array_to_string(NEW.aliases, ' '), '')), 'A') ||
                setweight(to_tsvector('english', coalesce(NEW.description, '')), 'B');
            RETURN NEW;
        END;
        $$
    """)

    # -------------------------------------------------------------------------
    # 3. Recreate the trigger so it also fires on UPDATE OF aliases.
    # -------------------------------------------------------------------------
    op.execute("""
        DROP TRIGGER IF EXISTS trg_predicate_registry_search_vector
            ON predicate_registry
    """)

    op.execute("""
        CREATE TRIGGER trg_predicate_registry_search_vector
        BEFORE INSERT OR UPDATE OF name, description, aliases
        ON predicate_registry
        FOR EACH ROW
        EXECUTE FUNCTION predicate_registry_search_vector_trigger()
    """)

    # -------------------------------------------------------------------------
    # 4. Backfill search_vector for all existing rows.
    #    Existing aliases are empty arrays, so the result is identical to the
    #    mem_023 backfill — but we re-run unconditionally to apply the updated
    #    tsvector expression (includes alias weight layer) to all rows.
    # -------------------------------------------------------------------------
    op.execute("""
        UPDATE predicate_registry
        SET search_vector =
            setweight(to_tsvector('english', coalesce(name, '')), 'A') ||
            setweight(to_tsvector('english', coalesce(array_to_string(aliases, ' '), '')), 'A') ||
            setweight(to_tsvector('english', coalesce(description, '')), 'B')
    """)

    # -------------------------------------------------------------------------
    # 5. GIN index on aliases for exact-alias lookups (used by alias resolution).
    #    The PK lookup for canonical names already uses the btree PK index;
    #    alias resolution queries use @> (array contains) which benefits from GIN.
    # -------------------------------------------------------------------------
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_predicate_registry_aliases
        ON predicate_registry
        USING GIN (aliases)
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_predicate_registry_aliases")

    # Restore the original trigger function (name + description only).
    op.execute("""
        CREATE OR REPLACE FUNCTION predicate_registry_search_vector_trigger()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            NEW.search_vector :=
                setweight(to_tsvector('english', coalesce(NEW.name, '')), 'A') ||
                setweight(to_tsvector('english', coalesce(NEW.description, '')), 'B');
            RETURN NEW;
        END;
        $$
    """)

    op.execute("""
        DROP TRIGGER IF EXISTS trg_predicate_registry_search_vector
            ON predicate_registry
    """)

    op.execute("""
        CREATE TRIGGER trg_predicate_registry_search_vector
        BEFORE INSERT OR UPDATE OF name, description
        ON predicate_registry
        FOR EACH ROW
        EXECUTE FUNCTION predicate_registry_search_vector_trigger()
    """)

    # Backfill search_vector back to the name+description expression.
    op.execute("""
        UPDATE predicate_registry
        SET search_vector =
            setweight(to_tsvector('english', coalesce(name, '')), 'A') ||
            setweight(to_tsvector('english', coalesce(description, '')), 'B')
    """)

    op.execute("""
        ALTER TABLE predicate_registry
            DROP COLUMN IF EXISTS aliases
    """)
