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
from typing import TYPE_CHECKING

from butlers.cli_auth.registry import PROVIDERS, CLIAuthProviderDef
from butlers.credential_store import CredentialStore

if TYPE_CHECKING:
    import asyncpg

logger = logging.getLogger(__name__)

_CATEGORY = "cli-auth"


def _db_key(provider: CLIAuthProviderDef) -> str:
    return f"cli-auth/{provider.name}"


async def persist_token(provider: CLIAuthProviderDef, store: CredentialStore) -> bool:
    """Read the CLI's token file from disk and store it in the DB.

    Called after a successful auth flow. Returns True if persisted.
    """
    if not provider.token_path.exists():
        logger.warning(
            "CLI auth persist: token file %s does not exist for %s",
            provider.token_path,
            provider.name,
        )
        return False

    try:
        content = provider.token_path.read_text(encoding="utf-8")
    except OSError:
        logger.exception("CLI auth persist: failed to read %s", provider.token_path)
        return False

    if not content.strip():
        logger.warning("CLI auth persist: token file %s is empty", provider.token_path)
        return False

    key = _db_key(provider)
    await store.store(
        key,
        content,
        category=_CATEGORY,
        description=f"CLI auth token for {provider.display_name}",
        is_sensitive=True,
    )
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
            content = await store.load(key)
        except Exception:
            logger.debug("CLI auth restore: failed to load %s from DB", key, exc_info=True)
            results[provider.name] = False
            continue

        if content is None:
            logger.debug("CLI auth restore: no stored token for %s", provider.name)
            results[provider.name] = False
            continue

        try:
            provider.token_path.parent.mkdir(parents=True, exist_ok=True)

            # Multiple providers may share the same token_path (e.g.
            # opencode-openai and opencode-go both use auth.json). Merge
            # the restored JSON into any existing file content so that one
            # provider's restore doesn't clobber another's credentials.
            final_content = content
            if provider.token_path.exists():
                try:
                    existing = json.loads(provider.token_path.read_text(encoding="utf-8"))
                    restored = json.loads(content)
                    if isinstance(existing, dict) and isinstance(restored, dict):
                        existing.update(restored)
                        final_content = json.dumps(existing, indent=2)
                except (json.JSONDecodeError, ValueError):
                    pass  # Not JSON — fall back to full overwrite

            provider.token_path.write_text(final_content, encoding="utf-8")
            provider.token_path.chmod(0o600)
            logger.info(
                "CLI auth restore: wrote token for %s to %s",
                provider.name,
                provider.token_path,
            )
            results[provider.name] = True
        except OSError:
            logger.exception(
                "CLI auth restore: failed to write %s for %s",
                provider.token_path,
                provider.name,
            )
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
        logger.debug("codex_auth_sync: baseline recording skipped", exc_info=True)


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
            exc_info=True,
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
