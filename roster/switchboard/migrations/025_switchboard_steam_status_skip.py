"""Seed a global ingestion rule that metadata-onlys Steam presence status_change events.

Revision ID: sw_025
Revises: sw_024
Create Date: 2026-07-12 00:00:00.000000

bu-f7yk5: Steam online/offline presence changes (`event.type='status_change'`)
must still be ingested/logged, but must not be routed to a domain butler or
produce proactive ``notify()`` messages. Repro: 2026-07-11 11:50 SGT a Steam
"Came online" status_change routed to the general butler, which sent a
Telegram welcome-back notification.

Setting `control.ingestion_tier='metadata'` on the envelope (already done in
``src/butlers/connectors/steam.py::build_status_change_envelope``) only
controls *persistence* (payload.raw=null, initial message_inbox lifecycle
state) — it is never read by ``pipeline.process()``'s routing/classification
decision. The actual LLM-classification/routing bypass is the pre-resolved
``triage_decision`` sourced from an ``ingestion_rules`` row (scope='global'),
evaluated by ``IngestionPolicyEvaluator`` before any LLM session spawns. No
rule existed for Steam status_change, so events still fell through to
``pass_through``, got LLM-classified, and could route to a butler + fire a
proactive notify.

Unlike the OwnTracks (sw_006), Home Assistant (sw_010), and ActivityWatch
(sw_018) skip rules, the `gaming` source_channel carries multiple event types
(play_session, achievement_unlock, game_purchase, friend_change, in addition
to status_change) that must remain fully routable — a bare
``rule_type='source_channel'`` rule would over-scope and silence all of them.
Steam's ``external_event_id`` already encodes the event type as a stable
prefix (e.g. ``"steam:status:<steam_id>:<poll_ts>"`` vs
``"steam:play:..."``/``"steam:achievement:..."``/``"steam:purchase:..."``/
``"steam:friend:..."``); ``_make_ingestion_envelope()`` in
``roster/switchboard/tools/ingestion/ingest.py`` now surfaces that
external_event_id as ``raw_key`` for the `gaming` channel specifically, so a
``rule_type='substring'`` rule matching the ``"steam:status:"`` prefix
targets *only* status_change events.

Action is `metadata_only` (not `skip`) to mirror the envelope's own
`ingestion_tier='metadata'` — the presence delta is still durably logged
(lifecycle_state='metadata_only') without spawning an LLM classification
session or routing to a butler.

The rule is disabled via ``UPDATE switchboard.ingestion_rules SET
enabled=false WHERE id='00000000-0000-0000-0001-000000000110'`` if LLM
routing is ever wanted back for Steam presence changes.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "sw_025"
down_revision = "sw_024"
branch_labels = None
depends_on = None


_RULE_ID = "00000000-0000-0000-0001-000000000110"


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
            'substring',
            '{{"pattern": "steam:status:"}}',
            'metadata_only',
            10,
            TRUE,
            'Metadata-only Steam presence status_change events',
            'Steam online/offline presence changes (external_event_id prefix steam:status:) bypass LLM classification and butler routing/notify. Rows still land in public.ingestion_events for direct DB querying. Other gaming-channel event types (play_session, achievement_unlock, game_purchase, friend_change) are unaffected and remain fully routable.',
            'seed'
          )
        ON CONFLICT (id) DO NOTHING
        """
    )


def downgrade() -> None:
    op.execute(f"DELETE FROM ingestion_rules WHERE id = '{_RULE_ID}'")
