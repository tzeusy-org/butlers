"""Platform-specific launch planning for the Dashboard CLI-auth sandbox.

The public :mod:`butlers.cli_auth.sandbox` module owns trust-boundary state.
This module keeps the Linux/Bubblewrap mechanics narrow, deterministic, and
unit-testable before the real subprocess lifecycle is wired in.
"""

from __future__ import annotations

import asyncio
import contextlib
import ctypes
import fcntl
import json
import logging
import os
import select
import signal
import stat
import tempfile
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

from butlers.cli_auth.registry import CLIAuthProviderDef
from butlers.cli_auth.sandbox import (
    DeviceAuthSandboxHandle,
    ReadonlySandboxAuthority,
    SandboxedChildProcess,
    SandboxedCommandResult,
    SandboxUnavailableError,
    read_validated_staged_device_auth_output,
)

logger = logging.getLogger(__name__)

_RUNTIME_HOME = "/home/runtime"
_RUNTIME_ENVIRONMENT = {
    "HOME": _RUNTIME_HOME,
    "PATH": "/usr/local/bin:/usr/bin",
    "TMPDIR": "/tmp",
    "XDG_CACHE_HOME": f"{_RUNTIME_HOME}/.cache",
    "XDG_CONFIG_HOME": f"{_RUNTIME_HOME}/.config",
    "XDG_DATA_HOME": f"{_RUNTIME_HOME}/.local/share",
}
_FORBIDDEN_CHILD_PREFIXES = ("/root", "/run", "/app", "/proc")
_READY_LINE = b"BUTLERS_RUNTIME_CLI_SANDBOX_READY\n"
_HANDSHAKE_TIMEOUT_S = 5.0
_PID1_GRACE_S = 2.0
_PID1_KILL_S = 2.0
_OUTER_CHILD_WAIT_S = 5.0
_MAX_BWRAP_INFO_BYTES = 4096
_MAX_READONLY_COMMAND_OUTPUT_BYTES = 16 * 1024
_READONLY_COMMAND_READ_CHUNK_BYTES = 4096
_DEFAULT_STAGE_ROOT = Path("/run/butlers/runtime-cli-auth-stage")
_DEFAULT_BWRAP_PATH = Path("/usr/bin/bwrap")
_DEFAULT_SHIM_PATH = Path("/usr/local/libexec/butlers/runtime-cli-sandbox-init")
_DEFAULT_RUNTIME_INPUT_MANIFEST = Path("/usr/local/share/butlers/runtime-cli-sandbox-inputs.json")
# Outer Bubblewrap runs as a leased unprivileged UID before resolving the
# source for its one stage bind.  This parent therefore permits traversal but
# never listing or writes; the random child directory remains 0700 and owned
# by that one UID, so a peer cannot discover or access it.
_STAGE_ROOT_MODE = 0o711
_STAGE_DIRECTORY_MODE = 0o700


class SandboxLaunchValidationError(RuntimeError):
    """Raised before launch when an OS-sandbox argument is unsafe."""


@dataclass(frozen=True)
class SandboxIdentity:
    """One exclusive outer identity assigned to a live sandbox domain."""

    uid: int
    gid: int


class InvocationIdentityPool:
    """Allocate one unprivileged identity per live invocation without reuse."""

    def __init__(self, *, first_id: int = 61000, last_id: int = 61999) -> None:
        if first_id <= 0 or last_id < first_id:
            raise ValueError("sandbox identity range is invalid")
        self._available: deque[SandboxIdentity] = deque(
            SandboxIdentity(uid=value, gid=value) for value in range(first_id, last_id + 1)
        )
        self._in_use: set[SandboxIdentity] = set()
        self._lock = asyncio.Lock()

    async def acquire(self) -> SandboxIdentity:
        """Lease an identity or fail closed when every domain is still live."""
        async with self._lock:
            if not self._available:
                raise SandboxUnavailableError("Dashboard CLI-auth sandbox identity pool exhausted")
            identity = self._available.popleft()
            self._in_use.add(identity)
            return identity

    async def release(self, identity: SandboxIdentity) -> None:
        """Return a lease only after the caller has proved domain cleanup."""
        async with self._lock:
            if identity not in self._in_use:
                raise SandboxLaunchValidationError("sandbox identity was not leased")
            self._in_use.remove(identity)
            self._available.appendleft(identity)


@dataclass(frozen=True)
class BubblewrapLaunchPlan:
    """The exact subprocess configuration permitted to enter the child domain."""

    argv: tuple[str, ...]
    environment: dict[str, str]
    pass_fds: tuple[int, ...]
    close_fds: bool = True


@dataclass(frozen=True)
class DeviceAuthSandboxInvocation:
    """Provider-specific inputs that are safe to expose inside one domain.

    The concrete provider-runtime resolver is intentionally a separate,
    injected boundary.  It must enumerate exact executable/runtime-library
    paths rather than widening this launcher's empty-root mount policy.
    """

    command: tuple[str, ...]
    readonly_inputs: tuple[SandboxReadonlyInput, ...]
    relative_output_path: Path


@dataclass(frozen=True)
class ReadonlySandboxInvocation:
    """Exact runtime inputs for a health or API-key command with no writeback."""

    command: tuple[str, ...]
    readonly_inputs: tuple[SandboxReadonlyInput, ...]


@dataclass(frozen=True)
class SandboxStage:
    """Parent-held trusted descriptor for one child-owned staged HOME."""

    path: Path
    root_fd: int


@dataclass(frozen=True)
class ReadonlySandboxInput:
    """One immutable parent source bound at one child-visible destination."""

    source: Path
    destination: Path


SandboxReadonlyInput = Path | ReadonlySandboxInput


