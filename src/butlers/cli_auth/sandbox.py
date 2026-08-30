"""Fail-closed primitives for Dashboard-owned CLI-auth sandbox launches.

This module owns the trust crossing between an untrusted device-auth child and
the Dashboard credential authority.  It intentionally exposes a small first
seam: once namespace-PID1 and inherited-descriptor checks are complete, read
the expected credential artifact through a trusted staged-HOME descriptor and
persist those exact bytes.  Callers never hand this path a canonical token
path, so the post-child persistence path cannot reopen one.
"""

from __future__ import annotations

import json
import logging
import os
import stat
from collections.abc import Awaitable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from butlers.cli_auth.persistence import persist_validated_staged_device_auth_bytes
from butlers.cli_auth.registry import CLIAuthProviderDef
from butlers.credential_store import CredentialStore

logger = logging.getLogger(__name__)

_MAX_DEVICE_AUTH_OUTPUT_BYTES = 1024 * 1024
_EXPECTED_DEVICE_AUTH_FILE_MODE = 0o600
_MAX_READONLY_AUTHORITY_BYTES = 1024 * 1024
_EXPECTED_READONLY_AUTHORITY_MODE = 0o600
_OPENCODE_OPENAI_AUTHORITY_KEY = "openai"
_OPENCODE_GO_AUTHORITY_KEY = "opencode-go"


class StagedOutputValidationError(RuntimeError):
    """Raised when a child-produced device-auth artifact is unsafe to consume."""


class SandboxUnavailableError(RuntimeError):
    """Raised when the mandatory Dashboard CLI-auth sandbox cannot launch."""


@dataclass(frozen=True)
class _DeviceAuthStageTreePolicy:
    """The only credential artifact and disposable scratch roots one CLI may write."""

    provider_name: str
    credential_artifact: Path
    scratch_roots: tuple[Path, ...] = ()


# Codex's device-code flow writes its private `log/codex-login.log` alongside
# the auth artifact. The log root stays disposable: validation never reads or
# persists its child-created bytes. A new provider (or verified CLI requirement)
# must declare its exact disposable roots here before they can coexist with the
# credential artifact in a staged HOME.
_DEVICE_AUTH_STAGE_TREE_POLICIES = (
    _DeviceAuthStageTreePolicy(
        provider_name="opencode-openai",
        credential_artifact=Path(".local") / "share" / "opencode" / "auth.json",
    ),
    _DeviceAuthStageTreePolicy(
        provider_name="codex",
        credential_artifact=Path(".codex") / "auth.json",
        scratch_roots=(Path(".codex") / "log",),
    ),
)


@dataclass(frozen=True)
class ReadonlySandboxAuthority:
    """One parent-validated authority copy staged only in a child HOME."""

    relative_path: Path
    content: bytes


@dataclass(frozen=True)
class SandboxedCommandResult:
    """Value-free process outcome returned after the child domain is discarded."""

    returncode: int
    output: bytes


class SandboxedChildProcess(Protocol):
    """The process-shaped surface consumed by the CLI-auth session parser."""

    stdout: object | None
    returncode: int | None

    def terminate(self) -> None: ...

    def kill(self) -> None: ...

    def wait(self) -> Awaitable[int]: ...


class DeviceAuthSandboxHandle(Protocol):
    """A sandbox-owned device-auth invocation and its containment receipt."""

    process: SandboxedChildProcess

    def finalize(self, *, succeeded: bool) -> Awaitable[bytes | None]: ...

    def terminate(self) -> Awaitable[None]: ...


class DashboardCLIAuthSandbox(Protocol):
    """Mandatory shared parent launcher for Dashboard CLI-auth children."""

    def launch_device_auth(
        self, provider: CLIAuthProviderDef
    ) -> Awaitable[DeviceAuthSandboxHandle]: ...

    def run_readonly_command(
        self,
        provider: CLIAuthProviderDef,
        *,
        command: tuple[str, ...],
        authority: ReadonlySandboxAuthority,
        timeout_s: float,
    ) -> Awaitable[SandboxedCommandResult]: ...


_DASHBOARD_SANDBOX: DashboardCLIAuthSandbox | None = None


def dashboard_cli_auth_sandbox() -> DashboardCLIAuthSandbox:
    """Return the only production launcher without a direct-child fallback."""
    global _DASHBOARD_SANDBOX
    if _DASHBOARD_SANDBOX is None:
        # Import lazily because the Linux platform module consumes the public
        # protocols above.  Construction itself is inert; exact-image and
        # pidfd preflight happens before each child launch and fails closed.
        from butlers.cli_auth.sandbox_platform import BubblewrapDashboardCLIAuthSandbox

        _DASHBOARD_SANDBOX = BubblewrapDashboardCLIAuthSandbox()
    return _DASHBOARD_SANDBOX


