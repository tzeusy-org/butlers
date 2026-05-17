"""Add embedding_model_version column to episodes, facts, and rules.

Revision ID: mem_004
Revises: mem_003
Create Date: 2026-05-17 00:00:00.000000

This migration is additive-only.  On PostgreSQL, adding a column with a
DEFAULT is non-blocking — the column is added to the catalog and the default
is applied lazily on read (pg 11+).  There is no table rewrite.

Existing rows receive the default value 'unknown', because the model that
produced their embeddings is not recorded anywhere.  A one-time backfill via
the memory_reembed MCP tool will update each row to the current model name
and regenerate its embedding.  New writes produced after this migration will
always record the producing model name.
"""

from __future__ import annotations

from alembic import op

revision = "mem_004"
down_revision = "mem_003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # -------------------------------------------------------------------------
    # Add embedding_model_version to episodes, facts, and rules.
    # All three tables carry an embedding vector(384) column; this column
    # records which model produced that vector so staleness can be detected
    # and re-embedding can be targeted precisely.
    # -------------------------------------------------------------------------
    for table in ("episodes", "facts", "rules"):
        op.execute(f"""
            ALTER TABLE {table}
            ADD COLUMN IF NOT EXISTS embedding_model_version TEXT DEFAULT 'unknown'
        """)


def downgrade() -> None:
    for table in ("episodes", "facts", "rules"):
        op.execute(f"""
            ALTER TABLE {table}
            DROP COLUMN IF EXISTS embedding_model_version
        """)
