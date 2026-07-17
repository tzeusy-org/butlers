"""Protocol-boundary coverage for WhatsApp bridge history replay."""

from __future__ import annotations

import asyncio
import json
import logging
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
    reader = asyncio.StreamReader()
    reader.feed_data(
        b"HTTP/1.0 200 OK\r\nContent-Type: application/json\r\n\r\n"
        b'{"schema_version":"whatsapp.backfill.v1","status":"accepted",'
        b'"window_hours":24,"replay_event_count":2}'
    )
    reader.feed_eof()
    writer = MagicMock()
    writer.drain = AsyncMock()
    writer.wait_closed = AsyncMock()
    open_connection = AsyncMock(return_value=(reader, writer))
    monkeypatch.setattr(
        "butlers.connectors.whatsapp_user_client.asyncio.open_unix_connection",
        open_connection,
    )

    await connector._request_backfill()

    open_connection.assert_awaited_once()
    assert open_connection.await_args.args == (connector._config.bridge_socket,)
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


async def test_request_backfill_accepts_fragmented_http_acknowledgement(
    monkeypatch,
    caplog,
) -> None:
    """A valid HTTP/1.0 acknowledgement may arrive over more than one stream read."""
    connector = WhatsAppUserClientConnector(
        WhatsAppUserClientConnectorConfig(
            switchboard_mcp_url="http://localhost:41100/sse",
            endpoint_identity="whatsapp:+12025551234",
            backfill_window_h=24,
        ),
        cursor_pool=MagicMock(),
    )
    reader = asyncio.StreamReader()
    reader.feed_data(
        b"HTTP/1.0 200 OK\r\nContent-Type: application/json\r\n\r\n"
        b'{"schema_version":"whatsapp.backfill.v1","status":"accepted",'
    )

    async def feed_remaining_response() -> None:
        await asyncio.sleep(0)
        reader.feed_data(b'"window_hours":24,"replay_event_count":2}')
        reader.feed_eof()

    remaining_response = asyncio.create_task(feed_remaining_response())
    writer = MagicMock()
    writer.drain = AsyncMock()
    writer.wait_closed = AsyncMock()
    monkeypatch.setattr(
        "butlers.connectors.whatsapp_user_client.asyncio.open_unix_connection",
        AsyncMock(return_value=(reader, writer)),
    )
    caplog.set_level(logging.INFO, logger="butlers.connectors.whatsapp_user_client")

    await connector._request_backfill()
    await remaining_response

    assert "Backfill request accepted by bridge" in caplog.text
    assert "Failed to request backfill from bridge" not in caplog.text


async def test_request_backfill_rejects_oversized_acknowledgement_before_eof(
    monkeypatch,
    caplog,
) -> None:
    """The bridge acknowledgement has a fixed byte ceiling even before EOF."""
    connector = WhatsAppUserClientConnector(
        WhatsAppUserClientConnectorConfig(
            switchboard_mcp_url="http://localhost:41100/sse",
            endpoint_identity="whatsapp:+12025551234",
            backfill_window_h=24,
        ),
        cursor_pool=MagicMock(),
    )
    reader = asyncio.StreamReader()
    reader.feed_data(b"HTTP/1.0 200 OK\r\nContent-Type: application/json\r\n\r\n" + b"x" * 65_536)
    writer = MagicMock()
    writer.drain = AsyncMock()
    writer.wait_closed = AsyncMock()
    monkeypatch.setattr(
        "butlers.connectors.whatsapp_user_client.asyncio.open_unix_connection",
        AsyncMock(return_value=(reader, writer)),
    )
    original_wait_for = asyncio.wait_for

    async def short_wait_for(awaitable, timeout):
        return await original_wait_for(awaitable, timeout=0.05)

    monkeypatch.setattr(
        "butlers.connectors.whatsapp_user_client.asyncio.wait_for",
        short_wait_for,
    )
    caplog.set_level(logging.WARNING, logger="butlers.connectors.whatsapp_user_client")

    await connector._request_backfill()

    assert "Bridge /backfill acknowledgement exceeded" in caplog.text
    writer.wait_closed.assert_awaited_once()


async def test_request_backfill_times_out_without_eof_without_stopping_connector(
    monkeypatch,
    caplog,
) -> None:
    """A bridge that never closes its HTTP/1.0 acknowledgement remains non-fatal."""
    connector = WhatsAppUserClientConnector(
        WhatsAppUserClientConnectorConfig(
            switchboard_mcp_url="http://localhost:41100/sse",
            endpoint_identity="whatsapp:+12025551234",
            backfill_window_h=24,
        ),
        cursor_pool=MagicMock(),
    )
    reader = asyncio.StreamReader()
    reader.feed_data(b"HTTP/1.0 200 OK\r\nContent-Type: application/json\r\n\r\n")
    writer = MagicMock()
    writer.drain = AsyncMock()
    writer.wait_closed = AsyncMock()
    monkeypatch.setattr(
        "butlers.connectors.whatsapp_user_client.asyncio.open_unix_connection",
        AsyncMock(return_value=(reader, writer)),
    )
    original_wait_for = asyncio.wait_for

    async def short_wait_for(awaitable, timeout):
        return await original_wait_for(awaitable, timeout=0.05)

    monkeypatch.setattr(
        "butlers.connectors.whatsapp_user_client.asyncio.wait_for",
        short_wait_for,
    )
    caplog.set_level(logging.WARNING, logger="butlers.connectors.whatsapp_user_client")

    await connector._request_backfill()

    assert "Failed to request backfill from bridge" in caplog.text
    assert "Backfill request accepted by bridge" not in caplog.text
    writer.wait_closed.assert_awaited_once()


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
    reader = asyncio.StreamReader()
    reader.feed_data(
        b"HTTP/1.0 200 OK\r\nContent-Type: application/json\r\n\r\n"
        b'{"schema_version":"whatsapp.backfill.v1","status":"rejected"}'
    )
    reader.feed_eof()
    writer = MagicMock()
    writer.drain = AsyncMock()
    writer.wait_closed = AsyncMock()
    monkeypatch.setattr(
        "butlers.connectors.whatsapp_user_client.asyncio.open_unix_connection",
        AsyncMock(return_value=(reader, writer)),
    )

    await connector._request_backfill()

    assert "Failed to request backfill from bridge" in caplog.text
