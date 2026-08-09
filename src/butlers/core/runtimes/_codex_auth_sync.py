"""Race-safe Codex auth.json reconciliation with the credential authority.

Codex runtime processes share a canonical ``~/.codex/auth.json`` and may
rotate it during a CLI invocation.  The dashboard writes ``cli-auth/codex``
to the shared Tier 1 credential store.  Before every new spawn, this module
reconciles the two states; after an operation that can rotate credentials, it
conditionally persists the final local state only when the authority snapshot
captured before that operation still wins.

The module deliberately keeps file and store failures non-fatal.  It never
logs token contents.  Its per-path lock serializes local async tasks; the
credential-store compare-and-set is the cross-process safety boundary.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from butlers.credential_store import CredentialStore

logger = logging.getLogger(__name__)

_CODEX_AUTH_KEY = "cli-auth/codex"
_CODEX_AUTH_CATEGORY = "cli-auth"
_CODEX_AUTH_DESCRIPTION = "CLI auth token for Codex (OpenAI)"

# Per-process file-state cache.  The digest covers the full auth document so a
# tail-only change cannot be missed on a filesystem with a coarse mtime clock.
_AUTH_SYNC_CACHE: dict[str, tuple[int, str]] = {}

# The raw credential snapshot captured when a file baseline was established.
# It is intentionally private, never logged, and needed only as the expected
# value for an atomic DB compare-and-set.  A digest alone cannot express that
# conditional update to PostgreSQL without another race-prone read.
_AUTH_AUTHORITY_CACHE: dict[str, str] = {}

# Local task serialization complements, but does not replace, the DB CAS.
_AUTH_SYNC_LOCKS: dict[str, asyncio.Lock] = {}

_STABLE_READ_ATTEMPTS = 3

# Credential authority synchronization is a best-effort pre/post-launch
# safeguard, not part of the runtime's model-execution budget.  A blocked
# pool checkout or row lock must leave the existing local-auth invocation
# runnable rather than consume the session timeout.
_AUTH_STORE_OPERATION_TIMEOUT_S = 2.0


@dataclass(frozen=True)
class CodexAuthSyncResult:
    """The authority snapshot safe to associate with a later subprocess.

    ``expected_store_value`` is excluded from repr so accidental debug logging
    of this result cannot disclose a credential.

    ``authority_known`` distinguishes a confirmed absent credential row from
    an unavailable or unsafe authority.  Runtime finalizers persist a rotation
    only when both it and ``expected_store_value`` are present; an absent row
    is never re-created implicitly after a subprocess.  Explicit dashboard
    authentication remains the supported bootstrap path for a new credential.
    """

    expected_store_value: str | None = field(repr=False)
    file_changed: bool = False
    authority_known: bool = True


@dataclass(frozen=True)
class _AuthFileSnapshot:
    """A coherent read of a valid local document and its full fingerprint."""

    value: str = field(repr=False)
    fingerprint: tuple[int, str]


def _cache_key(token_path: Path) -> str:
    return str(token_path)


def _clear_auth_baseline(token_path: Path) -> None:
    """Forget a vanished or untrustworthy local baseline."""
    key = _cache_key(token_path)
    _AUTH_SYNC_CACHE.pop(key, None)
    _AUTH_AUTHORITY_CACHE.pop(key, None)


def _read_stable_file(token_path: Path) -> tuple[bytes, tuple[int, str]] | None:
    """Read a file only when its identity and metadata stay stable.

    A CLI in another process can replace ``auth.json`` while this process is
    reading it.  Retrying the stat/read/stat sequence avoids associating one
    document's authority with a different document's fingerprint.  A writer
    that races after the final stat is still detected on the next comparison:
    callers record the fingerprint from *this* coherent read, never a fresh
    unrelated read after a database mutation.
    """
    for _ in range(_STABLE_READ_ATTEMPTS):
        try:
            before = os.stat(token_path)
            content = token_path.read_bytes()
            after = os.stat(token_path)
        except OSError:
            return None

        before_identity = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
        after_identity = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
        if before_identity != after_identity:
            continue

        return content, (after.st_mtime_ns, hashlib.sha256(content).hexdigest())
    return None


def _compute_file_fingerprint(path: Path) -> tuple[int, str] | None:
    """Return ``(mtime_ns, sha256_full_hex)`` or ``None`` for an unreadable file."""
    stable_read = _read_stable_file(path)
    return stable_read[1] if stable_read is not None else None


def _read_valid_auth_snapshot(token_path: Path) -> _AuthFileSnapshot | None:
    """Read a coherent non-empty JSON-object auth document without logging it."""
    stable_read = _read_stable_file(token_path)
    if stable_read is None:
        return None
    raw, fingerprint = stable_read
    try:
        content = raw.decode("utf-8")
        parsed = json.loads(content)
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
        return None
    if not isinstance(parsed, dict) or not parsed:
        return None
    return _AuthFileSnapshot(value=content, fingerprint=fingerprint)


def _read_valid_auth_document(token_path: Path) -> str | None:
    """Read a non-empty JSON-object auth document without logging its content."""
    snapshot = _read_valid_auth_snapshot(token_path)
    return snapshot.value if snapshot is not None else None


def _read_valid_auth_value(value: str) -> str | None:
    """Validate an in-memory auth document without writing it to disk."""
    try:
        parsed = json.loads(value)
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
    return value if isinstance(parsed, dict) and parsed else None


def _has_mode_0600(token_path: Path) -> bool:
    try:
        return stat.S_IMODE(os.stat(token_path).st_mode) == 0o600
    except OSError:
        return False


def _ensure_mode_0600(token_path: Path) -> bool:
    """Correct a readable auth file's mode, returning whether it changed."""
    if _has_mode_0600(token_path):
        return False
    os.chmod(token_path, 0o600)
    return True


