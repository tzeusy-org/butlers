"""model_catalog: add shared.model_catalog, shared.butler_model_overrides, complexity column

Revision ID: core_025
Revises: core_024
Create Date: 2026-03-11 00:00:00.000000

Creates the database foundation for dynamic model routing:

  1. shared.model_catalog — global registry of available model entries.
     Columns: id, alias (UNIQUE), runtime_type, model_id, extra_args (JSONB),
     complexity_tier (CHECK in trivial/medium/high/extra_high), enabled,
     priority, created_at, updated_at.

  2. shared.butler_model_overrides — per-butler overrides for catalog entries.
     Columns: id, butler_name, catalog_entry_id (FK CASCADE), enabled,
     priority, complexity_tier (nullable override).
     UNIQUE on (butler_name, catalog_entry_id).

  3. Adds complexity column (TEXT, nullable, DEFAULT 'medium') to
     scheduled_tasks for tier-aware dispatch.

  4. Seeds default catalog entries from model_catalog_defaults.toml
     (idempotent via ON CONFLICT DO NOTHING).
"""

from __future__ import annotations

import json
from pathlib import Path

from alembic import op

# revision identifiers, used by Alembic.
revision = "core_025"
down_revision = "core_024"
branch_labels = None
depends_on = None

_COMPLEXITY_TIERS = ("trivial", "medium", "high", "extra_high")


def _load_seed_entries() -> list[dict]:
    """Load default model catalog entries from model_catalog_defaults.toml."""
    import tomllib  # noqa: PLC0415

    defaults_path = Path(__file__).resolve().parents[3] / "model_catalog_defaults.toml"
    if not defaults_path.exists():
        return []
    with open(defaults_path, "rb") as f:
        data = tomllib.load(f)
    return data.get("models", [])


def upgrade() -> None:
    # -------------------------------------------------------------------------
    # 1. Create shared.model_catalog
    # -------------------------------------------------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS shared.model_catalog (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            alias           TEXT NOT NULL,
            runtime_type    TEXT NOT NULL,
            model_id        TEXT NOT NULL,
            extra_args      JSONB NOT NULL DEFAULT '[]'::jsonb,
            complexity_tier TEXT NOT NULL DEFAULT 'medium',
            enabled         BOOLEAN NOT NULL DEFAULT true,
            priority        INTEGER NOT NULL DEFAULT 0,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_model_catalog_alias UNIQUE (alias),
            CONSTRAINT chk_model_catalog_complexity_tier
                CHECK (complexity_tier IN ('trivial', 'medium', 'high', 'extra_high'))
        )
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_model_catalog_complexity_tier_enabled
        ON shared.model_catalog (complexity_tier, enabled, priority)
    """)

    # -------------------------------------------------------------------------
    # 2. Create shared.butler_model_overrides
    # -------------------------------------------------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS shared.butler_model_overrides (
            id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            butler_name      TEXT NOT NULL,
            catalog_entry_id UUID NOT NULL
                REFERENCES shared.model_catalog(id) ON DELETE CASCADE,
            enabled          BOOLEAN NOT NULL DEFAULT true,
            priority         INTEGER,
            complexity_tier  TEXT,
            CONSTRAINT uq_butler_model_overrides_butler_entry
                UNIQUE (butler_name, catalog_entry_id),
            CONSTRAINT chk_butler_model_overrides_complexity_tier
                CHECK (complexity_tier IS NULL
                       OR complexity_tier IN ('trivial', 'medium', 'high', 'extra_high'))
        )
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_butler_model_overrides_butler_name
        ON shared.butler_model_overrides (butler_name)
    """)

    # -------------------------------------------------------------------------
    # 3. Add complexity column to scheduled_tasks
    # -------------------------------------------------------------------------
    op.execute("""
        ALTER TABLE scheduled_tasks
            ADD COLUMN IF NOT EXISTS complexity TEXT DEFAULT 'medium'
    """)

    # -------------------------------------------------------------------------
    # 4. Seed default catalog entries from model_catalog_defaults.toml (idempotent)
    # -------------------------------------------------------------------------
    import sqlalchemy  # noqa: PLC0415

    seed_entries = _load_seed_entries()
    if seed_entries:
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
                "complexity_tier": entry.get("complexity_tier", "medium"),
                "priority": entry.get("priority", 0),
                "enabled": entry.get("enabled", True),
            }
            for entry in seed_entries
        ]
        op.get_bind().execute(seed_sql, seed_params)


def downgrade() -> None:
    # -------------------------------------------------------------------------
    # Reverse order: indexes, overrides, catalog, column
    # -------------------------------------------------------------------------
    op.execute("DROP INDEX IF EXISTS shared.idx_butler_model_overrides_butler_name")
    op.execute("DROP INDEX IF EXISTS shared.idx_model_catalog_complexity_tier_enabled")
    op.execute("DROP TABLE IF EXISTS shared.butler_model_overrides")
    op.execute("DROP TABLE IF EXISTS shared.model_catalog")

    op.execute("""
        ALTER TABLE scheduled_tasks
            DROP COLUMN IF EXISTS complexity
    """)
