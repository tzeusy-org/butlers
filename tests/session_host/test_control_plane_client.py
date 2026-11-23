"""The control plane's session-host client factory and Codex auth fencing, dark (bu-kiuh8v.11).

Real mTLS handshakes use the synthetic PKI in conftest.py; every credential is a
synthetic marker. Nothing here constructs a client on a live path.
"""

from __future__ import annotations

import asyncio
import dataclasses
import shutil
import stat
from pathlib import Path
from uuid import uuid4

import pytest

from butlers.goal_runner.contracts import HostOutcome, Launch
from butlers.goal_runner.session_host_client import (
    CodexAuthFencedHost,
    SessionHostClientConfig,
    session_host_clients,
)
from butlers.goal_runner.staging import (
    ControlSessionHost,
    CredentialUnavailable,
    ModelCredentials,
)
from butlers.session_host.codex_auth import SessionHostCodexAuth
from butlers.session_host.contracts import Refused
from butlers.session_host.control import LocalControl
from butlers.session_host.credential_volume import CredentialVolume
from butlers.session_host.lifecycle import InvocationOwner
from tests.session_host.test_codex_auth import Authority, VolumeTarget, codex_document, sha

pytestmark = pytest.mark.unit

_ORIGIN = "https://broker.test"


@pytest.fixture
def volume(tmp_path) -> CredentialVolume:
    root = tmp_path / "credentials"
    root.mkdir(mode=0o700)
    root.chmod(0o700)
    return CredentialVolume(root)


def _mount(tmp_path: Path) -> Path:
    mount = tmp_path / "pki"
    mount.mkdir(mode=0o700, exist_ok=True)
    return mount


def _config(source: Path, mount: Path, pin, **overrides) -> SessionHostClientConfig:
    """The runner's PKI as a deployment mounts it: CA, leaf and a private key file."""
    for name, target in (
        ("ca.crt", "ca.crt"),
        ("runner.crt", "tls.crt"),
        ("runner.key", "tls.key"),
    ):
        shutil.copyfile(source / name, mount / target)
    (mount / "tls.key").chmod(0o600)
    options: dict = dict(
        host="127.0.0.1",
        port=1,
        server_hostname="host.test",
        server_sha256=pin("server"),
        ca_path=mount / "ca.crt",
        certificate_path=mount / "tls.crt",
        key_path=mount / "tls.key",
    )
    options.update(overrides)
    return SessionHostClientConfig(**options)


@pytest.fixture
def host(tmp_path, pki, volume):
    stage = tmp_path / "stage"
    stage.mkdir(mode=0o700)
    stage.chmod(0o700)
    server, _client, pin = pki
    return LocalControl(
        owner=InvocationOwner(stage),
        factory=lambda runtime, model: None,
        **server(),
        caller_sha256=pin("runner"),
        server_sha256=pin("server"),
        broker_origin=_ORIGIN,
        credentials=volume,
    )


class Store:
    async def load(self, key: str) -> str | None:
        return "sk-synthetic"


# ── The factory: mounted PKI to one pinned channel ─────────────────────────


async def test_the_factory_reaches_the_host_through_the_mounted_pki(
    host, volume, pki, pki_root, tmp_path
):
    _server, _client, pin = pki
    port = await host.start()
    try:
        config = _config(pki_root, _mount(tmp_path), pin, port=port)
        clients = session_host_clients(config, ModelCredentials(Store()), broker_origin=_ORIGIN)
        assert isinstance(clients.host, ControlSessionHost)
        assert isinstance(clients.codex_auth, SessionHostCodexAuth)
        document = codex_document()
        assert await clients.codex_auth.install(document, expected_sha256=None) is True
        assert volume.status() == sha(document) == await clients.codex_auth.status()
        await clients.host.terminate(uuid4())  # revoke of an unknown id: answered, not raised
    finally:
        await host.close()


async def test_a_secret_mount_of_links_is_followed(host, volume, pki, pki_root, tmp_path):
    """Kubernetes mounts a secret's keys as links into a ``..data`` directory."""
    _server, _client, pin = pki
    data = _mount(tmp_path) / "..data"
    data.mkdir(mode=0o700)
    config = _config(pki_root, data, pin)
    linked = tmp_path / "linked"
    linked.mkdir(mode=0o700)
    for name in ("ca.crt", "tls.crt", "tls.key"):
        (linked / name).symlink_to(data / name)
    port = await host.start()
    try:
        config = dataclasses.replace(
            config,
            port=port,
            ca_path=linked / "ca.crt",
            certificate_path=linked / "tls.crt",
            key_path=linked / "tls.key",
        )
        clients = session_host_clients(config, ModelCredentials(Store()), broker_origin=_ORIGIN)
        assert await clients.codex_auth.status() is None
    finally:
        await host.close()


