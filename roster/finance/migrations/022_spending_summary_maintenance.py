"""Allow the Finance daemon to refresh its spending summary.

Revision ID: finance_022
Revises: finance_021

PostgreSQL 17 adds the relation-scoped MAINTAIN privilege. Grant it on the
Finance materialized view so the existing concurrent refresh can run with the
normal runtime role. Older servers retain the existing best-effort behavior.
"""

from alembic import op

revision = "finance_022"
down_revision = "finance_021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        DO $$ BEGIN
            IF current_schema() = 'finance'
                AND current_setting('server_version_num')::integer >= 170000 THEN
                EXECUTE 'GRANT MAINTAIN ON TABLE finance.spending_summaries TO butler_finance_rw';
            END IF;
        END $$;
    """)


def downgrade() -> None:
    op.execute("""
        DO $$ BEGIN
            IF current_schema() = 'finance'
                AND current_setting('server_version_num')::integer >= 170000 THEN
                EXECUTE 'REVOKE MAINTAIN ON TABLE finance.spending_summaries FROM butler_finance_rw';
            END IF;
        END $$;
    """)
