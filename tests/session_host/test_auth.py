"""Real TLS and existing CLIAuthSession consumers; no provider login or live key."""

import asyncio
from dataclasses import replace

import pytest
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
from butlers.session_host.control import HOST_IDENTITY, ControlService

pytestmark = pytest.mark.unit
pytest_plugins = ("tests.session_host.test_control",)


async def test_existing_cli_session_crosses_two_authenticated_hops_and_contains_before_commit(
    certificates,
):
    host_tls, runner_tls, wrong_tls = certificates
    control_tls = certificates.issue(
        "auth-control", AUTH_CONTROL_IDENTITY, ExtendedKeyUsageOID.SERVER_AUTH
    )
    dashboard_tls = certificates.issue(
        "dashboard-auth", DASHBOARD_AUTH_IDENTITY, ExtendedKeyUsageOID.CLIENT_AUTH
    )

    class Process:
        def __init__(self):
            self.stdout = asyncio.StreamReader()
            self.returncode = None

    class Handle:
        def __init__(self):
            self.process = Process()
            self.contained = False
            self.terminated = asyncio.Event()
            self.release = asyncio.Event()
            self.release.set()
            self.cancelled = 0
            self.process.stdout.feed_data(
                b"https://auth.openai.com/codex/device\nEnter code: ABCD-EFGH\n"
                b"Successfully logged in\n"
            )
            self.process.stdout.feed_eof()

        async def finalize(self, *, succeeded):
            assert succeeded
            self.contained = True
            self.process.returncode = 0
            return b'{"fixture":"staged-only-after-containment"}'

        async def terminate(self):
            self.terminated.set()
            try:
                await self.release.wait()
            except asyncio.CancelledError:
                self.cancelled += 1
                raise

    class Backend:
        def __init__(self):
            self.handles = []

        async def launch_device_auth(self, provider):
            assert provider is PROVIDERS["codex"]
            handle = Handle()
            if self.handles:
                handle.release.clear()
            self.handles.append(handle)
            return handle

    backend = Backend()
    hosted = AuthService(backend)
    host_dispatch = ControlService(None, auth_service=hosted)
    server = await asyncio.start_server(
        host_dispatch.handle, "127.0.0.1", 0, ssl=host_tls.server_context()
    )
    remote_host = RemoteAuthSandbox(
        AuthEndpoint("localhost", server.sockets[0].getsockname()[1], runner_tls, HOST_IDENTITY)
    )
    relayed = AuthService(remote_host, peer_identity=DASHBOARD_AUTH_IDENTITY)
    relay = await asyncio.start_server(
        relayed.handle, "127.0.0.1", 0, ssl=control_tls.server_context()
    )
    endpoint = AuthEndpoint(
        "localhost", relay.sockets[0].getsockname()[1], dashboard_tls, AUTH_CONTROL_IDENTITY
    )
    remote = RemoteAuthSandbox(endpoint)
    committed = []

    async def commit(provider, *, staged_output):
        assert backend.handles[0].contained
        committed.append((provider, staged_output))

    session = CLIAuthSession("fixture", PROVIDERS["codex"], sandbox=remote, on_success=commit)
    try:
        async with asyncio.timeout(5):
            await session.start()
            await session._done_event.wait()
            assert session.state == "success"
            assert session.auth_url == "https://auth.openai.com/codex/device"
            assert session.device_code == "ABCD-EFGH"
            assert committed == [
                (PROVIDERS["codex"], b'{"fixture":"staged-only-after-containment"}')
            ]
            with pytest.raises(SandboxUnavailableError):
                await RemoteAuthSandbox(replace(endpoint, tls=wrong_tls)).launch_device_auth(
                    PROVIDERS["codex"]
                )
            with pytest.raises(SandboxUnavailableError):
                await remote.launch_device_auth(
                    replace(PROVIDERS["codex"], command=["arbitrary", "command"])
                )
            assert len(backend.handles) == 1
            cancelled = await remote.launch_device_auth(PROVIDERS["codex"])
            await cancelled.terminate()
            await backend.handles[1].terminated.wait()
            assert not backend.handles[1].contained and len(committed) == 1
            closing = asyncio.create_task(host_dispatch.close())
            await asyncio.sleep(0)
            assert not closing.done() and backend.handles[1].cancelled == 0
            again = asyncio.create_task(host_dispatch.close())
            await asyncio.sleep(0)
            assert not again.done() and backend.handles[1].cancelled == 0
            backend.handles[1].release.set()
            await closing
            await again
    finally:
        for handle in backend.handles:
            handle.release.set()
        await session.kill()
        relay.close()
        server.close()
        await relay.wait_closed()
        await server.wait_closed()
        await relayed.close()
        await host_dispatch.close()