def _validate_child_path(path: Path) -> None:
    if not path.is_absolute() or any(part == ".." for part in path.parts):
        raise SandboxLaunchValidationError("sandbox path must be absolute and normalized")
    rendered = str(path)
    if rendered == "/" or any(
        rendered == forbidden or rendered.startswith(f"{forbidden}/")
        for forbidden in _FORBIDDEN_CHILD_PREFIXES
    ):
        raise SandboxLaunchValidationError("sandbox path would expose a forbidden child view")


def _readonly_input_binding(raw_input: SandboxReadonlyInput) -> ReadonlySandboxInput:
    """Normalize legacy same-path inputs without losing an explicit alias mount."""
    if isinstance(raw_input, Path):
        return ReadonlySandboxInput(source=raw_input, destination=raw_input)
    if isinstance(raw_input, ReadonlySandboxInput):
        return raw_input
    raise SandboxLaunchValidationError("sandbox readonly input is invalid")


def _mount_parent_directories(paths: tuple[SandboxReadonlyInput, ...]) -> tuple[str, ...]:
    """Create only parent directories needed by the explicit readonly mounts."""
    directories: set[Path] = {Path("/home"), Path("/usr"), Path("/tmp")}
    for raw_path in paths:
        parent = _readonly_input_binding(raw_path).destination.parent
        while parent != Path("/"):
            directories.add(parent)
            parent = parent.parent
    args: list[str] = []
    for directory in sorted(
        directories,
        key=lambda candidate: (len(candidate.parts), str(candidate)),
    ):
        args.extend(("--dir", str(directory)))
    return tuple(args)


def validate_handshake_fds(
    *, info_fd: int, block_fd: int, shim_gate_fd: int
) -> tuple[int, int, int]:
    """Accept only typed CLOEXEC FIFO endpoints from the trusted launcher."""

    def _validate(fd: int, *, label: str, access: int) -> None:
        try:
            metadata = os.fstat(fd)
            status_flags = fcntl.fcntl(fd, fcntl.F_GETFL)
            descriptor_flags = fcntl.fcntl(fd, fcntl.F_GETFD)
        except OSError as exc:
            raise SandboxLaunchValidationError(f"{label} pipe descriptor is unavailable") from exc
        if not stat.S_ISFIFO(metadata.st_mode):
            raise SandboxLaunchValidationError(f"{label} pipe must be a FIFO")
        if status_flags & os.O_ACCMODE != access:
            raise SandboxLaunchValidationError(f"{label} pipe has the wrong direction")
        if not descriptor_flags & fcntl.FD_CLOEXEC:
            raise SandboxLaunchValidationError(f"{label} pipe must start close-on-exec")

    _validate(info_fd, label="info", access=os.O_WRONLY)
    _validate(block_fd, label="block", access=os.O_RDONLY)
    _validate(shim_gate_fd, label="shim gate", access=os.O_RDONLY)
    if len({info_fd, block_fd, shim_gate_fd}) != 3:
        raise SandboxLaunchValidationError("handshake pipe descriptors must be distinct")
    return info_fd, block_fd, shim_gate_fd


def build_bubblewrap_launch_plan(
    *,
    bwrap_path: Path,
    shim_path: Path,
    identity: SandboxIdentity,
    stage_home: Path,
    command: tuple[str, ...],
    readonly_inputs: tuple[SandboxReadonlyInput, ...],
    info_fd: int,
    block_fd: int,
    shim_gate_fd: int,
) -> BubblewrapLaunchPlan:
    """Build the empty-root Bubblewrap command for one trusted invocation.

    This is deliberately a pure plan: callers validate image availability and
    setup-pipe provenance before spawning it.  The plan never binds a broad
    host root, canonical credential path, Dashboard application tree, or
    parent procfs into the child namespace.
    """
    validate_handshake_fds(info_fd=info_fd, block_fd=block_fd, shim_gate_fd=shim_gate_fd)
    if not command or not Path(command[0]).is_absolute() or any("\0" in item for item in command):
        raise SandboxLaunchValidationError("sandbox command must start with an absolute executable")
    if identity.uid != identity.gid or not 61000 <= identity.uid <= 61999:
        raise SandboxLaunchValidationError("sandbox identity is outside the reserved range")
    if not stage_home.is_absolute():
        raise SandboxLaunchValidationError("staged HOME must be absolute")

    child_inputs = tuple(_readonly_input_binding(raw) for raw in readonly_inputs) + (
        ReadonlySandboxInput(source=shim_path, destination=shim_path),
    )
    for child_input in child_inputs:
        _validate_child_path(child_input.source)
        _validate_child_path(child_input.destination)

    argv: list[str] = [
        str(bwrap_path),
        "--unshare-user",
        "--unshare-pid",
        "--unshare-ipc",
        "--unshare-uts",
        "--as-pid-1",
        "--die-with-parent",
        "--uid",
        str(identity.uid),
        "--gid",
        str(identity.gid),
        "--clearenv",
    ]
    for key, value in _RUNTIME_ENVIRONMENT.items():
        argv.extend(("--setenv", key, value))

    # Start from an empty root.  Every visible path below is listed here or
    # is created by Bubblewrap itself, so root/run/app/canonical homes cannot
    # accidentally leak through a parent bind.
    argv.extend(("--tmpfs", "/"))
    argv.extend(_mount_parent_directories(child_inputs))
    argv.extend(("--bind", str(stage_home), _RUNTIME_HOME))
    argv.extend(("--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp"))
    for child_input in child_inputs:
        argv.extend(("--ro-bind", str(child_input.source), str(child_input.destination)))
    argv.extend(
        (
            "--info-fd",
            str(info_fd),
            "--block-fd",
            str(block_fd),
            "--chdir",
            _RUNTIME_HOME,
            "--",
            str(shim_path),
            "--shim-gate-fd",
            str(shim_gate_fd),
            "--",
            *command,
        )
    )
    return BubblewrapLaunchPlan(
        argv=tuple(argv),
        environment=dict(_RUNTIME_ENVIRONMENT),
        pass_fds=(info_fd, block_fd, shim_gate_fd),
    )