def _validated_relative_parts(relative_output_path: Path) -> tuple[str, ...]:
    """Return a fixed-root relative path or reject escapes before ``openat``."""
    if relative_output_path.is_absolute():
        raise StagedOutputValidationError("staged output must be relative")
    parts = relative_output_path.parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise StagedOutputValidationError("staged output path is unsafe")
    return parts


def _read_parent_regular_file_bytes(path: Path) -> bytes:
    """Read one bounded mode-0600 authority document through one safe FD."""
    descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.geteuid()
            or stat.S_IMODE(metadata.st_mode) != _EXPECTED_READONLY_AUTHORITY_MODE
            or metadata.st_nlink != 1
            or metadata.st_size <= 0
            or metadata.st_size > _MAX_READONLY_AUTHORITY_BYTES
        ):
            raise StagedOutputValidationError("parent authority metadata is unsafe")

        chunks: list[bytes] = []
        remaining = metadata.st_size
        while remaining:
            chunk = os.read(descriptor, min(remaining, 64 * 1024))
            if not chunk:
                raise StagedOutputValidationError("parent authority changed during read")
            chunks.append(chunk)
            remaining -= len(chunk)
        if os.read(descriptor, 1):
            raise StagedOutputValidationError("parent authority exceeds its checked size")
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _reject_duplicate_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    """Build a JSON object without silently accepting duplicate credential keys."""
    document: dict[str, object] = {}
    for key, value in pairs:
        if key in document:
            raise StagedOutputValidationError("parent authority contains duplicate JSON keys")
        document[key] = value
    return document


def _reject_nonstandard_json_constant(_value: str) -> object:
    raise StagedOutputValidationError("parent authority contains a nonstandard JSON constant")


