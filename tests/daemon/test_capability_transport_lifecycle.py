"""Actual adapter resource ownership; synthetic operations are not durable A2 proof."""

import asyncio
import socket
import ssl
from contextlib import ExitStack, asynccontextmanager

import pytest

from butlers.capability_broker.production import (
    ProductionTransportUnavailable,
    _TransportOwner,
    close_private_transport,
    start_private_transport,
)
from butlers.capability_broker.transport.config import TransportConfig
from butlers.capability_broker.transport.server import PrivateApp, PrivateServer
from butlers.config import ButlerConfig, CapabilityBrokerTransportConfig
from butlers.daemon import ButlerDaemon
from tests.capability_broker.test_service_transport import (
    CONTROL,
    SERVICE,
    call_value,
    fixture_reply,
)
from tests.capability_broker.test_service_transport import material as _material
from tests.daemon.test_daemon import _patch_infra

pytestmark = pytest.mark.unit
tls_material = _material


def daemon(tmp_path):
    d = ButlerDaemon(tmp_path)
    d.config = ButlerConfig(name="health", port=9100)
    return d


def enabled():
    return CapabilityBrokerTransportConfig.model_validate(
        {
            "enabled": True,
            "role": "broker-control",
            "domain": "switchboard",
            "endpoint": "https://health.internal:9443/mcp",
            "peer": {"role": "owning-service", "domain": "health", "sans": ["DNS:health.internal"]},
            "material": {
                "ca_path": "/unopened/ca",
                "certificate_path": "/unopened/cert",
                "private_key_path": "/unopened/key",
            },
        }
    )


async def test_OFF_and_unadmitted_enabled_never_construct_private_resources(tmp_path, monkeypatch):
    import butlers.capability_broker.transport.client as client
    import butlers.capability_broker.transport.server as server

    def forbidden(*args, **kwargs):
        pytest.fail("unadmitted private resource construction")

    monkeypatch.setattr(client, "build_client", forbidden)
    monkeypatch.setattr(server, "build_server", forbidden)
    monkeypatch.setattr(ssl, "create_default_context", forbidden)
    monkeypatch.setattr(socket, "socket", forbidden)
    d = daemon(tmp_path)
    await start_private_transport(d)
    await close_private_transport(d)
    assert d._capability_transport_owner is None and d._capability_transport_cleanup is None
    d.config.capability_broker_transport = enabled()
    # These caller-owned values are deliberately not producer/profile authority.
    d._control_profile = "KEY_ABSENT_STAGE"
    d._owning_operation = lambda *args: fixture_reply(args[0])
    d._private_transport_admitted = True
    with pytest.raises(ProductionTransportUnavailable, match="prerequisites_unavailable"):
        await start_private_transport(d)
    assert d._capability_transport_owner is None


async def test_actual_startup_refuses_before_approval_delivery_or_public_MCP(tmp_path, monkeypatch):
    from butlers.core import approval_delivery_worker

    d = daemon(tmp_path)
    d.config.capability_broker_transport = enabled()
    patches = _patch_infra()
    # The shared infrastructure owner has two delivered representations across
    # this branch and its hosted public union. Neither/both must fail closed;
    # never silently omit the actual runtime-adapter patch before startup.
    runtime_keys = frozenset(patches) & {"get_adapter", "build_runtime_adapter"}
    assert runtime_keys in ({"get_adapter"}, {"build_runtime_adapter"})
    runtime_key = next(iter(runtime_keys))
    with ExitStack() as stack:
        for key in (
            "db_from_env",
            "run_migrations",
            "validate_credentials",
            "validate_module_credentials",
            "init_telemetry",
            "configure_logging",
            "sync_schedules",
            "FastMCP",
            "Spawner",
            runtime_key,
            "shutil_which",
            "start_mcp_server",
            "connect_switchboard",
            "create_audit_pool",
            "recover_route_inbox",
        ):
            stack.enter_context(patches[key])
        called = []

        async def forbidden(_):
            called.append("approval_delivery")
            pytest.fail("ordinary worker started before private admission")

        monkeypatch.setattr(approval_delivery_worker, "start_approval_delivery_worker", forbidden)
        try:
            with pytest.raises(ProductionTransportUnavailable):
                await d.start()
            assert called == []
            d._start_mcp_server.assert_not_awaited()
            assert not d._accepting_connections and d._capability_transport_owner is None
        finally:
            # The private-only rollback must not masquerade as general daemon
            # shutdown. Remove this test's controlled logging registration.
            if d._db_log_handler is not None:
                import logging

                logging.getLogger().removeHandler(d._db_log_handler)
                d._db_log_handler = None


