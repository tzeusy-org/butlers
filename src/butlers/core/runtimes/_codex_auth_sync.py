"""Codex auth.json rotation detection and credential-store sync.

After every Codex CLI invocation the adapter calls
:func:`check_and_persist_rotation`.  The function compares the current
``(mtime_ns, sha256_head)`` of ``~/.codex/auth.json`` against a
per-process cache.  On a mismatch it calls
:func:`butlers.cli_auth.persistence.persist_token` so the DB credential
store stays in sync with whatever tokens the Codex CLI wrote after its
last OAuth refresh.

The check is cheap: only the first 4 KiB of the file is hashed to
produce a ``(mtime_ns, sha256_head)`` fingerprint that is compared
against the per-process cache.

**Usage**

1. On daemon startup, after :func:`butlers.cli_auth.persistence.restore_tokens`
   writes the file, call :func:`record_auth_baseline` so the first
   post-startup invocation does not falsely trigger a persist.

2. After a Codex CLI subprocess returns (success *or* failure — the CLI
   may rotate the token before a failure manifests), fire and forget::

       asyncio.create_task(
           check_and_persist_rotation(token_path, store, butler_name="qa")
       )

   The task swallows all exceptions and logs with context.

**No circular imports**: this module imports ``butlers.cli_auth.persistence``
only inside the async function so the codex adapter can import it at
module level without a circular dependency.
"""

from __future__ import annotations

import hashlib
import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from butlers.credential_store import CredentialStore

logger = logging.getLogger(__name__)

# Per-process cache: maps canonical token path → (mtime_ns, sha256_head_hex)
# mtime_ns:       st_mtime_ns from os.stat() — nanosecond resolution
# sha256_head_hex: hex digest of first 4 KiB of the file
_AUTH_SYNC_CACHE: dict[str, tuple[int, str]] = {}

_HEAD_READ_BYTES = 4096  # bytes to hash for change detection


def _compute_file_fingerprint(path: Path) -> tuple[int, str] | None:
    """Return ``(mtime_ns, sha256_head_hex)`` or ``None`` if the file is missing/unreadable.

    The sha256 covers only the first ``_HEAD_READ_BYTES`` bytes which is
    sufficient for auth.json (typically < 1 KiB) and avoids reading large
    files on systems with low-resolution mtime clocks.

    The raw file content is never logged to satisfy the security-and-secrets bar.
    """
    try:
        stat = os.stat(path)
    except OSError:
        return None
    mtime_ns = stat.st_mtime_ns
    try:
        with path.open("rb") as fh:
            head = fh.read(_HEAD_READ_BYTES)
    except OSError:
        return None
    digest = hashlib.sha256(head).hexdigest()
    return (mtime_ns, digest)


def record_auth_baseline(token_path: Path) -> None:
    """Record the current fingerprint of *token_path* as the stable baseline.

    Call this after :func:`butlers.cli_auth.persistence.restore_tokens` writes
    the credential file on daemon startup so the first post-startup invocation
    does not falsely detect a rotation.

    If the file does not exist yet, the call is a no-op; the first invocation
    that writes the file will then be detected as a rotation and persisted.
    """
    fp = _compute_file_fingerprint(token_path)
    if fp is not None:
        _AUTH_SYNC_CACHE[str(token_path)] = fp
        logger.debug(
            "codex_auth_sync: baseline recorded for %s (mtime_ns=%d)",
            token_path,
            fp[0],
        )


def _has_rotated(token_path: Path) -> bool:
    """Return ``True`` when the file's fingerprint differs from the cached baseline.

    A ``True`` result means the file was written by the Codex CLI (or any other
    process) since the last time the baseline was recorded.  The first call per
    process for a given path (no cached baseline) always returns ``True`` so
    that a file written by the CLI before :func:`record_auth_baseline` was
    called is still persisted.
    """
    key = str(token_path)
    current = _compute_file_fingerprint(token_path)
    if current is None:
        # File absent — no point persisting a non-existent token.
        return False
    cached = _AUTH_SYNC_CACHE.get(key)
    return cached != current


async def check_and_persist_rotation(
    token_path: Path,
    store: CredentialStore,
    *,
    butler_name: str = "",
) -> None:
    """Detect Codex auth.json rotation and persist the new tokens to *store*.

    This is designed to be scheduled as a fire-and-forget
    :func:`asyncio.create_task`.  All exceptions are caught, logged with
    context, and swallowed so the caller's control flow is never disrupted.

    Parameters
    ----------
    token_path:
        Canonical path to the Codex auth.json (usually
        ``Path.home() / ".codex" / "auth.json"``).
    store:
        Initialised :class:`~butlers.credential_store.CredentialStore` to
        persist the rotated token into.
    butler_name:
        Butler identity for log context (e.g. ``"qa"``).
    """
    try:
        if not _has_rotated(token_path):
            return

        # Lazy import to avoid circular dependency at module scope.
        from butlers.cli_auth.persistence import persist_token
        from butlers.cli_auth.registry import PROVIDERS

        provider = PROVIDERS.get("codex")
        if provider is None:
            logger.warning(
                "codex_auth_sync: 'codex' provider not registered — skipping persist (butler=%s)",
                butler_name,
            )
            return

        # Temporarily override token_path in case the provider definition uses
        # the real home directory but tests supply a tmp_path.  We use the
        # concrete path passed in rather than the registry default.
        import dataclasses

        provider_with_path = dataclasses.replace(provider, token_path=token_path)

        persisted = await persist_token(provider_with_path, store)
        if persisted:
            # Update the cache baseline so repeated unchanged invocations are cheap.
            fp = _compute_file_fingerprint(token_path)
            if fp is not None:
                _AUTH_SYNC_CACHE[str(token_path)] = fp
            logger.info(
                "codex_auth_sync: rotated token persisted for butler=%s (path=%s)",
                butler_name,
                token_path,
            )
        else:
            logger.warning(
                "codex_auth_sync: persist_token returned False for butler=%s (path=%s) — "
                "token may be empty or unreadable",
                butler_name,
                token_path,
            )
    except Exception:
        logger.warning(
            "codex_auth_sync: unexpected error during rotation check (butler=%s, path=%s)",
            butler_name,
            token_path,
            exc_info=True,
        )
