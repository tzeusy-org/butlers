"""Retain complete subscription field claims without selecting a legacy winner.

Revision ID: finance_019
Revises: finance_018
"""

from alembic import op

revision = "finance_019"
down_revision = "finance_018"
branch_labels = None
depends_on = None


def upgrade():
    # Refusal is before any mutation; lossless duplicate repair is a separate explicit operation.
    op.execute("""DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM subscriptions GROUP BY service, frequency HAVING count(*) > 1)
        THEN RAISE EXCEPTION 'subscription_identity_ambiguous'; END IF;
    END $$""")
    for column in ("renewal_source", "price_source", "service_source"):
        op.execute(
            f"ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS {column} "
            "TEXT NOT NULL DEFAULT 'extracted' "
            f"CHECK ({column} IN ('owner', 'extracted'))"
        )
    op.execute(
        "ALTER TABLE bills ADD COLUMN IF NOT EXISTS payee_source TEXT "
        "NOT NULL DEFAULT 'extracted' CHECK (payee_source IN ('owner', 'extracted'))"
    )
    for column in ("date_epoch", "price_epoch", "value_revision"):
        op.execute(
            f"ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS {column} "
            f"BIGINT NOT NULL DEFAULT 0 CHECK ({column} >= 0)"
        )
    op.execute(
        "ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS field_claims JSONB "
        "NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(field_claims) = 'array')"
    )
    # Complete pair claims cannot be torn into independently applied fields.
    op.execute("ALTER TABLE subscriptions DROP CONSTRAINT IF EXISTS subscription_claim_shape")
    op.execute("""ALTER TABLE subscriptions ADD CONSTRAINT subscription_claim_shape CHECK (
        NOT jsonb_path_exists(field_claims,
          '$[*] ? (@.type() != "object" || !exists(@.field) || !exists(@.value) || !exists(@.epoch) || !exists(@.resolved))')
        AND NOT jsonb_path_exists(field_claims,
          '$[*] ? (@.field != "renewal" && @.field != "price")')
        AND NOT jsonb_path_exists(field_claims,
          '$[*] ? (@.field == "price" && (@.value.type() != "object" || !exists(@.value.amount) || !exists(@.value.currency) || @.value.amount.type() != "string" || @.value.currency.type() != "string"))')
    )""")
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_subscription_service_frequency "
        "ON subscriptions(service, frequency)"
    )


def downgrade():
    # Migration transactions hold both relations through the emptiness check and
    # column removal. A racing writer cannot create unobserved field history.
    op.execute("LOCK TABLE subscriptions, bills IN ACCESS EXCLUSIVE MODE")
    op.execute("""DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM subscriptions) OR EXISTS (SELECT 1 FROM bills)
        THEN RAISE EXCEPTION 'finance_field_claims_downgrade_unsafe'; END IF;
    END $$""")
    op.execute("DROP INDEX IF EXISTS uq_subscription_service_frequency")
    op.execute("ALTER TABLE subscriptions DROP CONSTRAINT IF EXISTS subscription_claim_shape")
    for column in (
        "renewal_source",
        "price_source",
        "service_source",
        "date_epoch",
        "price_epoch",
        "value_revision",
        "field_claims",
    ):
        op.execute(f"ALTER TABLE subscriptions DROP COLUMN IF EXISTS {column}")
    op.execute("ALTER TABLE bills DROP COLUMN IF EXISTS payee_source")