def _project_readonly_authority_for_provider(
    provider: CLIAuthProviderDef,
    content: bytes,
) -> bytes:
    """Re-serialize only one declared OpenCode authority entry for a child.

    OpenCode's canonical ``auth.json`` is intentionally shared by the OpenAI
    device-code and OpenCode Go API-key providers.  A health/test child needs
    exactly one entry, never a broad file copy that happens to include another
    provider's credential.  New staged-authority providers must opt in here
    with an exact schema rather than inheriting a permissive copy path.
    """
    try:
        document = json.loads(
            content.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_json_object,
            parse_constant=_reject_nonstandard_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, StagedOutputValidationError) as exc:
        raise StagedOutputValidationError("parent authority is not strict JSON") from exc
    if not isinstance(document, dict):
        raise StagedOutputValidationError("parent authority is not a JSON object")

    if provider.name == "opencode-go":
        projected = _project_opencode_go_authority(document)
    elif provider.name == "opencode-openai":
        projected = _project_opencode_openai_authority(document)
    else:
        raise StagedOutputValidationError("provider has no readonly authority projection")

    return json.dumps(
        projected,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def _required_nonempty_string(entry: dict[str, object], field: str) -> str:
    value = entry.get(field)
    if not isinstance(value, str) or not value:
        raise StagedOutputValidationError("parent authority lacks the provider entry")
    return value


def _project_opencode_go_authority(document: dict[str, object]) -> dict[str, object]:
    entry = document.get(_OPENCODE_GO_AUTHORITY_KEY)
    if not isinstance(entry, dict) or set(entry) != {"type", "key"} or entry.get("type") != "api":
        raise StagedOutputValidationError("parent authority lacks the provider entry")
    return {
        _OPENCODE_GO_AUTHORITY_KEY: {
            "type": "api",
            "key": _required_nonempty_string(entry, "key"),
        }
    }


def _project_opencode_openai_authority(document: dict[str, object]) -> dict[str, object]:
    """Reconstruct the pinned OpenCode OAuth entry from scalar allowlisted fields."""
    entry = document.get(_OPENCODE_OPENAI_AUTHORITY_KEY)
    if not isinstance(entry, dict) or entry.get("type") != "oauth":
        raise StagedOutputValidationError("parent authority lacks the provider entry")

    allowed_fields = {"type", "refresh", "access", "expires", "accountId", "enterpriseUrl"}
    if not {"type", "refresh", "access", "expires"}.issubset(entry) or set(entry) - allowed_fields:
        raise StagedOutputValidationError("parent authority lacks the provider entry")

    expires = entry.get("expires")
    if type(expires) is not int or expires < 0:
        raise StagedOutputValidationError("parent authority lacks the provider entry")

    projected_entry: dict[str, object] = {
        "type": "oauth",
        "refresh": _required_nonempty_string(entry, "refresh"),
        "access": _required_nonempty_string(entry, "access"),
        "expires": expires,
    }
    # These optional scalar fields are the only non-secret metadata in the
    # pinned OpenCode 1.17.7 OAuth schema.  Unknown or nested values reject
    # before launch instead of becoming child-visible authority.
    for field in ("accountId", "enterpriseUrl"):
        value = entry.get(field)
        if value is not None:
            if not isinstance(value, str) or not value:
                raise StagedOutputValidationError("parent authority lacks the provider entry")
            projected_entry[field] = value
    return {_OPENCODE_OPENAI_AUTHORITY_KEY: projected_entry}


def load_validated_readonly_authority(
    provider: CLIAuthProviderDef,
    *,
    expected_content: str | None = None,
) -> ReadonlySandboxAuthority | None:
    """Return a staged-copy descriptor without exposing its canonical path to a child.

    Health and API-key children receive only these bytes at their declared
    relative path beneath a fresh staged HOME.  The source is a root-owned
    regular mode-0600 file opened with ``O_NOFOLLOW``; child mutations are
    discarded with the whole stage and cannot write back to that source.
    """
    token_path = provider.token_path
    relative_path = provider.sandbox_authority_relative_path
    if token_path is None or relative_path is None:
        logger.warning("CLI auth sandbox: provider has no staged authority declaration")
        return None
    try:
        _validated_relative_parts(relative_path)
        content = _read_parent_regular_file_bytes(token_path)
        if expected_content is not None and content != expected_content.encode("utf-8"):
            raise StagedOutputValidationError("parent authority did not match its fenced baseline")
        content = _project_readonly_authority_for_provider(provider, content)
    except (OSError, UnicodeEncodeError, StagedOutputValidationError):
        logger.warning("CLI auth sandbox: validated parent authority copy is unavailable")
        return None
    return ReadonlySandboxAuthority(relative_path=relative_path, content=content)


def _require_private_directory(fd: int, *, expected_uid: int) -> None:
    """Validate a child-owned, non-group-writable directory descriptor."""
    metadata = os.fstat(fd)
    mode = stat.S_IMODE(metadata.st_mode)
    if not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != expected_uid or mode & 0o077:
        raise StagedOutputValidationError("staged directory metadata is unsafe")


def _stage_tree_policy(relative_output_path: Path) -> _DeviceAuthStageTreePolicy:
    """Return the provider-declared scratch policy for one fixed artifact path."""
    for policy in _DEVICE_AUTH_STAGE_TREE_POLICIES:
        if relative_output_path == policy.credential_artifact:
            return policy
    raise StagedOutputValidationError("staged output path has no provider tree policy")


def _validate_device_auth_stage_tree(
    stage_home_fd: int,
    relative_output_path: Path,
    *,
    expected_uid: int,
) -> None:
    """Reject peer artifacts and undeclared writes before consuming device output.

    Every directory is reached through the trusted staged-HOME descriptor with
    ``O_NOFOLLOW``.  The registered policies currently permit only the output
    artifact and its parent directories; the generic tree form makes a future
    provider declare an exact disposable scratch root rather than inherit an
    implicit broad HOME allowlist.
    """
    policy = _stage_tree_policy(relative_output_path)
    artifact_parts = _validated_relative_parts(policy.credential_artifact)
    if artifact_parts != _validated_relative_parts(relative_output_path):
        raise StagedOutputValidationError("staged output path does not match its provider policy")

    scratch_parts = {_validated_relative_parts(root) for root in policy.scratch_roots}
    if any(
        scratch == artifact_parts
        or scratch[: len(artifact_parts)] == artifact_parts
        or artifact_parts[: len(scratch)] == scratch
        for scratch in scratch_parts
    ):
        raise StagedOutputValidationError(
            "provider scratch policy overlaps its credential artifact"
        )

    allowed_children: dict[tuple[str, ...], set[str]] = {}
    for allowed_path in (artifact_parts, *scratch_parts):
        parent: tuple[str, ...] = ()
        for part in allowed_path:
            allowed_children.setdefault(parent, set()).add(part)
            parent = (*parent, part)

    required_children: dict[tuple[str, ...], set[str]] = {}
    parent = ()
    for part in artifact_parts:
        required_children.setdefault(parent, set()).add(part)
        parent = (*parent, part)

    def _validate_directory(directory_fd: int, prefix: tuple[str, ...]) -> None:
        _require_private_directory(directory_fd, expected_uid=expected_uid)
        children = set(os.listdir(directory_fd))
        if not (
            required_children.get(prefix, set()) <= children <= allowed_children.get(prefix, set())
        ):
            raise StagedOutputValidationError("staged HOME contains an undeclared artifact")

        for child in sorted(children):
            child_prefix = (*prefix, child)
            if child_prefix == artifact_parts:
                # The same descriptor-constrained reader below validates this
                # leaf's no-follow type/owner/mode/link-count/size contract.
                continue
            child_fd = os.open(
                child,
                os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW,
                dir_fd=directory_fd,
            )
            try:
                _require_private_directory(child_fd, expected_uid=expected_uid)
                if child_prefix not in scratch_parts:
                    _validate_directory(child_fd, child_prefix)
            finally:
                os.close(child_fd)

    root_fd = os.dup(stage_home_fd)
    try:
        _validate_directory(root_fd, ())
    finally:
        os.close(root_fd)


def _read_expected_output_from_stage_fd(
    stage_home_fd: int,
    relative_output_path: Path,
    *,
    expected_uid: int,
) -> bytes:
    """Open and read exactly one child output through a no-follow root FD.

    The configured path is a fixed relative artifact path.  Traversing each
    component from the trusted staged-HOME descriptor with ``O_NOFOLLOW`` and
    ``O_DIRECTORY`` is behaviorally equivalent to a beneath/no-symlink
    ``openat2`` resolution for this fixed path.  The returned bytes originate
    from the same descriptor whose link/owner/mode checks succeeded.
    """
    parts = _validated_relative_parts(relative_output_path)
    root_fd = os.dup(stage_home_fd)
    current_fd = root_fd
    try:
        _require_private_directory(current_fd, expected_uid=expected_uid)
        for part in parts[:-1]:
            next_fd = os.open(
                part,
                os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW,
                dir_fd=current_fd,
            )
            os.close(current_fd)
            current_fd = next_fd
            _require_private_directory(current_fd, expected_uid=expected_uid)

        output_fd = os.open(
            parts[-1],
            os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW,
            dir_fd=current_fd,
        )
        try:
            metadata = os.fstat(output_fd)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_uid != expected_uid
                or stat.S_IMODE(metadata.st_mode) != _EXPECTED_DEVICE_AUTH_FILE_MODE
                or metadata.st_nlink != 1
                or metadata.st_size <= 0
                or metadata.st_size > _MAX_DEVICE_AUTH_OUTPUT_BYTES
            ):
                raise StagedOutputValidationError("staged output metadata is unsafe")

            chunks: list[bytes] = []
            remaining = metadata.st_size
            while remaining:
                chunk = os.read(output_fd, min(remaining, 64 * 1024))
                if not chunk:
                    raise StagedOutputValidationError("staged output changed during read")
                chunks.append(chunk)
                remaining -= len(chunk)
            # Reject a file that grew after its fstat size check rather than
            # accepting a prefix controlled by a still-running child.
            if os.read(output_fd, 1):
                raise StagedOutputValidationError("staged output exceeds its checked size")
            return b"".join(chunks)
        finally:
            os.close(output_fd)
    finally:
        os.close(current_fd)


