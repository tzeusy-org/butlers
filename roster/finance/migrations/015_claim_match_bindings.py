"""Finance-local exclusive bindings from cost claims to transactions.

Revision ID: finance_015
Revises: finance_014
Create Date: 2026-09-15 00:00:00.000000
"""

from __future__ import annotations

from alembic import op

revision = "finance_015"
down_revision = "finance_014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS claim_match_bindings (
            claim_id UUID PRIMARY KEY REFERENCES public.cost_claims(id) ON DELETE CASCADE,
            transaction_id UUID NOT NULL REFERENCES transactions(id) ON DELETE CASCADE,
            bound_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (transaction_id)
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS claim_match_bindings")