async def test_real_volume_over_control_cas_preserves_newer_authority_and_denies_unsafe_files(
    tmp_path,
    certificates,
):
    import json
    import os

    from butlers.session_host.credential_volume import (
        CodexCredentialVolume,
        ControlCodexSync,
        codex_document,
    )
    from butlers.session_host.launch import Refused

    def document(marker):
        return json.dumps(
            {
                "auth_mode": "chatgpt",
                "OPENAI_API_KEY": None,
                "tokens": {
                    "id_token": marker,
                    "access_token": marker,
                    "refresh_token": marker,
                    "account_id": None,
                },
                "last_refresh": "fixture-refresh",
            }
        ).encode()

    class Authority:
        value = document("initial").decode()
        available = True
        writes = []

        async def load_codex_cli_auth(self):
            if not self.available:
                raise OSError("fixture unavailable")
            return self.value

        async def store_codex_cli_auth_if_unchanged(self, value, *, expected_value):
            self.writes.append(expected_value)
            if expected_value != self.value:
                return False
            self.value = value
            return True

    host_tls, runner_tls, _ = certificates
    volume_root = tmp_path / "host-volume"
    volume_root.mkdir(mode=0o700)
    volume = CodexCredentialVolume(volume_root, expected_uid=os.getuid())
    dispatcher = ControlService(None, auth_service=AuthService(None, volume=volume))
    server = await asyncio.start_server(
        dispatcher.handle, "127.0.0.1", 0, ssl=host_tls.server_context()
    )
    remote = RemoteAuthSandbox(
        AuthEndpoint(
            "localhost",
            server.sockets[0].getsockname()[1],
            runner_tls,
            HOST_IDENTITY,
        )
    )
    authority = Authority()
    sync = ControlCodexSync(remote, authority)
    try:
        async with asyncio.timeout(5):
            assert (await remote.read()).content is None
            before = await sync.prepare()
            assert before == document("initial")
            initial = await remote.read()
            assert initial.content == codex_document(before)
            # A complete committed replacement read back through a NEW TLS
            # connection remains authoritative even if its reply is discarded.
            await remote.replace(expected=initial.digest, content=document("rotation"))
            rotated = await remote.read()
            assert rotated.content == codex_document(document("rotation"))
            with pytest.raises(Refused):
                await remote.replace(expected=initial.digest, content=document("stale"))
            authority.value = document("new-dashboard-login").decode()
            assert await sync.finalize(expected_authority=before, rotated=rotated.content) is False
            assert authority.value == document("new-dashboard-login").decode()
            assert (await remote.read()).content == codex_document(authority.value.encode())
            # A genuine same-snapshot rotation is persisted; deletion and an
            # unavailable authority cannot manufacture a bootstrap credential.
            current = await sync.prepare()
            assert await sync.finalize(expected_authority=current, rotated=document("next")) is True
            assert (await remote.read()).content == codex_document(document("next"))
            authority.value = None
            with pytest.raises(Refused):
                await sync.prepare()
            assert (await remote.read()).content == codex_document(document("next"))
            authority.available = False
            with pytest.raises(Refused, match="credential_authority_unavailable"):
                await sync.prepare()
            assert len(authority.writes) == 2
            path = volume_root / "auth.json"
            path.chmod(0o644)
            with pytest.raises(Refused):
                await remote.read()
            path.unlink()
            target = tmp_path / "outside"
            target.write_bytes(document("outside"))
            path.symlink_to(target)
            with pytest.raises(Refused):
                await remote.read()
            assert target.read_bytes() == document("outside")
    finally:
        server.close()
        await server.wait_closed()
        await dispatcher.close()