async def test_a_wrong_server_pin_never_sends_a_command(host, volume, pki, pki_root, tmp_path):
    _server, _client, pin = pki
    port = await host.start()
    try:
        config = _config(pki_root, _mount(tmp_path), pin, port=port, server_sha256=pin("other"))
        clients = session_host_clients(config, ModelCredentials(Store()), broker_origin=_ORIGIN)
        with pytest.raises(Exception):  # noqa: B017 - refused at the pin, or the handshake
            await clients.codex_auth.install(codex_document(), expected_sha256=None)
        assert volume.status() is None
    finally:
        await host.close()


@pytest.mark.parametrize(
    "change",
    ["bad_pin", "own_pin", "missing_key", "loose_key", "directory_ca", "large_ca", "port", "host"],
)
def test_untrusted_configuration_refuses_with_a_fixed_code(pki, pki_root, tmp_path, change):
    _server, _client, pin = pki
    mount = _mount(tmp_path)
    config = _config(pki_root, mount, pin)
    overrides: dict = {}
    if change == "bad_pin":
        overrides["server_sha256"] = "z" * 64
    elif change == "own_pin":
        overrides["server_sha256"] = pin("runner")  # the host must not be the caller
    elif change == "missing_key":
        (mount / "tls.key").unlink()
    elif change == "loose_key":
        (mount / "tls.key").chmod(0o644)
    elif change == "directory_ca":
        (mount / "ca.crt").unlink()
        (mount / "ca.crt").mkdir()
    elif change == "large_ca":
        (mount / "ca.crt").write_bytes(b"x" * (64 * 1024 + 1))
    elif change == "port":
        overrides["port"] = 0
    elif change == "host":
        overrides["host"] = "host.test/../x"
    if overrides:
        config = dataclasses.replace(config, **overrides)
    with pytest.raises(Refused, match="^session_host_client_unavailable$") as raised:
        session_host_clients(config, ModelCredentials(Store()), broker_origin=_ORIGIN)
    assert str(mount) not in str(raised.value)
    assert stat.S_IMODE((mount).stat().st_mode) == 0o700


# ── Codex OAuth launches: push before, collect after ───────────────────────


class Inner:
    """The wrapped host: records what the volume held at launch, and may rotate it."""

    def __init__(self, volume: CredentialVolume, rotate_to: str | None = None):
        self.volume, self.rotate_to = volume, rotate_to
        self.seen: list[str | None] = []
        self.running = self.peak = 0
        self.terminated = []

    async def launch(self, launch: Launch) -> HostOutcome:
        self.running += 1
        self.peak = max(self.peak, self.running)
        try:
            self.seen.append(self.volume.status())
            await asyncio.sleep(0.01)
            if self.rotate_to is not None:
                self.volume.install(self.rotate_to.encode(), expected_sha256=self.volume.status())
            return HostOutcome(failure=RuntimeError("runtime_failed"))
        finally:
            self.running -= 1

    async def terminate(self, invocation_id):
        self.terminated.append(invocation_id)


def launch(runtime: str = "codex") -> Launch:
    return Launch(uuid4(), uuid4(), "t" * 43, runtime, "gpt-6-sol", "prompt", 30)


async def test_an_oauth_launch_runs_on_the_authority_and_its_rotation_comes_back(volume):
    authority = Authority(codex_document("one"))
    inner = Inner(volume, rotate_to=codex_document("rotated"))
    host = CodexAuthFencedHost(inner, authority, VolumeTarget(volume), runtimes={"codex"})
    outcome = await host.launch(launch())
    # A failed run may still have rotated its refresh token: it is collected anyway.
    assert str(outcome.failure) == "runtime_failed"
    assert inner.seen == [sha(codex_document("one"))]
    assert authority.value == codex_document("rotated") and authority.writes == 1
    # The next launch runs on the rotated document, and pushes nothing new.
    inner.rotate_to = None
    await host.launch(launch())
    assert inner.seen[-1] == sha(codex_document("rotated")) and authority.writes == 1


