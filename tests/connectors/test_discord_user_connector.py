"""Condensed Discord user connector tests — ingest.v1 contract only.

Verifies:
- ingest.v1 envelope production for MESSAGE_CREATE, MESSAGE_DELETE
- Returns None for missing ID or empty content
- Idempotency key format

[bu-35fm7]
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from butlers.connectors.discord_user import (
    DiscordUserConnector,
    DiscordUserConnectorConfig,
)

_ENDPOINT = "discord:user:123456789"


@pytest.fixture
def connector() -> DiscordUserConnector:
    config = DiscordUserConnectorConfig(
        switchboard_mcp_url="http://localhost:41100/sse",
        provider="discord",
        channel="discord",
        endpoint_identity=_ENDPOINT,
        discord_bot_token="Bot test-token",
        max_inflight=2,
    )
    return DiscordUserConnector(config, cursor_pool=MagicMock())


def test_message_create_schema_version(connector: DiscordUserConnector) -> None:
    """MESSAGE_CREATE envelope must carry schema_version='ingest.v1'."""
    event_data: dict[str, Any] = {
        "id": "111222333",
        "channel_id": "ch123",
        "content": "Hello!",
        "author": {"id": "user456"},
    }
    env = connector._normalize_to_ingest_v1("MESSAGE_CREATE", event_data)
    assert env is not None
    assert env["schema_version"] == "ingest.v1"
    assert env["source"]["channel"] == "discord"
    assert env["source"]["provider"] == "discord"
    assert env["source"]["endpoint_identity"] == _ENDPOINT


def test_message_create_event_fields(connector: DiscordUserConnector) -> None:
    """External event ID and thread ID must map from Discord IDs."""
    event_data: dict[str, Any] = {
        "id": "msg-abc",
        "channel_id": "ch-xyz",
        "content": "Some text",
        "author": {"id": "user-999"},
    }
    env = connector._normalize_to_ingest_v1("MESSAGE_CREATE", event_data)
    assert env is not None
    assert env["event"]["external_event_id"] == "msg-abc"
    assert env["event"]["external_thread_id"] == "ch-xyz"
    assert env["sender"]["identity"] == "user-999"


def test_message_delete_creates_tombstone(connector: DiscordUserConnector) -> None:
    """MESSAGE_DELETE must produce a tombstone envelope with '[Message deleted]' text."""
    event_data: dict[str, Any] = {"id": "del-msg-1", "channel_id": "ch1"}
    env = connector._normalize_to_ingest_v1("MESSAGE_DELETE", event_data)
    assert env is not None
    assert "[Message deleted]" in env["payload"]["normalized_text"]


def test_missing_id_returns_none(connector: DiscordUserConnector) -> None:
    """Events without a message ID must return None (not ingested)."""
    result = connector._normalize_to_ingest_v1("MESSAGE_CREATE", {"channel_id": "ch1"})
    assert result is None


def test_empty_content_returns_none(connector: DiscordUserConnector) -> None:
    """MESSAGE_CREATE with no text or media content must return None."""
    event_data: dict[str, Any] = {
        "id": "no-content",
        "channel_id": "ch1",
        "content": "",
        "author": {"id": "u1"},
    }
    result = connector._normalize_to_ingest_v1("MESSAGE_CREATE", event_data)
    assert result is None


def test_idempotency_key_format(connector: DiscordUserConnector) -> None:
    """Idempotency key must include provider, endpoint_identity, and message_id."""
    event_data: dict[str, Any] = {
        "id": "key-msg-999",
        "channel_id": "ch1",
        "content": "Test",
        "author": {"id": "u1"},
    }
    env = connector._normalize_to_ingest_v1("MESSAGE_CREATE", event_data)
    assert env is not None
    key = env["control"]["idempotency_key"]
    assert "discord" in key
    assert "key-msg-999" in key


# ---------------------------------------------------------------------------
# Filtered-content privacy tier (bu-glbjx)
#
# Content the connector deliberately does NOT submit (status='filtered':
# allowlist block, connector/global policy filter) persists a bounded preview
# only — the full raw event payload MUST NOT be retained
# (full_payload.payload.raw == {}). Errored content (status='error') is exempt
# and keeps its payload for diagnosis and replay.
# ---------------------------------------------------------------------------


async def test_allowlist_block_persists_no_raw_payload(connector: DiscordUserConnector) -> None:
    """An allowlist-filtered event records raw={} while keeping the preview."""
    connector._is_allowed = MagicMock(return_value=False)  # type: ignore[method-assign]
    connector._flush_and_drain = AsyncMock()  # type: ignore[method-assign]

    event_data: dict[str, Any] = {
        "id": "evt-blocked",
        "channel_id": "ch1",
        "content": "secret message",
        "author": {"id": "u1"},
    }
    await connector._process_dispatch_event("MESSAGE_CREATE", event_data)

    rows = connector._filtered_event_buffer._rows
    assert len(rows) == 1
    row = rows[0]
    assert row[8] == "filtered"
    assert row[9]["payload"]["raw"] == {}
    # Preview is retained so the filtered row stays operationally useful.
    assert row[6]


async def test_submission_error_retains_raw_payload(connector: DiscordUserConnector) -> None:
    """Errored content (status='error') is exempt: the raw payload is retained."""
    from butlers.ingestion_policy import PolicyDecision

    connector._is_allowed = MagicMock(return_value=True)  # type: ignore[method-assign]
    connector._ingestion_policy.evaluate = MagicMock(  # type: ignore[method-assign]
        return_value=PolicyDecision(action="pass_through")
    )
    connector._global_ingestion_policy.evaluate = MagicMock(  # type: ignore[method-assign]
        return_value=PolicyDecision(action="pass_through")
    )
    connector._submit_to_ingest = AsyncMock(side_effect=RuntimeError("boom"))  # type: ignore[method-assign]
    connector._flush_and_drain = AsyncMock()  # type: ignore[method-assign]

    event_data: dict[str, Any] = {
        "id": "evt-err",
        "channel_id": "ch1",
        "content": "hi",
        "author": {"id": "u2"},
    }
    await connector._process_dispatch_event("MESSAGE_CREATE", event_data)

    rows = connector._filtered_event_buffer._rows
    assert len(rows) == 1
    row = rows[0]
    assert row[8] == "error"
    raw = row[9]["payload"]["raw"]
    assert raw["event_type"] == "MESSAGE_CREATE"
    assert raw["id"] == "evt-err"
