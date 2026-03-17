"""merge_parallel_035_heads: merge token-ledger (core_035) and self-healing (core_036) branches

Revision ID: core_037
Revises: core_035, core_036
Create Date: 2026-03-17 00:00:00.000000

Two parallel PRs both introduced migrations labelled core_035:

  - core_035 (core_035_token_usage_ledger_and_limits.py)   — PR #662
  - core_035b (core_035_self_healing_tier_and_attempts.py) — PR #666

PR #666 also introduced core_036 (core_036_sessions_add_healing_fingerprint.py),
which depends on core_035b.

After both PRs landed on main the migration graph had two heads:

  core_034 → core_035  (token-ledger branch, no successor)
  core_034 → core_035b → core_036  (self-healing branch)

This migration is a no-op merge point that joins the two tips so that all
subsequent migrations have a single, unambiguous parent.
"""

from __future__ import annotations

# revision identifiers, used by Alembic.
revision = "core_037"
down_revision = ("core_035", "core_036")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
