"""Unit coverage for the uncredentialed restore-drill TCP relay."""

from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

from tests.restore_drill_endpoint_policy import (
    NONCANONICAL_PORT_REJECTED,
    REMOTE_IPV4_ACCEPTED,
    REMOTE_IPV4_REJECTED,
)

pytestmark = pytest.mark.unit

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "restore_drill_tcp_proxy.py"


class _TrackingWriter:
    """Minimal StreamWriter substitute that records the cleanup contract."""

    def __init__(self) -> None:
        self.chunks: list[bytes] = []
        self.close_calls = 0
        self.closed = asyncio.Event()

    def write(self, chunk: bytes) -> None:
        self.chunks.append(chunk)

    async def drain(self) -> None:
        return None

    def close(self) -> None:
        self.close_calls += 1
        self.closed.set()

    async def wait_closed(self) -> None:
        await asyncio.sleep(0)


class _BufferedPeerWriter(_TrackingWriter):
    """A peer whose graceful close blocks while its outbound buffer cannot drain."""

    def __init__(self) -> None:
        super().__init__()
        self.abort_calls = 0
        self.close_waiter: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        # ``StreamWriter.transport.abort()`` is the production forced-close
        # route.  Point the fake's public transport at itself to exercise it.
        self.transport = self

    def close(self) -> None:
        self.close_calls += 1

    def abort(self) -> None:
        self.abort_calls += 1
        self.closed.set()
        if not self.close_waiter.done():
            self.close_waiter.set_result(None)

    async def wait_closed(self) -> None:
        await self.close_waiter


async def _wait_for(predicate, *, attempts: int = 100) -> None:
    for _ in range(attempts):
        if predicate():
            return
        await asyncio.sleep(0)
    pytest.fail("timed out waiting for relay test condition")


