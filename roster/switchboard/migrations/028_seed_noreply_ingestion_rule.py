"""Seed ingestion rule to skip emails from noreply/no-reply senders.

Revision ID: sw_028
Revises: sw_027
Create Date: 2026-03-10 00:00:00.000000

Migration notes:
- Adds a global ingestion rule that matches sender addresses whose local part
  starts with "noreply" or "no-reply" (e.g. noreply@grab.com, no-reply@uber.com).
- Action is 'metadata_only' — the email is ingested for record-keeping but not
  routed to a butler as an interactive message.
- Uses two rows with match=local_part_prefix on the sender_address rule_type.
- Priority 5 ensures this runs before domain-specific routing rules (priority 10+).
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "sw_028"
down_revision = "sw_027"
branch_labels = None
depends_on = None

_NOREPLY_RULE_ID = "00000000-0000-0000-0001-000000000060"
_NO_REPLY_RULE_ID = "00000000-0000-0000-0001-000000000061"


def upgrade() -> None:
    op.execute(
        f"""
        INSERT INTO ingestion_rules
          (id, scope, rule_type, condition, action, priority, enabled,
           name, description, created_by)
        VALUES
          (
            '{_NOREPLY_RULE_ID}',
            'global',
            'sender_address',
            '{{"address": "noreply", "match": "local_part_prefix"}}',
            'metadata_only',
            5,
            TRUE,
            'Skip noreply senders',
            'Emails from noreply@* addresses are ingested as metadata only — not routed for interactive response.',
            'seed'
          ),
          (
            '{_NO_REPLY_RULE_ID}',
            'global',
            'sender_address',
            '{{"address": "no-reply", "match": "local_part_prefix"}}',
            'metadata_only',
            5,
            TRUE,
            'Skip no-reply senders',
            'Emails from no-reply@* addresses are ingested as metadata only — not routed for interactive response.',
            'seed'
          )
        ON CONFLICT (id) DO NOTHING
        """
    )


def downgrade() -> None:
    op.execute(
        f"""
        DELETE FROM ingestion_rules
        WHERE id IN ('{_NOREPLY_RULE_ID}', '{_NO_REPLY_RULE_ID}')
        """
    )
