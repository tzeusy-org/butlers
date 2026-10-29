"""Error rows from the Telegram user client scrub 777000 login codes. [bu-5orv9x]"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from butlers.connectors.telegram_user_client import (
    TelegramUserClientConnector,
    TelegramUserClientConnectorConfig,
)

_ENDPOINT = "telegram_user_client:telegram:user123"
_PAYLOAD_INDEX = 9
_SENDER_INDEX = 5


@pytest.fixture
def connector() -> TelegramUserClientConnector:
    config = TelegramUserClientConnectorConfig(
        switchboard_mcp_url="http://localhost:41100/sse",
        provider="telegram",
        channel="telegram_user_client",
        endpoint_identity=_ENDPOINT,
    )
    conn = TelegramUserClientConnector(config, cursor_pool=MagicMock())
    conn._flush_and_drain = AsyncMock()
    conn._build_ingestion_envelope = MagicMock(side_effect=RuntimeError("boom"))
    return conn


def _message(sender_id: int, text: str) -> MagicMock:
    msg = MagicMock()
    msg.id = 7
    msg.chat_id = 100
    msg.sender_id = sender_id
    msg.message = text
    msg.media = None
    msg.to_dict = lambda: {"id": 7, "message": text}
    return msg


async def test_error_row_for_service_sender_withholds_bare_code(
    connector: TelegramUserClientConnector,
) -> None:
    await connector._process_message(_message(777000, "482913"))
    (row,) = connector._filtered_event_buffer._rows
    assert row[_SENDER_INDEX] == "777000"
    assert "482913" not in str(row[_PAYLOAD_INDEX])


async def test_error_row_for_other_sender_is_unchanged(
    connector: TelegramUserClientConnector,
) -> None:
    await connector._process_message(_message(999, "482913"))
    (row,) = connector._filtered_event_buffer._rows
    assert row[_SENDER_INDEX] == "999"
    assert "482913" in str(row[_PAYLOAD_INDEX])


async def test_error_row_keeps_unknown_when_sender_lookup_fails(
    connector: TelegramUserClientConnector,
) -> None:
    connector._extract_sender_identity = MagicMock(side_effect=RuntimeError("lookup"))
    await connector._process_message(_message(777000, "482913"))
    (row,) = connector._filtered_event_buffer._rows
    assert row[_SENDER_INDEX] == "unknown"
