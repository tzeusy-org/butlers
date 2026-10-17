"""Bind each presented approval card to the digest of the call it showed.

Revision ID: approvals_018
Revises: approvals_017
Create Date: 2026-10-18 00:00:00.000000

``args_digest`` is the SHA-256 of the canonical JSON of the stored
``pending_actions.tool_args`` that the card rendered.  It is written under the
action row lock when the handoff starts and compared at approve time, so a tap
whose stored call has since changed executes nothing.  The RFC 0023
``presentation_key`` and the HMAC callback token are unchanged.  Rows presented
before this revision (and digest/collapsed presentations) keep ``NULL``.
"""

from __future__ import annotations

from alembic import op

revision = "approvals_018"
down_revision = "approvals_017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE approval_delivery_presentations ADD COLUMN IF NOT EXISTS args_digest TEXT"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE approval_delivery_presentations DROP COLUMN IF EXISTS args_digest")
