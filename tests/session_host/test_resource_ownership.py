"""Real factory/volume/plan ownership controls; no native or provider proof."""

import asyncio
import json
import os
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from butlers.cli_auth.registry import PROVIDERS
from butlers.cli_auth.sandbox import ReadonlySandboxAuthority
from butlers.cli_auth.sandbox_platform import (
    BubblewrapDashboardCLIAuthSandbox,
    ReadonlySandboxInvocation,
    SandboxIdentity,
    build_bubblewrap_launch_plan,
)
from butlers.session_host.auth import AuthService
from butlers.session_host.control import ControlService
from butlers.session_host.credential_volume import CodexCredentialVolume, codex_document

pytestmark = pytest.mark.unit


async def test_cancelled_volume_write_remains_owned_through_repeated_close_cancellation(
    tmp_path,
    monkeypatch,
):
    """A real fsync/rename worker, not its cancelled waiter, owns settlement."""
    root = tmp_path / "credentials"
    root.mkdir(mode=0o700)
    volume = CodexCredentialVolume(root, expected_uid=os.getuid())
    service = ControlService(None, auth_service=AuthService(None, volume=volume))
    content = codex_document(
        json.dumps(
            {
                "auth_mode": "chatgpt",
                "OPENAI_API_KEY": None,
                "tokens": {
                    "id_token": "fixture",
                    "access_token": "fixture",
                    "refresh_token": "fixture",
                    "account_id": None,
                },
                "last_refresh": "fixture-refresh",
            }
        ).encode()
    )
    entered, completed = asyncio.Event(), asyncio.Event()
    release = threading.Event()
    loop = asyncio.get_running_loop()
    original_fsync = os.fsync
    original_operation = volume._operation

    def fsync(descriptor):
        loop.call_soon_threadsafe(entered.set)
        if not release.wait(5):
            raise RuntimeError("fixture worker release was not observed")
        return original_fsync(descriptor)

    def operation(**kwargs):
        try:
            return original_operation(**kwargs)
        finally:
            loop.call_soon_threadsafe(completed.set)

    monkeypatch.setattr(os, "fsync", fsync)
    monkeypatch.setattr(volume, "_operation", operation)
    request = asyncio.create_task(volume.replace(expected=None, content=content))
    closing = None
    try:
        await asyncio.wait_for(entered.wait(), 2)
        request.cancel()
        with pytest.raises(asyncio.CancelledError):
            await request
        closing = asyncio.create_task(service.close())
        await asyncio.sleep(0)  # Let the actual close consumer enter its wait.
        assert not closing.done(), "cancelled request lost ownership of a live file worker"
        closing.cancel()
        await asyncio.sleep(0)
        closing.cancel()
        await asyncio.sleep(0)
        assert not closing.done()
        # A zero remaining shutdown budget is a truthful non-pass, not a
        # cancellation of the worker or permission to reuse the volume.
        from butlers.session_host.launch import Refused

        with pytest.raises(Refused, match="cleanup_incomplete"):
            await volume.close(deadline=loop.time())
        with pytest.raises(Refused):
            await volume.read()
        release.set()
        await asyncio.wait_for(completed.wait(), 2)
        with pytest.raises(asyncio.CancelledError):
            await closing
        await service.close()
        assert (root / "auth.json").read_bytes() == content
        assert list(root.glob(".auth-*.tmp")) == []
        # An independent reader must acquire the real released file lock.
        independent = CodexCredentialVolume(root, expected_uid=os.getuid())
        assert (await independent.read()).content == content
        await independent.close()
    finally:
        release.set()
        if entered.is_set():
            await asyncio.wait_for(completed.wait(), 2)
        await asyncio.gather(request, return_exceptions=True)
        if closing is not None:
            await asyncio.gather(closing, return_exceptions=True)