def _lock_for(token_path: Path) -> asyncio.Lock:
    key = _cache_key(token_path)
    lock = _AUTH_SYNC_LOCKS.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _AUTH_SYNC_LOCKS[key] = lock
    return lock


def _record_snapshot_baseline(token_path: Path, snapshot: _AuthFileSnapshot) -> None:
    """Associate exactly one coherent local document with its authority value."""
    key = _cache_key(token_path)
    _AUTH_SYNC_CACHE[key] = snapshot.fingerprint
    _AUTH_AUTHORITY_CACHE[key] = snapshot.value
    logger.debug("codex_auth_sync: baseline recorded for path=%s", token_path)


def record_auth_baseline(token_path: Path, *, authority_value: str | None = None) -> None:
    """Record a stable local auth baseline and its authority snapshot.

    Startup restoration calls this after writing the DB-backed token.  If the
    file is absent or changed to a different value before recording, forget the
    old baseline rather than later treating that unrelated value as trusted.
    """
    snapshot = _read_valid_auth_snapshot(token_path)
    if snapshot is None:
        _clear_auth_baseline(token_path)
        return
    if authority_value is not None and authority_value != snapshot.value:
        _clear_auth_baseline(token_path)
        return
    _record_snapshot_baseline(token_path, snapshot)


def codex_auth_file_matches_authority(token_path: Path, expected_value: str | None) -> bool:
    """Return whether the canonical file is still the exact expected document.

    A dashboard health probe runs against the canonical file rather than the
    runtime's isolated HOME.  Checking both before and after that external
    command lets its result be associated only with the credential bytes it
    actually consumed.  The document itself is never logged.
    """
    if expected_value is None:
        return False
    snapshot = _read_valid_auth_snapshot(token_path)
    return snapshot is not None and snapshot.value == expected_value


def _has_rotated(token_path: Path) -> bool:
    """Return whether *token_path* differs from its cached stable baseline."""
    fingerprint = _compute_file_fingerprint(token_path)
    if fingerprint is None:
        return False
    return _AUTH_SYNC_CACHE.get(_cache_key(token_path)) != fingerprint


async def _await_auth_store_operation(awaitable: Any, *, operation: str) -> Any:
    """Bound one best-effort credential-store operation.

    Callers catch the resulting timeout with their normal unavailable-store
    path, preserving local auth and withholding authority-dependent writes.
    Do not include exception detail here: drivers may retain query bind
    context containing credential material.
    """
    try:
        return await asyncio.wait_for(awaitable, timeout=_AUTH_STORE_OPERATION_TIMEOUT_S)
    except TimeoutError:
        logger.warning(
            "codex_auth_sync: credential authority %s timed out; preserving local auth",
            operation,
        )
        raise


