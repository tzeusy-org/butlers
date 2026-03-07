"""finance_predicates

Seeds finance-domain predicates into predicate_registry for bitemporal fact
migration of the 4 finance butler tables.

Temporal predicates (is_temporal=true — valid_at = posted_at):
  - transaction_debit   → debit (money-out) transactions
  - transaction_credit  → credit (money-in / refund) transactions

Property predicates (is_temporal=false — supersession):
  - account      → registered financial account
  - subscription → recurring service subscription
  - bill         → payable obligation

Revision ID: mem_009
Revises: mem_008
Create Date: 2026-03-08 00:00:00.000000

"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "mem_009"
down_revision = "mem_008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Temporal predicates — one fact per transaction event
    op.execute(
        "INSERT INTO predicate_registry"
        " (name, expected_subject_type, is_temporal, description) VALUES"
        " ('transaction_debit', 'person', true,"
        "  'Debit (money-out) transaction event, content = merchant amount currency'),"
        " ('transaction_credit', 'person', true,"
        "  'Credit (money-in / refund) transaction event, content = merchant amount currency')"
        " ON CONFLICT (name) DO UPDATE SET"
        " is_temporal = EXCLUDED.is_temporal,"
        " expected_subject_type = EXCLUDED.expected_subject_type,"
        " description = EXCLUDED.description"
    )

    # Property predicates — supersession (one active fact per account/subscription/bill)
    op.execute(
        "INSERT INTO predicate_registry"
        " (name, expected_subject_type, is_temporal, description) VALUES"
        " ('account', 'person', false,"
        "  'Financial account, content = institution type ****last_four'),"
        " ('subscription', 'person', false,"
        "  'Recurring service subscription, content = service amount/frequency'),"
        " ('bill', 'person', false,"
        "  'Payable bill obligation, content = payee amount due due_date')"
        " ON CONFLICT (name) DO UPDATE SET"
        " is_temporal = EXCLUDED.is_temporal,"
        " expected_subject_type = EXCLUDED.expected_subject_type,"
        " description = EXCLUDED.description"
    )


def downgrade() -> None:
    op.execute(
        "DELETE FROM predicate_registry"
        " WHERE name IN ("
        " 'transaction_debit', 'transaction_credit',"
        " 'account', 'subscription', 'bill'"
        " )"
    )