async def test_session_consumer_protects_home_ancestors_but_preserves_login_writable_home():
    sandbox = BubblewrapDashboardCLIAuthSandbox(
        exact_image_preflight=lambda: None,
        shim_input_resolver=lambda _: (),
        readonly_invocation_resolver=lambda provider, command: ReadonlySandboxInvocation(
            command=("/usr/local/bin/codex", *command[1:]),
            readonly_inputs=(),
        ),
    )
    process = SimpleNamespace(stdout=asyncio.StreamReader(), returncode=0)
    process.stdout.feed_eof()
    handle = SimpleNamespace(
        process=process,
        complete_readonly=AsyncMock(return_value=True),
        terminate=AsyncMock(),
    )
    sandbox._launch_invocation = AsyncMock(return_value=handle)
    await sandbox.run_staged_command(
        PROVIDERS["codex"],
        command=("codex", "exec"),
        expected_executable=Path("/usr/local/bin/codex"),
        authorities=(ReadonlySandboxAuthority(Path(".codex/config.toml"), b"fixture"),),
        timeout_s=1,
    )
    assert sandbox._launch_invocation.call_args.kwargs.get("immutable_home") is True
    info_r, info_w = os.pipe()
    block_r, block_w = os.pipe()
    gate_r, gate_w = os.pipe()
    try:
        inputs = dict(
            bwrap_path=Path("/usr/bin/bwrap"),
            shim_path=Path("/usr/bin/shim"),
            identity=SandboxIdentity(61000, 61000),
            stage_home=Path("/tmp/fixture-stage"),
            command=("/usr/local/bin/codex",),
            readonly_inputs=(),
            shim_readonly_inputs=(),
            info_fd=info_w,
            block_fd=block_r,
            shim_gate_fd=gate_r,
        )
        profile = build_bubblewrap_launch_plan(**inputs, immutable_home=True).argv
        ordinary = build_bubblewrap_launch_plan(**inputs).argv
        index = profile.index(str(inputs["stage_home"]))
        assert profile[index - 1 : index + 2] == (
            "--ro-bind",
            str(inputs["stage_home"]),
            "/home/runtime",
        )
        # Protect the full HOME ancestor, not just config.toml; there is no
        # later RW overlay of .codex/.config or of its config-bearing parent.
        writable = [profile[i + 1 : i + 3] for i, value in enumerate(profile) if value == "--bind"]
        assert writable == [(str(inputs["stage_home"] / ".cache"), "/home/runtime/.cache")]
        assert ("--tmpfs", "/tmp") in tuple(zip(profile, profile[1:], strict=False))
        assert "/home/runtime/.cache" in profile  # Explicit disposable writable scratch.
        assert ordinary[ordinary.index(str(inputs["stage_home"])) - 1] == "--bind"
        assert not any(
            ordinary[i : i + 3]
            == ("--bind", str(inputs["stage_home"] / ".cache"), "/home/runtime/.cache")
            for i in range(len(ordinary) - 2)
        )
    finally:
        for descriptor in (info_r, info_w, block_r, block_w, gate_r, gate_w):
            os.close(descriptor)


async def test_actual_host_factory_shares_live_and_quarantined_pool_on_deployed_stage(
    monkeypatch,
):
    import butlers.cli_auth.sandbox_platform as platform
    import butlers.session_host.service as service
    from butlers.cli_auth.sandbox import SandboxUnavailableError
    from butlers.session_host.control import TLSFiles

    platforms, bridges = [], []
    original_init = platform.BubblewrapDashboardCLIAuthSandbox.__init__
    original_bridge = service.BubblewrapSessionSandbox.__init__
    original_pool = platform.InvocationIdentityPool

    def construct(self, **kwargs):
        original_init(self, **kwargs)
        platforms.append(self)

    def bridge(self, *args, **kwargs):
        original_bridge(self, *args, **kwargs)
        bridges.append(self)

    monkeypatch.setattr(platform.BubblewrapDashboardCLIAuthSandbox, "__init__", construct)
    monkeypatch.setattr(service.BubblewrapSessionSandbox, "__init__", bridge)
    monkeypatch.setattr(platform, "InvocationIdentityPool", lambda: original_pool(last_id=61002))
    monkeypatch.setattr(service, "load_proofs", lambda _: ())
    monkeypatch.setattr(TLSFiles, "server_context", lambda _: object())
    server = SimpleNamespace(close=lambda: None, wait_closed=AsyncMock())
    monkeypatch.setattr(service.asyncio, "start_server", AsyncMock(return_value=server))
    running = await service.start_host(
        service.HostConfig(
            enabled=True,
            binary=Path("/usr/bin/codex"),
            broker_url="https://broker.invalid/mcp",
            proof_index=Path("/fixture/index.json"),
            tls=TLSFiles(Path("/fixture/ca"), Path("/fixture/cert"), Path("/fixture/key")),
            cli_auth_enabled=True,
            credential_volume=Path("/fixture/credentials"),
        )
    )
    try:
        assert len(platforms) == 1, "profiles constructed independent identity allocators"
        common = platforms[0]
        assert all(item._platform is common for item in bridges)
        assert running.consumer._auth_service._backend is common
        assert common._stage_root == Path("/tmp/session-host-stage")
        # Hold the first lease as an unsettled/quarantined domain. All three
        # real constructed consumers must respect it and shared exhaustion.
        owners = [
            bridges[0]._platform,
            bridges[-1]._platform,
            running.consumer._auth_service._backend,
        ]
        held = [await owner._identity_pool.acquire() for owner in owners]
        assert len(set(held)) == 3
        for owner in owners:
            with pytest.raises(SandboxUnavailableError, match="exhausted"):
                await owner._identity_pool.acquire()
        await common._identity_pool.release(held[1])
        replacement = await owners[2]._identity_pool.acquire()
        assert replacement == held[1] and replacement != held[0]
        await common._identity_pool.release(replacement)
        for identity in (held[0], held[2]):
            await common._identity_pool.release(identity)
    finally:
        await running.close()
