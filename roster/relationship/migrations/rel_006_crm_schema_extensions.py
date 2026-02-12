"""rel_006_crm_schema_extensions

Revision ID: rel_006
Revises: rel_005
Create Date: 2026-02-12 00:00:00.000000

"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "rel_006"
down_revision = "rel_005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Loans: two-party model + integer cents + currency.
    op.execute(
        """
        ALTER TABLE loans
        ADD COLUMN IF NOT EXISTS lender_contact_id UUID
        REFERENCES contacts(id) ON DELETE SET NULL
        """
    )
    op.execute(
        """
        ALTER TABLE loans
        ADD COLUMN IF NOT EXISTS borrower_contact_id UUID
        REFERENCES contacts(id) ON DELETE SET NULL
        """
    )
    op.execute("ALTER TABLE loans ADD COLUMN IF NOT EXISTS amount_cents BIGINT")
    op.execute("ALTER TABLE loans ADD COLUMN IF NOT EXISTS currency VARCHAR(3) DEFAULT 'USD'")

    op.execute("""
        UPDATE loans
        SET amount_cents = COALESCE(amount_cents, ROUND(amount * 100)::bigint),
            currency = COALESCE(currency, 'USD'),
            lender_contact_id = COALESCE(
                lender_contact_id,
                CASE WHEN direction = 'lent' THEN contact_id ELSE NULL END
            ),
            borrower_contact_id = COALESCE(
                borrower_contact_id,
                CASE WHEN direction = 'borrowed' THEN contact_id ELSE NULL END
            )
    """)

    # Groups: taxonomy type + member role.
    op.execute("ALTER TABLE groups ADD COLUMN IF NOT EXISTS type VARCHAR DEFAULT 'custom'")
    op.execute("ALTER TABLE group_members ADD COLUMN IF NOT EXISTS role VARCHAR")

    # Activity feed linking columns.
    op.execute("ALTER TABLE activity_feed ADD COLUMN IF NOT EXISTS entity_type VARCHAR")
    op.execute("ALTER TABLE activity_feed ADD COLUMN IF NOT EXISTS entity_id UUID")


def downgrade() -> None:
    op.execute("ALTER TABLE activity_feed DROP COLUMN IF EXISTS entity_id")
    op.execute("ALTER TABLE activity_feed DROP COLUMN IF EXISTS entity_type")

    op.execute("ALTER TABLE group_members DROP COLUMN IF EXISTS role")
    op.execute("ALTER TABLE groups DROP COLUMN IF EXISTS type")

    op.execute("ALTER TABLE loans DROP COLUMN IF EXISTS currency")
    op.execute("ALTER TABLE loans DROP COLUMN IF EXISTS amount_cents")
    op.execute("ALTER TABLE loans DROP COLUMN IF EXISTS borrower_contact_id")
    op.execute("ALTER TABLE loans DROP COLUMN IF EXISTS lender_contact_id")
