"""Actual central runtime assembly; stateful SQL protocols are SOFTWARE only.

REQ-capability-authorization-002; REQ-capability-authorization-008.
The real publisher/reader, file signer, TCP listeners, FastMCP and signed domain
exchange execute here. No SQL double establishes roles, locks or durability.
"""

import asyncio
import base64
from pathlib import Path

import httpx
import pytest
from anyio import Path as AsyncPath
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from butlers.control import SessionRefusalListener
from butlers.core.catalog_reader import DOMAINS
from tests.capability_broker.test_published_catalog import current_broker
from tests.contracts.test_catalog_publication import registered_catalog

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


def key_config(tmp_path, endpoint):
    key = Ed25519PrivateKey.generate()
    path = tmp_path / "test-only-signing-key.pem"
    raw = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    public = base64.b64encode(key.public_key().public_bytes_raw()).decode("ascii")
    try:
        path.write_bytes(raw)
        path.chmod(0o400)
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    return {
        "enabled": True,
        "key_file": str(path),
        "kid": "test",
        "verification_keys": {"test": public},
        "domain_endpoints": dict.fromkeys(DOMAINS, endpoint),
    }


class NativeListener(SessionRefusalListener):
    def __init__(self, app):
        super().__init__(host="127.0.0.1", port=0)
        self.app = app
        self.assertions = []

    def _application(self):
        async def observe(scope, receive, send):
            self.assertions.extend(
                v.decode("ascii")
                for k, v in scope.get("headers", ())
                if k == b"x-butlers-assertion"
            )
            await self.app(scope, receive, send)

        return observe

    def _application_lifespan(self):
        return self.app.router.lifespan_context(self.app)


async def test_actual_factory_current_catalog_signer_session_and_registered_read(tmp_path):
    from butlers.capability_broker.runtime import RuntimeConfig, build_runtime

    controlled, connection, _, token, store, _ = await current_broker()
    mcp, _ = await registered_catalog("health")
    native = NativeListener(mcp.http_app(path="/mcp", stateless_http=False, json_response=False))
    runtime = None
    config = key_config(tmp_path, "http://127.0.0.1:9/mcp")
    try:
        await native.start()
        config["domain_endpoints"] = dict.fromkeys(DOMAINS, f"http://127.0.0.1:{native.port}/mcp")
        runtime = await build_runtime(
            controlled.pool, RuntimeConfig.parse(config), host="127.0.0.1", port=0
        )
        await runtime.start()
        async with httpx.AsyncClient(timeout=5, trust_env=False) as client:
            url = f"http://127.0.0.1:{runtime.listener.port}/goals/{connection.goal}/mcp/"
            headers = {
                "Authorization": "Bearer " + token,
                "Accept": "application/json,text/event-stream",
            }
            listed = await client.post(
                url,
                headers=headers,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/list",
                    "params": {},
                },
            )
            assert listed.status_code == 200
            assert [t["name"] for t in listed.json()["result"]["tools"]] == ["health__lookup"]
            result = await client.post(
                url,
                headers=headers,
                json={
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {"name": "health__lookup", "arguments": {"query": "native"}},
                },
            )
            value = result.json()["result"]["structuredContent"]
            assert value["status"] == "returned" and value["effect_id"] is None
            assert value["value"]["content"][0]["text"] == "native"
            assert connection.used == 1 and connection.exposed
            from time import time

            from butlers.capability_broker.assertions import verify_assertion

            assert native.assertions
            verified = verify_assertion(
                native.assertions[-1],
                keys={"test": runtime.broker.signer.private_key.public_key()},
                audience="health",
                capability="lookup",
                arguments={"query": "native"},
                now=int(time()),
                claim_call_id=lambda *_: True,
            )
            assert verified["goal_id"] == str(connection.goal)
            assert verified["effect_id"] is None
            # This local signature check has no durable receiver replay store.
            assert runtime.broker.pool is controlled.pool
            assert dict(runtime.broker.approvals.clients) == {}
            refused = await client.post(
                url,
                headers={**headers, "Authorization": "Bearer invalid"},
                json={"jsonrpc": "2.0", "id": 3, "method": "tools/list"},
            )
            assert refused.status_code == 401
            assert (
                await client.post(url.replace("/goals/" + str(connection.goal), "/token"), json={})
            ).status_code == 401
            sent = len(native.assertions)
            store.current["health"]["live"] = False
            refused = await client.post(
                url,
                headers=headers,
                json={
                    "jsonrpc": "2.0",
                    "id": 4,
                    "method": "tools/list",
                    "params": {},
                },
            )
            assert refused.json()["result"]["tools"] == []
            blocked = await client.post(
                url,
                headers=headers,
                json={
                    "jsonrpc": "2.0",
                    "id": 5,
                    "method": "tools/call",
                    "params": {
                        "name": "health__lookup",
                        "arguments": {"query": "not dispatched"},
                    },
                },
            )
            assert blocked.json()["result"]["isError"] is True
            assert len(native.assertions) == sent
            assert connection.used == 1
    finally:
        try:
            if runtime is not None:
                await runtime.close()
                await runtime.close()
        finally:
            try:
                await native.close()
            finally:
                await AsyncPath(config["key_file"]).unlink()
    assert runtime.listener.cleanup_complete and native.cleanup_complete
    assert runtime.listener.task.done() and native.task.done()


