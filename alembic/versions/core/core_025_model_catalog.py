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

  4. Seeds 12 default catalog entries (idempotent via ON CONFLICT DO NOTHING):
     claude-haiku, claude-sonnet, claude-opus, gpt-5.1, gpt-5.3-spark,
     gpt-5.4, gpt-5.4-high, gemini-2.5-flash, gemini-2.5-pro,
     minimax-m2.5, glm-5, kimi-k2.5.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "core_025"
down_revision = "core_024"
branch_labels = None
depends_on = None

_COMPLEXITY_TIERS = ("trivial", "medium", "high", "extra_high")

# Default seed entries: (alias, runtime_type, model_id, complexity_tier, priority)
_SEED_ENTRIES = [
    ("claude-haiku", "claude-code", "claude-haiku-4-5", "trivial", 10),
    ("claude-sonnet", "claude-code", "claude-sonnet-4-5", "medium", 20),
    ("claude-opus", "claude-code", "claude-opus-4-5", "high", 30),
    ("gpt-5.1", "codex", "gpt-5.1", "medium", 40),
    ("gpt-5.3-spark", "codex", "gpt-5.3-spark", "trivial", 50),
    ("gpt-5.4", "codex", "gpt-5.4", "high", 60),
    ("gpt-5.4-high", "codex", "gpt-5.4-high", "extra_high", 70),
    ("gemini-2.5-flash", "gemini", "gemini-2.5-flash", "trivial", 80),
    ("gemini-2.5-pro", "gemini", "gemini-2.5-pro", "high", 90),
    ("minimax-m2.5", "minimax", "minimax-m2.5", "medium", 100),
    ("glm-5", "zhipu", "glm-5", "medium", 110),
    ("kimi-k2.5", "moonshot", "kimi-k2.5", "medium", 120),
]


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
            extra_args      JSONB NOT NULL DEFAULT '{}'::jsonb,
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
    # 4. Seed 12 default catalog entries (idempotent)
    # -------------------------------------------------------------------------
    for alias, runtime_type, model_id, complexity_tier, priority in _SEED_ENTRIES:
        op.execute(f"""
            INSERT INTO shared.model_catalog
                (alias, runtime_type, model_id, complexity_tier, priority)
            VALUES
                ('{alias}', '{runtime_type}', '{model_id}', '{complexity_tier}', {priority})
            ON CONFLICT (alias) DO NOTHING
        """)


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
