"""Real Unix-socket boundaries for the shared bridge transport."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from butlers.connectors.whatsapp_user_client import (
    WhatsAppUserClientConnector,
    WhatsAppUserClientConnectorConfig,
)

pytestmark = pytest.mark.unit
ACK = {
    "schema_version": "whatsapp.backfill.v1",
    "status": "accepted",
    "window_hours": 24,
    "replay_event_count": 2,
}


def ack_wire(body: bytes | None = None, *, status: int = 200, chunked: bool = False) -> bytes:
    body = json.dumps(ACK).encode() if body is None else body
    if chunked:
        return (
            f"HTTP/1.1 {status} OK\r\nTransfer-Encoding: chunked\r\n\r\n".encode()
            + f"{len(body):x}\r\n".encode()
            + body
            + b"\r\n0\r\n\r\n"
        )
    return f"HTTP/1.1 {status} OK\r\nContent-Length: {len(body)}\r\n\r\n".encode() + body


@asynccontextmanager
async def bridge_server(
    tmp_path,
    response: bytes,
    *,
    keep_open: bool = False,
    fragments: int = 0,
    delay: float = 0,
    response_eof: bool = False,
) -> AsyncIterator[SimpleNamespace]:
    """One bounded server fixture; every accepted task/writer is settled in finally."""
    path = str(tmp_path / "bridge.sock")
    tasks: set[asyncio.Task] = set()
    writers: set[asyncio.StreamWriter] = set()
    state = SimpleNamespace(
        path=path,
        requests=[],
        closed=0,
        ready=asyncio.Event(),
        peer_closed=asyncio.Event(),
        peer_close_kind=None,
        tearing_down=False,
    )

    async def serve(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        task = asyncio.current_task()
        assert task is not None
        tasks.add(task)
        writers.add(writer)
        try:
            headers = await reader.readuntil(b"\r\n\r\n")
            length = next(
                (
                    int(line.split(b":", 1)[1])
                    for line in headers.split(b"\r\n")
                    if line.lower().startswith(b"content-length:")
                ),
                0,
            )
            body = await reader.readexactly(length)
            state.requests.append((headers, body))
            state.ready.set()
            size = fragments or max(1, len(response))
            for pos in range(0, len(response), size):
                writer.write(response[pos : pos + size])
                await writer.drain()
                if delay:
                    await asyncio.sleep(delay)
            if response_eof:
                writer.write_eof()
        except (ConnectionError, asyncio.IncompleteReadError):
            pass
        finally:
            try:
                # Only peer EOF/reset while the fixture is still live proves client
                # ownership. Neither our writer.close nor teardown may set this.
                if keep_open and not state.tearing_down:
                    try:
                        remaining = await reader.read()
                    except ConnectionError:
                        state.peer_close_kind = "reset"
                    else:
                        assert remaining == b""
                        state.peer_close_kind = "eof"
                    if not state.tearing_down:
                        state.peer_closed.set()
            finally:
                writer.close()
                with contextlib.suppress(ConnectionError):
                    await writer.wait_closed()
                state.closed += 1
                writers.discard(writer)
                tasks.discard(task)

    server = await asyncio.start_unix_server(serve, path=path)
    try:
        yield state
    finally:
        state.tearing_down = True
        server.close()
        for writer in list(writers):
            writer.close()
        if tasks:
            done, pending = await asyncio.wait(list(tasks), timeout=1)
            for task in pending:
                task.cancel()
            await asyncio.gather(*done, *pending, return_exceptions=True)
        await server.wait_closed()
        assert not tasks and not writers


async def assert_peer_closed(server: SimpleNamespace) -> None:
    """Observe the real peer before any fixture-owned close/cancel can help."""
    assert not server.tearing_down
    async with asyncio.timeout(2):
        await server.peer_closed.wait()
    assert server.peer_close_kind in {"eof", "reset"}
    assert not server.tearing_down


def connector_for(path: str) -> WhatsAppUserClientConnector:
    return WhatsAppUserClientConnector(
        WhatsAppUserClientConnectorConfig(
            switchboard_mcp_url="http://localhost:41100/sse",
            endpoint_identity="whatsapp:synthetic",
            backfill_window_h=24,
            bridge_socket=path,
        ),
        cursor_pool=MagicMock(),
    )


async def test_connector_backfill_uses_http11(tmp_path, caplog) -> None:
    caplog.set_level(logging.INFO, logger="butlers.connectors.whatsapp_user_client")
    async with bridge_server(tmp_path, ack_wire()) as server:
        await connector_for(server.path)._request_backfill()
    assert "Backfill request accepted by bridge" in caplog.text
    assert len(server.requests) == 1
    assert server.requests[0][0].startswith(b"POST /backfill HTTP/1.1\r\n")
    assert json.loads(server.requests[0][1]) == {
        "schema_version": "whatsapp.backfill.v1",
        "window_hours": 24,
    }


async def test_framed_ack_does_not_require_eof(tmp_path, caplog, monkeypatch) -> None:
    monkeypatch.setattr("butlers.connectors.whatsapp_user_client._BACKFILL_ACK_TIMEOUT_S", 1.0)
    caplog.set_level(logging.INFO, logger="butlers.connectors.whatsapp_user_client")
    async with bridge_server(tmp_path, ack_wire(), keep_open=True) as server:
        await connector_for(server.path)._request_backfill()
        await assert_peer_closed(server)
    assert "Backfill request accepted by bridge" in caplog.text
    assert len(server.requests) == 1
    assert server.closed == 1


async def test_chunked_ack_uses_http_parser(tmp_path, caplog) -> None:
    caplog.set_level(logging.INFO, logger="butlers.connectors.whatsapp_user_client")
    async with bridge_server(tmp_path, ack_wire(chunked=True), fragments=17) as server:
        await connector_for(server.path)._request_backfill()
    assert "Backfill request accepted by bridge" in caplog.text
    assert len(server.requests) == 1


def sized_wire(size: int, kind: str) -> bytes:
    for padding in range(size):
        if kind == "header":
            raw = ack_wire().replace(
                b"\r\n\r\n", b"\r\nX-Pad: \t" + b"x" * padding + b" \t\r\n\r\n", 1
            )
        elif kind == "body":
            raw = ack_wire(json.dumps(ACK).encode() + b" " * padding)
        elif kind == "interim":
            raw = b"HTTP/1.1 100 Continue\r\nX-Pad: " + b"x" * padding + b"\r\n\r\n" + ack_wire()
        else:
            raw = ack_wire(chunked=True)[:-2] + b"X-Pad: " + b"x" * padding + b"\r\n\r\n"
        if len(raw) == size:
            return raw
    raise AssertionError("cannot build requested wire size")


@pytest.mark.parametrize("kind", ["header", "body", "interim", "trailer"])
@pytest.mark.parametrize("size", [4096, 4097])
async def test_raw_ack_limit_precedes_http_normalization(tmp_path, kind, size):
    from butlers.whatsapp_bridge_client import BridgeClient

    async with bridge_server(
        tmp_path, sized_wire(size, kind), fragments=31, keep_open=True
    ) as server:
        if size == 4096:
            assert await BridgeClient(server.path).request_backfill(24) == 2
        else:
            with pytest.raises(RuntimeError, match="bridge_ack_raw_limit"):
                await BridgeClient(server.path).request_backfill(24)
        await assert_peer_closed(server)
    assert len(server.requests) == 1
    assert server.closed == 1


@pytest.mark.parametrize(
    "response,error",
    [
        (ack_wire(status=307), RuntimeError),
        (ack_wire(b"not json"), ValueError),
        (ack_wire().replace(b"\r\n\r\n", b"\r\nContent-Encoding: gzip\r\n\r\n", 1), RuntimeError),
        (b"HTTP/1.1 200 OK\r\nContent-Length: 9999999999999\r\n\r\n", ValueError),
    ],
)
async def test_ack_refusal_has_no_write_retry(tmp_path, response, error):
    from butlers.whatsapp_bridge_client import BridgeClient

    async with bridge_server(
        tmp_path,
        response,
        keep_open=True,
        response_eof=response == b"HTTP/1.1 200 OK\r\nContent-Length: 9999999999999\r\n\r\n",
    ) as server:
        with pytest.raises(error):
            await BridgeClient(server.path).request_backfill(24)
        await assert_peer_closed(server)
    assert len(server.requests) == 1
    assert server.closed == 1


async def test_ack_deadline_is_absolute_during_trickle(tmp_path):
    from butlers.whatsapp_bridge_client import BridgeClient

    async with bridge_server(
        tmp_path, ack_wire(), fragments=1, delay=0.005, keep_open=True
    ) as server:
        with pytest.raises(TimeoutError):
            await BridgeClient(server.path).request_backfill(24, deadline_s=0.03)
        await assert_peer_closed(server)
    assert len(server.requests) == 1
    assert server.closed == 1


@pytest.mark.parametrize("prefix", [b"", b"HTTP/1.1 200 OK\r\nContent-Length: 200\r\n\r\n{"])
async def test_ack_cancellation_closes_owned_connection(tmp_path, prefix):
    from butlers.whatsapp_bridge_client import BridgeClient

    async with bridge_server(tmp_path, prefix, keep_open=True) as server:
        task = asyncio.create_task(BridgeClient(server.path).request_backfill(24))
        try:
            async with asyncio.timeout(1):
                await server.ready.wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await assert_peer_closed(server)
    assert server.closed == 1


@pytest.mark.parametrize(
    "status,check_status", [(200, True), (400, False), (307, True), (500, True)]
)
async def test_json_status_policy_and_proxy_isolation(tmp_path, monkeypatch, status, check_status):
    import httpx

    from butlers.whatsapp_bridge_client import BridgeClient

    for key in ["HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"]:
        monkeypatch.setenv(key, "http://127.0.0.1:9")
    monkeypatch.setenv("NO_PROXY", "")
    async with bridge_server(
        tmp_path, ack_wire(b'{"ok":true}', status=status), keep_open=True
    ) as server:
        if check_status and status != 200:
            with pytest.raises(httpx.HTTPStatusError):
                await BridgeClient(server.path).request_json("POST", "/send", {}, check_status=True)
        else:
            assert await BridgeClient(server.path).request_json(
                "POST", "/send", {}, check_status=check_status
            ) == {"ok": True}
        await assert_peer_closed(server)
    assert len(server.requests) == 1
    assert server.closed == 1


async def test_json_protocol_refusal_is_content_blind(tmp_path):
    from butlers.whatsapp_bridge_client import BridgeClient, BridgeProtocolError

    async with bridge_server(tmp_path, ack_wire(b"synthetic-private-body", status=400)) as server:
        with pytest.raises(BridgeProtocolError) as caught:
            await BridgeClient(server.path).request_json("POST", "/send", {})
    assert str(caught.value) == "bridge_json_invalid"
    assert len(server.requests) == 1


async def test_event_lines_fragmentation_and_early_close(tmp_path, caplog):
    from butlers.whatsapp_bridge_client import BridgeClient

    data = b':keepalive\r\n\r\ndata: not-json\n\ndata: {"value":1}\r\n'
    response = b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\n\r\n" + data
    async with bridge_server(
        tmp_path, response, fragments=7, delay=0.0001, keep_open=True
    ) as server:
        events = BridgeClient(server.path).stream_events()
        try:
            assert await anext(events) == {"value": 1}
        finally:
            await events.aclose()
        await assert_peer_closed(server)
    assert server.closed == 1
    assert "not-json" not in caplog.text
    assert "redacted" in caplog.text


@pytest.mark.parametrize("data", [b'data: {"value":1}', b'data: {"value":1}\n'])
async def test_event_final_line_and_eof(tmp_path, data):
    from butlers.whatsapp_bridge_client import BridgeClient

    response = b"HTTP/1.1 200 OK\r\n\r\n" + data
    async with bridge_server(tmp_path, response) as server:
        assert [event async for event in BridgeClient(server.path).stream_events()] == [
            {"value": 1}
        ]
    assert server.closed == 1


async def test_event_partial_chunks_do_not_reset_complete_line_deadline(tmp_path):
    from butlers.whatsapp_bridge_client import BridgeClient

    response = b"HTTP/1.1 200 OK\r\n\r\ndata: {" + b" " * 100 + b'"value":1}\n'
    async with bridge_server(
        tmp_path, response, fragments=1, delay=0.002, keep_open=True
    ) as server:
        events = BridgeClient(server.path).stream_events(line_timeout=0.02)
        try:
            with pytest.raises(TimeoutError):
                await anext(events)
        finally:
            await events.aclose()
        await assert_peer_closed(server)
    assert server.closed == 1


async def test_event_header_deadline_and_bounded_line(tmp_path):
    from butlers.whatsapp_bridge_client import BridgeClient, BridgeProtocolError

    async with bridge_server(tmp_path, b"HTTP/1.1 200", keep_open=True) as server:
        events = BridgeClient(server.path).stream_events(header_timeout=0.02)
        try:
            with pytest.raises(TimeoutError):
                await anext(events)
        finally:
            await events.aclose()
        await assert_peer_closed(server)
    # A second server has a fresh socket, independently positioned after valid headers.
    response = b"HTTP/1.1 200 OK\r\n\r\ndata: " + b"x" * 65537
    async with bridge_server(tmp_path, response, keep_open=True) as server:
        events = BridgeClient(server.path).stream_events()
        try:
            with pytest.raises(BridgeProtocolError, match="bridge_event_line_limit"):
                await anext(events)
        finally:
            await events.aclose()
        await assert_peer_closed(server)
    assert server.closed == 1


async def test_ack_actual_count_name_and_window_type(tmp_path):
    from butlers.whatsapp_bridge_client import BridgeClient

    for body, window, accepted in [
        (ACK | {"window_hours": 1}, 1, True),
        (ACK | {"window_hours": True}, 1, False),
        (
            {k: v for k, v in ACK.items() if k != "replay_event_count"} | {"replay_count": 2},
            24,
            False,
        ),
    ]:
        async with bridge_server(tmp_path, ack_wire(json.dumps(body).encode())) as server:
            if accepted:
                assert await BridgeClient(server.path).request_backfill(window) == 2
            else:
                with pytest.raises(RuntimeError):
                    await BridgeClient(server.path).request_backfill(window)
        assert len(server.requests) == 1


async def test_shared_factory_is_explicitly_env_independent_and_caller_owned():
    from butlers.whatsapp_bridge_client import BridgeClient

    client = BridgeClient("/synthetic/unused", timeout=None)
    async with client._client(client.make_transport(client.socket_path)) as http:
        assert http.trust_env is False
        assert http.follow_redirects is False
        assert http.timeout.read is None
        assert http.timeout.connect is None
    assert http.is_closed is True


@pytest.mark.parametrize(
    "method,status", [("GET", 200), ("GET", 400), ("POST", 200), ("POST", 500)]
)
async def test_dashboard_wrappers_keep_status_policy(tmp_path, method, status):
    from butlers.api.routers.whatsapp import _bridge_get, _bridge_post

    async with bridge_server(
        tmp_path, ack_wire(b'{"ok":true}', status=status), keep_open=True
    ) as server:
        if method == "GET":
            actual = await _bridge_get(server.path, "/status")
        else:
            actual = await _bridge_post(server.path, "/disconnect")
        await assert_peer_closed(server)
    assert actual == ({"ok": True} if status == 200 else None)
    assert len(server.requests) == 1
    assert server.closed == 1


async def test_events_cancellation_closes_owned_client(tmp_path):
    from butlers.whatsapp_bridge_client import BridgeClient

    async with bridge_server(tmp_path, b"HTTP/1.1 200 OK\r\n\r\n", keep_open=True) as server:
        events = BridgeClient(server.path).stream_events()
        task = asyncio.create_task(anext(events))
        try:
            async with asyncio.timeout(1):
                await server.ready.wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await events.aclose()
        await assert_peer_closed(server)
    assert server.closed == 1
