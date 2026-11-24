"""Admit M7 surface selection waits without claiming a provider failure.

Revision ID: approvals_025
Revises: approvals_024
Create Date: 2026-11-24 00:00:00.000000
"""

from alembic import op

revision = "approvals_025"
down_revision = "approvals_024"
branch_labels = None
depends_on = None

_CODES = (
    "'quiet_hours', 'owner_mode', 'surface_wait', 'owner_recipient_unavailable', "
    "'callback_secret_unavailable', 'transport_unavailable', 'provider_preflight_failed', "
    "'provider_outcome_unknown', 'action_approved', 'action_rejected', 'action_expired', "
    "'action_abandoned', 'action_cancelled', 'defer_rescheduled', 'cohort_empty'"
)


def upgrade() -> None:
    for table, column in (
        ("approval_delivery_presentations", "last_reason_code"),
        ("approval_delivery_attempts", "reason_code"),
    ):
        op.execute(f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {table}_{column}_check")
        op.execute(
            f"ALTER TABLE {table} ADD CONSTRAINT {table}_{column}_check "
            f"CHECK ({column} IN ({_CODES}))"
        )


def downgrade() -> None:
    # Keep honest reason receipts on source rollback, including other schema consumers.
    upgrade()