def _proxy_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("restore_drill_tcp_proxy", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_proxy_accepts_only_the_explicit_resolved_ipv4_route(monkeypatch) -> None:
    proxy = _proxy_module()
    monkeypatch.setenv("RESTORE_DRILL_PROXY_DB_HOST", "10.23.4.5")
    monkeypatch.setenv("RESTORE_DRILL_PROXY_DB_PORT", "5433")
    monkeypatch.setenv("POSTGRES_HOST", "must-not-be-used.example.test")
    monkeypatch.setenv("DATABASE_URL", "postgresql://must-not-be-used.example.test/db")

    config = proxy.load_restore_drill_proxy_config()

    assert config.db_host == "10.23.4.5"
    assert config.db_port == 5433


@pytest.mark.parametrize("remote_ipv4", REMOTE_IPV4_ACCEPTED)
def test_proxy_accepts_every_supported_remote_unicast_endpoint(
    monkeypatch, remote_ipv4: str
) -> None:
    """Keep the relay in parity with every other endpoint validator."""
    proxy = _proxy_module()
    monkeypatch.setenv("RESTORE_DRILL_PROXY_DB_HOST", remote_ipv4)
    monkeypatch.setenv("RESTORE_DRILL_PROXY_DB_PORT", "5432")

    assert proxy.load_restore_drill_proxy_config().db_host == remote_ipv4


@pytest.mark.parametrize(
    ("host", "port"),
    [
        ("postgres.example.test", "5432"),
        ("2001:db8::1", "5432"),
        *[(host, "5432") for host in REMOTE_IPV4_REJECTED],
        ("10.23.4.5", "0"),
        ("10.23.4.5", "65536"),
        *[("10.23.4.5", port) for port in NONCANONICAL_PORT_REJECTED],
    ],
)
def test_proxy_rejects_a_non_ipv4_or_invalid_port_route(monkeypatch, host: str, port: str) -> None:
    proxy = _proxy_module()
    monkeypatch.setenv("RESTORE_DRILL_PROXY_DB_HOST", host)
    monkeypatch.setenv("RESTORE_DRILL_PROXY_DB_PORT", port)

    with pytest.raises(ValueError):
        proxy.load_restore_drill_proxy_config()


@pytest.mark.asyncio
async def test_proxy_forces_buffered_peer_cleanup_after_bounded_graceful_close(monkeypatch) -> None:
    """A non-reading raw-TCP peer cannot pin relay cleanup behind a flush."""
    proxy = _proxy_module()
    monkeypatch.setattr(proxy, "_WRITER_CLOSE_TIMEOUT_S", 0.01, raising=False)
    writer = _BufferedPeerWriter()
    writer.write(b"buffered PostgreSQL response")

    await asyncio.wait_for(proxy._close_writers(writer), timeout=0.1)

    assert writer.chunks == [b"buffered PostgreSQL response"]
    assert writer.close_calls == 1
    assert writer.abort_calls == 1
    assert writer.closed.is_set()
    assert writer.close_waiter.done()
    assert not writer.close_waiter.cancelled()


@pytest.mark.asyncio
async def test_proxy_buffered_close_releases_admission_for_a_later_connection(monkeypatch) -> None:
    """Forced close must not strand the single executor relay slot."""
    proxy = _proxy_module()
    monkeypatch.setattr(proxy, "_WRITER_CLOSE_TIMEOUT_S", 0.01, raising=False)
    limits = proxy.RestoreDrillProxyLimits(
        max_active_clients=1,
        connect_timeout_s=0.1,
        idle_timeout_s=0.1,
        session_timeout_s=0.2,
    )
    relay = proxy.RestoreDrillRelay(
        config=proxy.RestoreDrillProxyConfig(db_host="10.23.4.5", db_port=5544),
        limits=limits,
    )
    remote_reader = asyncio.StreamReader()
    remote_writer = _BufferedPeerWriter()

    async def connected_remote(host: str, port: int):
        assert (host, port) == ("10.23.4.5", 5544)
        return remote_reader, remote_writer

    monkeypatch.setattr(proxy.asyncio, "open_connection", connected_remote)
    client_reader = asyncio.StreamReader()
    client_reader.feed_eof()
    client_writer = _BufferedPeerWriter()

    await asyncio.wait_for(relay.handle_client(client_reader, client_writer), timeout=0.1)

    assert client_writer.abort_calls == 1
    assert remote_writer.abort_calls == 1
    assert relay.active_client_count == 0

    retry_reader = asyncio.StreamReader()
    retry_reader.feed_eof()
    retry_writer = _TrackingWriter()
    await asyncio.wait_for(relay.handle_client(retry_reader, retry_writer), timeout=0.1)
    assert retry_writer.close_calls == 1
    assert relay.active_client_count == 0


@pytest.mark.asyncio
async def test_proxy_binds_the_validated_target_port_not_a_hard_coded_default(monkeypatch) -> None:
    """A non-default database port must be reachable through the internal relay."""
    proxy = _proxy_module()
    captured: dict[str, object] = {}
    started = asyncio.Event()

    class FakeServer:
        def close(self) -> None:
            captured["closed"] = True

        async def wait_closed(self) -> None:
            captured["waited_closed"] = True

    async def fake_start_server(callback, *, host: str, port: int, backlog: int):
        captured.update(callback=callback, host=host, port=port, backlog=backlog)
        started.set()
        return FakeServer()

    monkeypatch.setattr(proxy.asyncio, "start_server", fake_start_server)

    task = asyncio.create_task(
        proxy.run_restore_drill_proxy(
            proxy.RestoreDrillProxyConfig(db_host="10.23.4.5", db_port=5544),
            limits=proxy.RestoreDrillProxyLimits(
                max_active_clients=2,
                connect_timeout_s=0.1,
                idle_timeout_s=0.1,
                session_timeout_s=0.2,
            ),
        )
    )
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert captured["host"] == "0.0.0.0"
    assert captured["port"] == 5544
    assert captured["backlog"] == 2
    assert captured["closed"] is True
    assert captured["waited_closed"] is True


@pytest.mark.asyncio
async def test_proxy_rejects_a_saturated_client_before_dialing_upstream(monkeypatch) -> None:
    """A bounded relay must not queue accepted sockets while capacity is exhausted."""
    proxy = _proxy_module()
    limits = proxy.RestoreDrillProxyLimits(
        max_active_clients=2,
        connect_timeout_s=1.0,
        idle_timeout_s=1.0,
        session_timeout_s=2.0,
    )
    relay = proxy.RestoreDrillRelay(
        config=proxy.RestoreDrillProxyConfig(db_host="10.23.4.5", db_port=5544),
        limits=limits,
    )
    release_upstream = asyncio.Event()
    upstream_calls = 0

    async def blocked_open_connection(host: str, port: int):
        nonlocal upstream_calls
        assert (host, port) == ("10.23.4.5", 5544)
        upstream_calls += 1
        await release_upstream.wait()
        remote_reader = asyncio.StreamReader()
        remote_writer = _TrackingWriter()
        return remote_reader, remote_writer

    monkeypatch.setattr(proxy.asyncio, "open_connection", blocked_open_connection)
    admitted_readers = [asyncio.StreamReader() for _ in range(limits.max_active_clients)]
    admitted_writers = [_TrackingWriter() for _ in range(limits.max_active_clients)]
    admitted = [
        asyncio.create_task(relay.handle_client(reader, writer))
        for reader, writer in zip(admitted_readers, admitted_writers, strict=True)
    ]
    await _wait_for(lambda: upstream_calls == limits.max_active_clients)

    rejected_writer = _TrackingWriter()
    await asyncio.wait_for(
        relay.handle_client(asyncio.StreamReader(), rejected_writer), timeout=0.1
    )

    assert upstream_calls == limits.max_active_clients
    assert rejected_writer.close_calls == 1
    assert relay.active_client_count == limits.max_active_clients

    for reader in admitted_readers:
        reader.feed_eof()
    release_upstream.set()
    await asyncio.gather(*admitted)
    assert relay.active_client_count == 0


@pytest.mark.asyncio
async def test_proxy_connect_timeout_closes_client_and_releases_admission(monkeypatch) -> None:
    """A blackholed target cannot retain a client slot after its connect deadline."""
    proxy = _proxy_module()
    limits = proxy.RestoreDrillProxyLimits(
        max_active_clients=1,
        connect_timeout_s=0.01,
        idle_timeout_s=1.0,
        session_timeout_s=2.0,
    )
    relay = proxy.RestoreDrillRelay(
        config=proxy.RestoreDrillProxyConfig(db_host="10.23.4.5", db_port=5544),
        limits=limits,
    )
    blackhole = asyncio.Event()
    cancelled = asyncio.Event()
    calls = 0

    async def blackhole_open_connection(host: str, port: int):
        nonlocal calls
        calls += 1
        try:
            await blackhole.wait()
        finally:
            cancelled.set()

    monkeypatch.setattr(proxy.asyncio, "open_connection", blackhole_open_connection)
    timed_out_writer = _TrackingWriter()

    await relay.handle_client(asyncio.StreamReader(), timed_out_writer)

    assert calls == 1
    assert cancelled.is_set()
    assert timed_out_writer.close_calls == 1
    assert relay.active_client_count == 0

    async def completed_open_connection(host: str, port: int):
        nonlocal calls
        calls += 1
        remote_reader = asyncio.StreamReader()
        remote_writer = _TrackingWriter()
        return remote_reader, remote_writer

    monkeypatch.setattr(proxy.asyncio, "open_connection", completed_open_connection)
    retry_reader = asyncio.StreamReader()
    retry_reader.feed_eof()
    retry_writer = _TrackingWriter()
    await relay.handle_client(retry_reader, retry_writer)

    assert calls == 2
    assert retry_writer.close_calls == 1
    assert relay.active_client_count == 0


@pytest.mark.asyncio
async def test_proxy_idle_deadline_closes_both_writers_and_releases_admission(monkeypatch) -> None:
    """A connected client that sends no bytes cannot retain a relay session forever."""
    proxy = _proxy_module()
    limits = proxy.RestoreDrillProxyLimits(
        max_active_clients=1,
        connect_timeout_s=1.0,
        idle_timeout_s=0.01,
        session_timeout_s=1.0,
    )
    relay = proxy.RestoreDrillRelay(
        config=proxy.RestoreDrillProxyConfig(db_host="10.23.4.5", db_port=5544),
        limits=limits,
    )
    remote_reader = asyncio.StreamReader()
    remote_writer = _TrackingWriter()

    async def connected_remote(host: str, port: int):
        return remote_reader, remote_writer

    monkeypatch.setattr(proxy.asyncio, "open_connection", connected_remote)
    client_writer = _TrackingWriter()

    await relay.handle_client(asyncio.StreamReader(), client_writer)

    assert client_writer.close_calls == 1
    assert remote_writer.close_calls == 1
    assert relay.active_client_count == 0


@pytest.mark.asyncio
async def test_proxy_session_deadline_closes_an_active_stream(monkeypatch) -> None:
    """Continuous sessions retain a finite absolute deadline even before idle expiry."""
    proxy = _proxy_module()
    limits = proxy.RestoreDrillProxyLimits(
        max_active_clients=1,
        connect_timeout_s=1.0,
        idle_timeout_s=1.0,
        session_timeout_s=0.01,
    )
    relay = proxy.RestoreDrillRelay(
        config=proxy.RestoreDrillProxyConfig(db_host="10.23.4.5", db_port=5544),
        limits=limits,
    )
    remote_reader = asyncio.StreamReader()
    remote_writer = _TrackingWriter()

    async def connected_remote(host: str, port: int):
        return remote_reader, remote_writer

    monkeypatch.setattr(proxy.asyncio, "open_connection", connected_remote)
    client_reader = asyncio.StreamReader()
    client_reader.feed_data(b"active-before-deadline")
    client_writer = _TrackingWriter()

    await relay.handle_client(client_reader, client_writer)

    assert client_writer.close_calls == 1
    assert remote_writer.close_calls == 1
    assert relay.active_client_count == 0


@pytest.mark.asyncio
async def test_proxy_cancellation_and_shutdown_close_handlers_without_future_dials(
    monkeypatch,
) -> None:
    """Shutdown cancels active callbacks and rejects later callbacks before upstream work."""
    proxy = _proxy_module()
    monkeypatch.setattr(proxy, "_WRITER_CLOSE_TIMEOUT_S", 0.01)
    limits = proxy.RestoreDrillProxyLimits(
        max_active_clients=1,
        connect_timeout_s=1.0,
        idle_timeout_s=1.0,
        session_timeout_s=2.0,
    )
    relay = proxy.RestoreDrillRelay(
        config=proxy.RestoreDrillProxyConfig(db_host="10.23.4.5", db_port=5544),
        limits=limits,
    )
    remote_reader = asyncio.StreamReader()
    remote_writer = _BufferedPeerWriter()
    upstream_calls = 0

    async def connected_remote(host: str, port: int):
        nonlocal upstream_calls
        upstream_calls += 1
        return remote_reader, remote_writer

    monkeypatch.setattr(proxy.asyncio, "open_connection", connected_remote)
    active_writer = _BufferedPeerWriter()
    active = asyncio.create_task(relay.handle_client(asyncio.StreamReader(), active_writer))
    await _wait_for(lambda: upstream_calls == 1)

    await relay.shutdown()
    with pytest.raises(asyncio.CancelledError):
        await active

    later_writer = _TrackingWriter()
    await relay.handle_client(asyncio.StreamReader(), later_writer)

    assert active_writer.close_calls == 1
    assert remote_writer.close_calls == 1
    assert active_writer.abort_calls == 1
    assert remote_writer.abort_calls == 1
    assert later_writer.close_calls == 1
    assert upstream_calls == 1
    assert relay.active_client_count == 0


@pytest.mark.asyncio
async def test_proxy_forwards_bytes_to_a_nondefault_target_port(monkeypatch) -> None:
    """The configured nondefault listener/target port is also used for outbound dialing."""
    proxy = _proxy_module()
    client_reader = asyncio.StreamReader()
    client_reader.feed_data(b"postgres-startup")
    client_reader.feed_eof()
    client_writer = _TrackingWriter()
    remote_reader = asyncio.StreamReader()
    remote_writer = _TrackingWriter()
    dialed: list[tuple[str, int]] = []

    async def connected_remote(host: str, port: int):
        dialed.append((host, port))
        return remote_reader, remote_writer

    monkeypatch.setattr(proxy.asyncio, "open_connection", connected_remote)
    await proxy.relay_connection(
        client_reader,
        client_writer,
        config=proxy.RestoreDrillProxyConfig(db_host="10.23.4.5", db_port=5544),
        limits=proxy.RestoreDrillProxyLimits(
            max_active_clients=1,
            connect_timeout_s=1.0,
            idle_timeout_s=1.0,
            session_timeout_s=2.0,
        ),
    )

    assert dialed == [("10.23.4.5", 5544)]
    assert remote_writer.chunks == [b"postgres-startup"]
    assert client_writer.close_calls == 1
    assert remote_writer.close_calls == 1


@pytest.mark.asyncio
async def test_proxy_runner_closes_listener_before_awaiting_relay_shutdown(monkeypatch) -> None:
    """Cancellation must stop accepts before the runner drains active relay handlers."""
    proxy = _proxy_module()
    calls: list[str] = []
    serve_started = asyncio.Event()
    relay_shutdown = asyncio.Event()
    captured: dict[str, object] = {}
    original_relay = proxy.RestoreDrillRelay

    class CapturingRelay(original_relay):
        instance: CapturingRelay | None = None

        def __init__(self, *, config, limits) -> None:
            super().__init__(config=config, limits=limits)
            CapturingRelay.instance = self

        async def shutdown(self) -> None:
            await super().shutdown()
            calls.append("relay.shutdown")
            relay_shutdown.set()

    class FakeServer:
        def close(self) -> None:
            calls.append("server.close")

        async def wait_closed(self) -> None:
            calls.append("server.wait_closed")
            assert relay_shutdown.is_set(), "runner waited for listener close before relay shutdown"

        async def serve_forever(self) -> None:
            serve_started.set()
            await asyncio.Event().wait()

    async def fake_start_server(callback, *, host: str, port: int, backlog: int):
        captured["callback"] = callback
        assert (host, port, backlog) == ("0.0.0.0", 5544, 1)
        serve_started.set()
        return FakeServer()

    remote_reader = asyncio.StreamReader()
    remote_writer = _TrackingWriter()

    async def connected_remote(host: str, port: int):
        return remote_reader, remote_writer

    monkeypatch.setattr(proxy, "RestoreDrillRelay", CapturingRelay)
    monkeypatch.setattr(proxy.asyncio, "start_server", fake_start_server)
    monkeypatch.setattr(proxy.asyncio, "open_connection", connected_remote)
    task = asyncio.create_task(
        proxy.run_restore_drill_proxy(
            proxy.RestoreDrillProxyConfig(db_host="10.23.4.5", db_port=5544),
            limits=proxy.RestoreDrillProxyLimits(
                max_active_clients=1,
                connect_timeout_s=0.1,
                idle_timeout_s=0.1,
                session_timeout_s=0.2,
            ),
        )
    )
    await serve_started.wait()
    callback = captured["callback"]
    active_writer = _TrackingWriter()
    active = asyncio.create_task(callback(asyncio.StreamReader(), active_writer))
    await _wait_for(
        lambda: (
            CapturingRelay.instance is not None and CapturingRelay.instance.active_client_count == 1
        )
    )

    task.cancel()
    try:
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        if CapturingRelay.instance is not None and CapturingRelay.instance.active_client_count:
            await CapturingRelay.instance.shutdown()

    assert calls == ["server.close", "relay.shutdown", "server.wait_closed"]
    with pytest.raises(asyncio.CancelledError):
        await active
    assert active_writer.close_calls == 1
    assert remote_writer.close_calls == 1
    assert CapturingRelay.instance.active_client_count == 0
