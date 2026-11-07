"""Concrete, dark Linux process domain; no provider/runtime eligibility is implied.

Only individually pinned files enter an empty root. The existing repository's
PID1 gate shim closes setup descriptors before executing the payload. A pidfd
is acquired while that PID1 is blocked; only then can the payload be released.
Namespace-init death proves Linux has torn down that PID namespace's descendants,
including detached sessions. Direct Bubblewrap leader exit alone is insufficient.
Network is disabled until a separately reviewed restricted gateway exists.
"""

from __future__ import annotations

import asyncio
import fcntl
import hashlib
import json
import os
import select
import signal
import stat
from collections.abc import Coroutine
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, cast

from .codex import CODEX_HOME, NATIVE_FILES, CodexSettings
from .contracts import Refused
from .material import InvocationMaterial
from .opencode import CONFIG_NAME, OpenCodeSettings

_READY = b"BUTLERS_RUNTIME_CLI_SANDBOX_READY\n"
_MAX_OUTPUT = 1024 * 1024
_SETUP_SECONDS = 5.0
# The pinned Codex static binary is 274 MiB. Each launch holds a sealed copy.
_MAX_IMAGE_FILE = 384 * 1024 * 1024
# Linux UAPI <linux/fcntl.h>. Some supported CPython builds omit these names
# although their memfd_create syscall supports sealing. Unsupported kernels
# still refuse the actual operation; there is no unsealed fallback.
_F_ADD_SEALS = 1033
_F_GET_SEALS = 1034
_SEALS = 0x0008 | 0x0004 | 0x0002 | 0x0001  # WRITE, GROW, SHRINK, SEAL


def _open_source(path: Path) -> int:
    """Anchor every path component; neither final nor ancestor links are followed."""
    if not path.is_absolute() or ".." in path.parts:
        raise Refused("invalid_input_path")
    directory = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        for part in path.parts[1:-1]:
            child = os.open(
                part,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                dir_fd=directory,
            )
            os.close(directory)
            directory = child
        return os.open(
            path.name,
            os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK,
            dir_fd=directory,
        )
    except OSError:
        raise Refused("invalid_input_path") from None
    finally:
        os.close(directory)


def _identity(metadata: os.stat_result) -> tuple[int, ...]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_uid,
        metadata.st_nlink,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _snapshot(source: int, *, expected: str, maximum: int, observed: os.stat_result) -> int:
    """Return kernel-sealed bytes, hashed AFTER sealing, never the live source FD."""
    before = os.fstat(source)
    if _identity(before) != _identity(observed):
        raise Refused("input_changed_during_snapshot")
    snapshot = os.memfd_create("butlers-invocation-input", os.MFD_CLOEXEC | os.MFD_ALLOW_SEALING)
    try:
        total = 0
        while chunk := os.read(source, 65536):
            total += len(chunk)
            if total > maximum:
                raise Refused("input_size_exceeded")
            remaining = memoryview(chunk)
            while remaining:
                written = os.write(snapshot, remaining)
                if written <= 0:
                    raise Refused("input_snapshot_failed")
                remaining = remaining[written:]
        if total != before.st_size or _identity(before) != _identity(os.fstat(source)):
            raise Refused("input_changed_during_snapshot")
        fcntl.fcntl(snapshot, _F_ADD_SEALS, _SEALS)
        if fcntl.fcntl(snapshot, _F_GET_SEALS) & _SEALS != _SEALS:
            raise Refused("input_snapshot_unsealed")
        os.lseek(snapshot, 0, os.SEEK_SET)
        with os.fdopen(os.dup(snapshot), "rb") as stream:
            actual = hashlib.file_digest(stream, "sha256").hexdigest()
        if actual != expected:
            raise Refused("image_input_changed")
        os.lseek(snapshot, 0, os.SEEK_SET)
        return snapshot
    except BaseException:
        os.close(snapshot)
        raise


@dataclass(frozen=True)
class ImageFile:
    """One server-configured immutable image file, not a request-supplied mount."""

    source: Path
    destination: str
    sha256: str

    def open(self) -> int:
        destination = PurePosixPath(self.destination)
        if (
            not self.destination.startswith("/runtime/")
            or str(destination) != self.destination
            or ".." in destination.parts
            or len(self.sha256) != 64
        ):
            raise Refused("invalid_image_input")
        descriptor = _open_source(self.source)
        try:
            metadata = os.fstat(descriptor)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_uid not in (0, os.geteuid())
                or metadata.st_mode & 0o7222
                or metadata.st_nlink != 1
                or not 0 < metadata.st_size <= _MAX_IMAGE_FILE
            ):
                raise Refused("invalid_image_input")
            return _snapshot(
                descriptor, expected=self.sha256, maximum=_MAX_IMAGE_FILE, observed=metadata
            )
        finally:
            os.close(descriptor)


