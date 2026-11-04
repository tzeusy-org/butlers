"""Protocol-boundary coverage for WhatsApp bridge history replay."""

from __future__ import annotations

import json
import logging

import pytest

from tests.connectors.test_whatsapp_bridge_client import ACK, ack_wire, bridge_server, connector_for


async def test_request_backfill_uses_versioned_request_and_requires_accepted_ack(tmp_path, caplog):
    caplog.set_level(logging.INFO, logger="butlers.connectors.whatsapp_user_client")
    async with bridge_server(tmp_path, ack_wire()) as server:
        connector = connector_for(server.path)
        await connector._request_backfill()
    assert len(server.requests) == 1
    assert server.path == connector._config.bridge_socket
    headers, body = server.requests[0]
    assert b"POST /backfill HTTP/1.1" in headers
    assert b"content-type: application/json" in headers.lower()
    assert json.loads(body) == {"schema_version": "whatsapp.backfill.v1", "window_hours": 24}
    assert int(headers.lower().split(b"content-length: ")[1].split(b"\r\n", 1)[0]) == len(body)
    assert "Backfill request accepted by bridge" in caplog.text
    assert server.closed == 1


async def test_request_backfill_accepts_fragmented_http_acknowledgement(tmp_path, caplog):
    caplog.set_level(logging.INFO, logger="butlers.connectors.whatsapp_user_client")
    async with bridge_server(tmp_path, ack_wire(), fragments=17, delay=0.0001) as server:
        await connector_for(server.path)._request_backfill()
    assert "Backfill request accepted by bridge" in caplog.text
    assert "Failed to request backfill from bridge" not in caplog.text
    assert server.closed == 1


async def test_request_backfill_rejects_oversized_acknowledgement_before_eof(tmp_path, caplog):
    caplog.set_level(logging.WARNING, logger="butlers.connectors.whatsapp_user_client")
    response = b"HTTP/1.1 200 OK\r\nX-Pad: " + b"x" * 65536
    async with bridge_server(tmp_path, response, keep_open=True) as server:
        await connector_for(server.path)._request_backfill()
    assert "WhatsApp backfill request failed" in caplog.text
    assert "Bridge /backfill acknowledgement exceeded" not in caplog.text
    record = next(
        item for item in caplog.records if item.message == "WhatsApp backfill request failed"
    )
    assert record.failure_class == "RuntimeError"
    assert server.closed == 1


async def test_request_backfill_times_out_without_eof_without_stopping_connector(
    tmp_path,
    caplog,
    monkeypatch,
):
    monkeypatch.setattr("butlers.connectors.whatsapp_user_client._BACKFILL_ACK_TIMEOUT_S", 0.05)
    caplog.set_level(logging.WARNING, logger="butlers.connectors.whatsapp_user_client")
    # No framing/body completion: HTTP/1.1 still requires EOF for this response.
    async with bridge_server(
        tmp_path, b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n\r\n", keep_open=True
    ) as server:
        await connector_for(server.path)._request_backfill()
    assert "WhatsApp backfill request failed" in caplog.text
    assert "Backfill request accepted by bridge" not in caplog.text
    record = next(
        item for item in caplog.records if item.message == "WhatsApp backfill request failed"
    )
    assert record.failure_class == "TimeoutError"
    assert server.closed == 1


@pytest.mark.parametrize(
    "override",
    [
        {"status": "rejected"},
        {"schema_version": "other"},
        {"window_hours": 25},
        {"window_hours": True},
        {"replay_event_count": True},
        {"replay_event_count": -1},
        {"replay_event_count": "2"},
        {"replay_event_count": None},
    ],
)
async def test_request_backfill_rejects_nonaccepted_ack_without_raising(tmp_path, caplog, override):
    caplog.set_level(logging.WARNING, logger="butlers.connectors.whatsapp_user_client")
    async with bridge_server(tmp_path, ack_wire(json.dumps(ACK | override).encode())) as server:
        await connector_for(server.path)._request_backfill()
    assert "WhatsApp backfill request failed" in caplog.text
    record = next(
        item for item in caplog.records if item.message == "WhatsApp backfill request failed"
    )
    assert record.failure_class == "RuntimeError"
    assert server.closed == 1
