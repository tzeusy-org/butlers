"""rename_shared_indexes: rename legacy shared_ prefixed indexes and constraints.

Revision ID: core_047
Revises: core_046
Create Date: 2026-03-31 00:00:00.000000

Renames all indexes and constraints on public schema tables that still carry
the legacy ``shared_`` prefix from the original identity table creation in
core_002_identity.py.

These are metadata-only renames — no data is moved and no index is rebuilt.
Existing queries are unaffected by index renames in PostgreSQL.

Objects renamed on public.entities:
  chk_shared_entities_entity_type  → chk_entities_entity_type   (CHECK constraint)
  idx_shared_entities_tenant_canonical → idx_entities_tenant_canonical
  idx_shared_entities_aliases      → idx_entities_aliases
  idx_shared_entities_metadata     → idx_entities_metadata
  idx_shared_entities_name         → idx_entities_name           (if present)
  idx_shared_entities_name_trgm    → idx_entities_name_trgm      (if present)
  idx_shared_entities_aliases_trgm → idx_entities_aliases_trgm   (if present)
  idx_shared_entities_updated_at   → idx_entities_updated_at     (if present)

Objects renamed on public.contact_info:
  uq_shared_contact_info_type_value → uq_contact_info_type_value (UNIQUE constraint)
  idx_shared_contact_info_contact_id → idx_contact_info_contact_id
  ix_shared_contact_info_parent_id  → ix_contact_info_parent_id  (if present)

Objects renamed on public.entity_info:
  uq_shared_entity_info_entity_type → uq_entity_info_entity_type (UNIQUE constraint)
  idx_shared_entity_info_entity_id  → idx_entity_info_entity_id
  idx_shared_entity_info_type       → idx_entity_info_type

All renames use ``IF EXISTS`` guards so the migration is safe to apply against
databases where pre-collapse migrations may have already dropped or never created
some of the legacy names.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "core_047"
down_revision = "core_046"
branch_labels = None
depends_on = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_SCHEMA = "public"


def _rename_index_if_exists(old_name: str, new_name: str, schema: str = _SCHEMA) -> None:
    """Rename a PostgreSQL index if old_name exists and new_name does not; no-op otherwise.

    Uses schema-qualified existence checks and quoted identifiers for robustness.
    """
    op.execute(f"""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM pg_indexes
                WHERE schemaname = '{schema}'
                  AND indexname  = '{old_name}'
            ) AND NOT EXISTS (
                SELECT 1 FROM pg_indexes
                WHERE schemaname = '{schema}'
                  AND indexname  = '{new_name}'
            ) THEN
                ALTER INDEX "{schema}"."{old_name}" RENAME TO "{new_name}";
            END IF;
        END
        $$;
    """)


def _rename_constraint_if_exists(table_fqn: str, old_name: str, new_name: str) -> None:
    """Rename a table constraint if old_name exists and new_name does not; no-op otherwise.

    table_fqn must be schema-qualified (e.g. 'public.entities').
    Uses quoted identifiers and a NOT EXISTS guard for full idempotency.
    """
    schema, table = table_fqn.split(".")
    op.execute(f"""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.table_constraints
                WHERE constraint_schema = '{schema}'
                  AND table_name        = '{table}'
                  AND constraint_name   = '{old_name}'
            ) AND NOT EXISTS (
                SELECT 1 FROM information_schema.table_constraints
                WHERE constraint_schema = '{schema}'
                  AND table_name        = '{table}'
                  AND constraint_name   = '{new_name}'
            ) THEN
                ALTER TABLE "{schema}"."{table}" RENAME CONSTRAINT "{old_name}" TO "{new_name}";
            END IF;
        END
        $$;
    """)


# ---------------------------------------------------------------------------
# Upgrade
# ---------------------------------------------------------------------------


def upgrade() -> None:
    # -------------------------------------------------------------------------
    # public.entities — CHECK constraint
    # -------------------------------------------------------------------------
    _rename_constraint_if_exists(
        "public.entities",
        "chk_shared_entities_entity_type",
        "chk_entities_entity_type",
    )

    # public.entities — indexes (definite from core_002)
    _rename_index_if_exists("idx_shared_entities_tenant_canonical", "idx_entities_tenant_canonical")
    _rename_index_if_exists("idx_shared_entities_aliases", "idx_entities_aliases")
    _rename_index_if_exists("idx_shared_entities_metadata", "idx_entities_metadata")

    # public.entities — indexes (may exist from pre-collapse migrations)
    _rename_index_if_exists("idx_shared_entities_name", "idx_entities_name")
    _rename_index_if_exists("idx_shared_entities_name_trgm", "idx_entities_name_trgm")
    _rename_index_if_exists("idx_shared_entities_aliases_trgm", "idx_entities_aliases_trgm")
    _rename_index_if_exists("idx_shared_entities_updated_at", "idx_entities_updated_at")

    # -------------------------------------------------------------------------
    # public.contact_info — UNIQUE constraint + indexes
    # -------------------------------------------------------------------------
    _rename_constraint_if_exists(
        "public.contact_info",
        "uq_shared_contact_info_type_value",
        "uq_contact_info_type_value",
    )
    _rename_index_if_exists("idx_shared_contact_info_contact_id", "idx_contact_info_contact_id")
    _rename_index_if_exists("ix_shared_contact_info_parent_id", "ix_contact_info_parent_id")

    # -------------------------------------------------------------------------
    # public.entity_info — UNIQUE constraint + indexes
    # -------------------------------------------------------------------------
    _rename_constraint_if_exists(
        "public.entity_info",
        "uq_shared_entity_info_entity_type",
        "uq_entity_info_entity_type",
    )
    _rename_index_if_exists("idx_shared_entity_info_entity_id", "idx_entity_info_entity_id")
    _rename_index_if_exists("idx_shared_entity_info_type", "idx_entity_info_type")


# ---------------------------------------------------------------------------
# Downgrade — restore the old shared_ names
# ---------------------------------------------------------------------------


def downgrade() -> None:
    # public.entities — CHECK constraint
    _rename_constraint_if_exists(
        "public.entities",
        "chk_entities_entity_type",
        "chk_shared_entities_entity_type",
    )

    # public.entities — indexes
    _rename_index_if_exists("idx_entities_tenant_canonical", "idx_shared_entities_tenant_canonical")
    _rename_index_if_exists("idx_entities_aliases", "idx_shared_entities_aliases")
    _rename_index_if_exists("idx_entities_metadata", "idx_shared_entities_metadata")
    _rename_index_if_exists("idx_entities_name", "idx_shared_entities_name")
    _rename_index_if_exists("idx_entities_name_trgm", "idx_shared_entities_name_trgm")
    _rename_index_if_exists("idx_entities_aliases_trgm", "idx_shared_entities_aliases_trgm")
    _rename_index_if_exists("idx_entities_updated_at", "idx_shared_entities_updated_at")

    # public.contact_info — UNIQUE constraint + indexes
    _rename_constraint_if_exists(
        "public.contact_info",
        "uq_contact_info_type_value",
        "uq_shared_contact_info_type_value",
    )
    _rename_index_if_exists("idx_contact_info_contact_id", "idx_shared_contact_info_contact_id")
    _rename_index_if_exists("ix_contact_info_parent_id", "ix_shared_contact_info_parent_id")

    # public.entity_info — UNIQUE constraint + indexes
    _rename_constraint_if_exists(
        "public.entity_info",
        "uq_entity_info_entity_type",
        "uq_shared_entity_info_entity_type",
    )
    _rename_index_if_exists("idx_entity_info_entity_id", "idx_shared_entity_info_entity_id")
    _rename_index_if_exists("idx_entity_info_type", "idx_shared_entity_info_type")