async def test_off_is_literal_strict_and_performs_no_private_io(tmp_path, monkeypatch):
    from unittest.mock import Mock

    from butlers.capability_broker import runtime
    from butlers.capability_broker.contracts import BrokerRefused
    from butlers.control import StageConfigurationError, run_control

    fail = Mock(side_effect=AssertionError("OFF performed private IO"))
    monkeypatch.setattr(runtime.AssertionSigner, "from_file", fail)
    monkeypatch.setattr(runtime, "PublishedAuthorities", fail)
    monkeypatch.setattr(runtime, "SignedMCPDomains", fail)
    monkeypatch.setattr(runtime, "BrokerSessionListener", fail)
    unused = runtime.RuntimeConfig.parse(
        {
            "key_file": "${UNUSED_KEY}/missing",
            "verification_keys": {"unused": "not-a-key"},
            "domain_endpoints": {"unused": "${UNUSED_ENDPOINT}"},
        }
    )
    assert await runtime.build_runtime(object(), unused, host="127.0.0.1") is None
    assert runtime.load_runtime_config(None).enabled is False
    with pytest.raises(BrokerRefused):
        await runtime.build_runtime(
            object(), runtime.RuntimeConfig(domain_endpoints=None), host="127.0.0.1"
        )
    fail.assert_not_called()
    # The selected broker process also refuses OFF before constructing its daemon.
    import butlers.daemon

    monkeypatch.setattr(butlers.daemon, "ButlerDaemon", fail)
    with pytest.raises(StageConfigurationError, match="disabled"):
        await run_control(tmp_path, profile="TRUSTED_BROKER", session_host="127.0.0.1")
    fail.assert_not_called()
    for malformed in (
        {"enabled": "false"},
        {"extra": False},
        {"key_file": 3},
        {"domain_endpoints": {"health": False}},
        {"verification_keys": []},
    ):
        with pytest.raises(BrokerRefused, match="broker_unavailable"):
            runtime.RuntimeConfig.parse(malformed)
    config_path = tmp_path / "literal.toml"
    config_path.write_text('key_file = "${UNRESOLVED}/missing"\n')
    assert runtime.load_runtime_config(config_path).key_file == "${UNRESOLVED}/missing"
    config_path.write_text("enabled = false\nenabled = true\n")
    with pytest.raises(BrokerRefused):
        runtime.load_runtime_config(config_path)


