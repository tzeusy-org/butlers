"""obligation_ledger

Revision ID: finance_014
Revises: finance_013
Create Date: 2026-09-05 00:00:00.000000

bu-8cdl1.10 slice 2 of 3. Slice 1 (finance_013) gave subscriptions a
cancellation door (``cancellation_url``/``notice_period_days``/``cancel_by``)
but nothing yet turns that door into a forward-looking warning. This slice
adds ``obligation_ledger``: one row per (subscription, renewal period)
registering the derived ``warn_by`` date (``cancel_by - notice_period_days``),
an ``unknown_door`` flag when the door metadata is incomplete, and an optional
pre-charge price-change flag. The insight payload/dashboard surfacing of this
ledger (slice 3) lands separately.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "finance_014"
down_revision = "finance_013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS obligation_ledger (
            id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            subscription_id         UUID NOT NULL REFERENCES subscriptions(id) ON DELETE CASCADE,
            period                  DATE NOT NULL,
            warn_by                 DATE,
            unknown_door            BOOLEAN NOT NULL DEFAULT false,
            price_change_amount     NUMERIC(14, 2),
            price_change_direction  TEXT
                                         CHECK (price_change_direction IS NULL
                                             OR price_change_direction IN ('increase', 'decrease')),
            created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_obligation_ledger_subscription_period UNIQUE (subscription_id, period)
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_obligation_ledger_warn_by
            ON obligation_ledger (warn_by)
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS obligation_ledger")