async def _load_authoritative_codex_auth(store: CredentialStore) -> str | None:
    """Load Codex auth from its explicit shared-or-flat authority."""
    from butlers.cli_auth.persistence import load_persisted_token
    from butlers.cli_auth.registry import PROVIDERS

    provider = PROVIDERS.get("codex")
    if provider is None:
        raise RuntimeError("codex CLI auth provider is not registered")
    return await _await_auth_store_operation(
        load_persisted_token(provider, store),
        operation="load",
    )


async def _store_rotation_if_authority_unchanged(
    store: CredentialStore,
    *,
    expected_store_value: str | None,
    rotated_value: str,
) -> bool:
    """Persist a local Codex rotation only if the launch snapshot still wins."""
    return await _await_auth_store_operation(
        store.store_shared_if_unchanged(
            _CODEX_AUTH_KEY,
            rotated_value,
            expected_value=expected_store_value,
            category=_CODEX_AUTH_CATEGORY,
            description=_CODEX_AUTH_DESCRIPTION,
            is_sensitive=True,
        ),
        operation="conditional rotation write",
    )


async def _load_and_apply_authoritative_codex_auth(
    token_path: Path,
    store: CredentialStore,
    *,
    local_snapshot: _AuthFileSnapshot | None,
    butler_name: str,
) -> CodexAuthSyncResult:
    """Load the authority and apply it without flushing local rotation state.

    This helper is deliberately one-way.  It is used after a CAS conflict,
    where a newer dashboard or runtime authority must win; it never consults
    the mutable baseline cache to write local bytes back over that authority.
    """
    try:
        authoritative_value = await _load_authoritative_codex_auth(store)
    except Exception:
        logger.warning(
            "codex_auth_sync: credential authority load failed; preserving local auth "
            "(butler=%s path=%s)",
            butler_name,
            token_path,
        )
        return CodexAuthSyncResult(
            expected_store_value=None,
            file_changed=False,
            authority_known=False,
        )

    if authoritative_value is None:
        return CodexAuthSyncResult(expected_store_value=None, file_changed=False)
    if _read_valid_auth_value(authoritative_value) is None:
        logger.warning(
            "codex_auth_sync: ignored invalid authoritative Codex auth document "
            "(butler=%s path=%s)",
            butler_name,
            token_path,
        )
        return CodexAuthSyncResult(
            expected_store_value=None,
            file_changed=False,
            authority_known=False,
        )

    if local_snapshot is not None and local_snapshot.value == authoritative_value:
        try:
            mode_changed = _ensure_mode_0600(token_path)
        except OSError:
            logger.warning(
                "codex_auth_sync: failed to restrict canonical auth file mode (butler=%s path=%s)",
                butler_name,
                token_path,
            )
            return CodexAuthSyncResult(
                expected_store_value=None,
                file_changed=False,
                authority_known=False,
            )
        _record_snapshot_baseline(token_path, local_snapshot)
        return CodexAuthSyncResult(
            expected_store_value=authoritative_value,
            file_changed=mode_changed,
        )

    try:
        from butlers.cli_auth.persistence import _write_token_file_atomically

        _write_token_file_atomically(token_path, authoritative_value)
    except OSError:
        logger.warning(
            "codex_auth_sync: failed to reconcile canonical auth file; preserving local auth "
            "(butler=%s path=%s)",
            butler_name,
            token_path,
        )
        # The launch will use the old local file.  Do not pass the newer DB
        # value to post-invocation CAS, because that old subprocess must never
        # be able to replace the dashboard credential with a rotation.
        return CodexAuthSyncResult(
            expected_store_value=None,
            file_changed=False,
            authority_known=False,
        )

    written_snapshot = _read_valid_auth_snapshot(token_path)
    if written_snapshot is None or written_snapshot.value != authoritative_value:
        # Another process wrote a different value immediately after the atomic
        # replacement.  Do not associate that value with this authority; a
        # later operation will detect/reconcile it from a fresh snapshot.
        _clear_auth_baseline(token_path)
        return CodexAuthSyncResult(
            expected_store_value=None,
            file_changed=True,
            authority_known=False,
        )

    _record_snapshot_baseline(token_path, written_snapshot)
    logger.info(
        "codex_auth_sync: reconciled canonical auth before launch (butler=%s path=%s)",
        butler_name,
        token_path,
    )
    return CodexAuthSyncResult(expected_store_value=authoritative_value, file_changed=True)


