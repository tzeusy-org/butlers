"""ha_command_log: pre-call state and the ``already_satisfied`` outcome (bu-jv1moa.123).

Revision ID: home_011
Revises: home_010
Create Date: 2026-11-21 00:00:00.000000

REQ-module-home-assistant-003 and -004 (RFC 0028 Amendment 1).  ``pre_state`` holds the live
state read of every target entity just before the receipt claim of an approved critical call, so
the receipt keeps it beside ``requested_state`` and ``observed_state``.  ``already_satisfied`` is a
fifth terminal receipt status: the pre-call state already met the declared post-condition, nothing
was sent, and no effect is claimed.  It is not ``succeeded``.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "home_011"
down_revision = "home_010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE ha_command_log ADD COLUMN IF NOT EXISTS pre_state JSONB")
    op.execute(
        "ALTER TABLE ha_command_log DROP CONSTRAINT IF EXISTS ck_ha_command_log_actuation_status"
    )
    op.execute("""
        ALTER TABLE ha_command_log
            ADD CONSTRAINT ck_ha_command_log_actuation_status
            CHECK (status IS NULL OR status IN (
                'attempting', 'succeeded', 'failed', 'unverified', 'already_satisfied'
            ))
    """)


def downgrade() -> None:
    # Fails loudly if an already_satisfied receipt exists: a receipt is never rewritten.
    op.execute(
        "ALTER TABLE ha_command_log DROP CONSTRAINT IF EXISTS ck_ha_command_log_actuation_status"
    )
    op.execute("""
        ALTER TABLE ha_command_log
            ADD CONSTRAINT ck_ha_command_log_actuation_status
            CHECK (status IS NULL OR status IN (
                'attempting', 'succeeded', 'failed', 'unverified'
            ))
    """)
    op.execute("ALTER TABLE ha_command_log DROP COLUMN IF EXISTS pre_state")
