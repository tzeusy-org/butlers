"""Seed a global ingestion rule that skips Home Assistant state-change events.

Revision ID: sw_010
Revises: sw_009
Create Date: 2026-05-03 00:00:00.000000

Home Assistant emits one ingest envelope per ``person.*`` state-change event
(presence transitions, arrival/departure, etc.).  Each event was falling
through to ``action='pass_through'`` and spawning a Switchboard LLM
classification session plus a downstream butler LLM session — generating
~16 ``Conversation via home_assistant`` episodes/day in the Conversations
lane and burning tokens on signal whose value lives in the
``connectors.home_assistant_history`` evidence table itself.

This migration mirrors the OwnTracks skip rule (sw_006).  The connector
continues to write rows into ``connectors.home_assistant_history`` directly
(connector_writer role); only the LLM routing path is short-circuited.
The Chronicler ``home_assistant.history`` projection adapter is unaffected
and continues to render presence_episode rows on the Home lane.

The rule is disabled via ``UPDATE switchboard.ingestion_rules SET
enabled=false WHERE id='00000000-0000-0000-0001-000000000090'`` if LLM
routing is ever wanted back for Home Assistant events.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "sw_010"
down_revision = "sw_009"
branch_labels = None
depends_on = None


_RULE_ID = "00000000-0000-0000-0001-000000000090"


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
            '{{"source_channel": "home_assistant"}}',
            'skip',
            10,
            TRUE,
            'Skip Home Assistant state-change events',
            'Home Assistant webhook events bypass LLM classification. Rows still land in public.ingestion_events and connectors.home_assistant_history for direct DB querying and Chronicler projection.',
            'seed'
          )
        ON CONFLICT (id) DO NOTHING
        """
    )


def downgrade() -> None:
    op.execute(f"DELETE FROM ingestion_rules WHERE id = '{_RULE_ID}'")