async def reconcile_codex_auth(
    token_path: Path,
    store: CredentialStore,
    *,
    butler_name: str = "",
) -> CodexAuthSyncResult:
    """Synchronize canonical Codex auth before a new process can use it.

    A coherent changed local snapshot is conditionally flushed before loading
    the database.  A failed conditional write means a newer authority won and
    replaces the local file.  Missing files clear stale process cache state so
    a subsequent local login can be conditionally created again.
    """
    key = _cache_key(token_path)
    async with _lock_for(token_path):
        local_snapshot = _read_valid_auth_snapshot(token_path)
        if local_snapshot is None and not token_path.exists():
            _clear_auth_baseline(token_path)

        cached_authority = _AUTH_AUTHORITY_CACHE.get(key)
        cached_fingerprint = _AUTH_SYNC_CACHE.get(key)

        # A background-free, completed CLI rotation may be present from a
        # process that ended before its finalizer.  Flush exactly the coherent
        # local snapshot that was read here.  Do not recompute a fingerprint
        # after CAS: another process could already have written a successor.
        if (
            local_snapshot is not None
            and cached_authority is not None
            and cached_fingerprint != local_snapshot.fingerprint
        ):
            try:
                persisted = await _store_rotation_if_authority_unchanged(
                    store,
                    expected_store_value=cached_authority,
                    rotated_value=local_snapshot.value,
                )
            except Exception:
                logger.warning(
                    "codex_auth_sync: local rotation flush failed; preserving local auth "
                    "(butler=%s path=%s)",
                    butler_name,
                    token_path,
                )
                return CodexAuthSyncResult(
                    expected_store_value=None,
                    file_changed=False,
                    authority_known=False,
                )
            if persisted:
                _record_snapshot_baseline(token_path, local_snapshot)
                logger.info(
                    "codex_auth_sync: local rotation persisted before launch (butler=%s path=%s)",
                    butler_name,
                    token_path,
                )
                return CodexAuthSyncResult(
                    expected_store_value=local_snapshot.value,
                    file_changed=False,
                )
            return await _load_and_apply_authoritative_codex_auth(
                token_path,
                store,
                local_snapshot=local_snapshot,
                butler_name=butler_name,
            )

        try:
            authoritative_value = await _load_authoritative_codex_auth(store)
        except Exception:
            logger.warning(
                "codex_auth_sync: credential authority load failed; preserving local auth "
                "(butler=%s path=%s)",
                butler_name,
                token_path,
            )
            return CodexAuthSyncResult(
                expected_store_value=None,
                file_changed=False,
                authority_known=False,
            )

        if authoritative_value is None:
            # A deleted/revoked credential must never be resurrected from a
            # canonical file after this process forgets its old baseline.  New
            # device authentication is persisted explicitly by the dashboard;
            # runtime finalizers intentionally do not bootstrap absent rows.
            _clear_auth_baseline(token_path)
            return CodexAuthSyncResult(expected_store_value=None, file_changed=False)

        if _read_valid_auth_value(authoritative_value) is None:
            logger.warning(
                "codex_auth_sync: ignored invalid authoritative Codex auth document "
                "(butler=%s path=%s)",
                butler_name,
                token_path,
            )
            return CodexAuthSyncResult(
                expected_store_value=None,
                file_changed=False,
                authority_known=False,
            )

        # Reuse the already-loaded authoritative bytes to avoid a second
        # database read in the normal no-rotation path.
        if local_snapshot is not None and local_snapshot.value == authoritative_value:
            try:
                mode_changed = _ensure_mode_0600(token_path)
            except OSError:
                logger.warning(
                    "codex_auth_sync: failed to restrict canonical auth file mode "
                    "(butler=%s path=%s)",
                    butler_name,
                    token_path,
                )
                return CodexAuthSyncResult(
                    expected_store_value=None,
                    file_changed=False,
                    authority_known=False,
                )
            _record_snapshot_baseline(token_path, local_snapshot)
            return CodexAuthSyncResult(
                expected_store_value=authoritative_value,
                file_changed=mode_changed,
            )

        # Apply the already-loaded authority directly.  The helper reloads to
        # preserve its standalone race semantics, which is desirable here too:
        # a dashboard refresh between the first read and file replacement wins.
        return await _load_and_apply_authoritative_codex_auth(
            token_path,
            store,
            local_snapshot=local_snapshot,
            butler_name=butler_name,
        )