class BubblewrapDomain:
    """One inert server-selected command; execute once and settle independently.

    Construction performs no launch. Instances cannot be reused. A failed or
    absent PID1 receipt is never converted into namespace settlement evidence.
    The outer child is killed/reaped best-effort even when that proof is missing.
    """

    def __init__(
        self,
        *,
        image: tuple[ImageFile, ...],
        command: tuple[str, ...],
        shim: str = "/runtime/init",
        bwrap: Path = Path("/usr/bin/bwrap"),
        opencode: OpenCodeSettings | None = None,
        codex: CodexSettings | None = None,
    ) -> None:
        destinations = {entry.destination for entry in image}
        if (
            len(destinations) != len(image)
            or shim not in destinations
            or not command
            or command[0] not in destinations
            or any(type(item) is not str or "\0" in item for item in command)
            or sum(len(item) for item in command) > 16384
        ):
            raise Refused("invalid_domain_profile")
        if opencode is not None and (
            type(opencode) is not OpenCodeSettings or command != opencode.command
        ):
            raise Refused("invalid_domain_profile")
        if codex is not None and (
            type(codex) is not CodexSettings or opencode is not None or command != codex.command
        ):
            raise Refused("invalid_domain_profile")
        self._opencode = opencode
        self._codex = codex
        self._stdin: int | None = None
        self._image = image
        self._command = command
        self._shim = shim
        self._bwrap = bwrap
        self._process: asyncio.subprocess.Process | None = None
        self._spawn: asyncio.Task | None = None
        self._pidfd: int | None = None
        self._fds: set[int] = set()
        self._used = False
        self._released = False
        self.namespace_dead = False
        self.direct_child_reaped = False
        self._settled = False
        self._reaper: asyncio.Task | None = None
        self.output_bytes_received = 0
        self.output_ready = asyncio.Event()

    def _close(self, descriptor: int) -> None:
        if descriptor in self._fds:
            self._fds.remove(descriptor)
            os.close(descriptor)

    def _close_all(self) -> None:
        for descriptor in tuple(self._fds):
            self._close(descriptor)

    def _pipe(self) -> tuple[int, int]:
        pair = os.pipe2(os.O_CLOEXEC)
        self._fds.update(pair)
        return pair

    @staticmethod
    def _task(coroutine: Coroutine[Any, Any, Any]) -> asyncio.Task:
        try:
            return asyncio.create_task(coroutine)
        except BaseException:
            coroutine.close()
            raise

    async def _info(self, descriptor: int) -> int:
        os.set_blocking(descriptor, False)
        data = bytearray()
        deadline = asyncio.get_running_loop().time() + _SETUP_SECONDS
        while asyncio.get_running_loop().time() < deadline:
            try:
                chunk = os.read(descriptor, 4097 - len(data))
            except BlockingIOError:
                await asyncio.sleep(0.005)
                continue
            if not chunk:
                break
            data.extend(chunk)
            if len(data) > 4096:
                break
            try:
                value = json.loads(data)
            except (ValueError, UnicodeError):
                continue
            pid = value.get("child-pid") if type(value) is dict else None
            if type(pid) is int and pid > 1:
                return pid
            break
        raise Refused("namespace_receipt_missing")

    def _prepare_native(self, material: InvocationMaterial) -> None:
        if self._opencode is not None:
            material.prepare_opencode(self._opencode)
        if self._codex is not None:
            material.prepare_codex(self._codex)

    def _plan(
        self, material: InvocationMaterial
    ) -> tuple[list[str], tuple[int, ...], int, int, int]:
        self._prepare_native(material)
        metadata = self._bwrap.stat()
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != 0 or metadata.st_mode & 0o022:
            raise Refused("bubblewrap_unavailable")
        info_read, info_write = self._pipe()
        block_read, block_write = self._pipe()
        gate_read, gate_write = self._pipe()
        argv = [
            str(self._bwrap),
            "--unshare-user",
            "--unshare-pid",
            "--unshare-ipc",
            "--unshare-uts",
            "--unshare-net",
            "--disable-userns",
            "--assert-userns-disabled",
            "--as-pid-1",
            "--die-with-parent",
            "--new-session",
            "--cap-drop",
            "ALL",
            "--clearenv",
            "--setenv",
            "PATH",
            "/runtime",
            "--setenv",
            "BUTLERS_MODEL_CREDENTIAL",
            "/invocation/model-credential",
            "--setenv",
            "BUTLERS_MCP_CONFIG",
            "/invocation/mcp.json",
            "--setenv",
            "BUTLERS_PROMPT",
            "/invocation/prompt",
            "--tmpfs",
            "/",
            "--proc",
            "/proc",
            "--dev",
            "/dev",
            "--tmpfs",
            "/tmp",
            "--dir",
            "/invocation",
        ]
        inherited = [info_write, block_read, gate_read]
        directories = sorted(
            {
                str(p)
                for entry in self._image
                for p in PurePosixPath(entry.destination).parents
                if str(p) != "/"
            },
            key=lambda item: (item.count("/"), item),
        )
        for directory in directories:
            argv.extend(("--dir", directory))
        for entry in self._image:
            descriptor = entry.open()
            self._fds.add(descriptor)
            inherited.append(descriptor)
            argv.extend(("--perms", "0555", "--ro-bind-data", str(descriptor), entry.destination))
        native = self._opencode or self._codex
        if native is not None:
            # Replace only the fixed non-adapter env block. Never accept a
            # caller mapping or inherit the host HOME/config/credential stores.
            start = argv.index("--setenv")
            end = argv.index("--tmpfs")
            argv[start:end] = [
                item for key, value in native.environment for item in ("--setenv", key, value)
            ]
        names = ("model-credential", "prompt", "mcp.json")
        mounts = {name: f"/invocation/{name}" for name in names}
        if self._opencode is not None:
            names += (CONFIG_NAME,)
            mounts[CONFIG_NAME] = f"/invocation/{CONFIG_NAME}"
        if self._codex is not None:
            # Codex keeps its config beside writable runtime state, so its home
            # is fresh tmpfs scratch with these files mounted read-only inside.
            argv.extend(("--dir", CODEX_HOME))
            names += tuple(name for name, _ in NATIVE_FILES)
            mounts.update(NATIVE_FILES)
        for name in names:
            descriptor = _open_source(material.directory / name)
            metadata = os.fstat(descriptor)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_uid != os.geteuid()
                or stat.S_IMODE(metadata.st_mode) != 0o600
                or metadata.st_nlink != 1
                or metadata.st_size > 256 * 1024
            ):
                os.close(descriptor)
                raise Refused("invalid_staged_material")
            try:
                snapshot = _snapshot(
                    descriptor,
                    expected=material.content_hashes[name],
                    maximum=256 * 1024,
                    observed=metadata,
                )
            finally:
                os.close(descriptor)
            self._fds.add(snapshot)
            if native is not None and name == "prompt":
                # --ro-bind-data consumes snapshot's current offset. dup() or
                # subprocess stdin=snapshot would share that offset and leave
                # the CLI at EOF. Reopen the owned sealed memfd to obtain an
                # independent, readonly description starting at byte zero.
                stdin = os.open(f"/proc/self/fd/{snapshot}", os.O_RDONLY | os.O_CLOEXEC)
                self._fds.add(stdin)
                if (
                    _identity(os.fstat(stdin)) != _identity(os.fstat(snapshot))
                    or fcntl.fcntl(stdin, _F_GET_SEALS) & _SEALS != _SEALS
                    or os.lseek(stdin, 0, os.SEEK_CUR) != 0
                ):
                    raise Refused("invalid_prompt_input")
                self._stdin = stdin
            inherited.append(snapshot)
            argv.extend(("--perms", "0400", "--ro-bind-data", str(snapshot), mounts[name]))
        argv.extend(
            (
                "--info-fd",
                str(info_write),
                "--block-fd",
                str(block_read),
                "--chdir",
                "/tmp",
                "--",
                self._shim,
                "--shim-gate-fd",
                str(gate_read),
                "--",
                *self._command,
            )
        )
        return argv, tuple(inherited), info_read, block_write, gate_write

    async def execute(self, material: InvocationMaterial) -> bytes:
        if self._used:
            raise Refused("domain_already_started")
        self._used = True
        try:
            argv, inherited, info_read, block_write, gate_write = self._plan(material)
            native = self._opencode or self._codex
            if native is not None and self._stdin is None:
                raise Refused("prompt_input_missing")
            self._spawn = self._task(
                asyncio.create_subprocess_exec(
                    *argv,
                    stdin=self._stdin if native is not None else asyncio.subprocess.DEVNULL,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.DEVNULL,
                    env={},
                    close_fds=True,
                    pass_fds=inherited,
                    start_new_session=True,
                    limit=65536,
                )
            )
            self._process = cast(asyncio.subprocess.Process, await asyncio.shield(self._spawn))
            # Keep the three parent gates; all other inherited descriptors have
            # been copied into the child. EOF never releases our PID1 shim.
            for descriptor in tuple(self._fds):
                if descriptor not in (info_read, block_write, gate_write):
                    self._close(descriptor)
            pid = await self._info(info_read)
            self._pidfd = os.pidfd_open(pid, 0)
            os.write(block_write, b"1")
            os.write(gate_write, b"1")
            self._released = True
            self._close_all()
            assert self._process.stdout is not None
            async with asyncio.timeout(_SETUP_SECONDS):
                if await self._process.stdout.readexactly(len(_READY)) != _READY:
                    raise Refused("namespace_gate_failed")
            output = bytearray()
            while chunk := await self._process.stdout.read(65536):
                self.output_bytes_received += len(chunk)
                self.output_ready.set()
                output.extend(chunk)
                if len(output) > _MAX_OUTPUT:
                    raise Refused("runtime_output_limit")
            if await self._process.wait() != 0:
                raise Refused("runtime_failed")
            return bytes(output)
        except asyncio.CancelledError:
            raise
        except BaseException:
            raise Refused("sandbox_execution_failed") from None

    async def _dead(self, deadline: float) -> bool:
        if self._pidfd is None:
            return False
        while True:
            if select.select([self._pidfd], [], [], 0)[0]:
                return True
            if asyncio.get_running_loop().time() >= deadline:
                return False
            await asyncio.sleep(0.005)

    async def settle(self) -> bool:
        if self._settled:
            return True
        deadline = asyncio.get_running_loop().time() + 4
        try:
            if self._process is None and self._spawn is not None:
                remaining = max(0, deadline - asyncio.get_running_loop().time())
                done, _ = await asyncio.wait({self._spawn}, timeout=remaining)
                if done:
                    self._process = self._spawn.result()
            if self._process is None:
                # No subprocess was even attempted: staged input refusal is
                # settled. A pending/failed spawn remains unknown, not healthy.
                return self._spawn is None
            if self._pidfd is not None:
                try:
                    signal.pidfd_send_signal(self._pidfd, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                if not await self._dead(min(deadline, asyncio.get_running_loop().time() + 0.1)):
                    try:
                        signal.pidfd_send_signal(self._pidfd, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                self.namespace_dead = await self._dead(deadline)
            # Before the pidfd receipt the shim gate has not been released;
            # kill the outer child before closing gates (block EOF releases).
            if self._process.returncode is None:
                self._process.kill()
            remaining = max(0, deadline - asyncio.get_running_loop().time())
            # A flood can leave asyncio's pipe paused with unread data even
            # after SIGKILL. Drain without retaining output before waiting for
            # transport completion; direct-child exit is not pipe closure.
            if self._process.stdout is not None:
                async with asyncio.timeout(remaining):
                    while await self._process.stdout.read(65536):
                        pass
            remaining = max(0, deadline - asyncio.get_running_loop().time())
            self._reaper = self._task(self._process.wait())
            done, _ = await asyncio.wait({self._reaper}, timeout=remaining)
            self.direct_child_reaped = bool(done)
            self._settled = self.namespace_dead and self.direct_child_reaped
            return self._settled
        except (OSError, ValueError, TimeoutError, RuntimeError):
            return False
        finally:
            if self._reaper is not None and not self._reaper.done():
                self._reaper.cancel()
                await asyncio.gather(self._reaper, return_exceptions=True)
            # An unresolved subprocess creation may still consume pass_fds.
            # Retain those exact descriptors in quarantine instead of closing
            # them and allowing unrelated descriptor numbers to be reused.
            # settle=False keeps the invocation owner unhealthy; restart also
            # refuses its stale stage. No automatic retry is authorized.
            if self._spawn is None or self._spawn.done():
                self._close_all()
            if self._pidfd is not None and self.namespace_dead:
                os.close(self._pidfd)
                self._pidfd = None