class RuntimeCLIInputManifest:
    """Read the image-owned exact runtime inputs for registered CLI providers."""

    def __init__(
        self,
        path: Path = _DEFAULT_RUNTIME_INPUT_MANIFEST,
        *,
        expected_uid: int = 0,
    ) -> None:
        self._path = path
        self._expected_uid = expected_uid
        self._document: dict[str, object] | None = None

    def _read_document(self) -> dict[str, object]:
        if self._document is not None:
            return self._document
        descriptor = os.open(self._path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
        try:
            metadata = os.fstat(descriptor)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_uid != self._expected_uid
                or stat.S_IMODE(metadata.st_mode) != 0o444
                or metadata.st_nlink != 1
                or metadata.st_size <= 0
                or metadata.st_size > 64 * 1024
            ):
                raise SandboxLaunchValidationError("runtime-input manifest is unsafe")
            payload = os.read(descriptor, metadata.st_size + 1)
            if len(payload) != metadata.st_size:
                raise SandboxLaunchValidationError("runtime-input manifest changed during read")
        finally:
            _close_fd(descriptor)
        try:
            document = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SandboxLaunchValidationError("runtime-input manifest is invalid") from exc
        if (
            not isinstance(document, dict)
            or document.get("version") != 2
            or not isinstance(document.get("providers"), dict)
        ):
            raise SandboxLaunchValidationError("runtime-input manifest has an unsafe schema")
        self._document = document
        return document

    def _immutable_input(self, raw_path: object, *, executable: bool = False) -> Path:
        if not isinstance(raw_path, str):
            raise SandboxLaunchValidationError("runtime-input manifest path is invalid")
        path = Path(raw_path)
        _validate_child_path(path)
        try:
            metadata = path.lstat()
        except OSError as exc:
            raise SandboxLaunchValidationError(
                "runtime-input manifest source is unavailable"
            ) from exc
        if (
            metadata.st_uid != self._expected_uid
            or metadata.st_mode & 0o022
            or stat.S_ISLNK(metadata.st_mode)
            or not (stat.S_ISREG(metadata.st_mode) or stat.S_ISDIR(metadata.st_mode))
            or (
                executable
                and (not stat.S_ISREG(metadata.st_mode) or not metadata.st_mode & stat.S_IXUSR)
            )
        ):
            raise SandboxLaunchValidationError("runtime-input manifest source is unsafe")
        return path

    def _immutable_input_binding(self, raw_input: object) -> ReadonlySandboxInput:
        """Read one source-to-logical-path mount from the image manifest."""
        if not isinstance(raw_input, dict) or set(raw_input) != {"source", "destination"}:
            raise SandboxLaunchValidationError("runtime-input manifest binding is invalid")
        source = self._immutable_input(raw_input.get("source"))
        destination_raw = raw_input.get("destination")
        if not isinstance(destination_raw, str):
            raise SandboxLaunchValidationError("runtime-input manifest path is invalid")
        destination = Path(destination_raw)
        _validate_child_path(destination)
        return ReadonlySandboxInput(source=source, destination=destination)

    def _resolve(
        self,
        provider: CLIAuthProviderDef,
        command: tuple[str, ...],
    ) -> ReadonlySandboxInvocation:
        if (
            not command
            or command[0] != provider.binary()
            or any(not item or "\0" in item for item in command)
        ):
            raise SandboxLaunchValidationError("runtime CLI command is not provider-declared")
        document = self._read_document()
        providers = document["providers"]
        assert isinstance(providers, dict)
        raw_entry = providers.get(provider.name)
        if not isinstance(raw_entry, dict) or raw_entry.get("binary") != provider.binary():
            raise SandboxLaunchValidationError("runtime-input manifest has no provider entry")
        executable = self._immutable_input(raw_entry.get("executable"), executable=True)
        raw_inputs = raw_entry.get("readonly_inputs")
        if not isinstance(raw_inputs, list) or not raw_inputs:
            raise SandboxLaunchValidationError("runtime-input manifest has no readonly inputs")
        inputs: list[ReadonlySandboxInput] = [
            ReadonlySandboxInput(source=executable, destination=executable)
        ]
        destinations = {executable}
        for raw_input in raw_inputs:
            input_binding = self._immutable_input_binding(raw_input)
            if input_binding.destination in destinations:
                if input_binding not in inputs:
                    raise SandboxLaunchValidationError(
                        "runtime-input manifest has conflicting logical destinations"
                    )
                continue
            destinations.add(input_binding.destination)
            inputs.append(input_binding)
        return ReadonlySandboxInvocation(
            command=(str(executable), *command[1:]),
            readonly_inputs=tuple(inputs),
        )

    def resolve_device_auth(self, provider: CLIAuthProviderDef) -> DeviceAuthSandboxInvocation:
        """Resolve a registered device-auth command through the exact image manifest."""
        if provider.sandbox_authority_relative_path is None:
            raise SandboxLaunchValidationError(
                "device-auth provider has no staged output declaration"
            )
        invocation = self._resolve(provider, tuple(provider.command))
        return DeviceAuthSandboxInvocation(
            command=invocation.command,
            readonly_inputs=invocation.readonly_inputs,
            relative_output_path=provider.sandbox_authority_relative_path,
        )

    def resolve_readonly(
        self,
        provider: CLIAuthProviderDef,
        command: tuple[str, ...],
    ) -> ReadonlySandboxInvocation:
        """Resolve one status/API command without accepting an arbitrary executable."""
        return self._resolve(provider, command)


_RUNTIME_INPUT_MANIFEST: RuntimeCLIInputManifest | None = None


def _runtime_input_manifest() -> RuntimeCLIInputManifest:
    """Return the immutable image manifest lazily so import remains inert."""
    global _RUNTIME_INPUT_MANIFEST
    if _RUNTIME_INPUT_MANIFEST is None:
        _RUNTIME_INPUT_MANIFEST = RuntimeCLIInputManifest()
    return _RUNTIME_INPUT_MANIFEST


