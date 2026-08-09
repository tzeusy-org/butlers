"""CLI auth token persistence — DB-backed storage for CLI credential files.

After a successful device-code auth flow, the CLI writes tokens to a file
on disk (e.g. ``~/.codex/auth.json``). This module copies those tokens into
the shared credential store so they survive container restarts and pod
rescheduling in Kubernetes.

On startup, tokens are restored from the DB to the filesystem paths the
CLIs expect.

Key convention: ``cli-auth/{provider_name}`` (e.g. ``cli-auth/codex``).
Category: ``cli-auth``.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from butlers.cli_auth.registry import PROVIDERS, CLIAuthProviderDef
from butlers.credential_store import CredentialStore

if TYPE_CHECKING:
    import asyncpg

logger = logging.getLogger(__name__)

_CATEGORY = "cli-auth"


def _db_key(provider: CLIAuthProviderDef) -> str:
    return f"cli-auth/{provider.name}"


def _is_valid_codex_auth_document(content: str) -> bool:
    """Return whether *content* is a non-empty Codex auth JSON object."""
    try:
        parsed = json.loads(content)
    except (json.JSONDecodeError, TypeError, ValueError):
        return False
    return isinstance(parsed, dict) and bool(parsed)


def _uses_shared_authority(provider: CLIAuthProviderDef, store: CredentialStore) -> bool:
    """Whether *provider* must bypass a schema-local credential shadow.

    Codex device authentication is a shared runtime identity.  Daemons in
    schema-isolated deployments receive a local store plus the public pool as
    a fallback; normal ``load()`` resolution prefers the local row, which can
    otherwise hide a dashboard refresh stored in public.  Flat deployments
    have no fallback, so their local store remains the authority.
    """
    return provider.name == "codex" and store.shared_pool is not None


async def load_persisted_token(provider: CLIAuthProviderDef, store: CredentialStore) -> str | None:
    """Load a CLI credential from its provider-specific authority.

    Codex explicitly reads the shared pool whenever one exists.  Other CLI
    providers retain the existing store lookup semantics.
    """
    key = _db_key(provider)
    if _uses_shared_authority(provider, store):
        return await store.load_shared(key)
    return await store.load(key)


async def _store_persisted_token(
    provider: CLIAuthProviderDef,
    store: CredentialStore,
    content: str,
) -> None:
    """Persist *content* through the provider-specific credential authority."""
    key = _db_key(provider)
    kwargs = {
        "category": _CATEGORY,
        "description": f"CLI auth token for {provider.display_name}",
        "is_sensitive": True,
    }
    if _uses_shared_authority(provider, store):
        await store.store_shared(key, content, **kwargs)
    else:
        await store.store(key, content, **kwargs)


def _write_token_file_atomically(token_path: Path, content: str) -> None:
    """Replace a CLI auth file atomically with restrictive permissions.

    Readers either observe the old complete document or the newly fsynced
    complete document; they never observe a truncated write.  The helper does
    not log because its caller has the provider/butler context and because
    arbitrary filesystem errors can contain sensitive path or content detail.
    """
    token_path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{token_path.name}.", dir=token_path.parent)
    temp_path = Path(temp_name)
    try:
        os.fchmod(fd, 0o600)
        file_handle = os.fdopen(fd, "w", encoding="utf-8")
        fd = -1  # ownership transferred to file_handle
        with file_handle:
            file_handle.write(content)
            file_handle.flush()
            os.fsync(file_handle.fileno())
        os.replace(temp_path, token_path)
        # Keep the invariant even when a platform's replace behavior does not
        # retain the temporary file mode exactly as expected.
        os.chmod(token_path, 0o600)
    finally:
        if fd != -1:
            try:
                os.close(fd)
            except OSError:
                pass
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            # Preserve the original failure if there was one.  A later
            # reconciliation will safely replace a stale temporary artifact.
            pass


async def persist_token(provider: CLIAuthProviderDef, store: CredentialStore) -> bool:
    """Read the CLI's token file from disk and store it in the DB.

    Called after a successful auth flow. Returns True if persisted.
    """
    token_path = provider.token_path
    if token_path is None:
        logger.warning("CLI auth persist: provider=%s has no token path", provider.name)
        return False

    if not token_path.exists():
        logger.warning(
            "CLI auth persist: token file %s does not exist for %s",
            token_path,
            provider.name,
        )
        return False

    try:
        content = token_path.read_text(encoding="utf-8")
    except OSError:
        logger.warning(
            "CLI auth persist: failed to read token file for provider=%s path=%s",
            provider.name,
            token_path,
        )
        return False

    if not content.strip():
        logger.warning("CLI auth persist: token file %s is empty", provider.token_path)
        return False
    if provider.name == "codex" and not _is_valid_codex_auth_document(content):
        logger.warning("CLI auth persist: refusing invalid Codex auth document")
        return False

    key = _db_key(provider)
    await _store_persisted_token(provider, store, content)
    logger.info("CLI auth persist: stored token for %s (key=%s)", provider.name, key)
    return True


async def restore_tokens(store: CredentialStore) -> dict[str, bool]:
    """Restore all CLI auth tokens from the DB to their filesystem paths.

    Called on application startup. Returns a dict of provider name → success.
    """
    results: dict[str, bool] = {}

    for provider in PROVIDERS.values():
        key = _db_key(provider)
        try:
            content = await load_persisted_token(provider, store)
        except Exception:
            logger.warning("CLI auth restore: failed to load key=%s from credential store", key)
            results[provider.name] = False
            continue

        if content is None:
            logger.debug("CLI auth restore: no stored token for %s", provider.name)
            results[provider.name] = False
            continue
        if provider.name == "codex" and not _is_valid_codex_auth_document(content):
            logger.warning("CLI auth restore: refusing invalid stored Codex auth document")
            results[provider.name] = False
            continue

        try:
            token_path = provider.token_path
            if token_path is None:
                logger.warning("CLI auth restore: provider=%s has no token path", provider.name)
                results[provider.name] = False
                continue

            token_path.parent.mkdir(parents=True, exist_ok=True)

            # Multiple providers may share the same token_path (e.g.
            # opencode-openai and opencode-go both use auth.json). Merge
            # the restored JSON into any existing file content so that one
            # provider's restore doesn't clobber another's credentials.
            final_content = content
            if token_path.exists():
                try:
                    existing = json.loads(token_path.read_text(encoding="utf-8"))
                    restored = json.loads(content)
                    if isinstance(existing, dict) and isinstance(restored, dict):
                        existing.update(restored)
                        final_content = json.dumps(existing, indent=2)
                except (json.JSONDecodeError, TypeError, ValueError):
                    pass  # Not JSON — fall back to full overwrite

            _write_token_file_atomically(token_path, final_content)
            logger.info(
                "CLI auth restore: wrote token for %s to %s",
                provider.name,
                token_path,
            )
            results[provider.name] = True
        except OSError:
            logger.warning("CLI auth restore: failed to write token for provider=%s", provider.name)
            results[provider.name] = False

    return results


def _record_codex_baseline() -> None:
    """Record the codex ``auth.json`` baseline after a restore so the first
    post-startup invocation does not falsely detect a rotation. Best-effort."""
    try:
        from butlers.core.runtimes._codex_auth_sync import record_auth_baseline

        codex_provider = PROVIDERS.get("codex")
        if codex_provider is not None and codex_provider.token_path is not None:
            record_auth_baseline(codex_provider.token_path)
    except Exception:
        logger.debug("codex_auth_sync: baseline recording skipped")


async def restore_connector_cli_auth(pool: asyncpg.Pool | None, *, context: str) -> dict[str, bool]:
    """Restore CLI-auth tokens from the shared credential DB at connector startup.

    Standalone connector containers (whatsapp/telegram user clients, live-listener)
    spawn a ``DiscretionDispatcher`` -> ``CodexAdapter`` but, unlike the daemon
    (:mod:`butlers.lifecycle`) and the dashboard API (:mod:`butlers.api.app`),
    never restore the shared ``cli-auth/codex`` token to disk. Without it
    ``~/.codex/auth.json`` is absent, every discretion-tier codex call 401s, and
    weight-below-fail-open senders silently fail closed (IGNORE). This mirrors the
    daemon-startup restore (restore_tokens + codex baseline) onto a connector's own
    DB pool, honoring disposition A (shared daemon codex identity): the codex row
    lives under the single fixed key ``cli-auth/codex`` in ``butler_secrets`` and is
    read through whatever pool the connector already has (its cursor pool reaches the
    shared credential DB in the default single-DB deployment).

    Non-fatal but deliberately LOUD: on any failure, or when no codex token is
    restored, this logs at WARNING so a connector never *silently* runs without codex
    auth (the exact bu-wzbu9 fail-closed-IGNORE bug) — surfaced alongside each
    connector's existing discretion-auth health hook. Returns the per-provider restore
    results (``{}`` on outright failure).
    """
    if pool is None:
        logger.warning(
            "%s: no DB pool available to restore CLI auth from the credential DB; "
            "discretion-tier codex calls will 401 and fail closed until a credential DB "
            "is reachable",
            context,
        )
        return {}

    store = CredentialStore(pool)
    try:
        results = await restore_tokens(store)
    except Exception:
        logger.warning(
            "%s: CLI-auth restore from the credential DB failed; discretion-tier codex "
            "calls will 401 and fail closed until auth is restored",
            context,
        )
        return {}

    restored = sum(1 for v in results.values() if v)
    if restored:
        logger.info("%s: restored %d CLI auth token(s) from the credential DB", context, restored)

    if results.get("codex"):
        _record_codex_baseline()
    else:
        logger.warning(
            "%s: no codex CLI-auth token found in the credential DB — ~/.codex/auth.json is "
            "absent, so discretion-tier codex calls will 401 and low-weight senders fail closed "
            "(silent IGNORE). Ensure the daemon has authenticated codex (cli-auth/codex).",
            context,
        )

    return results
