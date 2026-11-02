"""subscription_cancellation_provenance

Revision ID: finance_017
Revises: finance_016
Create Date: 2026-10-18 00:00:00.000000

bu-svqpzo.10. A subscription's cancellation link and status reach the owner as
trusted only when the owner set them. ``cancellation_url_source`` and
``status_source`` record who wrote each field: ``owner`` (an owner-attested
session) or ``extracted`` (anything else, including a routed email or an
unreadable attestation).

Both columns are NOT NULL and default to ``extracted``, so every existing row
backfills as extracted -- historical writes cannot be told apart, even where old
expected-signal metadata claims the owner -- and any writer that does not name a
source fails closed.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "finance_017"
down_revision = "finance_016"
branch_labels = None
depends_on = None

_COLUMNS = ("cancellation_url_source", "status_source")


def upgrade() -> None:
    for column in _COLUMNS:
        op.execute(f"""
            ALTER TABLE subscriptions
                ADD COLUMN IF NOT EXISTS {column} TEXT NOT NULL DEFAULT 'extracted'
        """)
        op.execute(f"""
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM pg_constraint
                    WHERE conname = 'subscriptions_{column}_check'
                      AND conrelid = 'subscriptions'::regclass
                ) THEN
                    ALTER TABLE subscriptions
                        ADD CONSTRAINT subscriptions_{column}_check
                            CHECK ({column} IN ('owner', 'extracted'));
                END IF;
            END $$
        """)


def downgrade() -> None:
    for column in _COLUMNS:
        op.execute(
            f"ALTER TABLE subscriptions DROP CONSTRAINT IF EXISTS subscriptions_{column}_check"
        )
        op.execute(f"ALTER TABLE subscriptions DROP COLUMN IF EXISTS {column}")
