"""Real private service-route consumer; ephemeral TLS, no provider or sandbox proof."""

import asyncio
import json
import os
import socket
from dataclasses import replace

import pytest
import uvicorn
from cryptography.x509.oid import ExtendedKeyUsageOID

from butlers.cli_auth.registry import PROVIDERS
from butlers.cli_auth.sandbox import SandboxUnavailableError
from butlers.cli_auth.session import CLIAuthSession
from butlers.session_host.auth import (
    AUTH_CONTROL_IDENTITY,
    DASHBOARD_AUTH_IDENTITY,
    AuthEndpoint,
    AuthService,
    RemoteAuthSandbox,
)
from butlers.session_host.control import ControlService

pytestmark = pytest.mark.unit
pytest_plugins = ("tests.session_host.test_control",)


async def test_existing_service_listener_relays_private_tls_login_without_another_listener(
    certificates,
    tmp_path,
    monkeypatch,
):
    import butlers.session_host.service_auth as service_auth
    from butlers.session_host.service_auth import SERVICE_PATH

    host_tls, runner_tls, wrong_tls = certificates
    control_tls = certificates.issue(
        "service-auth-control", AUTH_CONTROL_IDENTITY, ExtendedKeyUsageOID.SERVER_AUTH
    )
    dashboard_tls = certificates.issue(
        "service-auth-dashboard", DASHBOARD_AUTH_IDENTITY, ExtendedKeyUsageOID.CLIENT_AUTH
    )
    from butlers.cli_auth.persistence import persist_validated_staged_device_auth_bytes
    from butlers.session_host.credential_volume import (
        CodexCredentialVolume,
        OpenCodeCredentialVolume,
        codex_document,
    )

    staged = json.dumps(
        {
            "auth_mode": "chatgpt",
            "OPENAI_API_KEY": None,
            "tokens": {
                "id_token": "fixture-id",
                "access_token": "fixture-access",
                "refresh_token": "fixture-refresh",
                "account_id": None,
            },
            "last_refresh": "fixture-refresh",
        }
    ).encode()

    class Authority:
        value = None
        available = True
        opencode = {"opencode-openai": None, "opencode-go": None}

        async def load_opencode_cli_auths(self):
            if not self.available:
                raise OSError("fixture authority unavailable")
            return dict(self.opencode)

        def require_system_global_pool(self):
            return self

        async def load_codex_cli_auth(self):
            if not self.available:
                raise OSError("fixture authority unavailable")
            return self.value

        async def store_codex_cli_auth_if_unchanged(self, value, *, expected_value):
            if self.value != expected_value:
                return False
            self.value = value
            return True

    authority = Authority()
    volume_root = tmp_path / "host-volume"
    volume_root.mkdir(mode=0o700)
    volume = CodexCredentialVolume(volume_root, expected_uid=os.getuid())
    opencode_volume = OpenCodeCredentialVolume(volume_root, expected_uid=os.getuid())
    provider = replace(PROVIDERS["codex"], token_path=tmp_path / "dashboard-auth" / "auth.json")

    class Handle:
        def __init__(self):
            self.process = type("Process", (), {})()
            self.process.stdout = asyncio.StreamReader()
            self.process.stdout.feed_data(
                b"https://auth.openai.com/codex/device\nEnter code: ABCD-EFGH\n"
                b"Successfully logged in\n"
            )
            self.process.stdout.feed_eof()
            self.process.returncode = None
            self.contained = False
            self.terminated = False

        async def finalize(self, *, succeeded):
            assert succeeded
            self.contained = True
            self.process.returncode = 0
            return staged

        async def terminate(self):
            self.terminated = True

    class Backend:
        def __init__(self):
            self.handles = []

        async def launch_device_auth(self, provider):
            assert provider is PROVIDERS["codex"]
            handle = Handle()
            self.handles.append(handle)
            return handle

    backend = Backend()
    host = ControlService(
        None, auth_service=AuthService(backend, volume=volume, opencode_volume=opencode_volume)
    )
    server = await asyncio.start_server(host.handle, "127.0.0.1", 0, ssl=host_tls.server_context())

    def tls_paths(files):
        return {k: str(getattr(files, k)) for k in ("ca", "certificate", "private_key")}

    control_config = tmp_path / "control-auth.json"
    control_config.write_text(
        json.dumps(
            {
                "tls": tls_paths(control_tls),
                "host": {
                    "hostname": "localhost",
                    "port": server.sockets[0].getsockname()[1],
                    "tls": tls_paths(runner_tls),
                },
            }
        )
    )
    control_config.chmod(0o444)
    monkeypatch.setattr(service_auth, "_CONTROL_CONFIG", control_config)
    monkeypatch.setenv("BUTLERS_SESSION_HOST_CLI_AUTH", "1")
    relay = service_auth.configured_control_relay(authority=authority)

    from fastmcp import FastMCP

    from butlers.daemon import ButlerDaemon

    app = ButlerDaemon._build_mcp_http_app(
        FastMCP("service-auth-fixture"),
        butler_name="switchboard",
        cli_auth_relay=relay,
    )
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    serving = uvicorn.Server(
        uvicorn.Config(app, access_log=False, log_level="critical", lifespan="on")
    )
    task = asyncio.create_task(serving.serve(sockets=[sock]))
    session = None
    try:
        async with asyncio.timeout(10):
            while not serving.started:
                if task.done():
                    task.result()
                await asyncio.sleep(0.001)
            import aiohttp

            async with aiohttp.ClientSession() as client:
                async with client.get(f"http://localhost:{port}/health") as response:
                    assert await response.json() == {"status": "ok"}
                async with client.post(f"http://localhost:{port}{SERVICE_PATH}") as response:
                    assert response.status == 404
                async with client.ws_connect(f"http://localhost:{port}{SERVICE_PATH}") as websocket:
                    # Neither a trusted-looking field nor plaintext JSON is a
                    # TLS handshake. No backend work can precede authentication.
                    await websocket.send_bytes(b'{"type":"cli_login","provider":"codex"}')
                    assert (await websocket.receive()).type in {
                        aiohttp.WSMsgType.CLOSE,
                        aiohttp.WSMsgType.CLOSED,
                    }
                    assert not backend.handles
            endpoint = AuthEndpoint(
                "localhost", port, dashboard_tls, AUTH_CONTROL_IDENTITY, service_path=SERVICE_PATH
            )
            from butlers.cli_auth.sandbox import dashboard_cli_auth_sandbox

            config = tmp_path / "dashboard-auth.json"
            config.write_text(
                json.dumps(
                    {
                        "hostname": "localhost",
                        "port": port,
                        "tls": tls_paths(dashboard_tls),
                    }
                )
            )
            config.chmod(0o444)
            monkeypatch.setattr(service_auth, "_DASHBOARD_CONFIG", config)
            monkeypatch.setenv("BUTLERS_SESSION_HOST_CLI_AUTH", "1")
            service_auth._dashboard_backend.cache_clear()
            committed = []

            async def commit(registered_provider, *, staged_output):
                assert backend.handles[0].contained
                assert registered_provider is PROVIDERS["codex"]
                committed.append(staged_output)
                return await persist_validated_staged_device_auth_bytes(
                    provider,
                    authority,
                    staged_output,
                    expected_authority_value=None,
                    codex_authority=authority,
                )

            session = CLIAuthSession(
                "service-fixture",
                PROVIDERS["codex"],
                sandbox=dashboard_cli_auth_sandbox(),
                on_success=commit,
            )
            await session.start()
            await session._done_event.wait()
            assert session.state == "success"
            assert committed == [staged]
            assert authority.value.encode() == codex_document(staged)
            assert (await volume.read()).content == authority.value.encode()
            assert not provider.token_path.exists()
            # The authenticated operation has no caller-provided content or
            # path. An extra authority field cannot change the host volume.
            from butlers.session_host.control import read_frame, write_frame

            reader, writer = await service_auth.connect_service(endpoint)
            try:
                await write_frame(writer, {"type": "codex_sync", "content": "forged"})
                assert await read_frame(reader) == {"type": "refused"}
            finally:
                writer.close()
                await writer.wait_closed()
            assert (await volume.read()).content == codex_document(staged)
            # Real authenticated dashboard -> existing control service -> host
            # uses only the current selected DB pair, not dashboard content.
            remote = RemoteAuthSandbox(endpoint)
            authority.opencode["opencode-go"] = json.dumps(
                {"opencode-go": {"type": "api", "key": "synthetic-key"}}
            )
            await remote.sync_provider("opencode-go")
            assert json.loads((await opencode_volume.read()).content) == {
                "opencode-go": {"type": "api", "key": "synthetic-key"}
            }
            assert (await remote.probe_provider("opencode-go")).state.value == "authenticated"
            authority.opencode["opencode-go"] = None
            await remote.sync_provider("opencode-go")
            assert (await opencode_volume.read()).content == b"{}"
            assert (await remote.probe_provider("opencode-go")).state.value == "not_authenticated"
            # A model-producing Go test has no admitted D18 status surrogate.
            with pytest.raises(SandboxUnavailableError):
                await remote.probe_provider("opencode-go", record=True)
            # An authoritative commit is factual even when its subsequent
            # host projection cannot be acknowledged. No local fallback and
            # no success may be manufactured from that partial outcome.
            authority.available = False
            with pytest.raises(SandboxUnavailableError):
                await remote.launch_device_auth(PROVIDERS["codex"])
            with pytest.raises(SandboxUnavailableError):
                await remote.launch_device_auth(PROVIDERS["opencode-openai"])
            assert len(backend.handles) == 1
            assert not await persist_validated_staged_device_auth_bytes(
                provider,
                authority,
                staged,
                expected_authority_value=authority.value,
                codex_authority=authority,
            )
            assert authority.value.encode() == codex_document(staged)
            assert not provider.token_path.exists()
            authority.available = True
            previous = authority.value
            authority.value = "{invalid-selected-authority"
            with pytest.raises(SandboxUnavailableError):
                await remote.launch_device_auth(PROVIDERS["codex"])
            authority.value = previous
            authority.opencode["opencode-openai"] = "{invalid-selected-authority"
            with pytest.raises(SandboxUnavailableError):
                await remote.launch_device_auth(PROVIDERS["opencode-openai"])
            authority.opencode["opencode-openai"] = None
            assert len(backend.handles) == 1

            with pytest.raises(SandboxUnavailableError):
                await RemoteAuthSandbox(replace(endpoint, tls=wrong_tls)).launch_device_auth(
                    PROVIDERS["codex"]
                )
            assert len(backend.handles) == 1
            assert backend.handles[0].terminated
            # A second real login disconnects before finalize. Closing the
            # existing service route must settle both transport hops without
            # authorizing a second credential commit.
            abandoned = await RemoteAuthSandbox(endpoint).launch_device_auth(PROVIDERS["codex"])
            await abandoned.terminate()
            serving.should_exit = True
            await task  # Actual existing ASGI lifespan owns relay settlement.
            await host.close()
            assert len(backend.handles) == 2
            assert all(handle.terminated for handle in backend.handles)
            assert not backend.handles[1].contained
            assert committed == [staged]
            assert relay._closing and relay._settlement.done()
            assert not relay._active
    finally:
        if session is not None:
            await session.kill()
        await relay.close()
        serving.should_exit = True
        try:
            await asyncio.wait_for(task, 5)
        finally:
            sock.close()
            server.close()
            await server.wait_closed()
            await host.close()
            service_auth._dashboard_backend.cache_clear()


