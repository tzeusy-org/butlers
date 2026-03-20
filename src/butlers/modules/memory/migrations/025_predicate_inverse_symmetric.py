"""predicate_inverse_symmetric — mem_025

Add inverse_of and is_symmetric columns to predicate_registry for
bidirectional graph traversal support.

Changes:
  1. inverse_of TEXT — names the inverse predicate (nullable FK-like reference).
     When a fact is stored with a predicate that has inverse_of set, the memory
     module auto-creates the mirrored fact (object→subject) using the inverse
     predicate name.
  2. is_symmetric BOOLEAN NOT NULL DEFAULT false — when true, the predicate is
     its own inverse (e.g. sibling_of, knows, lives_with).  Auto-creates the
     mirrored fact (object→subject) with the same predicate name.

Seeding:
  - sibling_of → is_symmetric = true
  - knows      → is_symmetric = true
  - lives_with → is_symmetric = true
  - parent_of  → inverse_of = 'child_of' (and child_of seeded as inverse_of = 'parent_of')
  - manages    → inverse_of = 'managed_by' (and managed_by seeded as inverse_of = 'manages')

child_of and managed_by are inserted by this migration if not already present.

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

# Inverse pairs: (forward_predicate, inverse_predicate, subject_type, object_type)
# Both directions are seeded; each names the other in inverse_of.
_INVERSE_PAIRS: list[tuple[str, str, str, str]] = [
    ("parent_of", "child_of", "person", "person"),
    ("manages", "managed_by", "person", "person"),
]

# Symmetric predicates: the inverse fact uses the same predicate name.
_SYMMETRIC_PREDICATES: list[str] = [
    "sibling_of",
    "knows",
    "lives_with",
]


def upgrade() -> None:
    # -------------------------------------------------------------------------
    # 1. Add inverse_of column
    # -------------------------------------------------------------------------
    op.execute("""
        ALTER TABLE predicate_registry
        ADD COLUMN IF NOT EXISTS inverse_of TEXT
    """)

    # -------------------------------------------------------------------------
    # 2. Add is_symmetric column
    # -------------------------------------------------------------------------
    op.execute("""
        ALTER TABLE predicate_registry
        ADD COLUMN IF NOT EXISTS is_symmetric BOOLEAN NOT NULL DEFAULT false
    """)

    # -------------------------------------------------------------------------
    # 3. Seed inverse pairs — insert the inverse predicate if absent, then
    #    set inverse_of on both the forward and inverse rows.
    # -------------------------------------------------------------------------
    for forward, inverse, subj_type, obj_type in _INVERSE_PAIRS:
        # Ensure the inverse predicate exists (it may not be seeded yet).
        op.execute(
            f"INSERT INTO predicate_registry"
            f" (name, expected_subject_type, expected_object_type,"
            f"  is_edge, description, status)"
            f" VALUES"
            f" ('{inverse}', '{obj_type}', '{subj_type}',"
            f"  true, 'Inverse of {forward}', 'active')"
            f" ON CONFLICT (name) DO NOTHING"
        )
        # Set inverse_of on the forward predicate.
        op.execute(
            f"UPDATE predicate_registry"
            f" SET inverse_of = '{inverse}'"
            f" WHERE name = '{forward}'"
        )
        # Set inverse_of on the inverse predicate (pointing back).
        op.execute(
            f"UPDATE predicate_registry"
            f" SET inverse_of = '{forward}'"
            f" WHERE name = '{inverse}'"
        )

    # -------------------------------------------------------------------------
    # 4. Mark symmetric predicates
    # -------------------------------------------------------------------------
    for name in _SYMMETRIC_PREDICATES:
        op.execute(
            f"UPDATE predicate_registry"
            f" SET is_symmetric = true"
            f" WHERE name = '{name}'"
        )

    # -------------------------------------------------------------------------
    # 5. Index on is_symmetric for fast filtering during write-time lookup
    # -------------------------------------------------------------------------
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_predicate_registry_is_symmetric
        ON predicate_registry (is_symmetric)
        WHERE is_symmetric = true
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_predicate_registry_is_symmetric")
    # Reset inverse_of on all rows that reference the predicates we set.
    for forward, inverse, *_ in _INVERSE_PAIRS:
        op.execute(
            f"UPDATE predicate_registry"
            f" SET inverse_of = NULL"
            f" WHERE name IN ('{forward}', '{inverse}')"
        )
        # Remove inverse predicate rows only when no facts reference them.
        # Guarded DELETE: if facts were created using the inverse predicate after
        # upgrade, we skip the delete to avoid leaving dangling predicate references.
        op.execute(
            f"DELETE FROM predicate_registry"
            f" WHERE name = '{inverse}'"
            f" AND NOT EXISTS (SELECT 1 FROM facts WHERE predicate = '{inverse}')"
        )
    # Reset is_symmetric
    for name in _SYMMETRIC_PREDICATES:
        op.execute(
            f"UPDATE predicate_registry"
            f" SET is_symmetric = false"
            f" WHERE name = '{name}'"
        )
    op.execute("ALTER TABLE predicate_registry DROP COLUMN IF EXISTS is_symmetric")
    op.execute("ALTER TABLE predicate_registry DROP COLUMN IF EXISTS inverse_of")
