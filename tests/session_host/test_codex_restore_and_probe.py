"""Startup restore and the dashboard health probe through the credential volume, dark.

bu-kiuh8v.14. With a session-host target, ``restore_tokens`` pushes the Codex
authority to the volume instead of writing ``~/.codex/auth.json``, and the Codex
health probe checks the volume's document instead of the local file. Every live
caller passes no target, so today's behavior is unchanged. All credentials are
synthetic markers.
"""

from __future__ import annotations

import base64
import dataclasses
import inspect
import json
import time

import pytest

from butlers.cli_auth import health, persistence
from butlers.cli_auth.registry import PROVIDERS
from butlers.session_host.credential_volume import CredentialVolume
from tests.session_host.test_codex_auth import Authority, VolumeTarget, codex_document, sha

pytestmark = pytest.mark.unit


@pytest.fixture
def volume(tmp_path) -> CredentialVolume:
    root = tmp_path / "credentials"
    root.mkdir(mode=0o700)
    root.chmod(0o700)
    return CredentialVolume(root)


@pytest.fixture
def local_codex(tmp_path, monkeypatch):
    """Point the Codex provider's local file somewhere it can be watched."""
    path = tmp_path / "home" / ".codex" / "auth.json"
    monkeypatch.setitem(
        PROVIDERS, "codex", dataclasses.replace(PROVIDERS["codex"], token_path=path)
    )
    return path


class Store:
    """The credential store for the non-Codex providers: nothing stored."""

    async def load(self, key: str) -> str | None:
        return None


def _jwt(expires_in: int) -> str:
    def part(value: dict) -> str:
        raw = json.dumps(value).encode()
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    return f"{part({'alg': 'none'})}.{part({'exp': int(time.time()) + expires_in})}.sig"


def live_document(marker: str = "one") -> str:
    """A device-auth document whose access token is an unexpired JWT."""
    document = json.loads(codex_document(marker))
    document["tokens"]["access_token"] = _jwt(3600)
    return json.dumps(document, sort_keys=True, separators=(",", ":"))


# ── Restore ────────────────────────────────────────────────────────────────


async def test_restore_with_a_target_pushes_to_the_volume_not_the_local_file(volume, local_codex):
    authority = Authority(codex_document())
    results = await persistence.restore_tokens(
        Store(),  # type: ignore[arg-type]
        codex_authority=authority,  # type: ignore[arg-type]
        session_host=VolumeTarget(volume),
    )
    assert results["codex"] is True
    assert volume.status() == sha(codex_document())
    assert not local_codex.exists()


async def test_restore_with_a_target_fails_closed_and_leaves_the_volume(volume, local_codex):
    volume.install(codex_document("held").encode(), expected_sha256=None)
    for authority in (Authority(None), Authority("not json"), None):
        results = await persistence.restore_tokens(
            Store(),  # type: ignore[arg-type]
            codex_authority=authority,  # type: ignore[arg-type]
            session_host=VolumeTarget(volume),
        )
        assert results["codex"] is False
    assert volume.status() == sha(codex_document("held"))
    assert not local_codex.exists()


async def test_restore_never_overwrites_an_unfenced_host_document(volume, local_codex):
    """After a control-plane restart there is no fence: a held rotation is not judged."""
    volume.install(codex_document("rotated").encode(), expected_sha256=None)
    results = await persistence.restore_tokens(
        Store(),  # type: ignore[arg-type]
        codex_authority=Authority(codex_document("one")),  # type: ignore[arg-type]
        session_host=VolumeTarget(volume),
    )
    assert results["codex"] is False
    assert volume.status() == sha(codex_document("rotated"))
    assert not local_codex.exists()


# ── Health probe ───────────────────────────────────────────────────────────


@pytest.fixture
def backend(monkeypatch):
    """The Codex backend probe: records the token it was given, accepts it."""
    seen: list[str] = []

    async def probe(access_token: str):
        seen.append(access_token)
        return False, None

    monkeypatch.setattr(health, "_probe_codex_backend", probe)
    return seen


async def test_the_probe_checks_the_volume_document_when_it_matches_the_authority(
    volume, local_codex, backend
):
    document = live_document()
    volume.install(document.encode(), expected_sha256=None)
    result = await health.probe_provider(
        PROVIDERS["codex"],
        codex_authority=Authority(document),  # type: ignore[arg-type]
        session_host=VolumeTarget(volume),
    )
    assert result.state is health.AuthHealthState.authenticated
    assert backend == [json.loads(document)["tokens"]["access_token"]]
    assert not local_codex.exists()  # the local file is neither read nor reconciled


@pytest.mark.parametrize("held", [None, "other"])
async def test_the_probe_is_not_run_when_the_volume_is_not_the_authority(
    volume, local_codex, backend, held
):
    if held is not None:
        volume.install(live_document(held).encode(), expected_sha256=None)
    result = await health.probe_provider(
        PROVIDERS["codex"],
        codex_authority=Authority(live_document()),  # type: ignore[arg-type]
        session_host=VolumeTarget(volume),
    )
    assert result.state is health.AuthHealthState.probe_failed
    assert backend == []
    # Parent-only health never rotates or installs: the volume is untouched.
    assert volume.status() == (None if held is None else sha(live_document(held)))


async def test_the_prepared_probe_rechecks_the_volume(volume, local_codex, backend):
    document = live_document()
    volume.install(document.encode(), expected_sha256=None)
    target = VolumeTarget(volume)
    ok = await health.probe_provider(
        PROVIDERS["codex"],
        prepared_codex_authority=document,
        codex_authority_prepared=True,
        session_host=target,
    )
    assert ok.state is health.AuthHealthState.authenticated
    volume.install(live_document("moved").encode(), expected_sha256=sha(document))
    moved = await health.probe_provider(
        PROVIDERS["codex"],
        prepared_codex_authority=document,
        codex_authority_prepared=True,
        session_host=target,
    )
    assert moved.state is health.AuthHealthState.probe_failed and len(backend) == 1


async def test_an_unreachable_host_fails_the_probe_without_detail(local_codex, backend):
    class Down:
        async def status(self):
            raise OSError("SYNTHETIC-DETAIL")

        async def install(self, document, expected_sha256):
            raise OSError("SYNTHETIC-DETAIL")

        async def read(self):
            raise OSError("SYNTHETIC-DETAIL")

    result = await health.probe_provider(
        PROVIDERS["codex"],
        codex_authority=Authority(live_document()),  # type: ignore[arg-type]
        session_host=Down(),
    )
    assert result.state is health.AuthHealthState.probe_failed
    assert "SYNTHETIC" not in (result.detail or "") and backend == []


async def test_probe_all_hands_the_target_to_codex_only(monkeypatch):
    calls = {}

    async def probe(provider, credential_store=None, **kwargs):
        calls[provider.name] = kwargs
        return health.AuthHealthResult(provider.name, health.AuthHealthState.unavailable)

    monkeypatch.setattr(health, "probe_provider", probe)
    target = object()
    await health.probe_all(codex_authority=None, session_host=target)  # type: ignore[arg-type]
    assert calls["codex"]["session_host"] is target
    assert all(
        kwargs.get("session_host") is None for name, kwargs in calls.items() if name != "codex"
    )


# ── Off by default ─────────────────────────────────────────────────────────


def test_the_live_restore_and_probe_paths_default_to_todays_behavior():
    for function in (persistence.restore_tokens, health.probe_provider, health.probe_all):
        parameter = inspect.signature(function).parameters["session_host"]
        assert parameter.default is None and parameter.kind is parameter.KEYWORD_ONLY
