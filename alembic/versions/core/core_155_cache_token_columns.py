"""cache_token_columns: cached/cache-write token counts on sessions + ledger.

Revision ID: core_155
Revises: core_154
Create Date: 2026-07-05 00:00:00.000000

Cost estimation previously priced only ``input_tokens``/``output_tokens``.
Prompt-cache reads and writes — the dominant token buckets in agentic
sessions, billed by vendors at distinct rates (Anthropic: reads 0.1x input,
writes 1.25x input) — were parsed by the runtime adapters and then dropped.

Columns (all additive, nullable/default-0 — existing rows keep working):

  sessions.cached_input_tokens         INTEGER NULL — prompt-cache reads
  sessions.cache_creation_tokens       INTEGER NULL — prompt-cache writes
  public.token_usage_ledger.cached_input_tokens   INTEGER NOT NULL DEFAULT 0
  public.token_usage_ledger.cache_creation_tokens INTEGER NOT NULL DEFAULT 0

``sessions`` is a per-butler-schema table (unqualified name resolves via the
migration's search_path); the ledger is public and shared, so its ALTER uses
IF NOT EXISTS to stay idempotent across the per-schema chain runs.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "core_155"
down_revision = "core_154"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE sessions
            ADD COLUMN IF NOT EXISTS cached_input_tokens INTEGER NULL,
            ADD COLUMN IF NOT EXISTS cache_creation_tokens INTEGER NULL
        """
    )
    op.execute(
        """
        ALTER TABLE public.token_usage_ledger
            ADD COLUMN IF NOT EXISTS cached_input_tokens INTEGER NOT NULL DEFAULT 0,
            ADD COLUMN IF NOT EXISTS cache_creation_tokens INTEGER NOT NULL DEFAULT 0
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE sessions
            DROP COLUMN IF EXISTS cached_input_tokens,
            DROP COLUMN IF EXISTS cache_creation_tokens
        """
    )
    op.execute(
        """
        ALTER TABLE public.token_usage_ledger
            DROP COLUMN IF EXISTS cached_input_tokens,
            DROP COLUMN IF EXISTS cache_creation_tokens
        """
    )
