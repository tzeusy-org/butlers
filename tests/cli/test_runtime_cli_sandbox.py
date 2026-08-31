"""Security contracts for Dashboard CLI-auth child isolation."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import shutil
import stat
import subprocess
import threading
import time
from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.cli_auth.registry import PROVIDERS

_REPO_ROOT = Path(__file__).resolve().parents[2]
_EXACT_IMAGE_HARNESS = _REPO_ROOT / "tests" / "cli" / "runtime_cli_sandbox_exact_image_harness.py"
_BASE_IMAGE_BUILD_INPUTS = (
    "Dockerfile.base",
    "scripts/runtime_cli_sandbox_init.c",
    "scripts/generate_runtime_cli_sandbox_manifest.py",
)


def _make_stage_output(stage_home: Path) -> tuple[Path, Path]:
    """Create the ordinary private staged output layout without credential content."""
    stage_home.mkdir()
    os.chmod(stage_home, 0o700)
    output = stage_home / ".local" / "share" / "opencode" / "auth.json"
    output.parent.mkdir(parents=True)
    for directory in (stage_home / ".local", stage_home / ".local" / "share", output.parent):
        os.chmod(directory, 0o700)
    output.write_text('{"openai":{"type":"oauth"}}', encoding="utf-8")
    os.chmod(output, 0o600)
    return stage_home, output


def _base_input_fingerprint() -> str:
    """Match compose.sh's receipt for base-image inputs without reading dotenv state."""
    payload = bytearray()
    for relative_path in _BASE_IMAGE_BUILD_INPUTS:
        digest = hashlib.sha256((_REPO_ROOT / relative_path).read_bytes()).hexdigest()
        payload.extend(relative_path.encode())
        payload.extend(b"\0")
        payload.extend(digest.encode())
        payload.extend(b"\0")
    return hashlib.sha256(payload).hexdigest()


def _is_explicit_exact_image_reference(image: str) -> bool:
    """Reject Docker's implicit or explicit mutable latest tag."""
    named_reference = image.split("@", 1)[0]
    terminal_component = named_reference.rsplit("/", 1)[-1]
    if terminal_component.endswith(":latest"):
        return False
    return "@sha256:" in image or ":" in terminal_component


@pytest.mark.parametrize(
    ("provider_name", "expected_key", "expected_entry"),
    [
        (
            "opencode-openai",
            "openai",
            {
                "type": "oauth",
                "refresh": "openai-refresh-only",
                "access": "openai-access-only",
                "expires": 1_700_000_000,
                "accountId": "account-only",
                "enterpriseUrl": "https://enterprise.example.test",
            },
        ),
        ("opencode-go", "opencode-go", {"type": "api", "key": "go-only"}),
    ],
)
def test_shared_opencode_authority_is_projected_to_the_calling_provider(
    tmp_path: Path,
    provider_name: str,
    expected_key: str,
    expected_entry: dict[str, object],
) -> None:
    """REQ-core-credentials-002: a shared auth file never leaks a peer authority."""
    from butlers.cli_auth.sandbox import load_validated_readonly_authority

    peer_sentinel = "PEER-OPENCODE-AUTHORITY-MUST-NOT-REACH-CHILD"
    canonical = tmp_path / ".local" / "share" / "opencode" / "auth.json"
    canonical.parent.mkdir(parents=True)
    entries = {
        "openai": {
            "type": "oauth",
            "refresh": peer_sentinel,
            "access": peer_sentinel,
            "expires": 1_700_000_000,
        },
        "opencode-go": {"type": "api", "key": peer_sentinel},
    }
    entries[expected_key] = expected_entry
    canonical.write_text(
        json.dumps(
            {
                **entries,
                "unrelated": {"token": peer_sentinel},
            }
        ),
        encoding="utf-8",
    )
    os.chmod(canonical, 0o600)

    authority = load_validated_readonly_authority(
        replace(PROVIDERS[provider_name], token_path=canonical)
    )

    assert authority is not None
    assert json.loads(authority.content) == {expected_key: expected_entry}
    assert peer_sentinel.encode() not in authority.content


@pytest.mark.parametrize(
    ("provider_name", "document"),
    [
        ("opencode-openai", b'{"opencode-go":{"type":"api","key":"go-only"}}'),
        ("opencode-go", b'{"openai":{"type":"oauth","refresh":"openai-only"}}'),
        ("opencode-openai", b'{"openai":{"type":"api"}}'),
        (
            "opencode-openai",
            b'{"openai":{"type":"oauth","refresh":"refresh-only","expires":1}}',
        ),
        ("opencode-go", b'{"opencode-go":{"type":"api"}}'),
        (
            "opencode-go",
            b'{"opencode-go":{"type":"api","key":"go-only","key":"duplicate"}}',
        ),
        (
            "opencode-openai",
            b'{"openai":{"type":"oauth","refresh":"refresh","access":"access",'
            b'"expires":1,"unexpected":{"sentinel":"must-not-reach-child"}}}',
        ),
        (
            "opencode-go",
            b'{"opencode-go":{"type":"api","key":"go-only","metadata":"must-not-reach-child"}}',
        ),
        (
            "opencode-openai",
            b'{"openai":{"type":"oauth","refresh":"refresh","access":"access","expires":-1}}',
        ),
        (
            "opencode-openai",
            b'{"openai":{"type":"oauth","refresh":"refresh","access":"access","expires":1.5}}',
        ),
        ("opencode-go", b"not-json"),
    ],
)
def test_shared_opencode_authority_missing_or_invalid_provider_entry_fails_closed(
    tmp_path: Path,
    provider_name: str,
    document: bytes,
) -> None:
    """REQ-core-credentials-002: a broad shared-file copy is never a fallback."""
    from butlers.cli_auth.sandbox import load_validated_readonly_authority

    canonical = tmp_path / ".local" / "share" / "opencode" / "auth.json"
    canonical.parent.mkdir(parents=True)
    canonical.write_bytes(document)
    os.chmod(canonical, 0o600)

    assert (
        load_validated_readonly_authority(replace(PROVIDERS[provider_name], token_path=canonical))
        is None
    )


@pytest.mark.parametrize(
    ("pid1_terminated", "payload_fds_closed"),
    [(False, True), (True, False)],
)
async def test_device_auth_guard_failure_prevents_staged_bytes_persistence(
    tmp_path: Path,
    pid1_terminated: bool,
    payload_fds_closed: bool,
) -> None:
    """REQ-core-credentials-002: PID1/FD failures persist no device-auth output."""
    from butlers.cli_auth.sandbox import persist_staged_device_auth_output

    provider = replace(
        PROVIDERS["opencode-openai"],
        token_path=tmp_path / ".local" / "share" / "opencode" / "auth.json",
    )
    stage_home, output = _make_stage_output(tmp_path / "stage-home")

    store = MagicMock()
    store.store_shared_if_unchanged = AsyncMock(return_value=True)
    stage_home_fd = os.open(stage_home, os.O_RDONLY | os.O_DIRECTORY)
    try:
        persisted = await persist_staged_device_auth_output(
            provider,
            store,
            stage_home_fd=stage_home_fd,
            relative_output_path=output.relative_to(stage_home),
            expected_uid=os.geteuid(),
            pid1_terminated=pid1_terminated,
            payload_fds_closed=payload_fds_closed,
            expected_authority_value=None,
        )
    finally:
        os.close(stage_home_fd)

    assert persisted is False
    store.store_shared_if_unchanged.assert_not_awaited()


async def test_device_auth_persists_validated_bytes_from_trusted_stage_fd(tmp_path: Path) -> None:
    """REQ-core-credentials-002: persistence projects one strict OpenAI authority."""
    from butlers.cli_auth.sandbox import persist_staged_device_auth_output

    canonical = tmp_path / "canonical" / "auth.json"
    canonical.parent.mkdir()
    canonical.write_text('{"canonical":"must-not-be-read"}', encoding="utf-8")

    stage_home, output = _make_stage_output(tmp_path / "stage-home")
    staged_content = """{
      "openai": {
        "type": "oauth",
        "refresh": "synthetic-device-refresh",
        "access": "synthetic-device-access",
        "expires": 1700000000,
        "enterpriseUrl": "https://enterprise.example.test",
        "accountId": "synthetic-account"
      }
    }"""
    expected_content = (
        '{"openai":{"access":"synthetic-device-access","accountId":"synthetic-account",'
        '"enterpriseUrl":"https://enterprise.example.test","expires":1700000000,'
        '"refresh":"synthetic-device-refresh","type":"oauth"}}'
    )
    output.write_text(staged_content, encoding="utf-8")
    os.chmod(output, 0o600)

    provider = replace(PROVIDERS["opencode-openai"], token_path=canonical)
    store = MagicMock()
    store.store_shared_if_unchanged = AsyncMock(return_value=True)
    stage_home_fd = os.open(stage_home, os.O_RDONLY | os.O_DIRECTORY)
    try:
        persisted = await persist_staged_device_auth_output(
            provider,
            store,
            stage_home_fd=stage_home_fd,
            relative_output_path=output.relative_to(stage_home),
            expected_uid=os.geteuid(),
            pid1_terminated=True,
            payload_fds_closed=True,
            expected_authority_value=None,
        )
    finally:
        os.close(stage_home_fd)

    assert persisted is True
    assert canonical.read_text(encoding="utf-8") == expected_content
    store.store_shared_if_unchanged.assert_awaited_once_with(
        "cli-auth/opencode-openai",
        expected_content,
        expected_value=None,
        category="cli-auth",
        description="CLI auth token for OpenCode (OpenAI)",
        is_sensitive=True,
    )


@pytest.mark.parametrize(
    "staged_content",
    [
        (
            b'{"openai":{"type":"oauth","refresh":"synthetic-refresh",'
            b'"access":"synthetic-access","expires":1700000000},'
            b'"opencode-go":{"type":"api","key":"peer-must-not-persist"}}'
        ),
        (
            b'{"openai":{"type":"oauth","refresh":"synthetic-refresh",'
            b'"access":"synthetic-access","expires":1700000000,'
            b'"unexpected":{"peer":"must-not-persist"}}}'
        ),
        (
            b'{"openai":{"type":"oauth","refresh":"synthetic-refresh",'
            b'"access":"synthetic-access","expires":1700000000,'
            b'"access":"duplicate-must-not-persist"}}'
        ),
        (
            b'{"openai":{"type":"oauth","refresh":"synthetic-refresh",'
            b'"access":"synthetic-access","expires":NaN}}'
        ),
    ],
)
async def test_device_auth_rejects_unprojectable_openai_output_before_persistence(
    tmp_path: Path,
    staged_content: bytes,
) -> None:
    """REQ-core-credentials-002: device output cannot carry peer or ambiguous authority."""
    from butlers.cli_auth.sandbox import persist_staged_device_auth_output

    provider = replace(
        PROVIDERS["opencode-openai"],
        token_path=tmp_path / ".local" / "share" / "opencode" / "auth.json",
    )
    stage_home, output = _make_stage_output(tmp_path / "stage-home")
    output.write_bytes(staged_content)
    os.chmod(output, 0o600)

    store = MagicMock()
    store.store_shared_if_unchanged = AsyncMock(return_value=True)
    stage_home_fd = os.open(stage_home, os.O_RDONLY | os.O_DIRECTORY)
    try:
        persisted = await persist_staged_device_auth_output(
            provider,
            store,
            stage_home_fd=stage_home_fd,
            relative_output_path=output.relative_to(stage_home),
            expected_uid=os.geteuid(),
            pid1_terminated=True,
            payload_fds_closed=True,
            expected_authority_value=None,
        )
    finally:
        os.close(stage_home_fd)

    assert persisted is False
    store.store_shared_if_unchanged.assert_not_awaited()


