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

from butlers.cli_auth.registry import PROVIDERS, CLIAuthProviderDef
from butlers.credential_store import CredentialStore

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
                    existing = json.loads(
                        provider.token_path.read_text(encoding="utf-8")
                    )
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
