"""transactions_dedup_constraint

Add a UNIQUE index on transactions(account_id, posted_at, amount, merchant) with
NULLS NOT DISTINCT so that NULL account_id values are treated as equal for dedup
purposes.  This gives ON CONFLICT (account_id, posted_at, amount, merchant) a
concrete conflict target in _insert_batch, turning the silent ON CONFLICT DO NOTHING
fallback into an effective guard against duplicate ingestion even when no
source_message_id is available (Priority-3 / composite dedup path).

Background: PR #891 review finding — _insert_batch used ON CONFLICT DO NOTHING
without a conflict target, which is a no-op.  The pre-insert _check_duplicate()
call provides a soft guard but is subject to TOCTOU races under concurrent
imports.  This index closes the gap at the database level.

NULL handling rationale: account_id is nullable (transactions may be imported
before an account record exists).  Standard PostgreSQL UNIQUE treats each NULL
as distinct from all other NULLs, so without NULLS NOT DISTINCT the constraint
would not catch duplicate unlinked rows.  NULLS NOT DISTINCT (PostgreSQL 15+)
collapses all NULLs into the same bucket for uniqueness purposes, matching the
semantics of _check_duplicate() which handles the NULL branch explicitly.

Revision ID: finance_003
Revises: finance_002
Create Date: 2026-03-28 00:00:00.000000

"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "finance_003"
down_revision = "finance_002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add a UNIQUE index with NULLS NOT DISTINCT so that rows with
    # account_id IS NULL are treated as duplicates of each other when
    # (posted_at, amount, merchant) match.  This enables the ON CONFLICT
    # clause in _insert_batch to actually fire.
    #
    # NULLS NOT DISTINCT is a PostgreSQL 15+ feature; the project targets
    # PostgreSQL 16 (see .github/workflows/ci.yml).
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_transactions_composite_dedup
            ON transactions (account_id, posted_at, amount, merchant)
            NULLS NOT DISTINCT
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_transactions_composite_dedup")
