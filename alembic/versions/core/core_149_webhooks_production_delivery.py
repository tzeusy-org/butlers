"""webhooks: add last_delivery_at / last_delivery_ok for production dispatch tracking.

Revision ID: core_149
Revises: core_148
Create Date: 2026-07-02 00:00:00.000000

Separates production-delivery state from test-fire state so the Settings
Console attention aggregator can surface ``kind="webhook_failure"`` items that
derive from real delivery failures rather than test results.

Before this migration the ``_check_failed_webhooks`` aggregator queried
``last_test_ok = false AND last_test_at >= <cutoff>`` which fired the amber
alert whenever a webhook *test* failed -- misleading when the endpoint was only
temporarily unreachable during a test.

After this migration:

* ``last_delivery_at`` / ``last_delivery_ok`` track the most recent production
  dispatch (i.e. a real domain event, not a test-fire).
* The attention aggregator queries the new columns instead.
* The existing ``last_test_at`` / ``last_test_ok`` columns remain unchanged --
  the test-fire endpoint continues to update them for the dashboard test-result
  display.
"""

from __future__ import annotations

from alembic import op

revision = "core_149"
down_revision = "core_148"
branch_labels = None
depends_on = None

_ALL_RUNTIME_ROLES = (
    "butler_chronicler_rw",
    "butler_education_rw",
    "butler_finance_rw",
    "butler_general_rw",
    "butler_health_rw",
    "butler_home_rw",
    "butler_lifestyle_rw",
    "butler_messenger_rw",
    "butler_qa_rw",
    "butler_relationship_rw",
    "butler_switchboard_rw",
    "butler_travel_rw",
    "connector_writer",
)

_TABLE_PRIVILEGES = "SELECT, INSERT, UPDATE, DELETE"


def _quote_ident(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _grant_best_effort(table_fqn: str, privilege: str, role: str) -> None:
    """GRANT privilege ON table TO role; tolerate older DBs missing roles."""
    op.execute(
        f"""
        DO $$
        BEGIN
            IF to_regclass('{table_fqn}') IS NOT NULL
               AND EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}')
            THEN
                EXECUTE 'GRANT {privilege} ON TABLE {table_fqn} TO {_quote_ident(role)}';
            END IF;
        EXCEPTION
            WHEN insufficient_privilege THEN NULL;
            WHEN undefined_object THEN NULL;
            WHEN undefined_table THEN NULL;
            WHEN invalid_schema_name THEN NULL;
        END
        $$;
        """
    )


def upgrade() -> None:
    op.execute(
        "ALTER TABLE public.webhooks "
        "ADD COLUMN IF NOT EXISTS last_delivery_at TIMESTAMPTZ"
    )
    op.execute(
        "ALTER TABLE public.webhooks "
        "ADD COLUMN IF NOT EXISTS last_delivery_ok BOOL"
    )

    # Grants: table-level privileges already cover new columns.
    for role in _ALL_RUNTIME_ROLES:
        _grant_best_effort("public.webhooks", _TABLE_PRIVILEGES, role)


def downgrade() -> None:
    op.execute(
        "ALTER TABLE IF EXISTS public.webhooks "
        "DROP COLUMN IF EXISTS last_delivery_ok"
    )
    op.execute(
        "ALTER TABLE IF EXISTS public.webhooks "
        "DROP COLUMN IF EXISTS last_delivery_at"
    )