def resolve_device_auth_runtime_inputs(provider: CLIAuthProviderDef) -> DeviceAuthSandboxInvocation:
    """Production device-auth resolver with no canonical path or host-tree fallback."""
    return _runtime_input_manifest().resolve_device_auth(provider)


def resolve_readonly_runtime_inputs(
    provider: CLIAuthProviderDef,
    command: tuple[str, ...],
) -> ReadonlySandboxInvocation:
    """Production health/API resolver with no direct-child fallback."""
    return _runtime_input_manifest().resolve_readonly(provider, command)


def _close_fd(fd: int | None) -> None:
    """Close one parent descriptor without turning cleanup into a new failure."""
    if fd is not None and fd >= 0:
        with contextlib.suppress(OSError):
            os.close(fd)


async def _read_bounded_to_eof(
    reader: asyncio.StreamReader,
    *,
    timeout_s: float,
) -> bytes:
    """Collect bounded untrusted stdout through EOF under one absolute deadline."""

    async def _collect() -> bytes:
        chunks: list[bytes] = []
        total = 0
        while True:
            # ``StreamReader.read(n)`` may return a partial buffered chunk
            # before EOF.  Keep reading until EOF, while requesting at most
            # one byte beyond remaining capacity so no unbounded value is
            # materialized before the cap check.
            remaining = _MAX_READONLY_COMMAND_OUTPUT_BYTES - total
            chunk = await reader.read(min(_READONLY_COMMAND_READ_CHUNK_BYTES, remaining + 1))
            if not chunk:
                return b"".join(chunks)
            total += len(chunk)
            if total > _MAX_READONLY_COMMAND_OUTPUT_BYTES:
                raise SandboxUnavailableError(
                    "Dashboard CLI-auth sandbox stdout exceeded its safe bound"
                )
            chunks.append(chunk)

    return await asyncio.wait_for(_collect(), timeout=timeout_s)


def _authority_relative_parts(authority: ReadonlySandboxAuthority) -> tuple[str, ...]:
    """Validate the fixed child-HOME target before the parent stages bytes."""
    path = authority.relative_path
    parts = path.parts
    if path.is_absolute() or not parts or any(part in {"", ".", ".."} for part in parts):
        raise SandboxLaunchValidationError("sandbox authority target is unsafe")
    if not authority.content or len(authority.content) > 1024 * 1024:
        raise SandboxLaunchValidationError("sandbox authority content is unsafe")
    return parts


def _write_all(fd: int, content: bytes) -> None:
    """Write bounded staged authority bytes without silently accepting a short write."""
    offset = 0
    while offset < len(content):
        written = os.write(fd, content[offset:])
        if written <= 0:
            raise OSError("short staged authority write")
        offset += written


def _stage_readonly_authority(
    stage: SandboxStage,
    identity: SandboxIdentity,
    authority: ReadonlySandboxAuthority,
) -> None:
    """Materialize one parent-validated authority copy below a trusted root FD."""
    parts = _authority_relative_parts(authority)
    current_fd = os.dup(stage.root_fd)
    try:
        for part in parts[:-1]:
            try:
                os.mkdir(part, 0o700, dir_fd=current_fd)
            except FileExistsError:
                pass
            next_fd = os.open(
                part,
                os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW,
                dir_fd=current_fd,
            )
            try:
                metadata = os.fstat(next_fd)
                if not stat.S_ISDIR(metadata.st_mode):
                    raise SandboxLaunchValidationError("sandbox authority parent is unsafe")
                os.fchown(next_fd, identity.uid, identity.gid)
                os.fchmod(next_fd, 0o700)
            except Exception:
                _close_fd(next_fd)
                raise
            _close_fd(current_fd)
            current_fd = next_fd

        authority_fd = os.open(
            parts[-1],
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW,
            0o600,
            dir_fd=current_fd,
        )
        try:
            os.fchown(authority_fd, identity.uid, identity.gid)
            os.fchmod(authority_fd, 0o600)
            _write_all(authority_fd, authority.content)
            os.fsync(authority_fd)
        finally:
            _close_fd(authority_fd)
    finally:
        _close_fd(current_fd)


def _set_no_new_privs() -> None:
    """Set Linux PR_SET_NO_NEW_PRIVS before entering Bubblewrap."""
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(38, 1, 0, 0, 0) != 0:  # PR_SET_NO_NEW_PRIVS
        error = ctypes.get_errno()
        raise OSError(error, "prctl(PR_SET_NO_NEW_PRIVS) failed")


def _outer_identity_preexec(identity: SandboxIdentity) -> Callable[[], None]:
    """Return the one-shot child setup that runs before Bubblewrap execs."""

    def _drop_privileges_and_lock() -> None:
        os.umask(0o077)
        os.setgroups([])
        os.setresgid(identity.gid, identity.gid, identity.gid)
        os.setresuid(identity.uid, identity.uid, identity.uid)
        _set_no_new_privs()

    return _drop_privileges_and_lock


def _parse_bubblewrap_info(payload: bytes) -> int | None:
    """Return a PID for one complete Bubblewrap receipt, else wait for more bytes."""
    if len(payload) > _MAX_BWRAP_INFO_BYTES:
        raise SandboxLaunchValidationError("Bubblewrap did not provide a bounded PID1 receipt")
    try:
        text = payload.decode("utf-8")
        leading = len(text) - len(text.lstrip())
        decoded, end = json.JSONDecoder().raw_decode(text, leading)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if text[end:].strip():
        raise SandboxLaunchValidationError("Bubblewrap PID1 receipt is invalid")
    if not isinstance(decoded, dict):
        raise SandboxLaunchValidationError("Bubblewrap PID1 receipt must be an object")
    child_pid = decoded.get("child-pid")
    if type(child_pid) is not int or child_pid <= 0:
        raise SandboxLaunchValidationError("Bubblewrap PID1 receipt has no valid child PID")
    return child_pid


