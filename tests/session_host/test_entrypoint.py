"""The session-host process entrypoint and its configurable bind, dark (bu-kiuh8v.15).

Real mTLS uses the synthetic PKI in conftest.py, laid out as a deployment mounts it.
The host it starts has no reviewed runtime profile, so it refuses every launch.
"""

from __future__ import annotations

import asyncio
import dataclasses
import shutil
import socket
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from butlers.goal_runner.session_host_client import SessionHostClientConfig, session_host_clients
from butlers.goal_runner.staging import ModelCredentials
from butlers.session_host.contracts import Refused
from butlers.session_host.control import LocalControl, local_command
from butlers.session_host.entrypoint import SessionHostServeConfig, serve
from butlers.session_host.lifecycle import InvocationOwner
from tests.session_host.test_codex_auth import codex_document, sha

pytestmark = pytest.mark.unit

_ORIGIN = "https://broker.test"


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _private_dir(path: Path) -> Path:
    path.mkdir(mode=0o700, parents=True)
    path.chmod(0o700)
    return path


@pytest.fixture
def mounted(tmp_path, pki, pki_root):
    """The host's PKI directory: its leaf and key, the client CA, and both pins."""
    _server, _client, pin = pki
    directory = _private_dir(tmp_path / "pki")
    shutil.copyfile(pki_root / "server.crt", directory / "tls.crt")
    shutil.copyfile(pki_root / "server.key", directory / "tls.key")
    shutil.copyfile(pki_root / "ca.crt", directory / "client-ca.crt")
    (directory / "tls.key").chmod(0o600)
    (directory / "server.sha256").write_text(pin("server") + "\n")
    (directory / "caller.sha256").write_text(pin("runner") + "\n")
    return directory


@pytest.fixture
def config(tmp_path, mounted):
    return SessionHostServeConfig(
        bind_host="127.0.0.1",
        port=_free_port(),
        pki_dir=mounted,
        staging_root=tmp_path / "staging",
        credentials_root=tmp_path / "credentials",
        broker_origin=_ORIGIN,
    )


def _client(config, pki, pki_root, tmp_path) -> SessionHostClientConfig:
    _server, _client_context, pin = pki
    directory = _private_dir(tmp_path / "runner-pki")
    for source, target in (
        ("ca.crt", "ca.crt"),
        ("runner.crt", "tls.crt"),
        ("runner.key", "tls.key"),
    ):
        shutil.copyfile(pki_root / source, directory / target)
    (directory / "tls.key").chmod(0o600)
    return SessionHostClientConfig(
        host=config.bind_host,
        port=config.port,
        server_hostname="host.test",
        server_sha256=pin("server"),
        ca_path=directory / "ca.crt",
        certificate_path=directory / "tls.crt",
        key_path=directory / "tls.key",
    )


class Store:
    async def load(self, key: str) -> str | None:
        return "sk-synthetic"


# ── The configurable bind ──────────────────────────────────────────────────


def _control(tmp_path, pki, **bind) -> LocalControl:
    server, _client_context, pin = pki
    return LocalControl(
        owner=InvocationOwner(_private_dir(tmp_path / "stage")),
        factory=lambda runtime, model: None,
        **server(),
        caller_sha256=pin("runner"),
        server_sha256=pin("server"),
        broker_origin=_ORIGIN,
        **bind,
    )


async def test_the_default_bind_stays_loopback_on_an_ephemeral_port(tmp_path, pki):
    control = _control(tmp_path, pki)
    port = await control.start()
    try:
        assert port != 0
        assert control._server is not None
        assert {s.getsockname()[0] for s in control._server.sockets} == {"127.0.0.1"}
    finally:
        await control.close()


async def test_a_configured_bind_listens_where_the_deployment_says(tmp_path, pki):
    _server, client, pin = pki
    port = _free_port()
    control = _control(tmp_path, pki, bind_host="127.0.0.1", bind_port=port)
    assert await control.start() == port
    try:
        reply = await local_command(
            port,
            context=client("runner"),
            server_hostname="host.test",
            server_sha256=pin("server"),
            command={"version": 1, "operation": "codex_auth_status"},
            host="127.0.0.1",
        )
        assert reply == {"status": "refused"}  # no volume on this host; still mTLS-pinned
    finally:
        await control.close()