@asynccontextmanager
async def resources(material, *, lifespan=None):
    server_ctx, client_ctx = material()
    calls = []

    async def operation(value, peer, assertion):
        calls.append(peer)
        return fixture_reply(value)

    app = PrivateApp("health", operation)
    if lifespan is not None:
        app.app.router.lifespan_context = lifespan(app.app.router.lifespan_context)
    # Select a test-only loopback endpoint before constructing both real
    # factories. A bind race is a test failure, never a retry.
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    server = PrivateServer(app, server_ctx, CONTROL, port=port)
    owner = await _TransportOwner.open(
        client_config=TransportConfig(f"https://localhost:{port}/mcp", SERVICE),
        context_factory=lambda: client_ctx,
        server_config=CONTROL,
        server_factory=lambda policy: server,
    )
    try:
        yield owner, server, calls
    finally:
        await owner.aclose()


def settled(owner, server):
    assert owner.closed and owner._task.done() and owner._client_close.done()
    assert owner.client.http.is_closed and owner.client.transport.pool.connections == []
    assert not owner.client.transport.backend.streams
    assert server.socket.fileno() == -1 and server._lifetime_task.done()
    assert not server.protocols and not server.handshakes and server.slot_count == 0


async def test_real_TLS_MCP_factories_and_same_task_lifespan_settle_on_repeated_close(tls_material):
    seen = []

    def tracking(original):
        @asynccontextmanager
        async def lifetime(app):
            seen.append(("enter", asyncio.current_task()))
            try:
                async with original(app):
                    yield
            finally:
                seen.append(("exit", asyncio.current_task()))

        return lifetime

    async with resources(tls_material, lifespan=tracking) as (owner, server, calls):
        result = await owner.client.call(call_value())
        assert result["outcome"] == "cancelled" and len(calls) == 1
    settled(owner, server)
    waiter = owner._waiter
    deadline = owner._deadline
    await owner.aclose()
    assert owner._waiter is waiter and owner._deadline == deadline
    assert [x[0] for x in seen] == ["enter", "exit"] and seen[0][1] is seen[1][1]


async def test_partial_factory_failure_closes_already_constructed_real_client(
    tls_material, monkeypatch
):
    import butlers.capability_broker.transport.client as client_module

    _, context = tls_material()
    original = client_module.build_client
    clients = []

    def capture(*args):
        client = original(*args)
        clients.append(client)
        return client

    def fail(_):
        raise LookupError("controlled_server_factory_failure")

    monkeypatch.setattr(client_module, "build_client", capture)
    with pytest.raises(LookupError, match="controlled_server_factory_failure"):
        await _TransportOwner.open(
            client_config=TransportConfig("https://localhost:9443/mcp", SERVICE),
            context_factory=lambda: context,
            server_config=CONTROL,
            server_factory=fail,
        )
    assert len(clients) == 1 and clients[0].http.is_closed
    assert clients[0].transport.pool.connections == [] and not clients[0].transport.backend.streams


@pytest.mark.parametrize("cancel", [False, True])
async def test_actual_daemon_start_rolls_back_private_owner_after_postentry_failure(
    tmp_path,
    tls_material,
    monkeypatch,
    cancel,
):
    import butlers.lifecycle as lifecycle

    d = daemon(tmp_path)
    async with resources(tls_material) as (owner, server, _):
        await owner.client.call(call_value())
        entered = asyncio.Event()

        async def startup(actual):
            actual._capability_transport_owner = owner
            entered.set()
            if cancel:
                await asyncio.Event().wait()
            raise LookupError("controlled_later_startup_failure")

        monkeypatch.setattr(lifecycle, "run_startup", startup)
        task = asyncio.create_task(d.start())
        await entered.wait()
        if cancel:
            task.cancel()
        with pytest.raises(asyncio.CancelledError if cancel else LookupError):
            await task
        assert d._capability_transport_owner is None
        settled(owner, server)


