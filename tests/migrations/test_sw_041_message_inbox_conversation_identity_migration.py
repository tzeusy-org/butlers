"""sw_041: pre-split message_inbox rows gain the stable conversation key once.

bu-7exe4.2.  Real PostgreSQL; seeded rows use synthetic identities only.
"""

from __future__ import annotations

import asyncio
import json
import shutil

import pytest
from sqlalchemy import create_engine, text

from alembic import command
from butlers.migrations import _build_alembic_config, run_migrations
from butlers.testing.migration import create_migration_db, migration_db_name

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(shutil.which("docker") is None, reason="Docker not available"),
]

_RECEIVED = "2026-10-01T12:00:00+00:00"

# (label, lifecycle_state, request_context, raw event)
_ROWS = [
    (
        "bot",
        "completed",
        {"source_channel": "telegram_bot", "source_thread_identity": "-100777:41"},
        {"external_thread_id": "-100777:41"},
    ),
    (
        "bot-pending",
        "accepted",
        {"source_channel": "telegram_bot", "source_thread_identity": "-100777:42"},
        {"external_thread_id": "-100777:42"},
    ),
    (
        "outbound",
        "completed",
        {"source_channel": "telegram_bot", "source_thread_identity": "206570151"},
        {},
    ),
    (
        "user-client",
        "completed",
        {"source_channel": "telegram_user_client", "source_thread_identity": "998877"},
        {"external_thread_id": "998877"},
    ),
    (
        "whatsapp-pending",
        "processing",
        {
            "source_channel": "whatsapp_user_client",
            "source_thread_identity": "6591234567@s.whatsapp.net",
        },
        {"external_thread_id": "6591234567@s.whatsapp.net"},
    ),
    (
        "email",
        "completed",
        {"source_channel": "email", "source_thread_identity": "gmail-thread-1"},
        {"external_thread_id": "gmail-thread-1"},
    ),
    (
        "already-split",
        "accepted",
        {
            "source_channel": "telegram_bot",
            "source_thread_identity": "-100777:43",
            "external_conversation_id": "telegram:-100777:topic:9",
        },
        {"external_conversation_id": "telegram:-100777:topic:9", "reply_target_ref": "-100777:43"},
    ),
    (
        "spotify",
        "completed",
        {"source_channel": "spotify_user_client", "source_thread_identity": "spotify:ctx:1"},
        {"external_thread_id": "spotify:ctx:1"},
    ),
    ("no-thread", "completed", {"source_channel": "api"}, {}),
]


def _snapshot(engine) -> dict[str, tuple[dict, dict]]:
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT normalized_text, request_context, raw_payload -> 'event' "
                "FROM switchboard.message_inbox"
            )
        )
        return {row[0]: (row[1], row[2]) for row in rows}


def test_sw_041_backfills_conversation_key_once(postgres_container) -> None:
    db_url = create_migration_db(postgres_container, migration_db_name())
    asyncio.run(run_migrations(db_url, chain="core"))
    config = _build_alembic_config(db_url, ["switchboard"], target_schema="switchboard")
    command.upgrade(config, "switchboard@sw_040")

    engine = create_engine(db_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            conn.execute(
                text(
                    "SELECT switchboard.switchboard_message_inbox_ensure_partition("
                    "CAST(:ts AS timestamptz))"
                ),
                {"ts": _RECEIVED},
            )
            for label, lifecycle_state, request_context, event in _ROWS:
                raw = {
                    "source": {"channel": request_context["source_channel"]},
                    "event": {"observed_at": _RECEIVED, **event},
                }
                conn.execute(
                    text(
                        "INSERT INTO switchboard.message_inbox "
                        "(received_at, request_context, raw_payload, normalized_text, "
                        "lifecycle_state) VALUES (CAST(:ts AS timestamptz), "
                        "CAST(:ctx AS jsonb), CAST(:raw AS jsonb), :label, :state)"
                    ),
                    {
                        "ts": _RECEIVED,
                        "ctx": json.dumps(request_context),
                        "raw": json.dumps(raw),
                        "label": label,
                        "state": lifecycle_state,
                    },
                )

        command.upgrade(config, "switchboard@sw_041")
        after = _snapshot(engine)
        keys = {label: ctx.get("external_conversation_id") for label, (ctx, _) in after.items()}
        assert keys == {
            "bot": "telegram:-100777",
            "bot-pending": "telegram:-100777",
            "outbound": "telegram:206570151",
            "user-client": "telegram:998877",
            "whatsapp-pending": "whatsapp:6591234567@s.whatsapp.net",
            "email": "gmail-thread-1",
            "spotify": "spotify:ctx:1",
            "already-split": "telegram:-100777:topic:9",
            "no-thread": None,
        }
        # Only still-recoverable rows of the split channels gain stored event keys.
        assert after["bot-pending"][1]["external_conversation_id"] == "telegram:-100777"
        assert after["bot-pending"][1]["reply_target_ref"] == "-100777:42"
        assert after["whatsapp-pending"][1]["external_conversation_id"] == (
            "whatsapp:6591234567@s.whatsapp.net"
        )
        assert "external_conversation_id" not in after["bot"][1]
        assert "external_conversation_id" not in after["email"][1]
        assert after["already-split"][1]["external_conversation_id"] == "telegram:-100777:topic:9"

        with engine.connect() as conn:
            assert conn.execute(
                text("SELECT to_regclass('switchboard.ix_message_inbox_conversation_received_at')")
            ).scalar()

        # Downgrade drops only the index; re-upgrade changes no row.
        command.downgrade(config, "switchboard@sw_040")
        with engine.connect() as conn:
            assert (
                conn.execute(
                    text(
                        "SELECT to_regclass("
                        "'switchboard.ix_message_inbox_conversation_received_at')"
                    )
                ).scalar()
                is None
            )
        command.upgrade(config, "switchboard@sw_041")
        assert _snapshot(engine) == after
    finally:
        engine.dispose()
