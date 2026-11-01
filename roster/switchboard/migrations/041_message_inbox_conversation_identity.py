"""Backfill the stable conversation key on historical ``message_inbox`` rows.

Revision ID: sw_041
Revises: sw_040
Create Date: 2026-10-08 00:00:00.000000

bu-7exe4.2.  Realtime history now selects rows by
``request_context ->> 'external_conversation_id'``, the channel-namespaced key
the connectors emit, instead of matching the overloaded per-message
``source_thread_identity``.  Rows written before the split carry only the old
field, so this one-time backfill derives the key the connector would emit today:

* Telegram (``telegram``, ``telegram_bot``, ``telegram_user_client``):
  ``<chat_id>`` or ``<chat_id>:<message_id>`` becomes ``telegram:<chat_id>``.
  Pre-split rows never recorded a forum topic, so they join the chat root.
* WhatsApp (``whatsapp``, ``whatsapp_user_client``): ``whatsapp:<chat_jid>``.
* Every other channel, the dashboard included, already used a
  conversation-stable thread id; it is copied verbatim, matching the fallback
  ingest applies to producers that send only ``external_thread_id``.

Rows that already carry a non-null key are left alone, so a rerun updates nothing.

Rows still ``accepted`` or ``processing`` are replayed by buffer recovery from
``raw_payload.event``.  For the three channels that adopted the split, their
stored event also gains ``external_conversation_id`` and ``reply_target_ref`` so
a recovered pre-split message anchors to its chat rather than minting a
per-message anchor.

Downgrade drops the lookup index only.  The backfilled key is additive: older
readers never select it, and rows written after the upgrade carry it as well,
so stripping it would erase legitimate data.
"""

from __future__ import annotations

from alembic import op

revision = "sw_041"
down_revision = "sw_040"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        r"""
        UPDATE message_inbox
        SET request_context = request_context || jsonb_build_object(
            'external_conversation_id',
            CASE
                WHEN request_context ->> 'source_channel'
                        IN ('telegram', 'telegram_bot', 'telegram_user_client')
                     AND request_context ->> 'source_thread_identity' ~ '^-?[0-9]+(:[0-9]+)?$'
                    THEN 'telegram:'
                        || split_part(request_context ->> 'source_thread_identity', ':', 1)
                WHEN request_context ->> 'source_channel' IN ('whatsapp', 'whatsapp_user_client')
                     AND request_context ->> 'source_thread_identity' NOT LIKE 'whatsapp:%'
                    THEN 'whatsapp:' || (request_context ->> 'source_thread_identity')
                ELSE request_context ->> 'source_thread_identity'
            END
        )
        WHERE request_context ->> 'source_thread_identity' IS NOT NULL
          AND request_context ->> 'external_conversation_id' IS NULL
        """
    )
    op.execute(
        r"""
        UPDATE message_inbox
        SET raw_payload = jsonb_set(
            raw_payload,
            '{event}',
            (raw_payload -> 'event') || jsonb_build_object(
                'external_conversation_id',
                CASE
                    WHEN raw_payload #>> '{source,channel}' = 'whatsapp_user_client'
                        THEN 'whatsapp:' || (raw_payload #>> '{event,external_thread_id}')
                    ELSE 'telegram:'
                        || split_part(raw_payload #>> '{event,external_thread_id}', ':', 1)
                END,
                'reply_target_ref',
                raw_payload #>> '{event,external_thread_id}'
            )
        )
        WHERE lifecycle_state IN ('accepted', 'processing')
          AND raw_payload #>> '{source,channel}'
              IN ('telegram_bot', 'telegram_user_client', 'whatsapp_user_client')
          AND raw_payload #>> '{event,external_thread_id}' IS NOT NULL
          AND raw_payload #>> '{event,external_conversation_id}' IS NULL
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_message_inbox_conversation_received_at
        ON message_inbox ((request_context ->> 'external_conversation_id'), received_at DESC)
        WHERE request_context ->> 'external_conversation_id' IS NOT NULL
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_message_inbox_conversation_received_at")
