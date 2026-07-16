"""Protocol-boundary coverage for WhatsApp bridge history replay."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

from butlers.connectors.whatsapp_user_client import (
    WhatsAppUserClientConnector,
    WhatsAppUserClientConnectorConfig,
)


async def test_request_backfill_uses_versioned_request_and_requires_accepted_ack(
    monkeypatch,
) -> None:
    """The connector only treats the bridge's explicit v1 acknowledgement as success."""
    connector = WhatsAppUserClientConnector(
        WhatsAppUserClientConnectorConfig(
            switchboard_mcp_url="http://localhost:41100/sse",
            endpoint_identity="whatsapp:+12025551234",
            backfill_window_h=24,
        ),
        cursor_pool=MagicMock(),
    )
    reader = MagicMock()
    reader.read = AsyncMock(
        return_value=(
            b"HTTP/1.0 200 OK\r\nContent-Type: application/json\r\n\r\n"
            b'{"schema_version":"whatsapp.backfill.v1","status":"accepted",'
            b'"window_hours":24,"replay_event_count":2}'
        )
    )
    writer = MagicMock()
    writer.drain = AsyncMock()
    writer.wait_closed = AsyncMock()
    open_connection = AsyncMock(return_value=(reader, writer))
    monkeypatch.setattr(
        "butlers.connectors.whatsapp_user_client.asyncio.open_unix_connection",
        open_connection,
    )

    await connector._request_backfill()

    open_connection.assert_awaited_once_with(connector._config.bridge_socket)
    writer.drain.assert_awaited_once()
    writer.wait_closed.assert_awaited_once()
    request = writer.write.call_args.args[0]
    headers, body = request.split(b"\r\n\r\n", maxsplit=1)
    assert b"POST /backfill HTTP/1.0" in headers
    assert b"Content-Type: application/json" in headers
    assert json.loads(body) == {
        "schema_version": "whatsapp.backfill.v1",
        "window_hours": 24,
    }
    assert int(headers.split(b"Content-Length: ")[1].split(b"\r\n", 1)[0]) == len(body)


async def test_request_backfill_rejects_nonaccepted_ack_without_raising(
    monkeypatch,
    caplog,
) -> None:
    """A malformed or rejected bridge acknowledgement is a non-fatal connector warning."""
    connector = WhatsAppUserClientConnector(
        WhatsAppUserClientConnectorConfig(
            switchboard_mcp_url="http://localhost:41100/sse",
            endpoint_identity="whatsapp:+12025551234",
            backfill_window_h=24,
        ),
        cursor_pool=MagicMock(),
    )
    reader = MagicMock()
    reader.read = AsyncMock(
        return_value=(
            b"HTTP/1.0 200 OK\r\nContent-Type: application/json\r\n\r\n"
            b'{"schema_version":"whatsapp.backfill.v1","status":"rejected"}'
        )
    )
    writer = MagicMock()
    writer.drain = AsyncMock()
    writer.wait_closed = AsyncMock()
    monkeypatch.setattr(
        "butlers.connectors.whatsapp_user_client.asyncio.open_unix_connection",
        AsyncMock(return_value=(reader, writer)),
    )

    await connector._request_backfill()

    assert "Failed to request backfill from bridge" in caplog.text
