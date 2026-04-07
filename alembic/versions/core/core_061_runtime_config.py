"""runtime_config: per-butler operational config table.

Revision ID: core_061
Revises: core_060
Create Date: 2026-04-07 00:00:00.000000

Creates the ``runtime_config`` table in the current butler schema.  This
table holds a single row per butler keyed by ``butler_name`` and stores
operational tuning knobs (model, runtime_type, concurrency limits,
core_groups, session timeout, CLI args).

The daemon seeds this table from ``[butler.runtime_seed]`` on first boot
via ``INSERT ... ON CONFLICT DO NOTHING``.  Subsequent boots read from
the DB and ignore the toml seed.  The dashboard reads/writes this table
via GET/PATCH API endpoints.

Columns:
  butler_name      TEXT PK — matches the butler's identity name
  core_groups      TEXT[] — nullable; NULL means all groups enabled
  model            TEXT — nullable; NULL uses catalog/default fallback
  runtime_type     TEXT NOT NULL DEFAULT 'codex'
  args             JSONB NOT NULL DEFAULT '[]'::jsonb
  max_concurrent   INT NOT NULL DEFAULT 3
  max_queued       INT NOT NULL DEFAULT 10
  session_timeout_s INT NOT NULL DEFAULT 900
  seeded_at        TIMESTAMPTZ NOT NULL DEFAULT now()
  updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
"""

from __future__ import annotations

from alembic import op

revision = "core_061"
down_revision = "core_060"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS runtime_config (
            butler_name      TEXT PRIMARY KEY,
            core_groups      TEXT[],
            model            TEXT,
            runtime_type     TEXT NOT NULL DEFAULT 'codex',
            args             JSONB NOT NULL DEFAULT '[]'::jsonb,
            max_concurrent   INT NOT NULL DEFAULT 3,
            max_queued       INT NOT NULL DEFAULT 10,
            session_timeout_s INT NOT NULL DEFAULT 900,
            seeded_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS runtime_config")