async def _read_bubblewrap_info(fd: int) -> int:
    """Read Bubblewrap's fragmented bounded info object without a blocking thread."""
    payload = bytearray()
    loop = asyncio.get_running_loop()
    receipt: asyncio.Future[int] = loop.create_future()
    original_flags = fcntl.fcntl(fd, fcntl.F_GETFL)
    fcntl.fcntl(fd, fcntl.F_SETFL, original_flags | os.O_NONBLOCK)

    def _finish_eof() -> None:
        if not receipt.done():
            if payload:
                receipt.set_exception(
                    SandboxLaunchValidationError("Bubblewrap PID1 receipt is invalid")
                )
            else:
                receipt.set_exception(
                    SandboxLaunchValidationError(
                        "Bubblewrap did not provide a bounded PID1 receipt"
                    )
                )

    def _consume_available_info() -> None:
        while not receipt.done():
            try:
                chunk = os.read(fd, min(4096, _MAX_BWRAP_INFO_BYTES + 1 - len(payload)))
            except BlockingIOError:
                return
            except OSError:
                receipt.set_exception(
                    SandboxLaunchValidationError("Bubblewrap PID1 receipt is unavailable")
                )
                return
            if not chunk:
                _finish_eof()
                return
            payload.extend(chunk)
            try:
                child_pid = _parse_bubblewrap_info(bytes(payload))
            except SandboxLaunchValidationError as exc:
                receipt.set_exception(exc)
                return
            if child_pid is not None:
                receipt.set_result(child_pid)
                return

    loop.add_reader(fd, _consume_available_info)
    _consume_available_info()
    try:
        return await receipt
    finally:
        loop.remove_reader(fd)
        try:
            fcntl.fcntl(fd, fcntl.F_SETFL, original_flags)
        except OSError:
            pass


@dataclass(frozen=True)
class _QuarantinedStartupGate:
    """Keep the pre-PID1 Bubblewrap gate closed for this Dashboard lifetime."""

    process: SandboxedChildProcess
    block_write_fd: int


def _default_pidfd_is_dead(pidfd: int, timeout_s: float) -> bool:
    """Poll the pidfd only; never waitid a namespace child owned by Bubblewrap."""
    readable, _, _ = select.select([pidfd], [], [], timeout_s)
    return bool(readable)


class _BubblewrapDeviceAuthHandle:
    """Own PID1 termination, stage cleanup, and UID release for one launch."""

    def __init__(
        self,
        *,
        process: SandboxedChildProcess,
        pidfd: int,
        stage: SandboxStage,
        identity: SandboxIdentity,
        identity_pool: InvocationIdentityPool,
        relative_output_path: Path | None,
        shim_ready: bool,
        pidfd_send_signal: Callable[..., object],
        pidfd_is_dead: Callable[[int, float], bool],
    ) -> None:
        self.process = process
        self._pidfd = pidfd
        self._stage = stage
        self._identity = identity
        self._identity_pool = identity_pool
        self._relative_output_path = relative_output_path
        self._shim_ready = shim_ready
        self._pidfd_send_signal = pidfd_send_signal
        self._pidfd_is_dead = pidfd_is_dead
        self._pid1_terminated: bool | None = None
        self._stage_discarded = False
        self._identity_released = False
        self._lifecycle_lock = asyncio.Lock()

    async def _wait_for_pid1_death(self, timeout_s: float) -> bool:
        return await asyncio.to_thread(self._pidfd_is_dead, self._pidfd, timeout_s)

    async def _wait_for_outer_child(self, *, kill_on_timeout: bool) -> bool:
        """Observe the direct Bubblewrap child without treating its exit as PID1 proof."""
        try:
            await asyncio.wait_for(self.process.wait(), timeout=_OUTER_CHILD_WAIT_S)
            return True
        except TimeoutError:
            if not kill_on_timeout:
                return False
            with contextlib.suppress(Exception):
                self.process.kill()
            try:
                await asyncio.wait_for(self.process.wait(), timeout=_OUTER_CHILD_WAIT_S)
            except (Exception, TimeoutError):
                return False
            return True
        except Exception:
            return False

    async def _kill_outer_child_and_prove_pid1_death(self) -> bool:
        """Use the direct child only as a fallback, then require pidfd death proof.

        A pidfd syscall failure says nothing about whether namespace PID1 (and
        its inherited stage bind) survives.  Killing the direct Bubblewrap
        child is best effort; stage cleanup remains forbidden until the pidfd
        independently becomes readable and the direct child can be observed.
        """
        with contextlib.suppress(Exception):
            self.process.kill()
        if not await self._wait_for_outer_child(kill_on_timeout=False):
            return False
        return await self._wait_for_pid1_death(_PID1_KILL_S)

    async def _terminate_pid1_then_wait_outer_child(self) -> bool:
        """Kill namespace PID1 first, then wait only for direct Bubblewrap child."""
        if self._pid1_terminated is not None:
            return self._pid1_terminated

        try:
            self._pidfd_send_signal(self._pidfd, signal.SIGTERM, None, 0)
        except ProcessLookupError:
            # PID1 may have exited normally after emitting provider output.
            pass
        except OSError:
            self._pid1_terminated = await self._kill_outer_child_and_prove_pid1_death()
            return self._pid1_terminated

        pid1_dead = await self._wait_for_pid1_death(_PID1_GRACE_S)
        if not pid1_dead:
            try:
                self._pidfd_send_signal(self._pidfd, signal.SIGKILL, None, 0)
            except ProcessLookupError:
                pass
            except OSError:
                self._pid1_terminated = await self._kill_outer_child_and_prove_pid1_death()
                return self._pid1_terminated
            pid1_dead = await self._wait_for_pid1_death(_PID1_KILL_S)

        if not pid1_dead:
            self._pid1_terminated = await self._kill_outer_child_and_prove_pid1_death()
            return self._pid1_terminated

        self._pid1_terminated = await self._wait_for_outer_child(kill_on_timeout=True)
        return self._pid1_terminated

    async def _discard_stage(self, *, release_identity: bool) -> None:
        if self._stage_discarded:
            return
        self._stage_discarded = True
        _close_fd(self._stage.root_fd)
        try:
            # ``rmtree`` does not follow child-created symlinks.  The stage is
            # already detached from the child namespace before this cleanup.
            await asyncio.to_thread(__import__("shutil").rmtree, self._stage.path)
        except FileNotFoundError:
            pass
        except OSError:
            # The UID lease is intentionally retained below if cleanup fails;
            # no later invocation may reuse a potentially live identity.
            release_identity = False

        _close_fd(self._pidfd)
        self._pidfd = -1
        if release_identity and not self._identity_released:
            await self._identity_pool.release(self._identity)
            self._identity_released = True

    async def finalize(self, *, succeeded: bool) -> bytes | None:
        """Return staged bytes only after PID1 death and shim closure receipt."""
        async with self._lifecycle_lock:
            pid1_terminated = await self._terminate_pid1_then_wait_outer_child()
            content: bytes | None = None
            if (
                succeeded
                and pid1_terminated
                and self._shim_ready
                and self._relative_output_path is not None
            ):
                content = read_validated_staged_device_auth_output(
                    stage_home_fd=self._stage.root_fd,
                    relative_output_path=self._relative_output_path,
                    expected_uid=self._identity.uid,
                    pid1_terminated=True,
                    payload_fds_closed=True,
                )
            if pid1_terminated:
                await self._discard_stage(release_identity=True)
            else:
                logger.warning(
                    "Dashboard CLI-auth sandbox retained a stage after unproven PID1 death"
                )
            return content

    async def complete_readonly(self) -> bool:
        """Discard a read-only stage only after namespace PID1 is proven dead."""
        async with self._lifecycle_lock:
            pid1_terminated = await self._terminate_pid1_then_wait_outer_child()
            if pid1_terminated:
                await self._discard_stage(release_identity=True)
            else:
                logger.warning(
                    "Dashboard CLI-auth sandbox retained a stage after unproven PID1 death"
                )
            return pid1_terminated

    async def terminate(self) -> None:
        """Cancel the full namespace before stage cleanup or identity reuse."""
        async with self._lifecycle_lock:
            pid1_terminated = await self._terminate_pid1_then_wait_outer_child()
            if pid1_terminated:
                await self._discard_stage(release_identity=True)
            else:
                logger.warning(
                    "Dashboard CLI-auth sandbox retained a stage after unproven PID1 death"
                )


