"""collection_vocabulary: a declared-name boundary for collections

Revision ID: gen_003
Revises: gen_002
Create Date: 2026-09-09 00:00:00.000000

Introduces the collection vocabulary General resolves ``item_create`` through
(bu-2jtfw.9). Two tables:

- ``collection_vocabulary``: one row per canonical collection name, with a
  required non-empty ``shape_description`` (a declared collection with no
  shape is rejected at the tool layer -- this column exists so that
  invariant has somewhere to be recorded, not enforced by a DB CHECK, since
  the tool layer is where the empty-string/whitespace-only rejection reads
  cleanly).
- ``collection_aliases``: off-canonical spellings that resolve to a
  canonical name. ``merged_into`` supports the consolidation rollback
  contract (S8): repointing an alias's canonical target is an UPDATE, never
  a delete, so a merge can be reversed by restoring the prior canonical.

``pg_trgm`` backs the fuzzy-resolution fallback in
``roster/general/tools/vocabulary.py`` (exact normalized match first, trigram
similarity above threshold second, else raise naming the nearest candidates).
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "gen_003"
down_revision = "gen_002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # NOTE: pg_trgm extension is intentionally NOT dropped in downgrade() --
    # other objects in this schema may depend on it.
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    op.execute("""
        CREATE TABLE IF NOT EXISTS collection_vocabulary (
            id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            canonical_name      TEXT NOT NULL UNIQUE,
            shape_description   TEXT NOT NULL,
            created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS collection_aliases (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            alias           TEXT NOT NULL UNIQUE,
            canonical_name  TEXT NOT NULL
                REFERENCES collection_vocabulary (canonical_name) ON DELETE CASCADE,
            merged_into     TEXT
                REFERENCES collection_vocabulary (canonical_name) ON DELETE SET NULL,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)

    # Trigram fallback resolution scans canonical_name; GIN + gin_trgm_ops is
    # the standard pg_trgm similarity-search index shape.
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_collection_vocabulary_name_trgm
        ON collection_vocabulary USING GIN (canonical_name gin_trgm_ops)
    """)

    # Bootstrap canonical collections capture() routes into by default so a
    # fresh butler is never stuck in a declare-then-create chicken-and-egg:
    # the very first capture() call needs somewhere to land.
    op.execute("""
        INSERT INTO collection_vocabulary (canonical_name, shape_description)
        VALUES
            ('notes', 'Freeform captured text with no other owner -- the second brain default.'),
            ('facts', 'A short standalone statement captured for later recall.'),
            ('preferences', 'A stated preference or standing instruction captured verbatim.')
        ON CONFLICT (canonical_name) DO NOTHING
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_collection_vocabulary_name_trgm")
    op.execute("DROP TABLE IF EXISTS collection_aliases")
    op.execute("DROP TABLE IF EXISTS collection_vocabulary")
