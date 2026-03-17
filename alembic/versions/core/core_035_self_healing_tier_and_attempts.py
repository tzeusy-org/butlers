"""self_healing_tier_and_attempts: widen CHECK constraints, create healing_attempts table

Revision ID: core_035
Revises: core_034
Create Date: 2026-03-17 00:00:00.000000

Changes:

  1. Widens shared.model_catalog.complexity_tier CHECK constraint to include
     'self_healing'.

  2. Widens shared.butler_model_overrides.complexity_tier CHECK constraint to
     include 'self_healing'.

  3. Creates shared.healing_attempts — the tracking table for self-healing
     investigation lifecycle:
       - id, fingerprint, butler_name, status, severity, exception_type,
         call_site, sanitized_msg, branch_name, worktree_path, pr_url,
         pr_number, session_ids, healing_session_id, created_at, updated_at,
         closed_at, error_detail.
       - Index on fingerprint for novelty/cooldown lookups.
       - Index on status for concurrency-cap and circuit-breaker queries.
       - Partial UNIQUE index on fingerprint WHERE status IN
         ('investigating', 'pr_open') — the atomic novelty gate.

  4. Seeds the 'healing-sonnet' entry from model_catalog_defaults.toml
     (idempotent via ON CONFLICT DO NOTHING).

PostgreSQL does not support ALTER … ADD/DROP CHECK directly; constraints are
dropped and recreated in-place.  The change is backward-compatible: existing
rows are untouched.
"""

from __future__ import annotations

import json
from pathlib import Path

from alembic import op

# revision identifiers, used by Alembic.
revision = "core_035"
down_revision = "core_034"
branch_labels = None
depends_on = None

_OLD_CATALOG_CHECK = "('trivial', 'medium', 'high', 'extra_high', 'discretion')"
_NEW_CATALOG_CHECK = "('trivial', 'medium', 'high', 'extra_high', 'discretion', 'self_healing')"

_OLD_OVERRIDES_CHECK = (
    "complexity_tier IS NULL OR complexity_tier IN "
    "('trivial', 'medium', 'high', 'extra_high', 'discretion')"
)
_NEW_OVERRIDES_CHECK = (
    "complexity_tier IS NULL OR complexity_tier IN "
    "('trivial', 'medium', 'high', 'extra_high', 'discretion', 'self_healing')"
)


def _load_self_healing_seed_entries() -> list[dict]:
    """Load self_healing-tier model catalog entries from model_catalog_defaults.toml."""
    import tomllib  # noqa: PLC0415

    # Path depth: core_035*.py → core/ (0) → versions/ (1) → alembic/ (2) → project root (3)
    defaults_path = Path(__file__).resolve().parents[3] / "model_catalog_defaults.toml"
    if not defaults_path.exists():
        return []
    with open(defaults_path, "rb") as f:
        data = tomllib.load(f)
    return [m for m in data.get("models", []) if m.get("complexity_tier") == "self_healing"]


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
    # 3. Create shared.healing_attempts
    # -------------------------------------------------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS shared.healing_attempts (
            id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            fingerprint         TEXT NOT NULL,
            butler_name         TEXT NOT NULL,
            status              TEXT NOT NULL DEFAULT 'investigating',
            severity            INTEGER NOT NULL,
            exception_type      TEXT NOT NULL,
            call_site           TEXT NOT NULL,
            sanitized_msg       TEXT,
            branch_name         TEXT,
            worktree_path       TEXT,
            pr_url              TEXT,
            pr_number           INTEGER,
            session_ids         UUID[] NOT NULL DEFAULT '{}',
            healing_session_id  UUID,
            created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
            closed_at           TIMESTAMPTZ,
            error_detail        TEXT
        )
    """)

    # Index on fingerprint for novelty gate and cooldown lookups.
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_healing_attempts_fingerprint
        ON shared.healing_attempts (fingerprint)
    """)

    # Index on status for concurrency-cap and circuit-breaker queries.
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_healing_attempts_status
        ON shared.healing_attempts (status)
    """)

    # Partial unique index: at most one active investigation per fingerprint.
    # This is the atomic novelty gate — INSERT fails if a concurrent dispatcher
    # already created an 'investigating' or 'pr_open' row for the same fingerprint.
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_healing_attempts_active_fingerprint
        ON shared.healing_attempts (fingerprint)
        WHERE status IN ('investigating', 'pr_open')
    """)

    # -------------------------------------------------------------------------
    # 4. Seed self_healing-tier entries from model_catalog_defaults.toml
    # -------------------------------------------------------------------------
    import sqlalchemy  # noqa: PLC0415

    self_healing_entries = _load_self_healing_seed_entries()
    if self_healing_entries:
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
            for entry in self_healing_entries
        ]
        op.get_bind().execute(seed_sql, seed_params)


def downgrade() -> None:
    # -------------------------------------------------------------------------
    # Remove healing_attempts table and indexes.
    # -------------------------------------------------------------------------
    op.execute("DROP INDEX IF EXISTS shared.uq_healing_attempts_active_fingerprint")
    op.execute("DROP INDEX IF EXISTS shared.idx_healing_attempts_status")
    op.execute("DROP INDEX IF EXISTS shared.idx_healing_attempts_fingerprint")
    op.execute("DROP TABLE IF EXISTS shared.healing_attempts")

    # -------------------------------------------------------------------------
    # Remove seed entries for the self_healing tier before reverting the CHECK
    # constraint.  Rows with complexity_tier = 'self_healing' must be gone
    # before we can restore the old constraint; if any are left the ALTER TABLE
    # will fail.  This includes the 'healing-sonnet' entry seeded by upgrade().
    # Per-butler overrides that reference those catalog entries are cascade-
    # deleted automatically (ON DELETE CASCADE on catalog_entry_id FK).
    # -------------------------------------------------------------------------
    op.execute("""
        DELETE FROM shared.model_catalog
        WHERE complexity_tier = 'self_healing'
    """)
    op.execute("""
        DELETE FROM shared.butler_model_overrides
        WHERE complexity_tier = 'self_healing'
    """)

    # -------------------------------------------------------------------------
    # Revert to the discretion-only constraint.
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