async def finalize_codex_auth_rotation(
    token_path: Path,
    store: CredentialStore,
    *,
    expected_store_value: str | None,
    authority_known: bool = True,
    butler_name: str = "",
) -> CodexAuthSyncResult:
    """Finalize one pre-warmed or spawned operation against its own snapshot.

    The caller supplies the credential bytes captured before its operation
    began.  This is intentionally different from generic reconciliation: a
    later dashboard refresh must not be replaced by reading a mutable process
    baseline cache and treating an older operation's local result as new.
    """
    if not authority_known or expected_store_value is None:
        # A pre-spawn store read failed, yielded an unsafe document, or
        # confirmed that the credential was deleted.  Do not reinterpret
        # ``None`` as an insert-only CAS expectation: it can resurrect a
        # revoked row or conflict after recovery and replace a valid local
        # rotation with an older DB value.  Leave local auth untouched until a
        # later preflight establishes a current authority snapshot.
        logger.debug(
            "codex_auth_sync: skipping operation finalization without writable authority "
            "(butler=%s path=%s)",
            butler_name,
            token_path,
        )
        return CodexAuthSyncResult(
            expected_store_value=None,
            file_changed=False,
            authority_known=authority_known,
        )

    async with _lock_for(token_path):
        local_snapshot = _read_valid_auth_snapshot(token_path)
        if local_snapshot is None and not token_path.exists():
            _clear_auth_baseline(token_path)

        if local_snapshot is None:
            return await _load_and_apply_authoritative_codex_auth(
                token_path,
                store,
                local_snapshot=None,
                butler_name=butler_name,
            )

        if expected_store_value is not None and local_snapshot.value == expected_store_value:
            # No local rotation: observe the authority rather than issuing a
            # no-op CAS, so a dashboard refresh that arrived during the
            # operation is immediately applied.
            return await _load_and_apply_authoritative_codex_auth(
                token_path,
                store,
                local_snapshot=local_snapshot,
                butler_name=butler_name,
            )

        try:
            persisted = await _store_rotation_if_authority_unchanged(
                store,
                expected_store_value=expected_store_value,
                rotated_value=local_snapshot.value,
            )
        except Exception:
            logger.warning(
                "codex_auth_sync: operation rotation flush failed; preserving local auth "
                "(butler=%s path=%s)",
                butler_name,
                token_path,
            )
            return CodexAuthSyncResult(
                expected_store_value=None,
                file_changed=False,
                authority_known=False,
            )

        if persisted:
            _record_snapshot_baseline(token_path, local_snapshot)
            logger.info(
                "codex_auth_sync: operation rotation persisted (butler=%s path=%s)",
                butler_name,
                token_path,
            )
            return CodexAuthSyncResult(
                expected_store_value=local_snapshot.value,
                file_changed=False,
            )

        # The operation's launch snapshot no longer owns the authority.  Load
        # and apply the winner, never retrying local->DB persistence here.
        return await _load_and_apply_authoritative_codex_auth(
            token_path,
            store,
            local_snapshot=local_snapshot,
            butler_name=butler_name,
        )


async def check_and_persist_rotation(
    token_path: Path,
    store: CredentialStore,
    *,
    expected_store_value: str | None,
    authority_known: bool = True,
    butler_name: str = "",
) -> None:
    """Compatibility wrapper for callers that finalize one runtime operation.

    New runtime paths await :func:`finalize_codex_auth_rotation` directly so
    retry attempts and health status share one deterministic final state.
    """
    try:
        await finalize_codex_auth_rotation(
            token_path,
            store,
            expected_store_value=expected_store_value,
            authority_known=authority_known,
            butler_name=butler_name,
        )
    except Exception:
        logger.warning(
            "codex_auth_sync: unexpected rotation persistence error (butler=%s path=%s)",
            butler_name,
            token_path,
        )
