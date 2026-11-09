"""The session host's runtime credential volume, dark (RFC 0038 D3, bu-kiuh8v.3).

Today the Codex device-auth document lives in butlers-up's ``~/.codex``. When
the session host takes over runtime OAuth, it lives here instead, and only the
control plane writes it, over the authenticated control channel. The session
host never reads the credential store or the database.

One closed document (Codex ``auth.json``), replaced only by compare-and-set on
its SHA-256, so a dashboard login and a rotation collected from the host can
never silently overwrite each other. Errors are fixed codes; no path, size or
byte of a document reaches an exception, log or response from here.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import threading
from pathlib import Path

from .contracts import Refused

_MAX_DOCUMENT = 64 * 1024


def _object_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError
        result[key] = value
    return result


def valid_document(document: bytes) -> bool:
    """One non-empty JSON object, UTF-8, without duplicate keys, at most 64 KiB."""
    if type(document) is not bytes or not 0 < len(document) <= _MAX_DOCUMENT:
        return False
    try:
        parsed = json.loads(document.decode("utf-8"), object_pairs_hook=_object_pairs)
    except (ValueError, UnicodeError, RecursionError):
        return False
    return isinstance(parsed, dict) and bool(parsed)


class CredentialVolume:
    """A private directory the trusted host constructor names, never a request."""

    CODEX_AUTH = "codex-auth.json"

    def __init__(self, root: Path) -> None:
        try:
            info = os.lstat(root)
        except OSError:
            raise Refused("credential_volume_unavailable") from None
        if (
            not stat.S_ISDIR(info.st_mode)
            or stat.S_IMODE(info.st_mode) != 0o700
            or info.st_uid != os.geteuid()
        ):
            raise Refused("credential_volume_unavailable")
        self.root = root
        self._lock = threading.Lock()

    def _directory(self) -> int:
        try:
            return os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        except OSError:
            raise Refused("credential_volume_unavailable") from None

    def _load(self, directory: int) -> bytes | None:
        try:
            descriptor = os.open(
                self.CODEX_AUTH, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=directory
            )
        except FileNotFoundError:
            return None
        except OSError:
            raise Refused("credential_volume_unavailable") from None
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode) or info.st_size > _MAX_DOCUMENT:
                raise Refused("credential_volume_unavailable")
            chunks, remaining = [], _MAX_DOCUMENT + 1
            while remaining > 0:
                chunk = os.read(descriptor, remaining)
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            return b"".join(chunks)
        except OSError:
            raise Refused("credential_volume_unavailable") from None
        finally:
            os.close(descriptor)

    def read(self) -> tuple[bytes, str] | None:
        """The current document and its SHA-256, or None when there is none."""
        with self._lock:
            directory = self._directory()
            try:
                document = self._load(directory)
            finally:
                os.close(directory)
        if document is None:
            return None
        return document, hashlib.sha256(document).hexdigest()

    def status(self) -> str | None:
        """The current document's SHA-256 only: the value-free CAS token."""
        found = self.read()
        return None if found is None else found[1]

    def install(self, document: bytes, *, expected_sha256: str | None) -> bool:
        """Replace the document only if the current one is ``expected_sha256``.

        ``None`` expects no document. Returns False, changing nothing, when
        the expectation is stale. The write is atomic and fsynced, mode 0600.
        """
        if not valid_document(document):
            raise Refused("invalid_credential_document")
        with self._lock:
            directory = self._directory()
            try:
                current = self._load(directory)
                actual = None if current is None else hashlib.sha256(current).hexdigest()
                if actual != expected_sha256:
                    return False
                self._replace(directory, document)
                return True
            finally:
                os.close(directory)

    def _replace(self, directory: int, document: bytes) -> None:
        temporary = f".{self.CODEX_AUTH}.{os.getpid()}.{threading.get_ident()}.tmp"
        try:
            descriptor = os.open(
                temporary,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                0o600,
                dir_fd=directory,
            )
        except OSError:
            raise Refused("credential_volume_unavailable") from None
        try:
            try:
                view = memoryview(document)
                while view:
                    view = view[os.write(descriptor, view) :]
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            os.replace(temporary, self.CODEX_AUTH, src_dir_fd=directory, dst_dir_fd=directory)
            os.fsync(directory)
        except OSError:
            try:
                os.unlink(temporary, dir_fd=directory)
            except OSError:
                pass
            raise Refused("credential_volume_unavailable") from None
