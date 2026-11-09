"""Codex auth retargeted to the session host's credential volume, dark (bu-kiuh8v.3).

Every credential here is a synthetic marker. Real mTLS handshakes use the
synthetic PKI in conftest.py. Assertions compare digests and markers, never print
a document, so a failure cannot echo one into the test output.
"""

from __future__ import annotations

import ast
import base64
import dataclasses
import hashlib
import inspect
import json
import stat
from pathlib import Path

import pytest

from butlers.cli_auth import persistence
from butlers.cli_auth.registry import PROVIDERS
from butlers.session_host.codex_auth import (
    SessionHostCodexAuth,
    collect_codex_rotation,
    push_codex_authority,
)
from butlers.session_host.contracts import Refused
from butlers.session_host.control import LocalControl, local_command
from butlers.session_host.credential_volume import CredentialVolume
from butlers.session_host.lifecycle import InvocationOwner

pytestmark = pytest.mark.unit

_REPO = Path(__file__).resolve().parents[2]


def codex_document(marker: str = "one") -> str:
    """A device-auth document of the audited Codex shape, with synthetic values."""
    return json.dumps(
        {
            "auth_mode": "chatgpt",
            "OPENAI_API_KEY": None,
            "tokens": {
                "id_token": f"SYNTHETIC-ID-{marker}",
                "access_token": f"SYNTHETIC-ACCESS-{marker}",
                "refresh_token": f"SYNTHETIC-REFRESH-{marker}",
                "account_id": "synthetic-account",
            },
            "last_refresh": "2026-11-09T00:00:00Z",
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def sha(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


@pytest.fixture
def volume(tmp_path) -> CredentialVolume:
    root = tmp_path / "credentials"
    root.mkdir(mode=0o700)
    root.chmod(0o700)
    return CredentialVolume(root)


# ── The volume ─────────────────────────────────────────────────────────────


def test_volume_installs_by_compare_and_set_and_reads_back(volume):
    first, second = codex_document("one"), codex_document("two")
    assert volume.status() is None and volume.read() is None
    assert volume.install(first.encode(), expected_sha256=None) is True
    assert volume.status() == sha(first)
    # A stale expectation loses, and the document is untouched.
    assert volume.install(second.encode(), expected_sha256=None) is False
    assert volume.install(second.encode(), expected_sha256=sha(second)) is False
    assert volume.status() == sha(first)
    assert volume.install(second.encode(), expected_sha256=sha(first)) is True
    document, digest = volume.read()
    assert digest == sha(second) and document == second.encode()
    path = volume.root / CredentialVolume.CODEX_AUTH
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert sorted(p.name for p in volume.root.iterdir()) == [CredentialVolume.CODEX_AUTH]


@pytest.mark.parametrize(
    "document",
    [b"", b"[]", b"{}", b"not json", b"\xff\xfe", b'{"a":1,"a":2}', b"{" + b" " * 70000 + b"}"],
)
def test_volume_refuses_a_document_that_is_not_one_small_json_object(volume, document):
    with pytest.raises(Refused, match="^invalid_credential_document$"):
        volume.install(document, expected_sha256=None)
    assert volume.status() is None


def test_volume_root_must_be_a_private_real_directory(tmp_path):
    loose = tmp_path / "loose"
    loose.mkdir(mode=0o755)
    loose.chmod(0o755)
    link = tmp_path / "link"
    link.symlink_to(loose)
    for root in (loose, link, tmp_path / "absent"):
        with pytest.raises(Refused, match="^credential_volume_unavailable$"):
            CredentialVolume(root)


def test_volume_never_follows_a_planted_link(volume, tmp_path):
    outside = tmp_path / "outside.json"
    outside.write_text(codex_document("outside"))
    (volume.root / CredentialVolume.CODEX_AUTH).symlink_to(outside)
    with pytest.raises(Refused, match="^credential_volume_unavailable$"):
        volume.read()
    with pytest.raises(Refused, match="^credential_volume_unavailable$"):
        volume.status()
    assert outside.read_text() == codex_document("outside")


# ── The control channel ────────────────────────────────────────────────────


@pytest.fixture
def control(tmp_path, pki, volume):
    stage = tmp_path / "stage"
    stage.mkdir(mode=0o700)
    stage.chmod(0o700)
    server, client, pin = pki

    def build(credentials=volume):
        return LocalControl(
            owner=InvocationOwner(stage),
            factory=lambda runtime, model: None,
            **server(),
            caller_sha256=pin("runner"),
            server_sha256=pin("server"),
            broker_origin="https://broker.test",
            credentials=credentials,
        )

    def sender(port, name="runner"):
        async def send(command):
            return await local_command(
                port,
                context=client(name),
                server_hostname="host.test",
                server_sha256=pin("server"),
                command=command,
            )

        return send

    return build, sender


async def test_the_runner_installs_and_reads_through_mtls(control, volume, caplog):
    build, sender = control
    host = build()
    port = await host.start()
    try:
        target = SessionHostCodexAuth(sender(port))
        assert await target.status() is None
        document = codex_document()
        assert await target.install(document, expected_sha256=None) is True
        assert await target.install(document, expected_sha256=None) is False  # stale CAS
        assert await target.status() == sha(document) == volume.status()
        assert await target.read() == (document, sha(document))
        assert "SYNTHETIC-" not in caplog.text
    finally:
        await host.close()


async def test_a_host_without_a_credential_volume_refuses_every_auth_operation(control):
    build, sender = control
    host = build(credentials=None)
    port = await host.start()
    try:
        target = SessionHostCodexAuth(sender(port))
        for call in (target.status(), target.read(), target.install(codex_document(), None)):
            with pytest.raises(Refused, match="^session_host_auth_unavailable$"):
                await call
    finally:
        await host.close()


async def test_another_caller_never_reaches_the_volume(control, volume):
    build, sender = control
    host = build()
    port = await host.start()
    try:
        volume.install(codex_document().encode(), expected_sha256=None)
        for name in ("other", "outsider"):
            target = SessionHostCodexAuth(sender(port, name))
            try:
                result = await target.read()
            except Exception:  # refused, or the handshake itself fails
                result = None
            assert result is None
        assert volume.status() == sha(codex_document())
    finally:
        await host.close()


@pytest.mark.parametrize(
    "command",
    [
        {"version": 1, "operation": "codex_auth_status", "extra": 1},
        {"version": 2, "operation": "codex_auth_read"},
        {"version": 1, "operation": "codex_auth_install", "document": "%%%"},
        {
            "version": 1,
            "operation": "codex_auth_install",
            "document": base64.b64encode(b"{}").decode(),
            "expected_sha256": None,
        },
        {
            "version": 1,
            "operation": "codex_auth_install",
            "document": base64.b64encode(codex_document().encode()).decode(),
            "expected_sha256": "not-a-digest",
        },
        {"version": 1, "operation": "codex_auth_delete"},
    ],
)
async def test_malformed_auth_commands_refuse_and_change_nothing(control, volume, command):
    build, sender = control
    host = build()
    port = await host.start()
    try:
        assert await sender(port)(command) == {"status": "refused"}
        assert volume.status() is None
    finally:
        await host.close()


@pytest.mark.parametrize(
    "reply",
    [
        {"status": "present"},
        {"status": "present", "sha256": "x" * 64},
        {"status": "absent", "sha256": "0" * 64},
        {"status": "refused"},
        {"status": "installed", "extra": True},
        ["not", "a", "dict"],
    ],
)
async def test_the_client_refuses_any_reply_outside_the_contract(reply):
    async def send(command):
        return reply

    target = SessionHostCodexAuth(send)
    for call in (target.status(), target.read(), target.install(codex_document(), None)):
        with pytest.raises(Refused, match="^session_host_auth_unavailable$"):
            await call


async def test_a_read_reply_must_match_its_own_digest():
    document = codex_document()

    async def send(command):
        encoded = base64.b64encode(document.encode()).decode()
        return {"status": "present", "document": encoded, "sha256": "0" * 64}

    with pytest.raises(Refused, match="^session_host_auth_unavailable$"):
        await SessionHostCodexAuth(send).read()


# ── Sync: authority to the volume, rotation back ───────────────────────────


class Authority:
    """The system-global Codex authority's two operations, with real CAS."""

    def __init__(self, value: str | None):
        self.value = value
        self.writes = 0

    async def load_codex_cli_auth(self) -> str | None:
        return self.value

    async def store_codex_cli_auth_if_unchanged(self, value, *, expected_value) -> bool:
        if self.value != expected_value:
            return False
        self.value, self.writes = value, self.writes + 1
        return True


class VolumeTarget:
    """The client's three calls served straight from a volume, without TLS."""

    def __init__(self, volume: CredentialVolume):
        self.volume = volume

    async def status(self):
        return self.volume.status()

    async def install(self, document, expected_sha256):
        return self.volume.install(document.encode(), expected_sha256=expected_sha256)

    async def read(self):
        found = self.volume.read()
        return None if found is None else (found[0].decode(), found[1])


async def test_push_installs_the_authority_and_is_idempotent(volume):
    authority, target = Authority(codex_document()), VolumeTarget(volume)
    first = await push_codex_authority(authority, target)
    assert first.known and first.installed_sha256 == sha(codex_document())
    again = await push_codex_authority(authority, target)
    assert again == first and volume.status() == sha(codex_document())


@pytest.mark.parametrize("value", [None, "", "[]", "not json"])
async def test_push_fails_closed_without_a_valid_authority(volume, value):
    volume.install(codex_document("stale").encode(), expected_sha256=None)
    snapshot = await push_codex_authority(Authority(value), VolumeTarget(volume))
    assert not snapshot.known
    # The volume keeps what it had: no blanking, no fallback.
    assert volume.status() == sha(codex_document("stale"))


async def test_push_fails_closed_when_the_volume_moves_under_it(volume):
    class Racing(VolumeTarget):
        async def install(self, document, expected_sha256):
            self.volume.install(codex_document("race").encode(), expected_sha256=expected_sha256)
            return await super().install(document, expected_sha256)

    snapshot = await push_codex_authority(Authority(codex_document()), Racing(volume))
    assert not snapshot.known and volume.status() == sha(codex_document("race"))


async def test_a_rotation_on_the_host_returns_to_the_authority_once(volume):
    authority, target = Authority(codex_document("one")), VolumeTarget(volume)
    snapshot = await push_codex_authority(authority, target)
    assert await collect_codex_rotation(authority, target, snapshot) is False  # unchanged
    volume.install(codex_document("rotated").encode(), expected_sha256=snapshot.installed_sha256)
    assert await collect_codex_rotation(authority, target, snapshot) is True
    assert authority.value == codex_document("rotated") and authority.writes == 1


async def test_a_rotation_loses_to_a_newer_dashboard_login(volume):
    authority, target = Authority(codex_document("one")), VolumeTarget(volume)
    snapshot = await push_codex_authority(authority, target)
    volume.install(codex_document("rotated").encode(), expected_sha256=snapshot.installed_sha256)
    authority.value = codex_document("new-login")
    assert await collect_codex_rotation(authority, target, snapshot) is False
    assert authority.value == codex_document("new-login")


async def test_an_uncollected_rotation_is_flushed_never_rolled_back(volume):
    """Review r4511269508: push V1, rotate on the host, skip collect, push again."""
    authority, target = Authority(codex_document("one")), VolumeTarget(volume)
    first = await push_codex_authority(authority, target)
    volume.install(codex_document("rotated").encode(), expected_sha256=first.installed_sha256)
    second = await push_codex_authority(authority, target, first)
    # The single-use refresh token on the host survives, and the authority has it.
    assert volume.status() == sha(codex_document("rotated"))
    assert authority.value == codex_document("rotated") and authority.writes == 1
    assert second.known and second.installed_sha256 == sha(codex_document("rotated"))
    # The new fence is coherent: nothing left to collect, and the next push is a no-op.
    assert await collect_codex_rotation(authority, target, second) is False
    assert await push_codex_authority(authority, target, second) == second


async def test_a_newer_login_still_replaces_an_uncollected_rotation(volume):
    authority, target = Authority(codex_document("one")), VolumeTarget(volume)
    first = await push_codex_authority(authority, target)
    volume.install(codex_document("rotated").encode(), expected_sha256=first.installed_sha256)
    authority.value = codex_document("new-login")
    second = await push_codex_authority(authority, target, first)
    assert second.known and volume.status() == sha(codex_document("new-login"))
    assert authority.value == codex_document("new-login") and authority.writes == 0


async def test_a_newer_login_replaces_an_unchanged_host_document(volume):
    authority, target = Authority(codex_document("one")), VolumeTarget(volume)
    first = await push_codex_authority(authority, target)
    authority.value = codex_document("new-login")
    second = await push_codex_authority(authority, target, first)
    assert second.known and volume.status() == sha(codex_document("new-login"))


@pytest.mark.parametrize("fence", ["none", "unknown"])
async def test_an_unfenced_host_document_fails_closed(volume, fence):
    authority, target = Authority(codex_document("one")), VolumeTarget(volume)
    volume.install(codex_document("unknown").encode(), expected_sha256=None)
    previous = None if fence == "none" else await push_codex_authority(Authority(None), target)
    pushed = await push_codex_authority(authority, target, previous)
    assert not pushed.known
    assert volume.status() == sha(codex_document("unknown"))
    assert authority.value == codex_document("one") and authority.writes == 0


async def test_a_flush_refuses_a_host_that_moves_between_status_and_read(volume):
    class Moving(VolumeTarget):
        async def read(self):
            found = await super().read()
            self.volume.install(codex_document("moved").encode(), expected_sha256=found[1])
            return await super().read()

    authority, target = Authority(codex_document("one")), VolumeTarget(volume)
    first = await push_codex_authority(authority, target)
    volume.install(codex_document("rotated").encode(), expected_sha256=first.installed_sha256)
    assert not (await push_codex_authority(authority, Moving(volume), first)).known
    assert authority.value == codex_document("one") and authority.writes == 0


async def test_the_fence_keeps_credential_material_out_of_repr(volume):
    authority = Authority(codex_document("repr-marker"))
    pushed = await push_codex_authority(authority, VolumeTarget(volume))
    assert pushed.known and "repr-marker" not in repr(pushed)


def test_a_volume_sweeps_temporaries_an_interrupted_install_left(tmp_path):
    root = tmp_path / "sweep"
    root.mkdir(mode=0o700)
    root.chmod(0o700)
    (root / ".codex-auth.json.1.2.tmp").write_bytes(b"partial")
    (root / CredentialVolume.CODEX_AUTH).write_text(codex_document())
    volume = CredentialVolume(root)
    assert sorted(p.name for p in root.iterdir()) == [CredentialVolume.CODEX_AUTH]
    assert volume.status() == sha(codex_document())


async def test_no_rotation_is_collected_without_a_known_snapshot(volume):
    authority, target = Authority(None), VolumeTarget(volume)
    snapshot = await push_codex_authority(authority, target)
    volume.install(codex_document("planted").encode(), expected_sha256=None)
    assert await collect_codex_rotation(authority, target, snapshot) is False
    assert authority.value is None and authority.writes == 0


# ── Dashboard login: off keeps today's file, on targets the session host ──


class LoginAuthority(Authority):
    def require_system_global_pool(self):
        return object()


def codex_provider(tmp_path):
    return dataclasses.replace(PROVIDERS["codex"], token_path=tmp_path / "home" / "auth.json")


async def test_login_with_the_session_host_off_writes_todays_local_file(tmp_path, volume):
    provider, authority = codex_provider(tmp_path), LoginAuthority(None)
    ok = await persistence.persist_validated_staged_device_auth_bytes(
        provider,
        authority,  # type: ignore[arg-type]
        codex_document().encode(),
        expected_authority_value=None,
        codex_authority=authority,  # type: ignore[arg-type]
    )
    assert ok and authority.value == codex_document()
    assert sha(provider.token_path.read_text()) == sha(codex_document())
    assert volume.status() is None


async def test_login_with_the_session_host_on_targets_its_volume_only(tmp_path, volume):
    provider, authority = codex_provider(tmp_path), LoginAuthority(None)
    volume.install(codex_document("previous").encode(), expected_sha256=None)
    ok = await persistence.persist_validated_staged_device_auth_bytes(
        provider,
        authority,  # type: ignore[arg-type]
        codex_document().encode(),
        expected_authority_value=None,
        codex_authority=authority,  # type: ignore[arg-type]
        session_host=VolumeTarget(volume),
    )
    assert ok and authority.value == codex_document()
    assert volume.status() == sha(codex_document())
    # butlers-up's runtime file is not the target any more.
    assert not provider.token_path.exists()


async def test_login_reports_failure_when_the_session_host_cannot_take_it(tmp_path, volume):
    class Down(VolumeTarget):
        async def status(self):
            raise Refused("session_host_auth_unavailable")

    provider, authority = codex_provider(tmp_path), LoginAuthority(None)
    ok = await persistence.persist_validated_staged_device_auth_bytes(
        provider,
        authority,  # type: ignore[arg-type]
        codex_document().encode(),
        expected_authority_value=None,
        codex_authority=authority,  # type: ignore[arg-type]
        session_host=Down(volume),
    )
    assert ok is False and not provider.token_path.exists()


async def test_the_session_host_target_is_codex_only(tmp_path, volume):
    provider = dataclasses.replace(
        PROVIDERS["opencode-openai"], token_path=tmp_path / "opencode" / "auth.json"
    )
    with pytest.raises(ValueError, match="Codex"):
        await persistence.persist_validated_staged_device_auth_bytes(
            provider,
            LoginAuthority(None),  # type: ignore[arg-type]
            b"{}",
            expected_authority_value=None,
            session_host=VolumeTarget(volume),
        )


async def test_login_over_real_mtls_lands_in_the_volume(tmp_path, control, volume):
    build, sender = control
    host = build()
    port = await host.start()
    try:
        provider, authority = codex_provider(tmp_path), LoginAuthority(None)
        ok = await persistence.persist_validated_staged_device_auth_bytes(
            provider,
            authority,  # type: ignore[arg-type]
            codex_document().encode(),
            expected_authority_value=None,
            codex_authority=authority,  # type: ignore[arg-type]
            session_host=SessionHostCodexAuth(sender(port)),
        )
        assert ok and volume.status() == sha(codex_document())
        assert not provider.token_path.exists()
    finally:
        await host.close()


# ── Default off: nothing live constructs a target ──────────────────────────


def test_the_live_login_paths_default_to_todays_behavior():
    from butlers.api.routers import cli_auth

    for function in (
        persistence.persist_validated_staged_device_auth_bytes,
        cli_auth._DeviceAuthAuthorityPersistence.__init__,
        cli_auth._build_on_success,
    ):
        parameter = inspect.signature(function).parameters["session_host"]
        assert parameter.default is None and parameter.kind is parameter.KEYWORD_ONLY
    persistence_object = cli_auth._DeviceAuthAuthorityPersistence(object())  # type: ignore[arg-type]
    assert persistence_object._session_host is None


def test_no_live_module_constructs_a_session_host_auth_target():
    """While the session host is dark, only its own package and tests build one."""
    assert _live_constructions(_REPO / "src" / "butlers") == []


_TARGETS = {
    "SessionHostCodexAuth",
    "CredentialVolume",
    # The control plane's client side (bu-kiuh8v.11): a channel to the host, and
    # the factory and fence that build one. Calling any of them is the switch.
    "ControlSessionHost",
    "CodexAuthFencedHost",
    "session_host_clients",
}
# The one control-plane module that defines the factory, and so builds targets.
_FACTORY_MODULE = Path("goal_runner") / "session_host_client.py"


def _live_constructions(root: Path) -> list[str]:
    """Calls outside ``session_host/`` that build a target or arm LocalControl.

    Follows ``from … import X as Y`` aliases, and counts ``LocalControl(...,
    credentials=...)`` since that serves the credential operations.
    """
    found = []
    for path in root.rglob("*.py"):
        relative = path.relative_to(root)
        if "session_host" in relative.parts or relative == _FACTORY_MODULE:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        names, controls = set(_TARGETS), {"LocalControl"}
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    if alias.name in _TARGETS:
                        names.add(alias.asname or alias.name)
                    elif alias.name == "LocalControl":
                        controls.add(alias.asname or alias.name)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
                armed = name in controls and any(
                    k.arg in ("credentials", None) for k in node.keywords
                )
                if name in names or armed:
                    found.append(f"{path.relative_to(root)}:{node.lineno}")
    return found


@pytest.mark.parametrize(
    "source",
    [
        "from butlers.session_host.codex_auth import SessionHostCodexAuth\n"
        "SessionHostCodexAuth(send)\n",
        "from butlers.session_host.codex_auth import SessionHostCodexAuth as Host\nHost(send)\n",
        "import butlers.session_host.credential_volume as v\nv.CredentialVolume(root)\n",
        "from butlers.session_host.control import LocalControl as C\nC(credentials=volume)\n",
        "from butlers.session_host.control import LocalControl\nLocalControl(**options)\n",
        "from butlers.goal_runner.session_host_client import session_host_clients\n"
        "session_host_clients(config, credentials, broker_origin=o)\n",
        "from butlers.goal_runner.session_host_client import CodexAuthFencedHost as F\n"
        "F(host, authority, target, runtimes={'codex'})\n",
        "from butlers.goal_runner.staging import ControlSessionHost\n"
        "ControlSessionHost(send, credentials, broker_origin=o)\n",
    ],
)
def test_the_construction_scan_catches_each_shape(tmp_path, source):
    (tmp_path / "live.py").write_text(source)
    assert _live_constructions(tmp_path) == ["live.py:2"]


def test_the_construction_scan_ignores_an_unarmed_control(tmp_path):
    source = "from butlers.session_host.control import LocalControl\nLocalControl(owner=o)\n"
    (tmp_path / "live.py").write_text(source)
    assert _live_constructions(tmp_path) == []
