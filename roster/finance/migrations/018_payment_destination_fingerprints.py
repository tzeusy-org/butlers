"""payment_destination_fingerprints

Revision ID: finance_018
Revises: finance_017
Create Date: 2026-10-18 00:00:00.000000

bu-s11n0s.21 (slice S1 of bu-s11n0s.9, payment-redirect guard). Finance
remembers, per counterparty, which payment destinations (bank account, IBAN,
payment handle) its bills have asked the owner to pay, without storing the raw
value: ``payment_destinations`` holds a keyed HMAC fingerprint, the last four
characters and the destination kind, and ``bills`` gains a nullable fingerprint
and verdict. Additive; the downgrade drops them.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "finance_018"
down_revision = "finance_017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS payment_destinations (
            id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            payee_key     TEXT NOT NULL,
            kind          TEXT NOT NULL CHECK (kind IN ('iban', 'account', 'handle')),
            fingerprint   TEXT NOT NULL,
            last4         TEXT NOT NULL,
            first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            last_seen_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            seen_count    INTEGER NOT NULL DEFAULT 1,
            CONSTRAINT uq_payment_destinations_payee_fingerprint UNIQUE (payee_key, fingerprint)
        )
    """)
    op.execute("ALTER TABLE bills ADD COLUMN IF NOT EXISTS destination_fingerprint TEXT")
    op.execute("ALTER TABLE bills ADD COLUMN IF NOT EXISTS destination_verdict TEXT")
    op.execute("ALTER TABLE bills DROP CONSTRAINT IF EXISTS bills_destination_verdict_check")
    op.execute("""
        ALTER TABLE bills ADD CONSTRAINT bills_destination_verdict_check
            CHECK (destination_verdict IS NULL
                OR destination_verdict IN ('known', 'new_for_counterparty', 'unverifiable'))
    """)


def downgrade() -> None:
    op.execute("ALTER TABLE bills DROP CONSTRAINT IF EXISTS bills_destination_verdict_check")
    op.execute("ALTER TABLE bills DROP COLUMN IF EXISTS destination_verdict")
    op.execute("ALTER TABLE bills DROP COLUMN IF EXISTS destination_fingerprint")
    op.execute("DROP TABLE IF EXISTS payment_destinations")