def read_validated_staged_device_auth_output(
    *,
    stage_home_fd: int,
    relative_output_path: Path,
    expected_uid: int,
    pid1_terminated: bool,
    payload_fds_closed: bool,
) -> bytes | None:
    """Return one validated staged device-auth result after containment.

    This is the non-persistence half of the trusted-root-FD seam.  A concrete
    OS launcher consumes these bytes only after it has proved namespace-PID1
    death and the PID1 shim's inherited-descriptor closure receipt.  Keeping
    it separate lets the session callback remain the sole credential-authority
    writer.
    """
    if not pid1_terminated or not payload_fds_closed:
        logger.warning("CLI auth sandbox: device-auth containment was not verified")
        return None

    try:
        _validate_device_auth_stage_tree(
            stage_home_fd,
            relative_output_path,
            expected_uid=expected_uid,
        )
        return _read_expected_output_from_stage_fd(
            stage_home_fd,
            relative_output_path,
            expected_uid=expected_uid,
        )
    except (OSError, StagedOutputValidationError):
        logger.warning("CLI auth sandbox: staged device-auth output validation failed")
        return None


async def persist_staged_device_auth_output(
    provider: CLIAuthProviderDef,
    store: CredentialStore,
    *,
    stage_home_fd: int,
    relative_output_path: Path,
    expected_uid: int,
    pid1_terminated: bool,
    payload_fds_closed: bool,
    expected_authority_value: str | None,
    codex_authority: CredentialStore | None = None,
) -> bool:
    """Persist one validated staged device-auth result after containment.

    PID1 termination and payload-FD closure are explicit trusted-launcher
    receipts.  Any missing receipt is checked before opening staged output or
    calling the credential store, preserving a no-persistence failure path.
    """
    content = read_validated_staged_device_auth_output(
        stage_home_fd=stage_home_fd,
        relative_output_path=relative_output_path,
        expected_uid=expected_uid,
        pid1_terminated=pid1_terminated,
        payload_fds_closed=payload_fds_closed,
    )
    if content is None:
        return False

    return await persist_validated_staged_device_auth_bytes(
        provider,
        store,
        content,
        expected_authority_value=expected_authority_value,
        codex_authority=codex_authority,
    )
