"""Separate calibration readiness from calibration delivery on the receipt.

Revision ID: education_005
Revises: education_004
Create Date: 2026-08-24 00:00:00.000000

``education_004`` gave the curriculum-request receipt a ``calibration_ready_at``
column, set when the correlated teaching flow has reached ``diagnosing`` or
beyond. That is honest about what it measures: calibration *began*. It says
nothing about whether the notice the session was asked to send ever left the
building, and no column on the receipt did.

This migration adds the two columns that carry that second, separate fact, and
a constraint that keeps them from drifting apart:

``calibration_notice_outcome``
    The notification path's own terminal outcome for the notice sent by the
    session that started this curriculum, copied verbatim from
    ``public.attention_ledger`` (``delivered``, ``coalesced``, ``deferred``,
    ``suppressed``, ``failed``), plus two values the ledger cannot supply:
    ``no_record`` (the ledger was consulted and holds nothing for that session)
    and ``unproven`` (the ledger could not be consulted, or there was no
    session id to consult it with). NULL means the question was never asked,
    which is what a receipt that failed before any calibration existed should
    say.

``calibration_notice_accepted_at``
    When, and only when, the outcome is ``delivered``: the moment the delivery
    channel accepted the notice, taken from the ledger row's ``occurred_at``.
    "Accepted by the channel" is the strongest thing the notification path can
    attest -- Switchboard's ``deliver()`` reports ``sent`` once the Messenger
    says the provider took the message. It is not a receipt from the owner and
    it is certainly not proof they read it, so the column is named for channel
    acceptance rather than for owner contact.

``curriculum_requests_notice_evidence``
    ``outcome = 'delivered'`` if and only if ``calibration_notice_accepted_at``
    is set. Without it the pair could drift into a row that names a delivery
    time under a non-delivery outcome, or claims ``delivered`` with no moment
    to point at -- either way, a receipt asserting contact it cannot evidence.
    ``IS NOT DISTINCT FROM`` rather than ``=`` so a NULL outcome yields false
    instead of NULL (an unknown CHECK passes, which would let a NULL outcome
    carry a timestamp).

See openspec/specs/dashboard-education-api/spec.md, "Curriculum request
receipt lifecycle", and bu-358jk.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "education_005"
down_revision = "education_004"
branch_labels = None
depends_on = None

# The ledger's own outcome vocabulary (butlers.core.attention_ledger), plus the
# two answers only this reader can give.
_NOTICE_OUTCOMES = (
    "delivered",
    "coalesced",
    "deferred",
    "suppressed",
    "failed",
    "no_record",
    "unproven",
)


def upgrade() -> None:
    op.execute("""
        ALTER TABLE education.curriculum_requests
            ADD COLUMN IF NOT EXISTS calibration_notice_outcome TEXT,
            ADD COLUMN IF NOT EXISTS calibration_notice_accepted_at TIMESTAMPTZ
    """)

    outcomes = ", ".join(f"'{value}'" for value in _NOTICE_OUTCOMES)
    op.execute(f"""
        ALTER TABLE education.curriculum_requests
            ADD CONSTRAINT curriculum_requests_notice_outcome CHECK (
                calibration_notice_outcome IS NULL
                OR calibration_notice_outcome IN ({outcomes})
            )
    """)

    op.execute("""
        ALTER TABLE education.curriculum_requests
            ADD CONSTRAINT curriculum_requests_notice_evidence CHECK (
                (calibration_notice_outcome IS NOT DISTINCT FROM 'delivered')
                = (calibration_notice_accepted_at IS NOT NULL)
            )
    """)


def downgrade() -> None:
    op.execute("""
        ALTER TABLE education.curriculum_requests
            DROP CONSTRAINT IF EXISTS curriculum_requests_notice_evidence,
            DROP CONSTRAINT IF EXISTS curriculum_requests_notice_outcome,
            DROP COLUMN IF EXISTS calibration_notice_accepted_at,
            DROP COLUMN IF EXISTS calibration_notice_outcome
    """)
