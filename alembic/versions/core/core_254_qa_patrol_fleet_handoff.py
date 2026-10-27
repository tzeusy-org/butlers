"""qa_patrols: record the fleet-condition handoff mode each scheduled patrol ran with.

Revision ID: core_254
Revises: core_253
Create Date: 2026-09-30 00:00:00.000000

bu-vfobja.  ``BUTLERS_FLEET_CONDITION_HANDOFF`` must be set identically in the
Dashboard API process (which runs the independent fleet controller) and the
daemon process (which runs QA's ``infra_state`` source).  With QA handing
per-butler liveness episodes over to a controller that is not taking them, the
legacy episodes stay open forever and nothing reports it.

Adds ``fleet_condition_handoff BOOLEAN``: the mode a scheduled QA patrol ran
with, so the controller can compare it with its own.  Nullable with no backfill:
a legacy or operator-synthetic row has no evidence of QA's mode, and unknown is
never read as agreement.

The core chain runs once per butler schema against this shared public table,
so the statement is idempotent.  Downgrade drops the column; the controller then
has no evidence and records no mismatch, never a false agreement.
"""

from __future__ import annotations

from alembic import op

revision = "core_254"
down_revision = "core_253"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE public.qa_patrols ADD COLUMN IF NOT EXISTS fleet_condition_handoff BOOLEAN"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE public.qa_patrols DROP COLUMN IF EXISTS fleet_condition_handoff")