class BubblewrapDashboardCLIAuthSandbox:
    """Concrete shared child launcher with a fail-closed startup handshake.

    Production construction binds the immutable exact-image resolver.  If the
    image manifest is absent or unsafe, that resolver fails closed rather than
    binding a broad host tree or falling back to a direct child process.
    """

    def __init__(
        self,
        *,
        bwrap_path: Path = _DEFAULT_BWRAP_PATH,
        shim_path: Path = _DEFAULT_SHIM_PATH,
        stage_root: Path = _DEFAULT_STAGE_ROOT,
        identity_pool: InvocationIdentityPool | None = None,
        exact_image_preflight: Callable[[], None] | None = None,
        invocation_resolver: (
            Callable[[CLIAuthProviderDef], DeviceAuthSandboxInvocation] | None
        ) = None,
        readonly_invocation_resolver: (
            Callable[[CLIAuthProviderDef, tuple[str, ...]], ReadonlySandboxInvocation] | None
        ) = None,
        stage_factory: Callable[[SandboxIdentity], SandboxStage] | None = None,
        spawn: Callable[..., Awaitable[SandboxedChildProcess]] = asyncio.create_subprocess_exec,
        pidfd_open: Callable[[int, int], int] | None = None,
        pidfd_send_signal: Callable[..., object] | None = None,
        pidfd_is_dead: Callable[[int, float], bool] = _default_pidfd_is_dead,
        release_payload: Callable[[int], None] | None = None,
    ) -> None:
        self._bwrap_path = bwrap_path
        self._shim_path = shim_path
        self._stage_root = stage_root
        self._identity_pool = identity_pool or InvocationIdentityPool()
        self._exact_image_preflight = exact_image_preflight or self._default_exact_image_preflight
        self._invocation_resolver = invocation_resolver or resolve_device_auth_runtime_inputs
        self._readonly_invocation_resolver = (
            readonly_invocation_resolver or resolve_readonly_runtime_inputs
        )
        self._stage_factory = stage_factory or self._create_stage
        self._spawn = spawn
        self._pidfd_open = pidfd_open or getattr(os, "pidfd_open", self._missing_pidfd_open)
        self._pidfd_send_signal = pidfd_send_signal or getattr(
            signal,
            "pidfd_send_signal",
            self._missing_pidfd_send_signal,
        )
        self._pidfd_is_dead = pidfd_is_dead
        self._release_payload = release_payload or self._write_payload_release
        self._quarantined_startup_gates: list[_QuarantinedStartupGate] = []

    @staticmethod
    def _missing_pidfd_open(_pid: int, _flags: int = 0) -> int:
        raise SandboxUnavailableError("Dashboard CLI-auth sandbox requires pidfd support")

    @staticmethod
    def _missing_pidfd_send_signal(*_args: object) -> None:
        raise SandboxUnavailableError("Dashboard CLI-auth sandbox requires pidfd support")

    def _default_exact_image_preflight(self) -> None:
        if os.geteuid() != 0:
            raise SandboxUnavailableError(
                "Dashboard CLI-auth sandbox requires its root image supervisor"
            )
        if not hasattr(os, "pidfd_open") or not hasattr(signal, "pidfd_send_signal"):
            raise SandboxUnavailableError("Dashboard CLI-auth sandbox requires pidfd support")
        for path, label in ((self._bwrap_path, "Bubblewrap"), (self._shim_path, "PID1 shim")):
            try:
                metadata = path.stat()
            except OSError as exc:
                raise SandboxUnavailableError(
                    f"Dashboard CLI-auth sandbox {label} is unavailable"
                ) from exc
            if (
                not stat.S_ISREG(metadata.st_mode)
                or not metadata.st_mode & stat.S_IXUSR
                or metadata.st_mode & 0o022
            ):
                raise SandboxUnavailableError(f"Dashboard CLI-auth sandbox {label} is unsafe")

    def _create_stage(self, identity: SandboxIdentity) -> SandboxStage:
        self._stage_root.mkdir(mode=_STAGE_ROOT_MODE, parents=True, exist_ok=True)
        metadata = self._stage_root.lstat()
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != 0
            or stat.S_IMODE(metadata.st_mode) != _STAGE_ROOT_MODE
        ):
            raise SandboxUnavailableError("Dashboard CLI-auth sandbox staging root is unsafe")
        stage_path = Path(tempfile.mkdtemp(prefix=f"{identity.uid}-", dir=self._stage_root))
        try:
            os.chown(stage_path, identity.uid, identity.gid)
            os.chmod(stage_path, _STAGE_DIRECTORY_MODE)
            root_fd = os.open(stage_path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
        except Exception:
            with contextlib.suppress(OSError):
                __import__("shutil").rmtree(stage_path)
            raise
        return SandboxStage(path=stage_path, root_fd=root_fd)

    @staticmethod
    def _write_payload_release(block_fd: int) -> None:
        os.write(block_fd, b"1")

    async def _read_info_pid(self, info_fd: int) -> int:
        try:
            return await asyncio.wait_for(
                _read_bubblewrap_info(info_fd),
                timeout=_HANDSHAKE_TIMEOUT_S,
            )
        except TimeoutError as exc:
            raise SandboxLaunchValidationError("Bubblewrap PID1 receipt timed out") from exc

    async def _verify_shim_ready(self, process: SandboxedChildProcess) -> None:
        if not isinstance(process.stdout, asyncio.StreamReader):
            raise SandboxLaunchValidationError("Bubblewrap child stdout is unavailable")
        try:
            ready_line = await asyncio.wait_for(
                process.stdout.readline(), timeout=_HANDSHAKE_TIMEOUT_S
            )
        except TimeoutError as exc:
            raise SandboxLaunchValidationError(
                "namespace PID1 shim did not acknowledge closure"
            ) from exc
        except ValueError as exc:
            # StreamReader rejects an oversized/no-newline receipt with
            # ValueError.  Normalize it into the startup validation path so
            # the already-started PID namespace receives pidfd cleanup.
            raise SandboxLaunchValidationError(
                "namespace PID1 shim closure receipt is invalid"
            ) from exc
        if ready_line != _READY_LINE:
            raise SandboxLaunchValidationError("namespace PID1 shim closure receipt is invalid")

    async def _abort_startup(
        self,
        *,
        process: SandboxedChildProcess | None,
        pidfd: int | None,
        stage: SandboxStage | None,
        identity: SandboxIdentity,
        block_write: int | None,
        phase: str,
    ) -> bool:
        """Abort only a proven domain; otherwise retain its execution gate closed.

        Bubblewrap's ``--block-fd`` treats EOF as a release, so the writer must
        outlive an unproven startup domain.  The separately gated namespace PID1
        rejects that EOF before provider execution, but keeping this writer open
        is defence in depth until the Dashboard itself exits.
        """
        if process is not None and pidfd is not None:
            handle = _BubblewrapDeviceAuthHandle(
                process=process,
                pidfd=pidfd,
                stage=stage if stage is not None else SandboxStage(Path("/nonexistent"), -1),
                identity=identity,
                identity_pool=self._identity_pool,
                relative_output_path=Path(".invalid"),
                shim_ready=False,
                pidfd_send_signal=self._pidfd_send_signal,
                pidfd_is_dead=self._pidfd_is_dead,
            )
            await handle.terminate()
            return False

        if process is not None:
            if stage is not None:
                _close_fd(stage.root_fd)
            if block_write is not None:
                self._quarantined_startup_gates.append(
                    _QuarantinedStartupGate(process=process, block_write_fd=block_write)
                )
                logger.warning(
                    "Dashboard CLI-auth sandbox retained a startup stage without PID1 proof "
                    "phase=%s cleanup_outcome=quarantined",
                    phase,
                )
                return True
            logger.warning(
                "Dashboard CLI-auth sandbox retained a startup stage without PID1 proof "
                "phase=%s cleanup_outcome=quarantined_without_gate",
                phase,
            )
            return False

        if stage is not None:
            _close_fd(stage.root_fd)
            with contextlib.suppress(OSError):
                await asyncio.to_thread(__import__("shutil").rmtree, stage.path)
        # No child domain started, so returning this identity is safe.
        await self._identity_pool.release(identity)
        return False

    async def _launch_invocation(
        self,
        *,
        command: tuple[str, ...],
        readonly_inputs: tuple[SandboxReadonlyInput, ...],
        relative_output_path: Path | None,
        authority: ReadonlySandboxAuthority | None,
    ) -> _BubblewrapDeviceAuthHandle:
        """Start one typed child domain through the shared PID1 handshake."""
        identity = await self._identity_pool.acquire()
        stage: SandboxStage | None = None
        process: SandboxedChildProcess | None = None
        pidfd: int | None = None
        info_read: int | None = None
        info_write: int | None = None
        block_read: int | None = None
        block_write: int | None = None
        shim_gate_read: int | None = None
        shim_gate_write: int | None = None
        phase = "stage_create"
        try:
            stage = self._stage_factory(identity)
            phase = "authority_stage"
            if authority is not None:
                _stage_readonly_authority(stage, identity, authority)
            phase = "pipe_create"
            info_read, info_write = os.pipe2(os.O_CLOEXEC)
            block_read, block_write = os.pipe2(os.O_CLOEXEC)
            shim_gate_read, shim_gate_write = os.pipe2(os.O_CLOEXEC)
            phase = "launch_plan"
            plan = build_bubblewrap_launch_plan(
                bwrap_path=self._bwrap_path,
                shim_path=self._shim_path,
                identity=identity,
                stage_home=stage.path,
                command=command,
                readonly_inputs=readonly_inputs,
                info_fd=info_write,
                block_fd=block_read,
                shim_gate_fd=shim_gate_read,
            )
            phase = "spawn"
            process = await self._spawn(
                *plan.argv,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                env=plan.environment,
                close_fds=plan.close_fds,
                pass_fds=plan.pass_fds,
                preexec_fn=_outer_identity_preexec(identity),
            )
            _close_fd(info_write)
            info_write = None
            _close_fd(block_read)
            block_read = None
            _close_fd(shim_gate_read)
            shim_gate_read = None

            phase = "pid1_receipt"
            child_pid = await self._read_info_pid(info_read)
            phase = "pidfd_open"
            pidfd = self._pidfd_open(child_pid, 0)
            phase = "bwrap_gate_release"
            self._release_payload(block_write)
            _close_fd(block_write)
            block_write = None
            phase = "shim_gate_release"
            self._release_payload(shim_gate_write)
            _close_fd(shim_gate_write)
            shim_gate_write = None
            phase = "shim_ready"
            await self._verify_shim_ready(process)

            return _BubblewrapDeviceAuthHandle(
                process=process,
                pidfd=pidfd,
                stage=stage,
                identity=identity,
                identity_pool=self._identity_pool,
                relative_output_path=relative_output_path,
                shim_ready=True,
                pidfd_send_signal=self._pidfd_send_signal,
                pidfd_is_dead=self._pidfd_is_dead,
            )
        except asyncio.CancelledError:
            # Cancellation can arrive after the block-FD release while the
            # parent is awaiting the namespace-init receipt.  Shield the same
            # pidfd-first cleanup path so task cancellation never abandons a
            # live namespace, stage, descriptor, or leased outer identity.
            cleanup_task = asyncio.create_task(
                self._abort_startup(
                    process=process,
                    pidfd=pidfd,
                    stage=stage,
                    identity=identity,
                    block_write=block_write,
                    phase=phase,
                )
            )
            try:
                gate_quarantined = await asyncio.shield(cleanup_task)
                if gate_quarantined:
                    block_write = None
            except Exception:
                # The original cancellation remains authoritative.  A failed
                # cleanup deliberately retains the UID lease rather than
                # risking domain reuse.
                logger.warning("Dashboard CLI-auth sandbox cancellation cleanup failed safely")
            raise
        except (OSError, SandboxLaunchValidationError, SandboxUnavailableError) as exc:
            gate_quarantined = await self._abort_startup(
                process=process,
                pidfd=pidfd,
                stage=stage,
                identity=identity,
                block_write=block_write,
                phase=phase,
            )
            if gate_quarantined:
                block_write = None
            logger.warning(
                "Dashboard CLI-auth sandbox startup failed safely phase=%s error_class=%s",
                phase,
                type(exc).__name__,
            )
            raise SandboxUnavailableError(
                "Dashboard CLI-auth sandbox startup failed safely"
            ) from exc
        finally:
            _close_fd(info_read)
            _close_fd(info_write)
            _close_fd(block_read)
            _close_fd(block_write)
            _close_fd(shim_gate_read)
            _close_fd(shim_gate_write)

    async def launch_device_auth(self, provider: CLIAuthProviderDef) -> DeviceAuthSandboxHandle:
        """Launch one device-auth payload only after a PID1 containment receipt."""
        self._exact_image_preflight()
        invocation = self._invocation_resolver(provider)
        return await self._launch_invocation(
            command=invocation.command,
            readonly_inputs=invocation.readonly_inputs,
            relative_output_path=invocation.relative_output_path,
            authority=None,
        )

    async def run_readonly_command(
        self,
        provider: CLIAuthProviderDef,
        *,
        command: tuple[str, ...],
        authority: ReadonlySandboxAuthority,
        timeout_s: float,
    ) -> SandboxedCommandResult:
        """Run a health/API command with a disposable staged authority copy."""
        self._exact_image_preflight()
        if timeout_s <= 0:
            raise SandboxUnavailableError("Dashboard CLI-auth sandbox timeout is invalid")
        invocation = self._readonly_invocation_resolver(provider, command)
        handle = await self._launch_invocation(
            command=invocation.command,
            readonly_inputs=invocation.readonly_inputs,
            relative_output_path=None,
            authority=authority,
        )
        try:
            if not isinstance(handle.process.stdout, asyncio.StreamReader):
                raise SandboxUnavailableError(
                    "Dashboard CLI-auth sandbox child stdout is unavailable"
                )
            output = await _read_bounded_to_eof(handle.process.stdout, timeout_s=timeout_s)
            if not await handle.complete_readonly():
                raise SandboxUnavailableError("Dashboard CLI-auth sandbox cleanup failed safely")
        except asyncio.CancelledError:
            cleanup_task = asyncio.create_task(handle.terminate())
            try:
                await asyncio.shield(cleanup_task)
            except Exception:
                logger.warning("Dashboard CLI-auth sandbox read-only cancellation cleanup failed")
            raise
        except Exception:
            await handle.terminate()
            raise

        if handle.process.returncode is None:
            raise SandboxUnavailableError("Dashboard CLI-auth sandbox child exit was not observed")
        return SandboxedCommandResult(returncode=handle.process.returncode, output=output)