async def test_profile_phase_retains_key_absent_and_forbidden_env_store_boundaries(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, Mock

    from butlers.config import ButlerConfig
    from butlers.control import (
        KEY_ABSENT_STAGE,
        TRUSTED_BROKER,
        StageConfigurationError,
        check_control_profile,
        check_private_key_absent,
        check_stored_private_key_absent,
    )
    from butlers.lifecycle import StartupState, _startup_config

    monkeypatch.setattr(Path, "iterdir", lambda _: iter([Path("GOAL_BROKER_SIGNING_KEY")]))
    check_control_profile(TRUSTED_BROKER, environment={})
    with pytest.raises(StageConfigurationError):
        check_private_key_absent(KEY_ABSENT_STAGE, environment={})
    for profile in (KEY_ABSENT_STAGE, TRUSTED_BROKER, "unrecognized"):
        with pytest.raises(StageConfigurationError):
            check_control_profile(profile, environment={"GOAL_BROKER_SIGNING_KEY_FILE": ""})
    monkeypatch.setattr("butlers.core.logging.configure_logging", Mock())
    config = ButlerConfig(name="switchboard", port=41100)
    daemon = SimpleNamespace(_control_profile=TRUSTED_BROKER, config=config)
    state = StartupState()
    await _startup_config(daemon, state)
    assert state.profile == TRUSTED_BROKER
    daemon.config = ButlerConfig(name="health", port=41100)
    with pytest.raises(ValueError, match="switchboard"):
        await _startup_config(daemon, StartupState())
    # The shared database phase's unchanged probe is still fail closed, name-only.
    pool = SimpleNamespace(fetchval=AsyncMock(side_effect=[True, True]))
    with pytest.raises(StageConfigurationError):
        await check_stored_private_key_absent(pool)
    assert all("secret_value" not in call.args[0] for call in pool.fetchval.call_args_list)

    from click.testing import CliRunner

    import butlers.control
    from butlers.cli import cli

    selected = AsyncMock()
    monkeypatch.setattr(butlers.control, "run_control", selected)
    result = await asyncio.to_thread(
        CliRunner().invoke,
        cli,
        [
            "control",
            "--profile",
            TRUSTED_BROKER,
            "--config",
            "roster/switchboard",
        ],
    )
    assert result.exit_code == 0
    assert selected.await_args.kwargs == {
        "profile": TRUSTED_BROKER,
        "session_host": "127.0.0.1",
        "runtime_config": None,
    }


async def test_factory_refuses_actual_role_catalog_and_key_failures_before_bind(tmp_path):
    import copy

    from butlers.capability_broker.contracts import BrokerRefused
    from butlers.capability_broker.runtime import RuntimeConfig, build_runtime

    controlled, conn, _, _, store, _ = await current_broker()
    config = key_config(tmp_path, "http://127.0.0.1:9/mcp")
    try:
        for failure in (
            "role",
            "disabled-fleet",
            "missing-member",
            "bad-key",
            "wrong-kid",
            "missing-endpoint",
            "key-mode",
        ):
            damaged = copy.deepcopy(config)
            original_fetchrow = conn.fetchrow
            members = list(store.members)
            if failure == "role":

                async def denied(sql, *args, original_fetchrow=original_fetchrow):
                    if "pg_roles" in sql:
                        return {
                            "name": "butler_health_rw",
                            "rolsuper": False,
                            "rolbypassrls": False,
                        }
                    return await original_fetchrow(sql, *args)

                conn.fetchrow = denied
            elif failure == "disabled-fleet":
                store.enabled = False
            elif failure == "missing-member":
                store.members.pop()
            elif failure == "bad-key":
                damaged["verification_keys"]["test"] = "not-base64"
            elif failure == "wrong-kid":
                damaged["kid"] = "unknown"
            elif failure == "missing-endpoint":
                damaged["domain_endpoints"].pop("health")
            else:
                await AsyncPath(config["key_file"]).chmod(0o600)
            try:
                with pytest.raises(BrokerRefused):
                    await build_runtime(
                        controlled.pool, RuntimeConfig.parse(damaged), host="127.0.0.1", port=0
                    )
            finally:
                conn.fetchrow = original_fetchrow
                store.enabled, store.members = True, members
                await AsyncPath(config["key_file"]).chmod(0o400)
        assert conn.used == 0 and not conn.exposed
    finally:
        await AsyncPath(config["key_file"]).unlink()


async def test_actual_normal_shutdown_preserves_first_cleanup_cancel_at_deadline(
    tmp_path, monkeypatch
):
    import signal
    from types import SimpleNamespace

    import butlers.daemon
    from butlers.capability_broker.session import BrokerSessionListener
    from butlers.control import run_control

    controlled, _, _, _, _, _ = await current_broker()
    config = key_config(tmp_path, "http://127.0.0.1:9/mcp")
    config_path = tmp_path / "runtime.toml"
    write_config(config_path, config)
    installed, closing, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    callbacks, observed, listeners = {}, [], []

    class Daemon:
        def __init__(self, config_dir, *, control_profile):
            assert control_profile == "TRUSTED_BROKER"
            self.db = SimpleNamespace(pool=controlled.pool)
            self.config = SimpleNamespace(shutdown_timeout_s=0.05)
            self._server_task = None
            self.closed = False
            observed.append(self)

        async def start(self):
            self._server_task = asyncio.create_task(asyncio.Event().wait())

        async def shutdown(self):
            closing.set()
            try:
                await release.wait()
            finally:
                self._server_task.cancel()
                await asyncio.gather(self._server_task, return_exceptions=True)
                self.closed = True

    loop = asyncio.get_running_loop()

    def install(sig, callback):
        callbacks[sig] = callback
        if sig == signal.SIGINT:
            installed.set()

    # Exercise the actual installed shutdown callback without signalling this
    # test process or any unrelated task/process group.
    monkeypatch.setattr(loop, "add_signal_handler", install)
    monkeypatch.setattr(loop, "remove_signal_handler", lambda sig: callbacks.pop(sig, None))
    monkeypatch.setattr(butlers.daemon, "ButlerDaemon", Daemon)
    original_start = BrokerSessionListener.start

    async def start(listener):
        listener.port = 0
        listeners.append(listener)
        await original_start(listener)

    monkeypatch.setattr(BrokerSessionListener, "start", start)
    task = asyncio.create_task(
        run_control(
            tmp_path, profile="TRUSTED_BROKER", session_host="127.0.0.1", runtime_config=config_path
        )
    )
    try:
        async with asyncio.timeout(10):
            await installed.wait()
            callbacks[signal.SIGTERM]()
            await closing.wait()
            # Normal shutdown has already won the operation race. The first
            # caller cancellation arrives only during its retained cleanup.
            assert not task.done()
            task.cancel("first cleanup cancellation")
            await asyncio.sleep(0)
            task.cancel("repeated cleanup cancellation")
            with pytest.raises(asyncio.CancelledError) as error:
                await task
        assert error.value.args == ("first cleanup cancellation",)
        assert isinstance(error.value.__cause__, TimeoutError)
        assert str(error.value.__cause__) == "control resource cleanup incomplete"
        daemon = observed[0]
        # Join the timeout-cancelled retained owner separately; this does not
        # turn its explicit incomplete result into in-bound cleanup success.
        done, pending = await asyncio.wait({daemon._control_cleanup_task}, timeout=1)
        assert not pending and done == {daemon._control_cleanup_task}
        assert daemon._control_cleanup_task.cancelled()
        assert daemon.closed and daemon._server_task.done()
        assert not callbacks
        for listener in listeners:
            assert listener.task.done() and listener.cleanup_complete is True
            async with httpx.AsyncClient(timeout=1, trust_env=False) as client:
                with pytest.raises(httpx.ConnectError):
                    await client.get(f"http://127.0.0.1:{listener.port}/health")
    finally:
        try:
            release.set()
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            if observed and hasattr(observed[0], "_control_cleanup_task"):
                done, pending = await asyncio.wait({observed[0]._control_cleanup_task}, timeout=1)
                assert not pending
        finally:
            await AsyncPath(config["key_file"]).unlink()


def write_config(path, config):
    import json

    path.write_text(
        "\n".join(
            [
                "enabled = true",
                "kid = " + json.dumps(config["kid"]),
                "key_file = " + json.dumps(config["key_file"]),
                "[verification_keys]",
                *(
                    json.dumps(k) + " = " + json.dumps(v)
                    for k, v in config["verification_keys"].items()
                ),
                "[domain_endpoints]",
                *(
                    json.dumps(k) + " = " + json.dumps(v)
                    for k, v in config["domain_endpoints"].items()
                ),
            ]
        )
        + "\n"
    )


@pytest.mark.parametrize(
    "failure", ["double-cancel", "factory", "lifespan-exit", "cleanup-timeout"]
)
async def test_actual_control_owner_cancellation_and_partial_start_release(
    tmp_path, monkeypatch, failure
):
    from contextlib import asynccontextmanager
    from types import SimpleNamespace

    import butlers.daemon
    from butlers.capability_broker.session import BrokerSessionListener
    from butlers.control import run_control

    controlled, _, _, _, store, _ = await current_broker()
    config = key_config(tmp_path, "http://127.0.0.1:9/mcp")
    config_path = tmp_path / "runtime.toml"
    write_config(config_path, config)
    entered, closing, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    observed = []

    class Daemon:
        def __init__(self, config_dir, *, control_profile):
            assert control_profile == "TRUSTED_BROKER"
            self.db = SimpleNamespace(pool=controlled.pool)
            self.config = SimpleNamespace(
                shutdown_timeout_s=0.05 if failure == "cleanup-timeout" else 1
            )
            self._server_task = None
            self.closed = False
            observed.append(self)

        async def start(self):
            self._server_task = asyncio.create_task(asyncio.Event().wait())

        async def shutdown(self):
            closing.set()
            try:
                if failure in {"double-cancel", "cleanup-timeout"}:
                    await release.wait()
            finally:
                self._server_task.cancel()
                await asyncio.gather(self._server_task, return_exceptions=True)
                self.closed = True

    monkeypatch.setattr(butlers.daemon, "ButlerDaemon", Daemon)
    original_start = BrokerSessionListener.start
    listeners = []

    async def start(listener):
        listener.port = 0
        listeners.append(listener)
        await original_start(listener)
        entered.set()

    monkeypatch.setattr(BrokerSessionListener, "start", start)
    if failure == "factory":
        store.enabled = False
    if failure == "lifespan-exit":
        original_lifespan = BrokerSessionListener._application_lifespan

        @asynccontextmanager
        async def broken(listener):
            async with original_lifespan(listener):
                yield
            raise RuntimeError("controlled_lifespan_exit")

        monkeypatch.setattr(BrokerSessionListener, "_application_lifespan", broken)
    task = asyncio.create_task(
        run_control(
            tmp_path, profile="TRUSTED_BROKER", session_host="127.0.0.1", runtime_config=config_path
        )
    )
    try:
        async with asyncio.timeout(10):
            if failure != "factory":
                await entered.wait()
                task.cancel()
            await closing.wait()
            if failure == "double-cancel":
                task.cancel()
                await asyncio.sleep(0)
                release.set()
            with pytest.raises(BaseException) as error:
                await task
        if failure == "factory":
            from butlers.capability_broker.contracts import BrokerRefused

            assert isinstance(error.value, BrokerRefused) and not listeners
        else:
            assert isinstance(error.value, asyncio.CancelledError)
        daemon = observed[0]
        if failure == "cleanup-timeout":
            # The owner reports incomplete at its ORIGINAL finite deadline; this
            # later observation joins the retained task, not false in-bound credit.
            assert isinstance(error.value.__cause__, TimeoutError)
            await asyncio.wait({daemon._control_cleanup_task}, timeout=1)
            assert daemon._control_cleanup_task.done()
            assert daemon._control_cleanup_task.cancelled()
        assert daemon.closed and daemon._server_task.done()
        assert daemon._control_cleanup_task.done()
        for listener in listeners:
            assert listener.task.done()
            if failure == "lifespan-exit":
                assert listener.cleanup_complete is False
                assert isinstance(error.value.__cause__, RuntimeError)
            else:
                assert listener.cleanup_complete is True
            async with httpx.AsyncClient(timeout=1, trust_env=False) as client:
                with pytest.raises(httpx.ConnectError):
                    await client.get(f"http://127.0.0.1:{listener.port}/health")
    finally:
        release.set()
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await AsyncPath(config["key_file"]).unlink()


async def test_replay_worker_and_private_clients_close_once_through_repeated_cancellation():
    from types import SimpleNamespace

    from butlers.capability_broker.runtime import CentralRuntime

    entered, release = asyncio.Event(), asyncio.Event()
    closed = []

    class Client:
        async def aclose(self):
            closed.append("client")
            entered.set()
            await release.wait()

    class Listener:
        async def close(self):
            closed.append("listener")

    runtime = CentralRuntime(
        SimpleNamespace(approvals=SimpleNamespace(clients={"home": Client()})), Listener()
    )
    runtime._replay_task = asyncio.create_task(asyncio.Event().wait())
    closing = asyncio.create_task(runtime.close())
    try:
        async with asyncio.timeout(2):
            await entered.wait()
            closing.cancel()
            await asyncio.sleep(0)
            closing.cancel()
            await asyncio.sleep(0)
            assert not closing.done()
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await closing
        await runtime.close()
        assert closed.count("client") == closed.count("listener") == 1
        assert runtime._replay_task.done() and all(
            task.done() for task in runtime._cleanup_children
        )
    finally:
        release.set()
        if not closing.done():
            closing.cancel()
        await asyncio.gather(closing, return_exceptions=True)
        await runtime.close()