async def test_shutdown_closes_private_resources_before_first_ordinary_await(
    tmp_path, tls_material
):
    d = daemon(tmp_path)
    async with resources(tls_material) as (owner, server, _):
        await owner.client.call(call_value())
        d._capability_transport_owner = owner
        reached = asyncio.Event()

        async def boot():
            try:
                await asyncio.Event().wait()
            finally:
                settled(owner, server)
                assert d._shutting_down and not d._accepting_connections
                reached.set()

        d._boot_registration_task = asyncio.create_task(boot())
        await asyncio.sleep(0)
        await d.shutdown()
        assert reached.is_set() and d._capability_transport_owner is None
        cleanup = d._capability_transport_cleanup
        await d.shutdown()
        assert d._capability_transport_cleanup is cleanup


async def test_second_cancellation_retains_real_lifespan_close_and_resource_settlement(
    tmp_path,
    tls_material,
):
    entered = asyncio.Event()
    release = asyncio.Event()

    def delayed(original):
        @asynccontextmanager
        async def lifetime(app):
            async with original(app):
                try:
                    yield
                finally:
                    entered.set()
                    await release.wait()

        return lifetime

    d = daemon(tmp_path)
    async with resources(tls_material, lifespan=delayed) as (owner, server, _):
        await owner.client.call(call_value())
        d._capability_transport_owner = owner
        close = asyncio.create_task(close_private_transport(d))
        try:
            await entered.wait()
            deadline = owner._deadline
            close.cancel()
            await asyncio.sleep(0)
            close.cancel()
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await close
            settled(owner, server)
            assert d._capability_transport_owner is None and owner._deadline == deadline
        finally:
            release.set()


async def test_failed_real_lifespan_cleanup_is_retained_not_reported_closed(tmp_path, tls_material):
    def failing(original):
        @asynccontextmanager
        async def lifetime(app):
            try:
                async with original(app):
                    yield
            finally:
                raise LookupError("controlled_cleanup_failure")

        return lifetime

    server_ctx, _ = tls_material()
    app = PrivateApp("health", lambda *args: None)
    app.app.router.lifespan_context = failing(app.app.router.lifespan_context)
    server = PrivateServer(app, server_ctx, CONTROL)
    owner = await _TransportOwner.open(server_config=CONTROL, server_factory=lambda _: server)
    d = daemon(tmp_path)
    d._capability_transport_owner = owner
    for _ in range(2):
        with pytest.raises(LookupError, match="controlled_cleanup_failure"):
            await close_private_transport(d)
        assert d._capability_transport_owner is owner and not owner.closed
    assert server.socket.fileno() == -1 and server._lifetime_task.done()
    assert owner._task.done() and not server.protocols and server.slot_count == 0


async def test_cancellation_during_actual_MCP_entry_closes_client_and_listener(
    tls_material, monkeypatch
):
    import butlers.capability_broker.transport.client as client_module

    server_ctx, client_ctx = tls_material()
    app = PrivateApp("health", lambda *args: None)
    original_lifetime = app.app.router.lifespan_context
    entered = asyncio.Event()
    exited = asyncio.Event()

    @asynccontextmanager
    async def partial(application):
        try:
            async with original_lifetime(application):
                entered.set()
                await asyncio.Event().wait()
                yield
        finally:
            exited.set()

    app.app.router.lifespan_context = partial
    server = PrivateServer(app, server_ctx, CONTROL)
    original_client = client_module.build_client
    clients = []

    def capture(*args):
        client = original_client(*args)
        clients.append(client)
        return client

    monkeypatch.setattr(client_module, "build_client", capture)
    task = asyncio.create_task(
        _TransportOwner.open(
            client_config=TransportConfig("https://localhost:9443/mcp", SERVICE),
            context_factory=lambda: client_ctx,
            server_config=CONTROL,
            server_factory=lambda _: server,
        )
    )
    try:
        async with asyncio.timeout(2):
            await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert exited.is_set() and server.socket.fileno() == -1
        assert server._lifetime_task.done() and not server.handshakes and server.slot_count == 0
        assert len(clients) == 1 and clients[0].http.is_closed
        assert (
            clients[0].transport.pool.connections == [] and not clients[0].transport.backend.streams
        )
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