async def test_a_rotation_nobody_collected_is_flushed_before_the_next_launch(volume):
    authority = Authority(codex_document("one"))
    inner = Inner(volume)
    host = CodexAuthFencedHost(inner, authority, VolumeTarget(volume), runtimes={"codex"})
    await host.launch(launch())
    # A rotation lands after collect (a lost reply, a cancelled collect).
    volume.install(codex_document("late").encode(), expected_sha256=volume.status())
    await host.launch(launch())
    assert authority.value == codex_document("late")
    assert inner.seen[-1] == sha(codex_document("late"))


async def test_a_newer_login_wins_over_a_rotation(volume):
    authority = Authority(codex_document("one"))
    inner = Inner(volume, rotate_to=codex_document("rotated"))
    host = CodexAuthFencedHost(inner, authority, VolumeTarget(volume), runtimes={"codex"})

    async def login_mid_run(launch_):
        authority.value = codex_document("login")
        return await Inner.launch(inner, launch_)

    inner.launch = login_mid_run  # type: ignore[method-assign]
    await host.launch(launch())
    assert authority.value == codex_document("login")
    inner.launch = Inner.launch.__get__(inner)  # type: ignore[method-assign]
    inner.rotate_to = None
    await host.launch(launch())
    assert inner.seen[-1] == sha(codex_document("login"))


@pytest.mark.parametrize("value", [None, "not json"])
async def test_no_valid_authority_refuses_the_launch_before_the_host(volume, value):
    inner = Inner(volume)
    host = CodexAuthFencedHost(inner, Authority(value), VolumeTarget(volume), runtimes={"codex"})
    with pytest.raises(CredentialUnavailable, match="^codex_auth_unavailable$"):
        await host.launch(launch())
    assert inner.seen == []


async def test_a_black_holed_host_refuses_the_launch_within_the_bound(
    monkeypatch, volume, pki, pki_root, tmp_path
):
    """A host IP that never answers SYN refuses with the fixed code, and frees the lock."""
    from butlers.session_host import control as control_module

    _server, _client, pin = pki

    async def black_hole(*args, **kwargs):
        await asyncio.Event().wait()

    monkeypatch.setattr(control_module, "_IO_SECONDS", 0.2)
    monkeypatch.setattr(control_module.asyncio, "open_connection", black_hole)
    config = _config(pki_root, _mount(tmp_path), pin, host="192.0.2.1", port=41198)
    clients = session_host_clients(config, ModelCredentials(Store()), broker_origin=_ORIGIN)
    inner = Inner(volume)
    host = CodexAuthFencedHost(
        inner, Authority(codex_document("one")), clients.codex_auth, runtimes={"codex"}
    )
    task = asyncio.create_task(host.launch(launch()))
    done, _pending = await asyncio.wait({task}, timeout=3)
    if not done:
        task.cancel()
        pytest.fail("the launch waited on an unbounded connect")
    assert isinstance(task.exception(), CredentialUnavailable)
    assert str(task.exception()) == "codex_auth_unavailable" and inner.seen == []


async def test_other_runtimes_pass_straight_through(volume):
    authority = Authority(None)  # would refuse a codex launch
    inner = Inner(volume)
    host = CodexAuthFencedHost(inner, authority, VolumeTarget(volume), runtimes={"codex"})
    await host.launch(launch("opencode"))
    assert inner.seen == [None] and authority.writes == 0
    invocation = uuid4()
    await host.terminate(invocation)
    assert inner.terminated == [invocation]


async def test_other_runtimes_are_not_held_behind_codex_launches(volume):
    inner = Inner(volume)
    authority = Authority(codex_document("one"))
    host = CodexAuthFencedHost(inner, authority, VolumeTarget(volume), runtimes={"codex"})
    await asyncio.gather(*(host.launch(launch()) for _ in range(3)), host.launch(launch("x")))
    assert len(inner.seen) == 4 and inner.peak == 2  # one codex run beside the other runtime


async def test_codex_oauth_launches_run_one_at_a_time(volume):
    """Refresh tokens are single-use: two runs on one document would spend it twice."""
    inner = Inner(volume)
    host = CodexAuthFencedHost(
        inner, Authority(codex_document()), VolumeTarget(volume), runtimes={"codex"}
    )
    await asyncio.gather(*(host.launch(launch()) for _ in range(3)))
    assert inner.peak == 1


def test_the_fenced_runtimes_are_named_explicitly(volume):
    with pytest.raises(TypeError):
        CodexAuthFencedHost(Inner(volume), Authority(None), VolumeTarget(volume))  # type: ignore[call-arg]