async def test_codex_device_auth_persists_only_strict_projected_authority(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """REQ-core-credentials-002: Codex persists auth while discarding its login log."""
    from butlers.cli_auth.sandbox import persist_staged_device_auth_output

    stage_home = tmp_path / "stage-home"
    stage_home.mkdir(mode=0o700)
    output = stage_home / ".codex" / "auth.json"
    output.parent.mkdir(mode=0o700)
    staged_content = """{
      "tokens": {
        "refresh_token": "synthetic-refresh",
        "id_token": "synthetic.id.signature",
        "account_id": "synthetic-account",
        "access_token": "synthetic.access.signature"
      },
      "last_refresh": "2026-08-12T00:00:00Z",
      "OPENAI_API_KEY": null,
      "auth_mode": "chatgpt"
    }"""
    expected_content = (
        '{"OPENAI_API_KEY":null,"auth_mode":"chatgpt",'
        '"last_refresh":"2026-08-12T00:00:00Z",'
        '"tokens":{"access_token":"synthetic.access.signature",'
        '"account_id":"synthetic-account","id_token":"synthetic.id.signature",'
        '"refresh_token":"synthetic-refresh"}}'
    )
    output.write_text(staged_content, encoding="utf-8")
    os.chmod(output, 0o600)
    login_log = output.parent / "log" / "codex-login.log"
    login_log.parent.mkdir(mode=0o700)
    login_log.write_text("opaque disposable login trace", encoding="utf-8")
    os.chmod(login_log, 0o600)
    tmp_sentinel = "opaque-disposable-tmp-state"
    tmp_file = output.parent / "tmp" / "state"
    tmp_file.parent.mkdir(mode=0o700)
    tmp_file.write_text(tmp_sentinel, encoding="utf-8")
    os.chmod(tmp_file, 0o600)

    provider = replace(PROVIDERS["codex"], token_path=tmp_path / "canonical" / "auth.json")
    authority = MagicMock()
    authority.require_system_global_pool = MagicMock()
    authority.store_codex_cli_auth_if_unchanged = AsyncMock(return_value=True)
    stage_home_fd = os.open(stage_home, os.O_RDONLY | os.O_DIRECTORY)
    try:
        with caplog.at_level(logging.WARNING, logger="butlers.cli_auth.sandbox"):
            persisted = await persist_staged_device_auth_output(
                provider,
                authority,
                stage_home_fd=stage_home_fd,
                relative_output_path=output.relative_to(stage_home),
                expected_uid=os.geteuid(),
                pid1_terminated=True,
                payload_fds_closed=True,
                codex_authority=authority,
                expected_authority_value=None,
            )
    finally:
        os.close(stage_home_fd)

    assert persisted is True
    authority.store_codex_cli_auth_if_unchanged.assert_awaited_once_with(
        expected_content,
        expected_value=None,
    )
    assert provider.token_path is not None
    assert provider.token_path.read_text(encoding="utf-8") == expected_content
    assert tmp_sentinel not in provider.token_path.read_text(encoding="utf-8")
    assert "staged device-auth output validation failed" not in caplog.text


async def test_codex_device_auth_rejects_undeclared_stage_child_before_persistence(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """REQ-core-credentials-002: the login-log exception cannot widen the Codex HOME."""
    from butlers.cli_auth.sandbox import persist_staged_device_auth_output

    stage_home = tmp_path / "stage-home"
    stage_home.mkdir(mode=0o700)
    codex_home = stage_home / ".codex"
    codex_home.mkdir(mode=0o700)
    output = codex_home / "auth.json"
    output.write_bytes(
        b'{"auth_mode":"chatgpt","OPENAI_API_KEY":null,'
        b'"tokens":{"id_token":"synthetic-id","access_token":"synthetic-access",'
        b'"refresh_token":"synthetic-refresh","account_id":null},'
        b'"last_refresh":"2026-08-12T00:00:00Z"}'
    )
    os.chmod(output, 0o600)
    log_dir = codex_home / "log"
    log_dir.mkdir(mode=0o700)
    login_log = log_dir / "codex-login.log"
    login_log.write_text("allowed log cannot mask a peer", encoding="utf-8")
    os.chmod(login_log, 0o600)
    tmp_file = codex_home / "tmp" / "state"
    tmp_file.parent.mkdir(mode=0o700)
    tmp_file.write_text("allowed tmp cannot mask a peer", encoding="utf-8")
    os.chmod(tmp_file, 0o600)
    stage_child_sentinel = "UNDECLARED-STAGE-CHILD-MUST-NOT-REACH-LOGS"
    peer_artifact = codex_home / stage_child_sentinel
    peer_artifact.write_text("must not cross the stage boundary", encoding="utf-8")
    os.chmod(peer_artifact, 0o600)

    provider = replace(PROVIDERS["codex"], token_path=tmp_path / "canonical" / "auth.json")
    authority = MagicMock()
    authority.require_system_global_pool = MagicMock()
    authority.store_codex_cli_auth_if_unchanged = AsyncMock(return_value=True)
    stage_home_fd = os.open(stage_home, os.O_RDONLY | os.O_DIRECTORY)
    try:
        with caplog.at_level(logging.WARNING, logger="butlers.cli_auth.sandbox"):
            persisted = await persist_staged_device_auth_output(
                provider,
                authority,
                stage_home_fd=stage_home_fd,
                relative_output_path=output.relative_to(stage_home),
                expected_uid=os.geteuid(),
                pid1_terminated=True,
                payload_fds_closed=True,
                codex_authority=authority,
                expected_authority_value=None,
            )
    finally:
        os.close(stage_home_fd)

    assert persisted is False
    authority.store_codex_cli_auth_if_unchanged.assert_not_awaited()
    assert "reason=unknown_child" in caplog.text
    assert stage_child_sentinel not in caplog.text


def test_codex_device_auth_rejects_public_tmp_scratch(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """REQ-core-credentials-002: the new tmp root remains private to its child."""
    from butlers.cli_auth.sandbox import read_validated_staged_device_auth_output

    stage_home = tmp_path / "stage-home"
    stage_home.mkdir(mode=0o700)
    codex_home = stage_home / ".codex"
    codex_home.mkdir(mode=0o700)
    output = codex_home / "auth.json"
    output.write_bytes(b"{}")
    os.chmod(output, 0o600)
    tmp_dir = codex_home / "tmp"
    tmp_dir.mkdir(mode=0o755)

    stage_home_fd = os.open(stage_home, os.O_RDONLY | os.O_DIRECTORY)
    try:
        with caplog.at_level(logging.WARNING, logger="butlers.cli_auth.sandbox"):
            result = read_validated_staged_device_auth_output(
                stage_home_fd=stage_home_fd,
                relative_output_path=output.relative_to(stage_home),
                expected_uid=os.geteuid(),
                pid1_terminated=True,
                payload_fds_closed=True,
            )
    finally:
        os.close(stage_home_fd)

    assert result is None
    assert "reason=directory_metadata" in caplog.text


def test_codex_device_auth_rejects_linked_tmp_scratch(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """REQ-core-credentials-002: tmp cannot redirect the parent outside its stage."""
    from butlers.cli_auth.sandbox import read_validated_staged_device_auth_output

    stage_home = tmp_path / "stage-home"
    stage_home.mkdir(mode=0o700)
    codex_home = stage_home / ".codex"
    codex_home.mkdir(mode=0o700)
    output = codex_home / "auth.json"
    output.write_bytes(b"{}")
    os.chmod(output, 0o600)
    target = tmp_path / "tmp-target"
    target.mkdir(mode=0o700)
    (codex_home / "tmp").symlink_to(target, target_is_directory=True)

    stage_home_fd = os.open(stage_home, os.O_RDONLY | os.O_DIRECTORY)
    try:
        with caplog.at_level(logging.WARNING, logger="butlers.cli_auth.sandbox"):
            result = read_validated_staged_device_auth_output(
                stage_home_fd=stage_home_fd,
                relative_output_path=output.relative_to(stage_home),
                expected_uid=os.geteuid(),
                pid1_terminated=True,
                payload_fds_closed=True,
            )
    finally:
        os.close(stage_home_fd)

    assert result is None
    assert "reason=stage_io" in caplog.text


def test_device_auth_stage_io_reason_is_value_free(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """REQ-core-credentials-002: stage I/O never reaches the diagnostic log."""
    from butlers.cli_auth.sandbox import read_validated_staged_device_auth_output

    stage_home, output = _make_stage_output(tmp_path / "stage-home")
    stage_home_fd = os.open(stage_home, os.O_RDONLY | os.O_DIRECTORY)
    io_sentinel = "STAGE-IO-DETAIL-MUST-NOT-REACH-LOGS"
    try:
        with (
            patch(
                "butlers.cli_auth.sandbox._validate_device_auth_stage_tree",
                side_effect=OSError(io_sentinel),
            ),
            caplog.at_level(logging.WARNING, logger="butlers.cli_auth.sandbox"),
        ):
            result = read_validated_staged_device_auth_output(
                stage_home_fd=stage_home_fd,
                relative_output_path=output.relative_to(stage_home),
                expected_uid=os.geteuid(),
                pid1_terminated=True,
                payload_fds_closed=True,
            )
    finally:
        os.close(stage_home_fd)

    assert result is None
    assert "reason=stage_io" in caplog.text
    assert io_sentinel not in caplog.text


async def test_codex_device_auth_rejects_login_log_without_auth_before_persistence(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """REQ-core-credentials-002: disposable Codex logs never substitute for auth output."""
    from butlers.cli_auth.sandbox import persist_staged_device_auth_output

    stage_home = tmp_path / "stage-home"
    stage_home.mkdir(mode=0o700)
    codex_home = stage_home / ".codex"
    codex_home.mkdir(mode=0o700)
    login_log = codex_home / "log" / "codex-login.log"
    login_log.parent.mkdir(mode=0o700)
    login_log.write_text("no credential artifact", encoding="utf-8")
    os.chmod(login_log, 0o600)
    tmp_sentinel = "tmp-without-auth-must-not-persist"
    tmp_file = codex_home / "tmp" / "state"
    tmp_file.parent.mkdir(mode=0o700)
    tmp_file.write_text(tmp_sentinel, encoding="utf-8")
    os.chmod(tmp_file, 0o600)

    provider = replace(PROVIDERS["codex"], token_path=tmp_path / "canonical" / "auth.json")
    authority = MagicMock()
    authority.require_system_global_pool = MagicMock()
    authority.store_codex_cli_auth_if_unchanged = AsyncMock(return_value=True)
    stage_home_fd = os.open(stage_home, os.O_RDONLY | os.O_DIRECTORY)
    try:
        with caplog.at_level(logging.WARNING, logger="butlers.cli_auth.sandbox"):
            persisted = await persist_staged_device_auth_output(
                provider,
                authority,
                stage_home_fd=stage_home_fd,
                relative_output_path=Path(".codex") / "auth.json",
                expected_uid=os.geteuid(),
                pid1_terminated=True,
                payload_fds_closed=True,
                codex_authority=authority,
                expected_authority_value=None,
            )
    finally:
        os.close(stage_home_fd)

    assert persisted is False
    authority.store_codex_cli_auth_if_unchanged.assert_not_awaited()
    assert "reason=missing_required_child" in caplog.text
    assert tmp_sentinel not in caplog.text


@pytest.mark.parametrize(
    "staged_content",
    [
        b'{"peer":"nonempty-object-must-not-persist"}',
        (
            b'{"auth_mode":"chatgpt","OPENAI_API_KEY":null,'
            b'"tokens":{"id_token":"synthetic.id.signature",'
            b'"access_token":"synthetic.access.signature",'
            b'"refresh_token":"synthetic-refresh","account_id":null},'
            b'"last_refresh":"2026-08-12T00:00:00Z",'
            b'"peer":{"must":"not-persist"}}'
        ),
        (
            b'{"auth_mode":"chatgpt","OPENAI_API_KEY":null,'
            b'"tokens":{"id_token":"synthetic.id.signature",'
            b'"access_token":"synthetic.access.signature",'
            b'"refresh_token":"synthetic-refresh","account_id":null,'
            b'"unexpected":{"must":"not-persist"}},'
            b'"last_refresh":"2026-08-12T00:00:00Z"}'
        ),
        (
            b'{"auth_mode":"chatgpt","OPENAI_API_KEY":null,'
            b'"tokens":{"id_token":"synthetic.id.signature",'
            b'"access_token":"synthetic.access.signature",'
            b'"refresh_token":"synthetic-refresh",'
            b'"refresh_token":"duplicate-must-not-persist","account_id":null},'
            b'"last_refresh":"2026-08-12T00:00:00Z"}'
        ),
        (
            b'{"auth_mode":"chatgpt","OPENAI_API_KEY":null,'
            b'"tokens":{"id_token":"synthetic.id.signature",'
            b'"access_token":"synthetic.access.signature",'
            b'"refresh_token":"synthetic-refresh","account_id":null},'
            b'"last_refresh":NaN}'
        ),
    ],
)
async def test_codex_device_auth_rejects_unprojectable_output_before_persistence(
    tmp_path: Path,
    staged_content: bytes,
) -> None:
    """REQ-core-credentials-002: Codex accepts no generic nonempty JSON fallback."""
    from butlers.cli_auth.sandbox import persist_staged_device_auth_output

    stage_home = tmp_path / "stage-home"
    stage_home.mkdir(mode=0o700)
    output = stage_home / ".codex" / "auth.json"
    output.parent.mkdir(mode=0o700)
    output.write_bytes(staged_content)
    os.chmod(output, 0o600)

    authority = MagicMock()
    authority.require_system_global_pool = MagicMock()
    authority.store_codex_cli_auth_if_unchanged = AsyncMock(return_value=True)
    stage_home_fd = os.open(stage_home, os.O_RDONLY | os.O_DIRECTORY)
    try:
        persisted = await persist_staged_device_auth_output(
            PROVIDERS["codex"],
            authority,
            stage_home_fd=stage_home_fd,
            relative_output_path=output.relative_to(stage_home),
            expected_uid=os.geteuid(),
            pid1_terminated=True,
            payload_fds_closed=True,
            codex_authority=authority,
            expected_authority_value=None,
        )
    finally:
        os.close(stage_home_fd)

    assert persisted is False
    authority.store_codex_cli_auth_if_unchanged.assert_not_awaited()


@pytest.mark.parametrize("attack", ["symlink", "hardlink", "escape"])
async def test_device_auth_rejects_linked_or_escaped_staged_output_before_persistence(
    tmp_path: Path, attack: str
) -> None:
    """REQ-core-credentials-002: links and paths outside the trusted root persist nothing."""
    from butlers.cli_auth.sandbox import persist_staged_device_auth_output

    stage_home, output = _make_stage_output(tmp_path / "stage-home")
    canonical = tmp_path / "canonical" / "auth.json"
    canonical.parent.mkdir()
    canonical.write_text('{"openai":{"type":"oauth"}}', encoding="utf-8")
    os.chmod(canonical, 0o600)

    relative_output_path = output.relative_to(stage_home)
    if attack == "symlink":
        output.unlink()
        output.symlink_to(canonical)
    elif attack == "hardlink":
        output.unlink()
        os.link(canonical, output)
    else:
        relative_output_path = Path("..") / "canonical" / "auth.json"

    provider = replace(PROVIDERS["opencode-openai"], token_path=canonical)
    store = MagicMock()
    store.store_shared_if_unchanged = AsyncMock(return_value=True)
    stage_home_fd = os.open(stage_home, os.O_RDONLY | os.O_DIRECTORY)
    try:
        persisted = await persist_staged_device_auth_output(
            provider,
            store,
            stage_home_fd=stage_home_fd,
            relative_output_path=relative_output_path,
            expected_uid=os.geteuid(),
            pid1_terminated=True,
            payload_fds_closed=True,
            expected_authority_value=None,
        )
    finally:
        os.close(stage_home_fd)

    assert persisted is False
    store.store_shared_if_unchanged.assert_not_awaited()


async def test_device_auth_rejects_a_peer_staged_credential_artifact_before_persistence(
    tmp_path: Path,
) -> None:
    """REQ-core-credentials-002: one valid output cannot mask a peer authority file."""
    from butlers.cli_auth.sandbox import persist_staged_device_auth_output

    provider = replace(
        PROVIDERS["opencode-openai"],
        token_path=tmp_path / ".local" / "share" / "opencode" / "auth.json",
    )
    stage_home, output = _make_stage_output(tmp_path / "stage-home")
    output.write_bytes(
        b'{"openai":{"type":"oauth","refresh":"synthetic-refresh",'
        b'"access":"synthetic-access","expires":1700000000}}'
    )
    os.chmod(output, 0o600)
    peer_artifact = output.with_name("peer-credential-artifact.json")
    peer_artifact.write_text('{"peer":"must-not-persist"}', encoding="utf-8")
    os.chmod(peer_artifact, 0o600)

    store = MagicMock()
    store.store_shared_if_unchanged = AsyncMock(return_value=True)
    stage_home_fd = os.open(stage_home, os.O_RDONLY | os.O_DIRECTORY)
    try:
        persisted = await persist_staged_device_auth_output(
            provider,
            store,
            stage_home_fd=stage_home_fd,
            relative_output_path=output.relative_to(stage_home),
            expected_uid=os.geteuid(),
            pid1_terminated=True,
            payload_fds_closed=True,
            expected_authority_value=None,
        )
    finally:
        os.close(stage_home_fd)

    assert persisted is False
    store.store_shared_if_unchanged.assert_not_awaited()


async def test_opencode_device_auth_authority_cas_conflict_persists_nothing_or_projects_no_file(
    tmp_path: Path,
) -> None:
    """REQ-core-credentials-002: a concurrent authority replacement wins over staged output."""
    from butlers.cli_auth.sandbox import persist_staged_device_auth_output

    canonical = tmp_path / "canonical" / "auth.json"
    provider = replace(PROVIDERS["opencode-openai"], token_path=canonical)
    stage_home, output = _make_stage_output(tmp_path / "stage-home")
    output.write_bytes(
        b'{"openai":{"type":"oauth","refresh":"new-refresh",'
        b'"access":"new-access","expires":1700000000}}'
    )
    os.chmod(output, 0o600)

    store = MagicMock()
    store.store_shared_if_unchanged = AsyncMock(return_value=False)
    stage_home_fd = os.open(stage_home, os.O_RDONLY | os.O_DIRECTORY)
    try:
        persisted = await persist_staged_device_auth_output(
            provider,
            store,
            stage_home_fd=stage_home_fd,
            relative_output_path=output.relative_to(stage_home),
            expected_uid=os.geteuid(),
            pid1_terminated=True,
            payload_fds_closed=True,
            expected_authority_value='{"openai":{"access":"owner-newer"}}',
        )
    finally:
        os.close(stage_home_fd)

    assert persisted is False
    store.store_shared_if_unchanged.assert_awaited_once()
    assert not canonical.exists()


async def test_device_auth_authority_write_error_is_value_free_and_projects_no_file(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """REQ-core-credentials-002: authority write errors never log or project staged bytes."""
    from butlers.cli_auth.sandbox import persist_staged_device_auth_output

    staged_sentinel = "STAGED-AUTHORITY-WRITE-MUST-NOT-LOG"
    canonical = tmp_path / "canonical" / "auth.json"
    provider = replace(PROVIDERS["opencode-openai"], token_path=canonical)
    stage_home, output = _make_stage_output(tmp_path / "stage-home")
    output.write_bytes(
        b'{"openai":{"type":"oauth","refresh":"'
        + staged_sentinel.encode("utf-8")
        + b'","access":"synthetic-access","expires":1700000000}}'
    )
    os.chmod(output, 0o600)

    store = MagicMock()
    store.store_shared_if_unchanged = AsyncMock(side_effect=RuntimeError(staged_sentinel))
    stage_home_fd = os.open(stage_home, os.O_RDONLY | os.O_DIRECTORY)
    try:
        with caplog.at_level("WARNING", logger="butlers.cli_auth.persistence"):
            persisted = await persist_staged_device_auth_output(
                provider,
                store,
                stage_home_fd=stage_home_fd,
                relative_output_path=output.relative_to(stage_home),
                expected_uid=os.geteuid(),
                pid1_terminated=True,
                payload_fds_closed=True,
                expected_authority_value=None,
            )
    finally:
        os.close(stage_home_fd)

    assert persisted is False
    assert not canonical.exists()
    assert staged_sentinel not in caplog.text


async def test_opencode_device_auth_cas_success_reconciles_the_canonical_runtime_file(
    tmp_path: Path,
) -> None:
    """REQ-core-credentials-002: a confirmed authority write is usable without restart."""
    from butlers.cli_auth.sandbox import persist_staged_device_auth_output

    canonical = tmp_path / "canonical" / "auth.json"
    provider = replace(PROVIDERS["opencode-openai"], token_path=canonical)
    stage_home, output = _make_stage_output(tmp_path / "stage-home")
    output.write_bytes(
        b'{"openai":{"type":"oauth","refresh":"synthetic-refresh",'
        b'"access":"synthetic-access","expires":1700000000}}'
    )
    os.chmod(output, 0o600)

    store = MagicMock()
    store.store_shared_if_unchanged = AsyncMock(return_value=True)
    stage_home_fd = os.open(stage_home, os.O_RDONLY | os.O_DIRECTORY)
    try:
        persisted = await persist_staged_device_auth_output(
            provider,
            store,
            stage_home_fd=stage_home_fd,
            relative_output_path=output.relative_to(stage_home),
            expected_uid=os.geteuid(),
            pid1_terminated=True,
            payload_fds_closed=True,
            expected_authority_value=None,
        )
    finally:
        os.close(stage_home_fd)

    assert persisted is True
    assert provider.is_authenticated() is True
    assert canonical.read_text(encoding="utf-8") == (
        '{"openai":{"access":"synthetic-access","expires":1700000000,'
        '"refresh":"synthetic-refresh","type":"oauth"}}'
    )


async def test_codex_device_auth_authority_cas_conflict_persists_nothing_or_projects_no_file(
    tmp_path: Path,
) -> None:
    """REQ-core-credentials-001: a newer system-global Codex authority cannot be replaced."""
    from butlers.cli_auth.sandbox import persist_staged_device_auth_output

    canonical = tmp_path / "canonical" / "auth.json"
    provider = replace(PROVIDERS["codex"], token_path=canonical)
    stage_home = tmp_path / "stage-home"
    stage_home.mkdir(mode=0o700)
    output = stage_home / ".codex" / "auth.json"
    output.parent.mkdir(mode=0o700)
    output.write_bytes(
        b'{"auth_mode":"chatgpt","OPENAI_API_KEY":null,'
        b'"tokens":{"id_token":"synthetic-id","access_token":"synthetic-access",'
        b'"refresh_token":"synthetic-refresh","account_id":null},'
        b'"last_refresh":"2026-08-12T00:00:00Z"}'
    )
    os.chmod(output, 0o600)

    authority = MagicMock()
    authority.require_system_global_pool = MagicMock()
    authority.store_codex_cli_auth_if_unchanged = AsyncMock(return_value=False)
    stage_home_fd = os.open(stage_home, os.O_RDONLY | os.O_DIRECTORY)
    try:
        persisted = await persist_staged_device_auth_output(
            provider,
            authority,
            stage_home_fd=stage_home_fd,
            relative_output_path=output.relative_to(stage_home),
            expected_uid=os.geteuid(),
            pid1_terminated=True,
            payload_fds_closed=True,
            codex_authority=authority,
            expected_authority_value='{"auth_mode":"chatgpt","newer":"owner"}',
        )
    finally:
        os.close(stage_home_fd)

    assert persisted is False
    authority.store_codex_cli_auth_if_unchanged.assert_awaited_once()
    assert not canonical.exists()


class _FakeDeviceAuthProcess:
    """Minimal process-shaped child owned by the sandbox test handle."""

    def __init__(self, stdout: bytes, *, returncode: int = 0) -> None:
        self.stdout = asyncio.StreamReader()
        self.stdout.feed_data(stdout)
        self.stdout.feed_eof()
        self.returncode = returncode
        self.terminated = False
        self.wait_calls = 0

    async def wait(self) -> int:
        self.wait_calls += 1
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.terminated = True


class _RecordingDeviceAuthHandle:
    """Sandbox handle that exposes staged bytes only after finalization."""

    def __init__(self, events: list[str], *, staged_output: bytes | None) -> None:
        self.process = _FakeDeviceAuthProcess(b"Successfully logged in\n")
        self._events = events
        self._staged_output = staged_output

    async def finalize(self, *, succeeded: bool) -> bytes | None:
        self._events.append("finalize")
        assert succeeded is True
        assert self.process.wait_calls == 0, "only the sandbox handle may wait the direct child"
        return self._staged_output

    async def terminate(self) -> None:
        self._events.append("terminate")


class _RecordingDeviceAuthLauncher:
    """Records the shared-launcher boundary while preserving session behavior."""

    def __init__(self, events: list[str], *, staged_output: bytes | None) -> None:
        self._events = events
        self._staged_output = staged_output

    async def launch_device_auth(self, _provider):
        self._events.append("launch")
        return _RecordingDeviceAuthHandle(self._events, staged_output=self._staged_output)


async def test_device_auth_prepares_the_authority_fence_before_sandbox_launch() -> None:
    """REQ-core-credentials-002: a preparable authority fence orders before child spawn."""
    from butlers.cli_auth.session import CLIAuthSession

    events: list[str] = []

    class _PreparedCallback:
        requires_prelaunch_prepare = True

        async def prepare_for_device_auth(self, _provider) -> bool:
            events.append("prepare")
            return True

        async def __call__(self, _provider, *, staged_output: bytes) -> bool:
            del staged_output
            events.append("persist")
            return True

    session = CLIAuthSession(
        id="authority-before-launch",
        provider=PROVIDERS["codex"],
        on_success=_PreparedCallback(),
        sandbox=_RecordingDeviceAuthLauncher(
            events,
            staged_output=b'{"auth_mode":"chatgpt"}',
        ),
    )

    await session.start()
    await session.wait()

    assert session.state == "success"
    assert events == ["prepare", "launch", "finalize", "persist"]


async def test_device_auth_does_not_launch_when_authority_preparation_fails() -> None:
    """REQ-core-credentials-002: unavailable prelaunch authority means no child domain."""
    from butlers.cli_auth.session import CLIAuthSession

    events: list[str] = []

    class _UnavailableCallback:
        requires_prelaunch_prepare = True

        async def prepare_for_device_auth(self, _provider) -> bool:
            events.append("prepare")
            return False

        async def __call__(self, _provider, *, staged_output: bytes) -> bool:
            del staged_output
            events.append("persist")
            return True

    session = CLIAuthSession(
        id="authority-unavailable",
        provider=PROVIDERS["opencode-openai"],
        on_success=_UnavailableCallback(),
        sandbox=_RecordingDeviceAuthLauncher(
            events,
            staged_output=b'{"openai":{"type":"oauth"}}',
        ),
    )

    await session.start()

    assert session.state == "failed"
    assert session.message == "Credential authority unavailable."
    assert session._done_event.is_set()
    assert events == ["prepare"]


async def test_device_auth_does_not_launch_without_a_persistence_callback() -> None:
    """REQ-core-credentials-002: an absent authority callback is a prelaunch failure."""
    from butlers.cli_auth.session import CLIAuthSession

    events: list[str] = []
    session = CLIAuthSession(
        id="authority-callback-missing",
        provider=PROVIDERS["opencode-openai"],
        sandbox=_RecordingDeviceAuthLauncher(
            events,
            staged_output=b'{"openai":{"type":"oauth"}}',
        ),
    )

    await session.start()

    assert session.state == "failed"
    assert session.message == "Credential authority unavailable."
    assert session._done_event.is_set()
    assert events == []


async def test_device_auth_session_uses_sandbox_handle_before_persisting_staged_bytes() -> None:
    """REQ-core-credentials-002: device auth has no direct-child fallback or pre-PID1 write."""
    from butlers.cli_auth.session import CLIAuthSession

    events: list[str] = []
    staged_output = b'{"tokens":{"access_token":"not-a-real-token"}}'

    async def _on_success(_provider, *, staged_output: bytes) -> bool:
        events.append("persist")
        assert staged_output == b'{"tokens":{"access_token":"not-a-real-token"}}'
        return True

    with patch(
        "butlers.cli_auth.session.asyncio.create_subprocess_exec", new_callable=AsyncMock
    ) as direct_spawn:
        session = CLIAuthSession(
            id="sandboxed-device-auth",
            provider=PROVIDERS["codex"],
            on_success=_on_success,
            sandbox=_RecordingDeviceAuthLauncher(events, staged_output=staged_output),
        )
        await session.start()
        await session.wait()

    assert session.state == "success"
    assert events == ["launch", "finalize", "persist"]
    direct_spawn.assert_not_awaited()


async def test_device_auth_session_does_not_persist_when_sandbox_finalization_fails() -> None:
    """REQ-core-credentials-002: a failed PID1/FD finalization cannot call persistence."""
    from butlers.cli_auth.session import CLIAuthSession

    events: list[str] = []
    on_success = AsyncMock(return_value=True)
    session = CLIAuthSession(
        id="sandboxed-device-auth-failure",
        provider=PROVIDERS["codex"],
        on_success=on_success,
        sandbox=_RecordingDeviceAuthLauncher(events, staged_output=None),
    )

    await session.start()
    await session.wait()

    assert session.state == "failed"
    assert session.message == "CLI authentication result could not be validated safely."
    assert events == ["launch", "finalize"]
    on_success.assert_not_awaited()


async def test_opencode_device_auth_false_persistence_callback_is_a_terminal_failure() -> None:
    """REQ-core-credentials-002: every provider requires a confirmed authority write."""
    from butlers.cli_auth.session import CLIAuthSession

    events: list[str] = []
    provider = replace(
        PROVIDERS["opencode-openai"],
        success_pattern=re.compile(r"Successfully logged in"),
    )

    async def _not_persisted(_provider, *, staged_output: bytes) -> bool:
        assert staged_output == b'{"openai":{"type":"oauth"}}'
        events.append("persist")
        return False

    session = CLIAuthSession(
        id="opencode-device-auth-unpersisted",
        provider=provider,
        on_success=_not_persisted,
        sandbox=_RecordingDeviceAuthLauncher(
            events,
            staged_output=b'{"openai":{"type":"oauth"}}',
        ),
    )
    await session.start()
    await session.wait()

    assert session.state == "failed"
    assert session.message == "Authentication was not saved to the credential authority."
    assert session.message != "Authentication successful."
    assert events == ["launch", "finalize", "persist"]


async def test_opencode_device_auth_persistence_exception_is_value_free_and_terminal(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """REQ-core-credentials-002: staged authority bytes never reach callback diagnostics."""
    from butlers.cli_auth.session import CLIAuthSession

    events: list[str] = []
    staged_sentinel = "STAGED-OPENCODE-AUTHORITY-MUST-NOT-LOG"
    provider = replace(
        PROVIDERS["opencode-openai"],
        success_pattern=re.compile(r"Successfully logged in"),
    )

    async def _raise(_provider, *, staged_output: bytes) -> bool:
        assert staged_sentinel.encode() in staged_output
        events.append("persist")
        raise RuntimeError(f"persistence failed for {staged_sentinel}")

    session = CLIAuthSession(
        id="opencode-device-auth-persistence-exception",
        provider=provider,
        on_success=_raise,
        sandbox=_RecordingDeviceAuthLauncher(
            events,
            staged_output=(
                b'{"openai":{"type":"oauth","access":"' + staged_sentinel.encode() + b'"}}'
            ),
        ),
    )
    with caplog.at_level("INFO", logger="butlers.cli_auth.session"):
        await session.start()
        await session.wait()

    assert session.state == "failed"
    assert session.message == "Authentication was not saved to the credential authority."
    assert events == ["launch", "finalize", "persist"]
    assert staged_sentinel not in caplog.text
    assert "on_success callback failed safely" in caplog.text


async def test_device_auth_session_oversized_stdout_terminates_the_handle_and_marks_terminal() -> (
    None
):
    """REQ-core-credentials-002: malformed provider stdout cannot leak a live domain."""
    from butlers.cli_auth.session import CLIAuthSession

    events: list[str] = []
    on_success = AsyncMock(return_value=True)

    class _OversizedOutputProcess:
        def __init__(self) -> None:
            self.stdout = asyncio.StreamReader(limit=64)
            self.stdout.feed_data(b"x" * 65)
            self.returncode: int | None = None

        async def wait(self) -> int:
            self.returncode = 0
            return 0

        def terminate(self) -> None:
            self.returncode = 0

        def kill(self) -> None:
            self.returncode = 0

    class _OversizedOutputHandle:
        def __init__(self) -> None:
            self.process = _OversizedOutputProcess()

        async def finalize(self, *, succeeded: bool) -> bytes | None:
            del succeeded
            events.append("finalize")
            return None

        async def terminate(self) -> None:
            events.append("terminate")

    class _OversizedOutputLauncher:
        async def launch_device_auth(self, _provider):
            events.append("launch")
            return _OversizedOutputHandle()

    session = CLIAuthSession(
        id="oversized-device-auth-output",
        provider=PROVIDERS["codex"],
        on_success=on_success,
        sandbox=_OversizedOutputLauncher(),
    )
    await session.start()
    await session.wait(timeout=1.0)
    assert session._reader_task is not None
    await session._reader_task

    assert session.state == "failed"
    assert session.message == "CLI authentication output was invalid."
    assert session._done_event.is_set()
    assert events == ["launch", "terminate"]
    on_success.assert_not_awaited()


class _BlockingSessionProcess:
    """A sandbox process whose payload holds stdout open indefinitely."""

    def __init__(self, reader_started: asyncio.Event, *, initial_output: bytes = b"") -> None:
        self.stdout = asyncio.StreamReader()
        if initial_output:
            self.stdout.feed_data(initial_output)
        self.returncode: int | None = None
        self._reader_started = reader_started

    async def wait(self) -> int:
        self.returncode = 0
        return 0

    def terminate(self) -> None:
        self.returncode = 0

    def kill(self) -> None:
        self.returncode = 0


class _BlockingSessionHandle:
    """Records whether reader cancellation reaches the shared domain owner."""

    def __init__(
        self,
        reader_started: asyncio.Event,
        events: list[str],
        *,
        initial_output: bytes = b"",
    ) -> None:
        self.process = _BlockingSessionProcess(reader_started, initial_output=initial_output)
        self._reader_started = reader_started
        self._events = events

    async def finalize(self, *, succeeded: bool) -> bytes | None:
        del succeeded
        self._events.append("finalize")
        return None

    async def terminate(self) -> None:
        self._events.append("terminate")


class _BlockingSessionLauncher:
    def __init__(
        self,
        reader_started: asyncio.Event,
        events: list[str],
        *,
        initial_output: bytes = b"",
    ) -> None:
        self._reader_started = reader_started
        self._events = events
        self._initial_output = initial_output

    async def launch_device_auth(self, _provider):
        self._events.append("launch")
        return _BlockingSessionHandle(
            self._reader_started,
            self._events,
            initial_output=self._initial_output,
        )


async def test_device_auth_reader_cancellation_terminates_the_shared_sandbox_domain() -> None:
    """REQ-core-credentials-002: cancelling the reader cannot abandon a child domain."""
    from butlers.cli_auth.session import CLIAuthSession

    reader_started = asyncio.Event()
    events: list[str] = []
    on_success = AsyncMock(return_value=True)
    session = CLIAuthSession(
        id="sandbox-reader-cancel",
        provider=PROVIDERS["codex"],
        on_success=on_success,
        sandbox=_BlockingSessionLauncher(reader_started, events),
    )

    await session.start()
    assert session._reader_task is not None
    # The current reader has started waiting for payload output; cancelling it
    # must first reach the handle that owns PID1/FD/stage lifecycle.
    await asyncio.sleep(0)
    session._reader_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await session._reader_task

    assert events == ["launch", "terminate"]
    assert session._done_event.is_set()
    on_success.assert_not_awaited()


async def test_device_auth_success_line_stays_provisional_until_sandbox_finalization() -> None:
    """REQ-core-credentials-002: a payload holding stdout open cannot suppress cleanup."""
    from butlers.cli_auth.session import CLIAuthSession

    events: list[str] = []
    on_success = AsyncMock(return_value=True)
    session = CLIAuthSession(
        id="sandbox-provisional-success",
        provider=replace(PROVIDERS["codex"], timeout_seconds=0.05),
        on_success=on_success,
        sandbox=_BlockingSessionLauncher(
            asyncio.Event(),
            events,
            initial_output=b"Successfully logged in\n",
        ),
    )

    await session.start()
    await asyncio.sleep(0.01)
    assert session.state != "success"
    assert session.message == "Finalizing authentication safely."
    await session.wait(timeout=1.0)

    assert session.state == "expired"
    assert events.count("terminate") >= 1
    on_success.assert_not_awaited()


class _FinalizationBlockingHandle:
    """Lets the test cancel a reader after EOF has entered handle finalization."""

    def __init__(self, finalize_started: asyncio.Event, events: list[str]) -> None:
        self.process = _FakeDeviceAuthProcess(b"Successfully logged in\n")
        self._finalize_started = finalize_started
        self._events = events

    async def finalize(self, *, succeeded: bool) -> bytes | None:
        assert succeeded is True
        self._events.append("finalize")
        self._finalize_started.set()
        await asyncio.Event().wait()
        return None

    async def terminate(self) -> None:
        self._events.append("terminate")


class _FinalizationBlockingLauncher:
    def __init__(self, finalize_started: asyncio.Event, events: list[str]) -> None:
        self._finalize_started = finalize_started
        self._events = events

    async def launch_device_auth(self, _provider):
        self._events.append("launch")
        return _FinalizationBlockingHandle(self._finalize_started, self._events)


async def test_device_auth_cancellation_during_finalization_terminates_the_domain() -> None:
    """REQ-core-credentials-002: cancellation after EOF still cleans PID1/staging ownership."""
    from butlers.cli_auth.session import CLIAuthSession

    finalize_started = asyncio.Event()
    events: list[str] = []
    on_success = AsyncMock(return_value=True)
    session = CLIAuthSession(
        id="sandbox-finalize-cancel",
        provider=PROVIDERS["codex"],
        on_success=on_success,
        sandbox=_FinalizationBlockingLauncher(finalize_started, events),
    )

    await session.start()
    await asyncio.wait_for(finalize_started.wait(), timeout=1.0)
    assert session._reader_task is not None
    session._reader_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await session._reader_task

    assert events == ["launch", "finalize", "terminate"]
    assert session._done_event.is_set()
    on_success.assert_not_awaited()


def test_default_dashboard_cli_auth_sandbox_is_the_concrete_bubblewrap_launcher() -> None:
    """REQ-core-credentials-002: production sessions cannot select a direct-child adapter."""
    from butlers.cli_auth.sandbox import dashboard_cli_auth_sandbox
    from butlers.cli_auth.sandbox_platform import BubblewrapDashboardCLIAuthSandbox

    assert isinstance(dashboard_cli_auth_sandbox(), BubblewrapDashboardCLIAuthSandbox)


def test_default_dashboard_sandbox_binds_exact_image_runtime_input_resolvers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """REQ-core-credentials-002: production construction has no unset-resolver fallback."""
    import butlers.cli_auth.sandbox as sandbox_module
    from butlers.cli_auth.sandbox import dashboard_cli_auth_sandbox
    from butlers.cli_auth.sandbox_platform import (
        resolve_device_auth_runtime_inputs,
        resolve_readonly_runtime_inputs,
    )

    monkeypatch.setattr(sandbox_module, "_DASHBOARD_SANDBOX", None)
    sandbox = dashboard_cli_auth_sandbox()

    assert sandbox._invocation_resolver is resolve_device_auth_runtime_inputs
    assert sandbox._readonly_invocation_resolver is resolve_readonly_runtime_inputs


def test_runtime_input_manifest_resolves_only_declared_provider_inputs(tmp_path: Path) -> None:
    """REQ-core-credentials-002: the image manifest is the only runtime-input authority."""
    from butlers.cli_auth.sandbox_platform import (
        ReadonlySandboxInput,
        RuntimeCLIInputManifest,
        SandboxLaunchValidationError,
    )

    runtime_root = tmp_path / "runtime"
    runtime_root.mkdir(mode=0o755)
    executable = runtime_root / "codex"
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    os.chmod(executable, 0o555)
    manifest_path = tmp_path / "runtime-cli-sandbox-inputs.json"
    manifest_path.write_text(
        json.dumps(
            {
                "version": 2,
                "providers": {
                    "codex": {
                        "binary": "codex",
                        "executable": str(executable),
                        "readonly_inputs": [
                            {
                                "source": str(runtime_root),
                                "destination": str(runtime_root),
                            }
                        ],
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    os.chmod(manifest_path, 0o444)
    resolver = RuntimeCLIInputManifest(manifest_path, expected_uid=os.geteuid())

    device_auth = resolver.resolve_device_auth(PROVIDERS["codex"])
    readonly = resolver.resolve_readonly(
        PROVIDERS["codex"],
        ("codex", "auth", "list"),
    )

    assert device_auth.command[0] == str(executable)
    assert device_auth.relative_output_path == Path(".codex") / "auth.json"
    assert readonly.command == (str(executable), "auth", "list")
    assert readonly.readonly_inputs == (
        ReadonlySandboxInput(source=executable, destination=executable),
        ReadonlySandboxInput(source=runtime_root, destination=runtime_root),
    )
    with pytest.raises(SandboxLaunchValidationError, match="provider-declared"):
        resolver.resolve_readonly(PROVIDERS["codex"], ("opencode", "auth", "list"))


def test_runtime_input_manifest_binds_terminal_source_at_logical_loader_path(
    tmp_path: Path,
) -> None:
    """REQ-core-credentials-002: an ELF alias never becomes an unchecked bind source."""
    from butlers.cli_auth.sandbox_platform import (
        ReadonlySandboxInput,
        RuntimeCLIInputManifest,
        SandboxIdentity,
        build_bubblewrap_launch_plan,
    )

    runtime_root = tmp_path / "runtime"
    runtime_root.mkdir(mode=0o755)
    executable = runtime_root / "codex"
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    os.chmod(executable, 0o555)
    terminal_loader = runtime_root / "ld-linux-terminal.so"
    terminal_loader.write_bytes(b"immutable test loader")
    os.chmod(terminal_loader, 0o444)
    logical_loader = tmp_path / "logical" / "lib64" / "ld-linux-test.so"
    manifest_path = tmp_path / "runtime-cli-sandbox-inputs.json"
    manifest_path.write_text(
        json.dumps(
            {
                "version": 2,
                "providers": {
                    "codex": {
                        "binary": "codex",
                        "executable": str(executable),
                        "readonly_inputs": [
                            {
                                "source": str(terminal_loader),
                                "destination": str(logical_loader),
                            }
                        ],
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    os.chmod(manifest_path, 0o444)
    readonly = RuntimeCLIInputManifest(
        manifest_path,
        expected_uid=os.geteuid(),
    ).resolve_readonly(PROVIDERS["codex"], ("codex", "auth", "list"))

    assert (
        ReadonlySandboxInput(
            source=terminal_loader,
            destination=logical_loader,
        )
        in readonly.readonly_inputs
    )

    info_read, info_write = os.pipe2(os.O_CLOEXEC)
    block_read, block_write = os.pipe2(os.O_CLOEXEC)
    shim_gate_read, shim_gate_write = os.pipe2(os.O_CLOEXEC)
    try:
        plan = build_bubblewrap_launch_plan(
            bwrap_path=Path("/usr/bin/bwrap"),
            shim_path=Path("/usr/local/libexec/butlers/runtime-cli-sandbox-init"),
            identity=SandboxIdentity(uid=61000, gid=61000),
            stage_home=tmp_path / "stage",
            command=readonly.command,
            readonly_inputs=readonly.readonly_inputs,
            info_fd=info_write,
            block_fd=block_read,
            shim_gate_fd=shim_gate_read,
        )
    finally:
        for fd in (
            info_read,
            info_write,
            block_read,
            block_write,
            shim_gate_read,
            shim_gate_write,
        ):
            os.close(fd)

    rendered = tuple(plan.argv)
    assert (
        "--ro-bind",
        str(terminal_loader),
        str(logical_loader),
    ) in tuple(rendered[index : index + 3] for index in range(len(rendered) - 2))


async def test_invocation_identity_pool_never_reuses_a_live_identity() -> None:
    """REQ-core-credentials-002: exhaustion fails closed rather than sharing a child UID."""
    from butlers.cli_auth.sandbox_platform import InvocationIdentityPool, SandboxUnavailableError

    pool = InvocationIdentityPool(first_id=61000, last_id=61000)
    first = await pool.acquire()

    with pytest.raises(SandboxUnavailableError, match="identity pool exhausted"):
        await pool.acquire()

    await pool.release(first)
    assert await pool.acquire() == first


def test_stage_parent_is_traversable_but_not_listable_by_child_identities() -> None:
    """REQ-core-credentials-002: a child can bind only its unguessable 0700 stage."""
    from butlers.cli_auth.sandbox_platform import _STAGE_DIRECTORY_MODE, _STAGE_ROOT_MODE

    assert _STAGE_ROOT_MODE == 0o711
    assert _STAGE_ROOT_MODE & 0o066 == 0
    assert _STAGE_ROOT_MODE & 0o011 == 0o011
    assert _STAGE_DIRECTORY_MODE == 0o700


def test_bubblewrap_launch_plan_is_minimal_and_uses_only_typed_handshake_fds(
    tmp_path: Path,
) -> None:
    """REQ-core-credentials-002: the child view excludes broad host mounts and secrets."""
    from butlers.cli_auth.sandbox_platform import (
        SandboxIdentity,
        build_bubblewrap_launch_plan,
    )

    info_read, info_write = os.pipe2(os.O_CLOEXEC)
    block_read, block_write = os.pipe2(os.O_CLOEXEC)
    shim_gate_read, shim_gate_write = os.pipe2(os.O_CLOEXEC)
    try:
        plan = build_bubblewrap_launch_plan(
            bwrap_path=Path("/usr/bin/bwrap"),
            shim_path=Path("/usr/local/libexec/butlers/runtime-cli-sandbox-init"),
            identity=SandboxIdentity(uid=61000, gid=61000),
            stage_home=tmp_path / "stage-home",
            command=("/usr/local/bin/codex", "login", "--device-auth"),
            readonly_inputs=(
                Path("/usr/local/bin/codex"),
                Path("/usr/bin/node"),
                Path("/lib/x86_64-linux-gnu/libc.so.6"),
                Path("/etc/ssl/certs/ca-certificates.crt"),
                Path("/etc/resolv.conf"),
            ),
            info_fd=info_write,
            block_fd=block_read,
            shim_gate_fd=shim_gate_read,
        )

        assert plan.pass_fds == (info_write, block_read, shim_gate_read)
    finally:
        for fd in (
            info_read,
            info_write,
            block_read,
            block_write,
            shim_gate_read,
            shim_gate_write,
        ):
            os.close(fd)

    assert plan.close_fds is True
    assert plan.environment == {
        "HOME": "/home/runtime",
        "PATH": "/usr/local/bin:/usr/bin",
        "TMPDIR": "/tmp",
        "XDG_CACHE_HOME": "/home/runtime/.cache",
        "XDG_CONFIG_HOME": "/home/runtime/.config",
        "XDG_DATA_HOME": "/home/runtime/.local/share",
    }
    for required in (
        "--unshare-user",
        "--unshare-pid",
        "--unshare-ipc",
        "--unshare-uts",
        "--as-pid-1",
        "--die-with-parent",
        "--clearenv",
        "--proc",
        "--dev",
        "--tmpfs",
        "--info-fd",
        "--block-fd",
        "--shim-gate-fd",
    ):
        assert required in plan.argv

    rendered = "\0".join(plan.argv)
    assert "--ro-bind\0/\0/" not in rendered
    assert "--bind\0/\0/" not in rendered
    absolute_values = [value for value in plan.argv if value.startswith("/")]
    for forbidden in ("/root", "/run", "/app"):
        assert not any(
            value == forbidden or value.startswith(f"{forbidden}/") for value in absolute_values
        )
    assert "/proc" in plan.argv
    assert not any(value.startswith("/proc/") for value in absolute_values)
    assert "runtime_probe_control" not in rendered


def test_bubblewrap_handshake_rejects_untyped_or_wrong_direction_fds() -> None:
    """REQ-core-credentials-002: only launcher-created write-info/read-block pipes may pass."""
    from butlers.cli_auth.sandbox_platform import (
        SandboxLaunchValidationError,
        validate_handshake_fds,
    )

    info_read, info_write = os.pipe2(os.O_CLOEXEC)
    block_read, block_write = os.pipe2(os.O_CLOEXEC)
    shim_gate_read, shim_gate_write = os.pipe2(os.O_CLOEXEC)
    try:
        assert validate_handshake_fds(
            info_fd=info_write,
            block_fd=block_read,
            shim_gate_fd=shim_gate_read,
        ) == (
            info_write,
            block_read,
            shim_gate_read,
        )
        with pytest.raises(SandboxLaunchValidationError, match="info pipe"):
            validate_handshake_fds(
                info_fd=info_read,
                block_fd=block_read,
                shim_gate_fd=shim_gate_read,
            )
        with pytest.raises(SandboxLaunchValidationError, match="block pipe"):
            validate_handshake_fds(
                info_fd=info_write,
                block_fd=block_write,
                shim_gate_fd=shim_gate_read,
            )
        with pytest.raises(SandboxLaunchValidationError, match="shim gate pipe"):
            validate_handshake_fds(
                info_fd=info_write,
                block_fd=block_read,
                shim_gate_fd=shim_gate_write,
            )
    finally:
        for fd in (
            info_read,
            info_write,
            block_read,
            block_write,
            shim_gate_read,
            shim_gate_write,
        ):
            os.close(fd)


class _HandshakeStreamReader(asyncio.StreamReader):
    """Records when the parent can first consume the namespace-init receipt."""

    def __init__(self, events: list[str]) -> None:
        super().__init__()
        self._events = events

    async def readline(self) -> bytes:
        self._events.append("shim_ready")
        return await super().readline()


class _HandshakeProcess:
    """Direct Bubblewrap child stand-in for startup-handshake ordering tests."""

    def __init__(self, events: list[str]) -> None:
        self.stdout = _HandshakeStreamReader(events)
        self.stdout.feed_data(b"BUTLERS_RUNTIME_CLI_SANDBOX_READY\nprovider output\n")
        self.stdout.feed_eof()
        self.returncode: int | None = None
        self.wait_calls = 0

    async def wait(self) -> int:
        self.wait_calls += 1
        self.returncode = 0
        return 0

    def terminate(self) -> None:
        self.returncode = 0

    def kill(self) -> None:
        self.returncode = 0


class _BlockingHandshakeProcess(_HandshakeProcess):
    """A child whose namespace-init receipt never arrives before cancellation."""

    def __init__(self, events: list[str]) -> None:
        self.stdout = asyncio.StreamReader()
        self.returncode = None
        self.wait_calls = 0
        self._events = events


async def test_bubblewrap_info_reader_accepts_a_fragmented_receipt() -> None:
    """REQ-core-credentials-002: Bubblewrap's multi-write receipt is one record."""
    from butlers.cli_auth.sandbox_platform import _read_bubblewrap_info

    info_read, info_write = os.pipe()
    first_fragment_written = threading.Event()

    def _write_fragments() -> None:
        try:
            os.write(info_write, b'{"child-pid":')
            first_fragment_written.set()
            time.sleep(0.05)
            os.write(info_write, b" 7123}\n")
        finally:
            os.close(info_write)

    writer = threading.Thread(target=_write_fragments)
    writer.start()
    assert first_fragment_written.wait(timeout=1)
    try:
        assert await _read_bubblewrap_info(info_read) == 7123
    finally:
        writer.join(timeout=1)
        os.close(info_read)


async def test_bubblewrap_launcher_opens_pidfd_before_releasing_payload_or_reading_shim(
    tmp_path: Path,
) -> None:
    """REQ-core-credentials-002: PID1 receipt precedes payload release and CLI output."""
    from butlers.cli_auth.sandbox_platform import (
        BubblewrapDashboardCLIAuthSandbox,
        DeviceAuthSandboxInvocation,
        InvocationIdentityPool,
        SandboxStage,
    )

    events: list[str] = []
    process = _HandshakeProcess(events)
    captured_block_fd: list[int] = []
    captured_shim_gate_fd: list[int] = []

    async def _spawn(*argv: str, **kwargs: object) -> _HandshakeProcess:
        events.append("spawn")
        assert kwargs["close_fds"] is True
        assert kwargs["stdin"] is asyncio.subprocess.DEVNULL
        assert kwargs["stderr"] is asyncio.subprocess.STDOUT
        assert kwargs["pass_fds"] == (
            int(argv[argv.index("--info-fd") + 1]),
            int(argv[argv.index("--block-fd") + 1]),
            int(argv[argv.index("--shim-gate-fd") + 1]),
        )
        info_fd = int(argv[argv.index("--info-fd") + 1])
        captured_block_fd.append(os.dup(int(argv[argv.index("--block-fd") + 1])))
        captured_shim_gate_fd.append(os.dup(int(argv[argv.index("--shim-gate-fd") + 1])))
        os.write(info_fd, b'{"child-pid": 7123}\n')
        return process

    def _open_pidfd(pid: int, _flags: int = 0) -> int:
        assert pid == 7123
        events.append("pidfd_open")
        return os.open("/dev/null", os.O_RDONLY | os.O_CLOEXEC)

    def _release_payload(block_fd: int) -> None:
        events.append("bwrap_release" if len(events) == 2 else "shim_release")
        os.write(block_fd, b"1")

    stage_home = tmp_path / "stage-home"
    stage_home.mkdir(mode=0o700)
    stage_fd = os.open(stage_home, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)

    def _stage_factory(_identity) -> SandboxStage:
        return SandboxStage(path=stage_home, root_fd=stage_fd)

    invocation = DeviceAuthSandboxInvocation(
        command=("/usr/bin/true",),
        readonly_inputs=(Path("/usr/bin/true"),),
        relative_output_path=Path(".codex") / "auth.json",
    )
    sandbox = BubblewrapDashboardCLIAuthSandbox(
        bwrap_path=tmp_path / "bwrap",
        shim_path=tmp_path / "runtime-cli-sandbox-init",
        identity_pool=InvocationIdentityPool(first_id=61000, last_id=61000),
        exact_image_preflight=lambda: None,
        invocation_resolver=lambda _provider: invocation,
        stage_factory=_stage_factory,
        spawn=_spawn,
        pidfd_open=_open_pidfd,
        release_payload=_release_payload,
        pidfd_send_signal=lambda *_args: None,
        pidfd_is_dead=lambda _pidfd, _timeout: True,
    )

    handle = await sandbox.launch_device_auth(PROVIDERS["codex"])
    try:
        assert events == ["spawn", "pidfd_open", "bwrap_release", "shim_release", "shim_ready"]
        assert os.read(captured_block_fd[0], 1) == b"1"
        assert os.read(captured_shim_gate_fd[0], 1) == b"1"
        assert await handle.process.stdout.readline() == b"provider output\n"
    finally:
        for fd in [*captured_block_fd, *captured_shim_gate_fd]:
            os.close(fd)
        await handle.terminate()


async def test_pidfd_open_failure_after_pid1_receipt_retains_stage_and_identity(
    tmp_path: Path,
) -> None:
    """REQ-core-credentials-002: no pidfd proof means no stage or UID reuse."""
    from butlers.cli_auth.sandbox import SandboxUnavailableError
    from butlers.cli_auth.sandbox_platform import (
        BubblewrapDashboardCLIAuthSandbox,
        DeviceAuthSandboxInvocation,
        InvocationIdentityPool,
        SandboxStage,
    )

    events: list[str] = []
    process = _HandshakeProcess(events)
    pool = InvocationIdentityPool(first_id=61000, last_id=61000)
    stage_home = tmp_path / "stage-home"
    stage_home.mkdir(mode=0o700)
    stage_fd = os.open(stage_home, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    block_observer_fd: list[int] = []
    shim_gate_observer_fd: list[int] = []

    async def _spawn(*argv: str, **_kwargs: object) -> _HandshakeProcess:
        info_fd = int(argv[argv.index("--info-fd") + 1])
        block_observer_fd.append(os.dup(int(argv[argv.index("--block-fd") + 1])))
        shim_gate_observer_fd.append(os.dup(int(argv[argv.index("--shim-gate-fd") + 1])))
        os.write(info_fd, b'{"child-pid": 7123}\n')
        return process

    def _pidfd_open(_pid: int, _flags: int = 0) -> int:
        events.append("pidfd_open")
        raise OSError("synthetic pidfd-open failure")

    sandbox = BubblewrapDashboardCLIAuthSandbox(
        bwrap_path=tmp_path / "bwrap",
        shim_path=tmp_path / "runtime-cli-sandbox-init",
        identity_pool=pool,
        exact_image_preflight=lambda: None,
        invocation_resolver=lambda _provider: DeviceAuthSandboxInvocation(
            command=("/usr/bin/true",),
            readonly_inputs=(Path("/usr/bin/true"),),
            relative_output_path=Path(".codex") / "auth.json",
        ),
        stage_factory=lambda _identity: SandboxStage(path=stage_home, root_fd=stage_fd),
        spawn=_spawn,
        pidfd_open=_pidfd_open,
        release_payload=lambda _fd: events.append("block_release"),
    )

    try:
        with pytest.raises(SandboxUnavailableError, match="startup failed safely"):
            await sandbox.launch_device_auth(PROVIDERS["codex"])

        assert events == ["pidfd_open"]
        assert block_observer_fd
        assert shim_gate_observer_fd
        os.set_blocking(block_observer_fd[0], False)
        with pytest.raises(BlockingIOError):
            os.read(block_observer_fd[0], 1)
        assert os.read(shim_gate_observer_fd[0], 1) == b""
        assert process.wait_calls == 0
        assert len(sandbox._quarantined_startup_gates) == 1
        assert stage_home.exists(), "a PID1 without a pidfd can retain the stage bind"
        with pytest.raises(SandboxUnavailableError, match="identity pool exhausted"):
            await pool.acquire()
    finally:
        for retained in sandbox._quarantined_startup_gates:
            os.close(retained.block_write_fd)
        for fd in [*block_observer_fd, *shim_gate_observer_fd]:
            os.close(fd)
        try:
            os.close(stage_fd)
        except OSError:
            pass
        shutil.rmtree(stage_home, ignore_errors=True)


async def test_pid1_receipt_eof_quarantines_the_bwrap_gate_before_provider_execution(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """REQ-core-credentials-002: receipt EOF cannot release an unproven domain."""
    from butlers.cli_auth.sandbox import SandboxUnavailableError
    from butlers.cli_auth.sandbox_platform import (
        BubblewrapDashboardCLIAuthSandbox,
        DeviceAuthSandboxInvocation,
        InvocationIdentityPool,
        SandboxStage,
    )

    events: list[str] = []
    process = _HandshakeProcess(events)
    pool = InvocationIdentityPool(first_id=61000, last_id=61000)
    stage_home = tmp_path / "stage-home"
    stage_home.mkdir(mode=0o700)
    stage_fd = os.open(stage_home, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    block_observer_fd: list[int] = []
    shim_gate_observer_fd: list[int] = []

    async def _spawn(*argv: str, **_kwargs: object) -> _HandshakeProcess:
        info_fd = int(argv[argv.index("--info-fd") + 1])
        info_fd_copy = os.dup(info_fd)
        os.close(info_fd_copy)
        block_observer_fd.append(os.dup(int(argv[argv.index("--block-fd") + 1])))
        shim_gate_observer_fd.append(os.dup(int(argv[argv.index("--shim-gate-fd") + 1])))
        return process

    sandbox = BubblewrapDashboardCLIAuthSandbox(
        bwrap_path=tmp_path / "bwrap",
        shim_path=tmp_path / "runtime-cli-sandbox-init",
        identity_pool=pool,
        exact_image_preflight=lambda: None,
        invocation_resolver=lambda _provider: DeviceAuthSandboxInvocation(
            command=("/usr/bin/true",),
            readonly_inputs=(Path("/usr/bin/true"),),
            relative_output_path=Path(".codex") / "auth.json",
        ),
        stage_factory=lambda _identity: SandboxStage(path=stage_home, root_fd=stage_fd),
        spawn=_spawn,
        release_payload=lambda _fd: events.append("payload_release"),
    )

    try:
        with caplog.at_level("WARNING", logger="butlers.cli_auth.sandbox_platform"):
            with pytest.raises(SandboxUnavailableError, match="startup failed safely"):
                await sandbox.launch_device_auth(PROVIDERS["codex"])

        assert events == []
        os.set_blocking(block_observer_fd[0], False)
        with pytest.raises(BlockingIOError):
            os.read(block_observer_fd[0], 1)
        assert os.read(shim_gate_observer_fd[0], 1) == b""
        assert process.wait_calls == 0
        assert len(sandbox._quarantined_startup_gates) == 1
        assert stage_home.exists()
        assert "phase=pid1_receipt cleanup_outcome=quarantined" in caplog.text
        assert "phase=pid1_receipt error_class=SandboxLaunchValidationError" in caplog.text
        assert "/usr/bin/true" not in caplog.text
        with pytest.raises(SandboxUnavailableError, match="identity pool exhausted"):
            await pool.acquire()
    finally:
        for retained in sandbox._quarantined_startup_gates:
            os.close(retained.block_write_fd)
        for fd in [*block_observer_fd, *shim_gate_observer_fd]:
            os.close(fd)
        try:
            os.close(stage_fd)
        except OSError:
            pass
        shutil.rmtree(stage_home, ignore_errors=True)


async def test_bubblewrap_launcher_oversized_shim_ready_line_aborts_the_started_domain(
    tmp_path: Path,
) -> None:
    """REQ-core-credentials-002: malformed shim stdout follows startup containment cleanup."""
    from butlers.cli_auth.sandbox import SandboxUnavailableError
    from butlers.cli_auth.sandbox_platform import (
        BubblewrapDashboardCLIAuthSandbox,
        DeviceAuthSandboxInvocation,
        InvocationIdentityPool,
        SandboxStage,
    )

    class _OversizedReadyProcess:
        def __init__(self) -> None:
            self.stdout = asyncio.StreamReader(limit=64)
            self.stdout.feed_data(b"x" * 65)
            self.returncode: int | None = None
            self.wait_calls = 0

        async def wait(self) -> int:
            self.wait_calls += 1
            self.returncode = 0
            return 0

        def terminate(self) -> None:
            self.returncode = 0

        def kill(self) -> None:
            self.returncode = 0

    process = _OversizedReadyProcess()
    pool = InvocationIdentityPool(first_id=61000, last_id=61000)
    stage_home = tmp_path / "stage-home"
    stage_home.mkdir(mode=0o700)
    stage_fd = os.open(stage_home, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    block_observer_fd: list[int] = []

    async def _spawn(*argv: str, **_kwargs: object) -> _OversizedReadyProcess:
        info_fd = int(argv[argv.index("--info-fd") + 1])
        block_observer_fd.append(os.dup(int(argv[argv.index("--block-fd") + 1])))
        block_observer_fd.append(os.dup(int(argv[argv.index("--shim-gate-fd") + 1])))
        os.write(info_fd, b'{"child-pid": 7123}\n')
        return process

    sandbox = BubblewrapDashboardCLIAuthSandbox(
        bwrap_path=tmp_path / "bwrap",
        shim_path=tmp_path / "runtime-cli-sandbox-init",
        identity_pool=pool,
        exact_image_preflight=lambda: None,
        invocation_resolver=lambda _provider: DeviceAuthSandboxInvocation(
            command=("/usr/bin/true",),
            readonly_inputs=(Path("/usr/bin/true"),),
            relative_output_path=Path(".codex") / "auth.json",
        ),
        stage_factory=lambda _identity: SandboxStage(path=stage_home, root_fd=stage_fd),
        spawn=_spawn,
        pidfd_open=lambda _pid, _flags=0: os.open("/dev/null", os.O_RDONLY | os.O_CLOEXEC),
        pidfd_send_signal=lambda *_args: None,
        pidfd_is_dead=lambda _pidfd, _timeout: True,
    )

    try:
        with pytest.raises(SandboxUnavailableError, match="startup failed safely"):
            await sandbox.launch_device_auth(PROVIDERS["codex"])

        assert process.wait_calls == 1
        assert not stage_home.exists()
        reused = await pool.acquire()
        assert reused.uid == 61000
        await pool.release(reused)
    finally:
        for fd in block_observer_fd:
            os.close(fd)
        try:
            os.close(stage_fd)
        except OSError:
            pass
        shutil.rmtree(stage_home, ignore_errors=True)


async def test_readonly_command_stages_authority_before_launch_and_discards_child_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """REQ-core-credentials-002: health/API children see only a disposable authority copy."""
    from butlers.cli_auth.sandbox import ReadonlySandboxAuthority
    from butlers.cli_auth.sandbox_platform import (
        BubblewrapDashboardCLIAuthSandbox,
        InvocationIdentityPool,
        ReadonlySandboxInvocation,
        SandboxStage,
    )

    canonical = tmp_path / "canonical" / "auth.json"
    canonical.parent.mkdir()
    canonical.write_text('{"canonical":"must-not-change"}', encoding="utf-8")
    os.chmod(canonical, 0o600)
    stage_home = tmp_path / "stage-home"
    stage_home.mkdir(mode=0o700)
    stage_fd = os.open(stage_home, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    process = _HandshakeProcess([])
    stage_calls = 0
    chowns: list[tuple[int, int, int]] = []
    block_readers: list[int] = []

    def _record_fchown(fd: int, uid: int, gid: int) -> None:
        chowns.append((fd, uid, gid))

    monkeypatch.setattr(os, "fchown", _record_fchown)

    async def _spawn(*argv: str, **_kwargs: object) -> _HandshakeProcess:
        nonlocal stage_calls
        assert stage_calls == 1
        staged_authority = stage_home / ".codex" / "auth.json"
        assert staged_authority.read_bytes() == canonical.read_bytes()
        metadata = staged_authority.stat()
        assert stat.S_IMODE(metadata.st_mode) == 0o600
        staged_authority.write_text('{"child":"discarded"}', encoding="utf-8")
        info_fd = int(argv[argv.index("--info-fd") + 1])
        block_readers.append(os.dup(int(argv[argv.index("--block-fd") + 1])))
        block_readers.append(os.dup(int(argv[argv.index("--shim-gate-fd") + 1])))
        os.write(info_fd, b'{"child-pid": 7123}\n')
        return process

    def _stage_factory(_identity) -> SandboxStage:
        nonlocal stage_calls
        stage_calls += 1
        return SandboxStage(path=stage_home, root_fd=stage_fd)

    sandbox = BubblewrapDashboardCLIAuthSandbox(
        bwrap_path=tmp_path / "bwrap",
        shim_path=tmp_path / "runtime-cli-sandbox-init",
        identity_pool=InvocationIdentityPool(first_id=61000, last_id=61000),
        exact_image_preflight=lambda: None,
        readonly_invocation_resolver=lambda _provider, command: ReadonlySandboxInvocation(
            command=command,
            readonly_inputs=(Path("/usr/bin/true"),),
        ),
        stage_factory=_stage_factory,
        spawn=_spawn,
        pidfd_open=lambda _pid, _flags=0: os.open("/dev/null", os.O_RDONLY | os.O_CLOEXEC),
        pidfd_send_signal=lambda *_args: None,
        pidfd_is_dead=lambda _pidfd, _timeout: True,
    )

    try:
        result = await sandbox.run_readonly_command(
            PROVIDERS["codex"],
            command=("/usr/bin/true",),
            authority=ReadonlySandboxAuthority(
                relative_path=Path(".codex") / "auth.json",
                content=canonical.read_bytes(),
            ),
            timeout_s=1.0,
        )
    finally:
        for fd in block_readers:
            os.close(fd)

    assert result.returncode == 0
    assert result.output == b"provider output\n"
    assert canonical.read_text(encoding="utf-8") == '{"canonical":"must-not-change"}'
    assert not stage_home.exists()
    assert len(chowns) == 2
    assert all((uid, gid) == (61000, 61000) for _fd, uid, gid in chowns)


async def test_readonly_command_rejects_an_unsafe_authority_target_before_spawn(
    tmp_path: Path,
) -> None:
    """REQ-core-credentials-002: a staged-copy path escape launches no child domain."""
    from butlers.cli_auth.sandbox import ReadonlySandboxAuthority, SandboxUnavailableError
    from butlers.cli_auth.sandbox_platform import (
        BubblewrapDashboardCLIAuthSandbox,
        InvocationIdentityPool,
        ReadonlySandboxInvocation,
        SandboxStage,
    )

    stage_home = tmp_path / "stage-home"
    stage_home.mkdir(mode=0o700)
    stage_fd = os.open(stage_home, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    spawn = AsyncMock()
    sandbox = BubblewrapDashboardCLIAuthSandbox(
        bwrap_path=tmp_path / "bwrap",
        shim_path=tmp_path / "runtime-cli-sandbox-init",
        identity_pool=InvocationIdentityPool(first_id=61000, last_id=61000),
        exact_image_preflight=lambda: None,
        readonly_invocation_resolver=lambda _provider, command: ReadonlySandboxInvocation(
            command=command,
            readonly_inputs=(Path("/usr/bin/true"),),
        ),
        stage_factory=lambda _identity: SandboxStage(path=stage_home, root_fd=stage_fd),
        spawn=spawn,
    )

    with pytest.raises(SandboxUnavailableError, match="startup failed safely"):
        await sandbox.run_readonly_command(
            PROVIDERS["codex"],
            command=("/usr/bin/true",),
            authority=ReadonlySandboxAuthority(
                relative_path=Path("..") / "canonical" / "auth.json",
                content=b"not-a-real-credential",
            ),
            timeout_s=1.0,
        )

    spawn.assert_not_awaited()
    assert not stage_home.exists()


async def test_readonly_command_rejects_oversized_stdout_before_materializing_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """REQ-core-credentials-002: untrusted child stdout has a fixed materialization bound."""
    from butlers.cli_auth.sandbox import ReadonlySandboxAuthority, SandboxUnavailableError
    from butlers.cli_auth.sandbox_platform import (
        _MAX_READONLY_COMMAND_OUTPUT_BYTES,
        BubblewrapDashboardCLIAuthSandbox,
        InvocationIdentityPool,
        ReadonlySandboxInvocation,
        SandboxStage,
    )

    class _BoundedReadStream(asyncio.StreamReader):
        async def read(self, n: int = -1) -> bytes:
            assert 0 < n <= _MAX_READONLY_COMMAND_OUTPUT_BYTES
            return await super().read(n)

    process = _HandshakeProcess([])
    process.stdout = _BoundedReadStream()
    process.stdout.feed_data(
        b"BUTLERS_RUNTIME_CLI_SANDBOX_READY\n" + b"x" * (_MAX_READONLY_COMMAND_OUTPUT_BYTES + 1)
    )
    process.stdout.feed_eof()
    stage_home = tmp_path / "stage-home"
    stage_home.mkdir(mode=0o700)
    stage_fd = os.open(stage_home, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    block_readers: list[int] = []
    monkeypatch.setattr(os, "fchown", lambda *_args: None)

    async def _spawn(*argv: str, **_kwargs: object) -> _HandshakeProcess:
        info_fd = int(argv[argv.index("--info-fd") + 1])
        block_readers.append(os.dup(int(argv[argv.index("--block-fd") + 1])))
        block_readers.append(os.dup(int(argv[argv.index("--shim-gate-fd") + 1])))
        os.write(info_fd, b'{"child-pid": 7123}\n')
        return process

    sandbox = BubblewrapDashboardCLIAuthSandbox(
        bwrap_path=tmp_path / "bwrap",
        shim_path=tmp_path / "runtime-cli-sandbox-init",
        identity_pool=InvocationIdentityPool(first_id=61000, last_id=61000),
        exact_image_preflight=lambda: None,
        readonly_invocation_resolver=lambda _provider, command: ReadonlySandboxInvocation(
            command=command,
            readonly_inputs=(Path("/usr/bin/true"),),
        ),
        stage_factory=lambda _identity: SandboxStage(path=stage_home, root_fd=stage_fd),
        spawn=_spawn,
        pidfd_open=lambda _pid, _flags=0: os.open("/dev/null", os.O_RDONLY | os.O_CLOEXEC),
        pidfd_send_signal=lambda *_args: None,
        pidfd_is_dead=lambda _pidfd, _timeout: True,
    )

    try:
        with pytest.raises(SandboxUnavailableError, match="stdout exceeded"):
            await sandbox.run_readonly_command(
                PROVIDERS["codex"],
                command=("/usr/bin/true",),
                authority=ReadonlySandboxAuthority(
                    relative_path=Path(".codex") / "auth.json",
                    content=b'{"safe":"staged"}',
                ),
                timeout_s=1.0,
            )
    finally:
        for fd in block_readers:
            os.close(fd)

    assert not stage_home.exists()


async def test_readonly_command_waits_for_eof_after_a_partial_first_stdout_chunk(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """REQ-core-credentials-002: a partial read never looks like a completed child."""
    from butlers.cli_auth.sandbox import ReadonlySandboxAuthority
    from butlers.cli_auth.sandbox_platform import (
        BubblewrapDashboardCLIAuthSandbox,
        InvocationIdentityPool,
        ReadonlySandboxInvocation,
        SandboxStage,
    )

    first_chunk_seen = asyncio.Event()

    class _PartialReadStream(asyncio.StreamReader):
        async def read(self, n: int = -1) -> bytes:
            chunk = await super().read(n)
            if chunk:
                first_chunk_seen.set()
            return chunk

    process = _HandshakeProcess([])
    process.stdout = _PartialReadStream()
    process.stdout.feed_data(b"BUTLERS_RUNTIME_CLI_SANDBOX_READY\npartial-result")
    stage_home = tmp_path / "stage-home"
    stage_home.mkdir(mode=0o700)
    stage_fd = os.open(stage_home, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    block_readers: list[int] = []
    monkeypatch.setattr(os, "fchown", lambda *_args: None)

    async def _spawn(*argv: str, **_kwargs: object) -> _HandshakeProcess:
        info_fd = int(argv[argv.index("--info-fd") + 1])
        block_readers.append(os.dup(int(argv[argv.index("--block-fd") + 1])))
        block_readers.append(os.dup(int(argv[argv.index("--shim-gate-fd") + 1])))
        os.write(info_fd, b'{"child-pid": 7123}\n')
        return process

    sandbox = BubblewrapDashboardCLIAuthSandbox(
        bwrap_path=tmp_path / "bwrap",
        shim_path=tmp_path / "runtime-cli-sandbox-init",
        identity_pool=InvocationIdentityPool(first_id=61000, last_id=61000),
        exact_image_preflight=lambda: None,
        readonly_invocation_resolver=lambda _provider, command: ReadonlySandboxInvocation(
            command=command,
            readonly_inputs=(Path("/usr/bin/true"),),
        ),
        stage_factory=lambda _identity: SandboxStage(path=stage_home, root_fd=stage_fd),
        spawn=_spawn,
        pidfd_open=lambda _pid, _flags=0: os.open("/dev/null", os.O_RDONLY | os.O_CLOEXEC),
        pidfd_send_signal=lambda *_args: None,
        pidfd_is_dead=lambda _pidfd, _timeout: True,
    )

    task = asyncio.create_task(
        sandbox.run_readonly_command(
            PROVIDERS["codex"],
            command=("/usr/bin/true",),
            authority=ReadonlySandboxAuthority(
                relative_path=Path(".codex") / "auth.json",
                content=b'{"safe":"staged"}',
            ),
            timeout_s=1.0,
        )
    )
    try:
        await asyncio.wait_for(first_chunk_seen.wait(), timeout=1.0)
        await asyncio.sleep(0)
        assert not task.done()
        assert process.wait_calls == 0
        process.stdout.feed_data(b"-second-chunk")
        await asyncio.sleep(0)
        assert process.wait_calls == 0
        process.stdout.feed_eof()
        result = await task
    finally:
        for fd in block_readers:
            os.close(fd)

    assert result.output == b"partial-result-second-chunk"
    assert not stage_home.exists()


async def test_readonly_command_times_out_after_partial_stdout_and_cleans_the_domain(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """REQ-core-credentials-002: a no-EOF child cannot retain its stage or identity."""
    from butlers.cli_auth.sandbox import ReadonlySandboxAuthority
    from butlers.cli_auth.sandbox_platform import (
        BubblewrapDashboardCLIAuthSandbox,
        InvocationIdentityPool,
        ReadonlySandboxInvocation,
        SandboxStage,
    )

    process = _HandshakeProcess([])
    process.stdout = asyncio.StreamReader()
    process.stdout.feed_data(b"BUTLERS_RUNTIME_CLI_SANDBOX_READY\npartial-result")
    stage_home = tmp_path / "stage-home"
    stage_home.mkdir(mode=0o700)
    stage_fd = os.open(stage_home, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    block_readers: list[int] = []
    monkeypatch.setattr(os, "fchown", lambda *_args: None)

    async def _spawn(*argv: str, **_kwargs: object) -> _HandshakeProcess:
        info_fd = int(argv[argv.index("--info-fd") + 1])
        block_readers.append(os.dup(int(argv[argv.index("--block-fd") + 1])))
        block_readers.append(os.dup(int(argv[argv.index("--shim-gate-fd") + 1])))
        os.write(info_fd, b'{"child-pid": 7123}\n')
        return process

    sandbox = BubblewrapDashboardCLIAuthSandbox(
        bwrap_path=tmp_path / "bwrap",
        shim_path=tmp_path / "runtime-cli-sandbox-init",
        identity_pool=InvocationIdentityPool(first_id=61000, last_id=61000),
        exact_image_preflight=lambda: None,
        readonly_invocation_resolver=lambda _provider, command: ReadonlySandboxInvocation(
            command=command,
            readonly_inputs=(Path("/usr/bin/true"),),
        ),
        stage_factory=lambda _identity: SandboxStage(path=stage_home, root_fd=stage_fd),
        spawn=_spawn,
        pidfd_open=lambda _pid, _flags=0: os.open("/dev/null", os.O_RDONLY | os.O_CLOEXEC),
        pidfd_send_signal=lambda *_args: None,
        pidfd_is_dead=lambda _pidfd, _timeout: True,
    )

    try:
        with pytest.raises(TimeoutError):
            await sandbox.run_readonly_command(
                PROVIDERS["codex"],
                command=("/usr/bin/true",),
                authority=ReadonlySandboxAuthority(
                    relative_path=Path(".codex") / "auth.json",
                    content=b'{"safe":"staged"}',
                ),
                timeout_s=0.01,
            )
    finally:
        for fd in block_readers:
            os.close(fd)

    assert process.wait_calls == 1
    assert not stage_home.exists()


async def test_readonly_command_uses_one_absolute_timeout_across_multiple_chunks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """REQ-core-credentials-002: each small chunk cannot renew the total deadline."""
    from butlers.cli_auth import sandbox_platform
    from butlers.cli_auth.sandbox import ReadonlySandboxAuthority
    from butlers.cli_auth.sandbox_platform import (
        BubblewrapDashboardCLIAuthSandbox,
        InvocationIdentityPool,
        ReadonlySandboxInvocation,
        SandboxStage,
    )

    class _SlowChunkStream(asyncio.StreamReader):
        def __init__(self) -> None:
            super().__init__()
            self.read_calls = 0
            self.second_chunk_seen = asyncio.Event()
            self._block = asyncio.Event()

        async def read(self, n: int = -1) -> bytes:
            self.read_calls += 1
            if self.read_calls == 1:
                return b"first-chunk"
            if self.read_calls == 2:
                self.second_chunk_seen.set()
                return b"second-chunk"
            await self._block.wait()
            return b""

    process = _HandshakeProcess([])
    process.stdout = _SlowChunkStream()
    process.stdout.feed_data(b"BUTLERS_RUNTIME_CLI_SANDBOX_READY\n")
    stage_home = tmp_path / "stage-home"
    stage_home.mkdir(mode=0o700)
    stage_fd = os.open(stage_home, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    block_readers: list[int] = []
    monkeypatch.setattr(os, "fchown", lambda *_args: None)

    async def _spawn(*argv: str, **_kwargs: object) -> _HandshakeProcess:
        info_fd = int(argv[argv.index("--info-fd") + 1])
        block_readers.append(os.dup(int(argv[argv.index("--block-fd") + 1])))
        block_readers.append(os.dup(int(argv[argv.index("--shim-gate-fd") + 1])))
        os.write(info_fd, b'{"child-pid": 7123}\n')
        return process

    sandbox = BubblewrapDashboardCLIAuthSandbox(
        bwrap_path=tmp_path / "bwrap",
        shim_path=tmp_path / "runtime-cli-sandbox-init",
        identity_pool=InvocationIdentityPool(first_id=61000, last_id=61000),
        exact_image_preflight=lambda: None,
        readonly_invocation_resolver=lambda _provider, command: ReadonlySandboxInvocation(
            command=command,
            readonly_inputs=(Path("/usr/bin/true"),),
        ),
        stage_factory=lambda _identity: SandboxStage(path=stage_home, root_fd=stage_fd),
        spawn=_spawn,
        pidfd_open=lambda _pid, _flags=0: os.open("/dev/null", os.O_RDONLY | os.O_CLOEXEC),
        pidfd_send_signal=lambda *_args: None,
        pidfd_is_dead=lambda _pidfd, _timeout: True,
    )
    real_wait_for = sandbox_platform.asyncio.wait_for
    absolute_timeout_values: list[float] = []

    async def _deterministic_wait_for(awaitable: object, timeout: float) -> object:
        """Expire the collector after two chunks without depending on wall-clock scheduling."""
        coroutine_code = getattr(awaitable, "cr_code", None)
        if coroutine_code is None or coroutine_code.co_name != "_collect":
            return await real_wait_for(awaitable, timeout)  # type: ignore[arg-type]

        absolute_timeout_values.append(timeout)
        collection_task = asyncio.create_task(awaitable)  # type: ignore[arg-type]
        try:
            await real_wait_for(process.stdout.second_chunk_seen.wait(), timeout=1.0)
        finally:
            collection_task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await collection_task
        raise TimeoutError

    monkeypatch.setattr(sandbox_platform.asyncio, "wait_for", _deterministic_wait_for)

    try:
        with pytest.raises(TimeoutError):
            await sandbox.run_readonly_command(
                PROVIDERS["codex"],
                command=("/usr/bin/true",),
                authority=ReadonlySandboxAuthority(
                    relative_path=Path(".codex") / "auth.json",
                    content=b'{"safe":"staged"}',
                ),
                timeout_s=0.08,
            )
    finally:
        for fd in block_readers:
            os.close(fd)

    assert absolute_timeout_values == [0.08]
    assert process.stdout.read_calls == 3
    assert process.wait_calls == 1
    assert not stage_home.exists()


async def test_readonly_command_cancellation_waiting_for_next_chunk_cleans_the_domain(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """REQ-core-credentials-002: direct reader cancellation cannot leak the child domain."""
    from butlers.cli_auth.sandbox import ReadonlySandboxAuthority
    from butlers.cli_auth.sandbox_platform import (
        BubblewrapDashboardCLIAuthSandbox,
        InvocationIdentityPool,
        ReadonlySandboxInvocation,
        SandboxStage,
    )

    first_chunk_seen = asyncio.Event()

    class _BlockingAfterChunkStream(asyncio.StreamReader):
        async def read(self, n: int = -1) -> bytes:
            chunk = await super().read(n)
            if chunk:
                first_chunk_seen.set()
            return chunk

    process = _HandshakeProcess([])
    process.stdout = _BlockingAfterChunkStream()
    process.stdout.feed_data(b"BUTLERS_RUNTIME_CLI_SANDBOX_READY\npartial-result")
    stage_home = tmp_path / "stage-home"
    stage_home.mkdir(mode=0o700)
    stage_fd = os.open(stage_home, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    block_readers: list[int] = []
    pool = InvocationIdentityPool(first_id=61000, last_id=61000)
    monkeypatch.setattr(os, "fchown", lambda *_args: None)

    async def _spawn(*argv: str, **_kwargs: object) -> _HandshakeProcess:
        info_fd = int(argv[argv.index("--info-fd") + 1])
        block_readers.append(os.dup(int(argv[argv.index("--block-fd") + 1])))
        block_readers.append(os.dup(int(argv[argv.index("--shim-gate-fd") + 1])))
        os.write(info_fd, b'{"child-pid": 7123}\n')
        return process

    sandbox = BubblewrapDashboardCLIAuthSandbox(
        bwrap_path=tmp_path / "bwrap",
        shim_path=tmp_path / "runtime-cli-sandbox-init",
        identity_pool=pool,
        exact_image_preflight=lambda: None,
        readonly_invocation_resolver=lambda _provider, command: ReadonlySandboxInvocation(
            command=command,
            readonly_inputs=(Path("/usr/bin/true"),),
        ),
        stage_factory=lambda _identity: SandboxStage(path=stage_home, root_fd=stage_fd),
        spawn=_spawn,
        pidfd_open=lambda _pid, _flags=0: os.open("/dev/null", os.O_RDONLY | os.O_CLOEXEC),
        pidfd_send_signal=lambda *_args: None,
        pidfd_is_dead=lambda _pidfd, _timeout: True,
    )

    task = asyncio.create_task(
        sandbox.run_readonly_command(
            PROVIDERS["codex"],
            command=("/usr/bin/true",),
            authority=ReadonlySandboxAuthority(
                relative_path=Path(".codex") / "auth.json",
                content=b'{"safe":"staged"}',
            ),
            timeout_s=1.0,
        )
    )
    try:
        await asyncio.wait_for(first_chunk_seen.wait(), timeout=1.0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        for fd in block_readers:
            os.close(fd)

    assert process.wait_calls == 1
    assert not stage_home.exists()
    reused = await pool.acquire()
    assert reused.uid == 61000
    await pool.release(reused)


async def test_bubblewrap_startup_cancellation_after_release_terminates_pid1_and_cleans_up(
    tmp_path: Path,
) -> None:
    """REQ-core-credentials-002: cancellation after release cannot leak a live domain."""
    from butlers.cli_auth.sandbox_platform import (
        BubblewrapDashboardCLIAuthSandbox,
        DeviceAuthSandboxInvocation,
        InvocationIdentityPool,
        SandboxStage,
    )

    events: list[str] = []
    process = _BlockingHandshakeProcess(events)
    cleanup_waited = asyncio.Event()

    async def _wait() -> int:
        process.wait_calls += 1
        process.returncode = 0
        cleanup_waited.set()
        return 0

    process.wait = _wait  # type: ignore[method-assign]
    pool = InvocationIdentityPool(first_id=61000, last_id=61000)
    released = asyncio.Event()
    stage_home = tmp_path / "stage-home"
    stage_home.mkdir(mode=0o700)
    stage_fd = os.open(stage_home, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    block_observer_fd: list[int] = []

    async def _spawn(*argv: str, **_kwargs: object) -> _BlockingHandshakeProcess:
        info_fd = int(argv[argv.index("--info-fd") + 1])
        block_observer_fd.append(os.dup(int(argv[argv.index("--block-fd") + 1])))
        block_observer_fd.append(os.dup(int(argv[argv.index("--shim-gate-fd") + 1])))
        os.write(info_fd, b'{"child-pid": 7123}\n')
        return process

    def _release_payload(block_fd: int) -> None:
        events.append("block_release")
        os.write(block_fd, b"1")
        if events.count("block_release") == 2:
            released.set()

    sandbox = BubblewrapDashboardCLIAuthSandbox(
        bwrap_path=tmp_path / "bwrap",
        shim_path=tmp_path / "runtime-cli-sandbox-init",
        identity_pool=pool,
        exact_image_preflight=lambda: None,
        invocation_resolver=lambda _provider: DeviceAuthSandboxInvocation(
            command=("/usr/bin/true",),
            readonly_inputs=(Path("/usr/bin/true"),),
            relative_output_path=Path(".codex") / "auth.json",
        ),
        stage_factory=lambda _identity: SandboxStage(path=stage_home, root_fd=stage_fd),
        spawn=_spawn,
        pidfd_open=lambda _pid, _flags=0: os.open("/dev/null", os.O_RDONLY | os.O_CLOEXEC),
        release_payload=_release_payload,
        pidfd_send_signal=lambda *_args: events.append("pidfd_signal"),
        pidfd_is_dead=lambda _pidfd, _timeout: True,
    )

    task = asyncio.create_task(sandbox.launch_device_auth(PROVIDERS["codex"]))
    await asyncio.wait_for(released.wait(), timeout=1.0)
    task.cancel()
    try:
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        for fd in block_observer_fd:
            os.close(fd)

    await asyncio.wait_for(cleanup_waited.wait(), timeout=1.0)
    assert events == ["block_release", "block_release", "pidfd_signal"]
    assert process.wait_calls == 1
    assert not stage_home.exists()
    reused_identity = await pool.acquire()
    assert reused_identity.uid == 61000
    await pool.release(reused_identity)


@pytest.mark.integration
def test_exact_image_bubblewrap_handshake_runs_only_when_explicitly_enabled() -> None:
    """REQ-core-credentials-002: the real image forwards the shim gate before exec."""
    if os.environ.get("BUTLERS_RUN_EXACT_IMAGE_SANDBOX_TEST") != "1":
        pytest.skip("set BUTLERS_RUN_EXACT_IMAGE_SANDBOX_TEST=1 with an explicit rebuilt image tag")
    docker = shutil.which("docker")
    if docker is None:
        pytest.fail("Docker is required when the exact-image sandbox handshake test is enabled")

    image = os.environ.get("BUTLERS_RUNTIME_SANDBOX_IMAGE")
    if not image or not _is_explicit_exact_image_reference(image):
        pytest.fail(
            "BUTLERS_RUNTIME_SANDBOX_IMAGE must name the explicitly rebuilt, non-latest app image"
        )
    inspect = subprocess.run(
        [docker, "image", "inspect", "--format", "{{json .Config}}", image],
        check=False,
        capture_output=True,
        text=True,
    )
    if inspect.returncode != 0:
        pytest.fail(f"exact sandbox image is unavailable: {image}")
    image_config = json.loads(inspect.stdout)
    image_labels = image_config.get("Labels") or {}
    assert image_labels.get("butlers.base.input_sha") == _base_input_fingerprint()
    expected_git_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        cwd=_REPO_ROOT,
        text=True,
    ).stdout.strip()
    image_environment = {
        item.split("=", 1)[0]: item.split("=", 1)[1]
        for item in image_config.get("Env", [])
        if "=" in item
    }
    assert image_environment.get("GIT_SHA") == expected_git_sha

    seccomp_profile = _REPO_ROOT / "deploy" / "seccomp" / "dashboard-runtime-cli-sandbox.json"
    completed = subprocess.run(
        [
            docker,
            "run",
            "--rm",
            "--security-opt",
            "apparmor=unconfined",
            "--security-opt",
            "systempaths=unconfined",
            "--security-opt",
            f"seccomp={seccomp_profile}",
            "--volume",
            f"{_EXACT_IMAGE_HARNESS}:/tmp/runtime-cli-sandbox-exact-image.py:ro",
            "--entrypoint",
            "/bin/sh",
            image,
            "-c",
            "PYTHONPATH=/app/src exec python /tmp/runtime-cli-sandbox-exact-image.py",
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout) == {"launch": "ok", "termination": "proven"}


@pytest.mark.parametrize(
    ("image", "expected"),
    [
        ("butlers-app", False),
        ("butlers-app:latest", False),
        ("butlers-app:latest@sha256:abc", False),
        ("registry.test:5000/butlers-app", False),
        ("butlers-app:bu-0uqgo.15", True),
        ("butlers-app@sha256:abc", True),
    ],
)
def test_exact_image_reference_rejects_mutable_latest_forms(image: str, expected: bool) -> None:
    """REQ-core-credentials-002: exact-image evidence cannot use a mutable tag."""
    assert _is_explicit_exact_image_reference(image) is expected


async def test_bubblewrap_handle_retains_the_identity_when_pid1_death_is_not_proven(
    tmp_path: Path,
) -> None:
    """REQ-core-credentials-002: failed pidfd containment cannot reuse a live UID."""
    from butlers.cli_auth.sandbox import SandboxUnavailableError
    from butlers.cli_auth.sandbox_platform import (
        BubblewrapDashboardCLIAuthSandbox,
        DeviceAuthSandboxInvocation,
        InvocationIdentityPool,
        SandboxStage,
    )

    events: list[str] = []
    process = _HandshakeProcess(events)
    pool = InvocationIdentityPool(first_id=61000, last_id=61000)
    block_observer_fd: list[int] = []
    stage_home = tmp_path / "stage-home"
    stage_home.mkdir(mode=0o700)
    stage_fd = os.open(stage_home, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)

    async def _spawn(*argv: str, **_kwargs: object) -> _HandshakeProcess:
        info_fd = int(argv[argv.index("--info-fd") + 1])
        block_observer_fd.append(os.dup(int(argv[argv.index("--block-fd") + 1])))
        block_observer_fd.append(os.dup(int(argv[argv.index("--shim-gate-fd") + 1])))
        os.write(info_fd, b'{"child-pid": 7123}\n')
        return process

    sandbox = BubblewrapDashboardCLIAuthSandbox(
        bwrap_path=tmp_path / "bwrap",
        shim_path=tmp_path / "runtime-cli-sandbox-init",
        identity_pool=pool,
        exact_image_preflight=lambda: None,
        invocation_resolver=lambda _provider: DeviceAuthSandboxInvocation(
            command=("/usr/bin/true",),
            readonly_inputs=(Path("/usr/bin/true"),),
            relative_output_path=Path(".codex") / "auth.json",
        ),
        stage_factory=lambda _identity: SandboxStage(path=stage_home, root_fd=stage_fd),
        spawn=_spawn,
        pidfd_open=lambda _pid, _flags=0: os.open("/dev/null", os.O_RDONLY | os.O_CLOEXEC),
        pidfd_send_signal=lambda *_args: events.append("pidfd_signal"),
        pidfd_is_dead=lambda _pidfd, _timeout: False,
    )

    try:
        handle = await sandbox.launch_device_auth(PROVIDERS["codex"])
        with patch(
            "butlers.cli_auth.sandbox_platform.read_validated_staged_device_auth_output"
        ) as read_stage:
            assert await handle.finalize(succeeded=True) is None
        read_stage.assert_not_called()
        assert process.wait_calls == 1
        assert events.count("pidfd_signal") == 2
        assert process.returncode == 0
        assert stage_home.exists(), "unproven PID1 death may retain the stage bind"
        with pytest.raises(SandboxUnavailableError, match="identity pool exhausted"):
            await pool.acquire()
    finally:
        for fd in block_observer_fd:
            os.close(fd)
        try:
            os.close(stage_fd)
        except OSError:
            pass
        shutil.rmtree(stage_home, ignore_errors=True)


async def test_pidfd_signal_error_kills_the_direct_child_and_retains_stage_until_death_is_proven(
    tmp_path: Path,
) -> None:
    """REQ-core-credentials-002: a failed pidfd signal never permits unsafe stage removal."""
    from butlers.cli_auth.sandbox import SandboxUnavailableError
    from butlers.cli_auth.sandbox_platform import (
        BubblewrapDashboardCLIAuthSandbox,
        DeviceAuthSandboxInvocation,
        InvocationIdentityPool,
        SandboxStage,
    )

    events: list[str] = []

    class _PidfdSignalErrorProcess(_HandshakeProcess):
        def kill(self) -> None:
            events.append("outer_kill")
            self.returncode = 0

    process = _PidfdSignalErrorProcess(events)
    pool = InvocationIdentityPool(first_id=61000, last_id=61000)
    stage_home = tmp_path / "stage-home"
    stage_home.mkdir(mode=0o700)
    stage_fd = os.open(stage_home, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    block_observer_fd: list[int] = []

    async def _spawn(*argv: str, **_kwargs: object) -> _PidfdSignalErrorProcess:
        info_fd = int(argv[argv.index("--info-fd") + 1])
        block_observer_fd.append(os.dup(int(argv[argv.index("--block-fd") + 1])))
        block_observer_fd.append(os.dup(int(argv[argv.index("--shim-gate-fd") + 1])))
        os.write(info_fd, b'{"child-pid": 7123}\n')
        return process

    def _pidfd_send_signal(*_args: object) -> None:
        events.append("pidfd_signal")
        raise OSError("synthetic pidfd failure")

    def _pidfd_is_dead(_pidfd: int, _timeout: float) -> bool:
        events.append("pidfd_death_check")
        return False

    sandbox = BubblewrapDashboardCLIAuthSandbox(
        bwrap_path=tmp_path / "bwrap",
        shim_path=tmp_path / "runtime-cli-sandbox-init",
        identity_pool=pool,
        exact_image_preflight=lambda: None,
        invocation_resolver=lambda _provider: DeviceAuthSandboxInvocation(
            command=("/usr/bin/true",),
            readonly_inputs=(Path("/usr/bin/true"),),
            relative_output_path=Path(".codex") / "auth.json",
        ),
        stage_factory=lambda _identity: SandboxStage(path=stage_home, root_fd=stage_fd),
        spawn=_spawn,
        pidfd_open=lambda _pid, _flags=0: os.open("/dev/null", os.O_RDONLY | os.O_CLOEXEC),
        pidfd_send_signal=_pidfd_send_signal,
        pidfd_is_dead=_pidfd_is_dead,
    )

    try:
        handle = await sandbox.launch_device_auth(PROVIDERS["codex"])
        await handle.terminate()

        assert events.index("pidfd_signal") < events.index("outer_kill")
        assert events.index("outer_kill") < events.index("pidfd_death_check")
        assert process.wait_calls == 1
        assert stage_home.exists(), "a live PID1 can retain the bind-mounted stage"
        with pytest.raises(SandboxUnavailableError, match="identity pool exhausted"):
            await pool.acquire()
    finally:
        for fd in block_observer_fd:
            os.close(fd)
        try:
            os.close(stage_fd)
        except OSError:
            pass
        shutil.rmtree(stage_home, ignore_errors=True)


@pytest.mark.skipif(shutil.which("cc") is None, reason="C compiler is required for shim contract")
@pytest.mark.parametrize(
    ("release", "expected_returncode", "expected_stdout"),
    [
        (b"1", 0, "BUTLERS_RUNTIME_CLI_SANDBOX_READY\nprovider-ran"),
        (None, 125, ""),
        (b"x", 125, ""),
    ],
)
def test_runtime_cli_sandbox_init_requires_an_explicit_shim_gate_release(
    tmp_path: Path,
    release: bytes | None,
    expected_returncode: int,
    expected_stdout: str,
) -> None:
    """REQ-core-credentials-002: EOF cannot release provider execution."""
    source = _REPO_ROOT / "scripts" / "runtime_cli_sandbox_init.c"
    shim = tmp_path / "runtime-cli-sandbox-init"
    subprocess.run(
        ["cc", "-O2", "-Wall", "-Wextra", "-Werror", "-o", str(shim), str(source)],
        check=True,
        capture_output=True,
        text=True,
    )

    gate_read, gate_write = os.pipe()
    os.set_inheritable(gate_read, True)
    process = subprocess.Popen(
        [
            str(shim),
            "--shim-gate-fd",
            str(gate_read),
            "--",
            "/bin/sh",
            "-c",
            "printf provider-ran",
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        close_fds=True,
        pass_fds=(gate_read,),
        text=True,
    )
    os.close(gate_read)
    if release is not None:
        os.write(gate_write, release)
    os.close(gate_write)
    stdout, stderr = process.communicate(timeout=5)

    assert process.returncode == expected_returncode, stderr
    assert stdout == expected_stdout


@pytest.mark.skipif(shutil.which("cc") is None, reason="C compiler is required for shim contract")
def test_runtime_cli_sandbox_init_closes_inherited_descriptors_before_provider_exec(
    tmp_path: Path,
) -> None:
    """REQ-core-credentials-002: the real PID1 shim removes an inherited secret FD."""
    source = _REPO_ROOT / "scripts" / "runtime_cli_sandbox_init.c"
    shim = tmp_path / "runtime-cli-sandbox-init"
    subprocess.run(
        ["cc", "-O2", "-Wall", "-Wextra", "-Werror", "-o", str(shim), str(source)],
        check=True,
        capture_output=True,
        text=True,
    )

    secret_path = tmp_path / "secret-sentinel"
    secret_path.write_text("INHERITED-FD-SENTINEL", encoding="utf-8")
    initial_fd = os.open(secret_path, os.O_RDONLY)
    inherited_fd = os.dup2(initial_fd, 200)
    os.close(initial_fd)
    os.set_inheritable(inherited_fd, True)
    gate_read, gate_write = os.pipe()
    os.set_inheritable(gate_read, True)
    os.write(gate_write, b"1")
    os.close(gate_write)
    try:
        result = subprocess.run(
            [
                str(shim),
                "--shim-gate-fd",
                str(gate_read),
                "--",
                "/bin/sh",
                "-c",
                'test ! -e "/proc/self/fd/$1"; printf provider-ran',
                "sh",
                str(inherited_fd),
            ],
            check=False,
            close_fds=True,
            pass_fds=(inherited_fd, gate_read),
            capture_output=True,
            text=True,
            timeout=5,
        )
    finally:
        os.close(inherited_fd)
        os.close(gate_read)

    assert result.returncode == 0, result.stderr
    assert result.stdout == "BUTLERS_RUNTIME_CLI_SANDBOX_READY\nprovider-ran"
    assert "INHERITED-FD-SENTINEL" not in result.stdout + result.stderr
