"""discretion_complexity_tier: widen CHECK constraints to include 'discretion' tier

Revision ID: core_034
Revises: core_033
Create Date: 2026-03-16 00:00:00.000000

Adds 'discretion' as a valid complexity_tier value in the two tables that
carry CHECK constraints on that column:

  - shared.model_catalog.complexity_tier
  - shared.butler_model_overrides.complexity_tier

PostgreSQL does not support ALTER … ADD/DROP CHECK directly; the migration
drops and recreates each constraint in-place.  The change is backward-
compatible: existing rows are untouched, the CHECK only applies to future
writes.

Also seeds the default discretion catalog entries from model_catalog_defaults.toml
that have complexity_tier='discretion'. These cannot be seeded in core_025 because
the constraint is not yet widened at that point.
"""

from __future__ import annotations

import json
from pathlib import Path

from alembic import op

# revision identifiers, used by Alembic.
revision = "core_034"
down_revision = "core_033"
branch_labels = None
depends_on = None

_OLD_CATALOG_CHECK = "('trivial', 'medium', 'high', 'extra_high')"
_NEW_CATALOG_CHECK = "('trivial', 'medium', 'high', 'extra_high', 'discretion')"

_OLD_OVERRIDES_CHECK = (
    "complexity_tier IS NULL OR complexity_tier IN ('trivial', 'medium', 'high', 'extra_high')"
)
_NEW_OVERRIDES_CHECK = (
    "complexity_tier IS NULL OR complexity_tier IN "
    "('trivial', 'medium', 'high', 'extra_high', 'discretion')"
)


def _load_discretion_seed_entries() -> list[dict]:
    """Load discretion-tier model catalog entries from model_catalog_defaults.toml."""
    import tomllib  # noqa: PLC0415

    defaults_path = Path(__file__).resolve().parents[3] / "model_catalog_defaults.toml"
    if not defaults_path.exists():
        return []
    with open(defaults_path, "rb") as f:
        data = tomllib.load(f)
    return [m for m in data.get("models", []) if m.get("complexity_tier") == "discretion"]


def upgrade() -> None:
    # -------------------------------------------------------------------------
    # 1. Widen shared.model_catalog CHECK constraint
    # -------------------------------------------------------------------------
    op.execute("""
        ALTER TABLE shared.model_catalog
            DROP CONSTRAINT IF EXISTS chk_model_catalog_complexity_tier
    """)
    op.execute(f"""
        ALTER TABLE shared.model_catalog
            ADD CONSTRAINT chk_model_catalog_complexity_tier
            CHECK (complexity_tier IN {_NEW_CATALOG_CHECK})
    """)

    # -------------------------------------------------------------------------
    # 2. Widen shared.butler_model_overrides CHECK constraint
    # -------------------------------------------------------------------------
    op.execute("""
        ALTER TABLE shared.butler_model_overrides
            DROP CONSTRAINT IF EXISTS chk_butler_model_overrides_complexity_tier
    """)
    op.execute(f"""
        ALTER TABLE shared.butler_model_overrides
            ADD CONSTRAINT chk_butler_model_overrides_complexity_tier
            CHECK ({_NEW_OVERRIDES_CHECK})
    """)

    # -------------------------------------------------------------------------
    # 3. Seed discretion-tier entries from model_catalog_defaults.toml
    #
    # core_025 seeds model_catalog_defaults.toml entries, but it cannot seed
    # 'discretion' tier rows because the constraint is not yet widened at that
    # point. We seed them here after the constraint is widened, idempotently.
    # -------------------------------------------------------------------------
    import sqlalchemy  # noqa: PLC0415

    discretion_entries = _load_discretion_seed_entries()
    if discretion_entries:
        seed_sql = sqlalchemy.text(
            "INSERT INTO shared.model_catalog"
            " (alias, runtime_type, model_id, extra_args,"
            "  complexity_tier, priority, enabled)"
            " VALUES"
            " (:alias, :runtime_type, :model_id,"
            " CAST(:extra_args AS jsonb), :complexity_tier, :priority, :enabled)"
            " ON CONFLICT (alias) DO NOTHING"
        )
        seed_params = [
            {
                "alias": entry["alias"],
                "runtime_type": entry["runtime_type"],
                "model_id": entry["model_id"],
                "extra_args": json.dumps(entry.get("extra_args", [])),
                "complexity_tier": entry["complexity_tier"],
                "priority": entry.get("priority", 0),
                "enabled": entry.get("enabled", True),
            }
            for entry in discretion_entries
        ]
        op.get_bind().execute(seed_sql, seed_params)


def downgrade() -> None:
    # -------------------------------------------------------------------------
    # Revert to the original four-tier constraints.
    # Note: this will FAIL if any rows already contain 'discretion' — the caller
    # must remove those rows first.
    # -------------------------------------------------------------------------
    op.execute("""
        ALTER TABLE shared.butler_model_overrides
            DROP CONSTRAINT IF EXISTS chk_butler_model_overrides_complexity_tier
    """)
    op.execute(f"""
        ALTER TABLE shared.butler_model_overrides
            ADD CONSTRAINT chk_butler_model_overrides_complexity_tier
            CHECK ({_OLD_OVERRIDES_CHECK})
    """)

    op.execute("""
        ALTER TABLE shared.model_catalog
            DROP CONSTRAINT IF EXISTS chk_model_catalog_complexity_tier
    """)
    op.execute(f"""
        ALTER TABLE shared.model_catalog
            ADD CONSTRAINT chk_model_catalog_complexity_tier
            CHECK (complexity_tier IN {_OLD_CATALOG_CHECK})
    """)
