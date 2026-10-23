"""Codex volume CAS in the host; database CAS stays in the control process.

Only the fixed auth.json in an explicitly mounted host volume is accessible.
There is no path, provider, environment or credential-store selection on wire.
Device-auth and rotation bytes retain the existing closed Codex projection.
"""

from __future__ import annotations

import asyncio
import fcntl
import hashlib
import json
import os
import stat
import time
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path
from uuid import uuid4

from butlers.cli_auth.persistence import _project_codex_device_auth_document
from butlers.session_host.launch import Refused


def codex_document(content: bytes) -> bytes:
    try:
        if type(content) is not bytes or not 0 < len(content) <= 65536:
            raise ValueError
        projected = _project_codex_device_auth_document(content.decode("utf-8"))
        if projected is None:
            raise ValueError
        return projected.encode("utf-8")
    except (ValueError, UnicodeError):
        raise Refused("invalid_model_credential") from None


@dataclass(frozen=True)
class VolumeSnapshot:
    content: bytes | None = field(repr=False)

    @property
    def digest(self):
        return None if self.content is None else hashlib.sha256(self.content).hexdigest()


class CodexCredentialVolume:
    _filename = "auth.json"
    _lockname = ".auth.lock"
    _document = staticmethod(codex_document)

    def __init__(self, root: Path, *, expected_uid: int = 0):
        if not isinstance(root, Path) or not root.is_absolute():
            raise Refused("invalid_credential_volume")
        self._root, self._uid = root, expected_uid
        self._workers: set[asyncio.Future] = set()
        self._closing = False
        self._close_deadline = None

    def _settled(self, worker):
        if worker.cancelled():
            # A cancelled executor future cannot prove its thread exited.
            # This cannot be caused by a request, and remains quarantined.
            self._closing = True
            return
        self._workers.discard(worker)
        # A cancelled waiter cannot consume a late filesystem exception.
        # Retrieve it here without logging credential bytes or file details.
        worker.exception()

    async def _run(self, **kwargs):
        if self._closing or len(self._workers) >= 4:
            raise Refused("credential_volume_unavailable")
        worker = asyncio.get_running_loop().run_in_executor(
            None, partial(self._operation, **kwargs)
        )
        self._workers.add(worker)
        worker.add_done_callback(self._settled)
        # wait() never propagates caller cancellation into the executor future.
        # Its completion arrives only when the actual thread has exited its
        # finally block, releasing the file lock and every descriptor.
        await asyncio.wait({worker})
        return worker.result()

    async def close(self, *, deadline: float | None = None):
        self._closing = True
        if self._close_deadline is None:
            self._close_deadline = (
                deadline if deadline is not None else asyncio.get_running_loop().time() + 32
            )
        deadline = self._close_deadline if deadline is None else min(deadline, self._close_deadline)
        pending = set(self._workers)
        cancelled = False
        if any(worker.cancelled() for worker in pending):
            raise Refused("credential_volume_cleanup_incomplete")
        while pending and asyncio.get_running_loop().time() < deadline:
            try:
                _, pending = await asyncio.wait(
                    pending, timeout=max(0, deadline - asyncio.get_running_loop().time())
                )
            except asyncio.CancelledError:
                cancelled = True
        if pending:
            # Python cannot terminate a running filesystem thread. Retain it,
            # deny further admission, and refuse a successful close receipt.
            raise Refused("credential_volume_cleanup_incomplete")
        if cancelled:
            raise asyncio.CancelledError

    def _metadata(self, descriptor, *, directory=False):
        value = os.fstat(descriptor)
        if (
            value.st_uid != self._uid
            or stat.S_IMODE(value.st_mode) != (0o700 if directory else 0o600)
            or not (stat.S_ISDIR(value.st_mode) if directory else stat.S_ISREG(value.st_mode))
            or (not directory and value.st_nlink != 1)
        ):
            raise Refused("credential_volume_unavailable")
        return value

    def _read(self, directory):
        try:
            descriptor = os.open(
                self._filename,
                os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
                dir_fd=directory,
            )
        except FileNotFoundError:
            return VolumeSnapshot(None)
        try:
            before = self._metadata(descriptor)
            if not 0 < before.st_size <= 65536:
                raise Refused("credential_volume_unavailable")
            content = os.read(descriptor, 65537)
            after = os.fstat(descriptor)
            if len(content) != before.st_size or (
                before.st_ino,
                before.st_mtime_ns,
                before.st_size,
            ) != (after.st_ino, after.st_mtime_ns, after.st_size):
                raise Refused("credential_volume_unavailable")
            # Validate but preserve the actual bytes for exact-version CAS.
            self._document(content)
            return VolumeSnapshot(content)
        finally:
            os.close(descriptor)

    def _operation(self, *, replace=False, expected=None, content=None):
        directory = lock = None
        temporary = None
        try:
            directory = os.open(self._root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            self._metadata(directory, directory=True)
            lock = os.open(
                self._lockname,
                os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
                0o600,
                dir_fd=directory,
            )
            self._metadata(lock)
            deadline = time.monotonic() + 2
            while True:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise Refused("credential_volume_unavailable") from None
                    time.sleep(0.01)
            current = self._read(directory)
            if not replace:
                return current
            if current.digest != expected:
                raise Refused("credential_volume_conflict")
            temporary = f".auth-{uuid4().hex}.tmp"
            descriptor = os.open(
                temporary,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                0o600,
                dir_fd=directory,
            )
            try:
                pending = memoryview(content)
                while pending:
                    written = os.write(descriptor, pending)
                    if written <= 0:
                        raise OSError
                    pending = pending[written:]
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            os.rename(temporary, self._filename, src_dir_fd=directory, dst_dir_fd=directory)
            temporary = None
            os.fsync(directory)
            return self._read(directory)
        except OSError:
            raise Refused("credential_volume_unavailable") from None
        finally:
            if temporary is not None and directory is not None:
                try:
                    os.unlink(temporary, dir_fd=directory)
                except FileNotFoundError:
                    pass
            if lock is not None:
                os.close(lock)
            if directory is not None:
                os.close(directory)

    async def read(self) -> VolumeSnapshot:
        return await self._run()

    async def replace(self, *, expected: str | None, content: bytes) -> VolumeSnapshot:
        if expected is not None and (
            type(expected) is not str
            or len(expected) != 64
            or any(c not in "0123456789abcdef" for c in expected)
        ):
            raise Refused("invalid_credential_volume")
        content = self._document(content)
        return await self._run(replace=True, expected=expected, content=content)


def opencode_document(content: bytes) -> bytes:
    """Only the two independently selected OpenCode entries; no inherited peers."""
    from butlers.cli_auth.persistence import (
        _parse_strict_device_auth_document,
        _project_opencode_go_runtime_entry,
        _project_opencode_openai_device_auth_document,
    )

    try:
        if type(content) is not bytes or not 0 < len(content) <= 65536:
            raise ValueError
        document = _parse_strict_device_auth_document(content.decode())
        if set(document) - {"openai", "opencode-go"}:
            raise ValueError
        result = {}
        if "openai" in document:
            projected = _project_opencode_openai_device_auth_document(
                json.dumps({"openai": document["openai"]})
            )
            if projected is None:
                raise ValueError
            result.update(json.loads(projected))
        if "opencode-go" in document:
            projected = _project_opencode_go_runtime_entry(document["opencode-go"])
            if projected is None:
                raise ValueError
            result["opencode-go"] = projected
        return json.dumps(result, sort_keys=True, separators=(",", ":")).encode()
    except (ValueError, UnicodeError, TypeError):
        raise Refused("invalid_model_credential") from None


class OpenCodeCredentialVolume(CodexCredentialVolume):
    """A separate fixed file/lock under the same trusted private volume root."""

    _filename = "opencode-auth.json"
    _lockname = ".opencode-auth.lock"
    _document = staticmethod(opencode_document)


class ControlOpenCodeSync:
    def __init__(self, volume, authority):
        self._volume, self._authority = volume, authority
        self._lock = asyncio.Lock()

    async def _selected(self):
        try:
            async with asyncio.timeout(2):
                values = await self._authority.load_opencode_cli_auths()
            if type(values) is not dict or set(values) != {"opencode-openai", "opencode-go"}:
                raise ValueError
            document = {}
            for name, value in values.items():
                if value is None:
                    continue
                if type(value) is not str:
                    raise ValueError
                projected = opencode_document(value.encode())
                entry = json.loads(projected)
                if set(entry) != {"openai" if name == "opencode-openai" else "opencode-go"}:
                    raise ValueError
                document.update(entry)
            content = opencode_document(json.dumps(document).encode())
        except Exception:
            raise Refused("credential_authority_unavailable") from None
        return values, content

    async def check_login_authority(self):
        # Initial enrollment may have no row. The selected owning store must
        # nevertheless be reachable and every present sibling must be valid.
        await self._selected()

    async def _prepare_locked(self):
        values, content = await self._selected()
        current = await self._volume.read_opencode()
        if current.content != content:
            await self._volume.replace_opencode(expected=current.digest, content=content)
        return values, content

    async def prepare(self):
        async with self._lock:
            _, content = await self._prepare_locked()
            return content

    async def probe(self, backend, *, record=False):
        """Only registered non-model OpenAI auth-list, fenced to its DB value."""
        from butlers.api.routers.cli_auth import _persist_selected_test_outcome_if_current
        from butlers.cli_auth.health import AuthHealthState

        started = time.monotonic()
        async with self._lock:
            values, _ = await self._prepare_locked()
            result = await backend.probe_provider("opencode-openai")
            expected = values["opencode-openai"]
            if record and expected is not None:
                try:
                    async with asyncio.timeout(2):
                        await _persist_selected_test_outcome_if_current(
                            self._authority,
                            provider="opencode-openai",
                            key="cli-auth/opencode-openai",
                            ok=result.state == AuthHealthState.authenticated,
                            detail=result.detail,
                            message=result.detail,
                            latency_ms=round((time.monotonic() - started) * 1000),
                            expected_store_value=expected,
                        )
                except Exception:
                    # Like the existing API outcome writer, audit unavailability
                    # cannot manufacture a different provider test result.
                    pass
            return result


class ControlCodexSync:
    """Explicit control-side authority; never instantiated in the session host.

    An invocation carries its own before-launch database value, independent of
    mutable process caches. Rotation cannot resurrect a deleted authority or
    overwrite a newer dashboard login. Conflicting host changes are read back
    by the next caller, never blindly replayed.
    """

    def __init__(self, volume, authority):
        self._volume, self._authority = volume, authority
        self._lock = asyncio.Lock()

    async def _authority_call(self, operation):
        try:
            async with asyncio.timeout(2):
                return await operation
        except Exception:
            raise Refused("credential_authority_unavailable") from None

    async def prepare(self) -> bytes:
        async with self._lock:
            value = await self._authority_call(self._authority.load_codex_cli_auth())
            if type(value) is not str:
                raise Refused("credential_authority_unavailable")
            content = codex_document(value.encode())
            current = await self._volume.read()
            if current.content != content:
                await self._volume.replace(expected=current.digest, content=content)
            # Return the exact DB value, not canonicalized bytes, as the CAS
            # snapshot associated with this invocation's rotation finalizer.
            return value.encode()

    async def check_login_authority(self):
        value = await self._authority_call(self._authority.load_codex_cli_auth())
        if value is not None:
            if type(value) is not str:
                raise Refused("credential_authority_unavailable")
            codex_document(value.encode())

    async def finalize(self, *, expected_authority: bytes, rotated: bytes):
        if type(expected_authority) is not bytes:
            raise Refused("credential_authority_unavailable")
        codex_document(expected_authority)
        rotated = codex_document(rotated)
        async with self._lock:
            saved = await self._authority_call(
                self._authority.store_codex_cli_auth_if_unchanged(
                    rotated.decode(), expected_value=expected_authority.decode()
                )
            )
            # Even a rejected stale rotation reconciles the volume from the
            # current explicit authority. It never writes an absent DB row.
            value = await self._authority_call(self._authority.load_codex_cli_auth())
            if type(value) is not str:
                raise Refused("credential_authority_unavailable")
            content = codex_document(value.encode())
            current = await self._volume.read()
            if current.content != content:
                await self._volume.replace(expected=current.digest, content=content)
            return saved is True

    async def health(self, *, record=False):
        """Parent-only fixed HTTP probe; no CLI or local canonical file.

        The tested DB value stays in control. Optional audit/cache persistence
        uses the existing same-value transaction, never a dashboard assertion.
        """
        from butlers.cli_auth.health import (
            AuthHealthResult,
            AuthHealthState,
            _parse_unexpired_codex_access_token,
            _probe_codex_backend,
        )

        started = time.monotonic()
        value = await self._authority_call(self._authority.load_codex_cli_auth())
        token, detail = _parse_unexpired_codex_access_token(value)
        if token is None:
            state = AuthHealthState.not_authenticated
        else:
            revoked, detail = await _probe_codex_backend(token)
            state = AuthHealthState.not_authenticated if revoked else AuthHealthState.authenticated
        result = AuthHealthResult("codex", state, detail or "Codex authority validated.")
        if record and type(value) is str:
            from butlers.api.routers.cli_auth import _persist_codex_test_outcome_if_current

            try:
                await self._authority_call(
                    _persist_codex_test_outcome_if_current(
                        self._authority,
                        key="cli-auth/codex",
                        ok=state == AuthHealthState.authenticated,
                        detail=result.detail,
                        message=result.detail,
                        latency_ms=round((time.monotonic() - started) * 1000),
                        expected_store_value=value,
                    )
                )
            except Exception:
                pass  # Preserve the existing API's best-effort outcome-write contract.
        return result