async def test_a_black_holed_host_times_out_at_connect(monkeypatch, pki):
    """A non-loopback peer that never answers SYN is bounded, not left to the kernel."""
    from butlers.session_host import control as control_module

    _server, client, pin = pki
    started = asyncio.Event()

    async def black_hole(*args, **kwargs):
        started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(control_module, "_IO_SECONDS", 0.2)
    monkeypatch.setattr(control_module.asyncio, "open_connection", black_hole)
    task = asyncio.create_task(
        local_command(
            41198,
            context=client("runner"),
            server_hostname="host.test",
            server_sha256=pin("server"),
            command={"version": 1, "operation": "codex_auth_status"},
            host="192.0.2.1",
        )
    )
    done, _pending = await asyncio.wait({task}, timeout=3)
    if not done:
        task.cancel()
        pytest.fail("the connect was not bounded")
    assert started.is_set()
    assert isinstance(task.exception(), TimeoutError)
    assert issubclass(TimeoutError, OSError)  # callers map it as an unreachable host


@pytest.mark.parametrize(
    "bind",
    [
        {"bind_host": "host.test"},
        {"bind_host": "127.0.0.1 "},
        {"bind_host": ""},
        {"bind_port": -1},
        {"bind_port": 65536},
        {"bind_port": True},
    ],
)
def test_a_bind_that_is_not_an_address_and_port_refuses(tmp_path, pki, bind):
    with pytest.raises(Refused, match="^invalid_control_identity$"):
        _control(tmp_path, pki, **bind)


# ── The entrypoint ─────────────────────────────────────────────────────────


async def test_the_host_serves_the_credential_volume_until_stopped(config, pki, pki_root, tmp_path):
    stop = asyncio.Event()
    task = asyncio.create_task(serve(config, stop))
    clients = session_host_clients(
        _client(config, pki, pki_root, tmp_path), ModelCredentials(Store()), broker_origin=_ORIGIN
    )
    try:
        for _ in range(100):
            try:
                assert await clients.codex_auth.status() is None
                break
            except OSError:
                await asyncio.sleep(0.05)
        else:
            pytest.fail("the session host never listened")
        document = codex_document()
        assert await clients.codex_auth.install(document, expected_sha256=None) is True
        assert await clients.codex_auth.read() == (document, sha(document))
        volume = config.credentials_root
        assert stat.S_IMODE(volume.stat().st_mode) == 0o700
        assert stat.S_IMODE(config.staging_root.stat().st_mode) == 0o700
    finally:
        stop.set()
        await asyncio.wait_for(task, 10)


async def test_without_a_credential_volume_every_auth_operation_refuses(
    config, pki, pki_root, tmp_path
):
    config = dataclasses.replace(config, credentials_root=None)
    stop = asyncio.Event()
    task = asyncio.create_task(serve(config, stop))
    clients = session_host_clients(
        _client(config, pki, pki_root, tmp_path), ModelCredentials(Store()), broker_origin=_ORIGIN
    )
    try:
        for _ in range(100):
            try:
                await clients.codex_auth.status()
            except Refused:
                break
            except OSError:
                await asyncio.sleep(0.05)
        else:
            pytest.fail("the session host never listened")
    finally:
        stop.set()
        await asyncio.wait_for(task, 10)


@pytest.mark.parametrize(
    "change",
    ["missing_pin", "wrong_server_pin", "same_pins", "loose_key", "large_cert", "dirty_staging"],
)
async def test_untrusted_material_refuses_before_listening(config, mounted, pki, change):
    _server, _client_context, pin = pki
    if change == "missing_pin":
        (mounted / "caller.sha256").unlink()
    elif change == "wrong_server_pin":
        (mounted / "server.sha256").write_text(pin("other"))
    elif change == "same_pins":
        (mounted / "caller.sha256").write_text(pin("server"))
    elif change == "loose_key":
        (mounted / "tls.key").chmod(0o640)
    elif change == "large_cert":
        (mounted / "tls.crt").write_bytes(b"x" * (64 * 1024 + 1))
    elif change == "dirty_staging":
        _private_dir(config.staging_root)
        (config.staging_root / "left-over").write_text("x")
    with pytest.raises(Refused) as raised:
        await serve(config, asyncio.Event())
    assert str(mounted) not in str(raised.value)
    with socket.socket() as probe:  # nothing was left listening
        assert probe.connect_ex(("127.0.0.1", config.port)) != 0


def test_the_cli_starts_nothing_from_the_daemon_or_runner():
    """The host process imports neither the butler daemon nor the goal runner."""
    code = (
        "import sys\n"
        "from click.testing import CliRunner\n"
        "from butlers.cli import cli\n"
        "result = CliRunner().invoke(cli, ['session-host', '--help'])\n"
        "assert result.exit_code == 0, result.output\n"
        "assert '--pki-dir' in result.output and '--bind-host' in result.output\n"
        "import butlers.session_host.entrypoint\n"
        "leaked = [m for m in ('butlers.daemon', 'butlers.goal_runner') if m in sys.modules]\n"
        "assert not leaked, leaked\n"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, timeout=60)
    assert result.returncode == 0, result.stderr.decode()[-2000:]