async def test_off_factories_do_not_read_config_and_enabled_missing_config_never_falls_back(
    monkeypatch,
    tmp_path,
):
    import butlers.session_host.service_auth as service_auth
    from butlers.cli_auth.sandbox import dashboard_cli_auth_sandbox

    def forbidden(_):
        pytest.fail("OFF opened a transport configuration")

    real = service_auth._configuration
    monkeypatch.setattr(service_auth, "_configuration", forbidden)
    monkeypatch.setenv("BUTLERS_SESSION_HOST_CLI_AUTH", "0")
    assert service_auth.configured_control_relay() is None
    assert service_auth.configured_dashboard_sandbox() is None
    monkeypatch.setenv("BUTLERS_SESSION_HOST_CLI_AUTH", "1")
    # The configured production factory requires a selected authority even
    # before transport material is read. It cannot select the raw relay ABI.
    relay = service_auth.configured_control_relay()
    messages = []

    async def receive():
        pytest.fail("missing authority must refuse before receiving TLS records")

    async def send(message):
        messages.append(message)

    async def existing_service(*_):
        pytest.fail("private refusal must not become an ordinary service request")

    await relay.wrap(existing_service)(
        {"type": "websocket", "path": service_auth.SERVICE_PATH}, receive, send
    )
    assert messages == [{"type": "websocket.close", "code": 1008, "reason": ""}]
    await relay.close()
    monkeypatch.setattr(service_auth, "_configuration", real)
    monkeypatch.setattr(service_auth, "_DASHBOARD_CONFIG", tmp_path / "absent.json")
    service_auth._dashboard_backend.cache_clear()
    monkeypatch.setenv("BUTLERS_SESSION_HOST_CLI_AUTH", "1")
    with pytest.raises(SandboxUnavailableError, match="CLI auth transport unavailable"):
        await dashboard_cli_auth_sandbox().launch_device_auth(PROVIDERS["codex"])
    service_auth._dashboard_backend.cache_clear()
