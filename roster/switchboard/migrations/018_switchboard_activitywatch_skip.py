"""Seed a global ingestion rule that skips ActivityWatch window-focus events.

Revision ID: sw_018
Revises: sw_017
Create Date: 2026-07-05 00:00:00.000000

The ActivityWatch connector (bu-whhll.6, src/butlers/connectors/activitywatch.py)
emits one ingest envelope per window-focus event — potentially dozens per hour
during active desktop use. Each event would otherwise fall through to
``action='pass_through'`` and spawn a Switchboard LLM classification session
plus a downstream butler LLM session, burning tokens on signal whose value
lives in the ``connectors.activitywatch_events`` evidence table itself.

This migration mirrors the OwnTracks (sw_006) and Home Assistant (sw_010)
skip rules. The connector continues to write rows into
``connectors.activitywatch_events`` directly (connector_writer role); only
the LLM routing path is short-circuited. The Chronicler
``activitywatch.window`` projection adapter is unaffected and continues to
render app_focus point events / screen_episode rows.

The rule is disabled via ``UPDATE switchboard.ingestion_rules SET
enabled=false WHERE id='00000000-0000-0000-0001-000000000100'`` if LLM
routing is ever wanted back for ActivityWatch events.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "sw_018"
down_revision = "sw_017"
branch_labels = None
depends_on = None


_RULE_ID = "00000000-0000-0000-0001-000000000100"


def upgrade() -> None:
    op.execute(
        f"""
        INSERT INTO ingestion_rules
          (id, scope, rule_type, condition, action, priority, enabled,
           name, description, created_by)
        VALUES
          (
            '{_RULE_ID}',
            'global',
            'source_channel',
            '{{"source_channel": "activitywatch"}}',
            'skip',
            10,
            TRUE,
            'Skip ActivityWatch window-focus events',
            'ActivityWatch events bypass LLM classification. Rows still land in public.ingestion_events and connectors.activitywatch_events for direct DB querying and Chronicler projection.',
            'seed'
          )
        ON CONFLICT (id) DO NOTHING
        """
    )


def downgrade() -> None:
    op.execute(f"DELETE FROM ingestion_rules WHERE id = '{_RULE_ID}'")
