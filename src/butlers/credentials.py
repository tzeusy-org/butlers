"""Credential validation for butler startup.

Checks that all required environment variables (core, butler-level, and
module-level) are present, and warns about missing optional vars.  Reports
all missing variables in a single aggregated error.
"""

from __future__ import annotations

import logging
import os
import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from butlers.credential_store import CredentialStore

logger = logging.getLogger(__name__)


class CredentialError(Exception):
    """Raised when required credentials are missing."""


def validate_credentials(
    env_required: list[str],
    env_optional: list[str],
    module_credentials: dict[str, list[str]] | None = None,
) -> None:
    """Validate that all required environment variables are set.

    Parameters
    ----------
    env_required:
        Required env vars from ``[butler.env].required``.
    env_optional:
        Optional env vars from ``[butler.env].optional`` (warn if missing).
    module_credentials:
        Dict mapping module name to list of required env var names.

    Raises
    ------
    CredentialError
        If any required env vars are missing, with an aggregated report
        identifying each missing variable and its source component.
    """
    missing: list[tuple[str, str]] = []  # (var_name, source_component)

    # NOTE: ANTHROPIC_API_KEY is no longer checked here — it may live in the
    # butler_secrets DB table (added by butlers-987).  Core credentials are
    # validated asynchronously via validate_core_credentials_async() after the
    # database pool is available (daemon step 8c).

    # Check [butler.env].required
    for var in env_required:
        if not os.environ.get(var):
            missing.append((var, "butler.env"))

    # Check module credentials
    if module_credentials:
        for module_name, cred_vars in module_credentials.items():
            for var in cred_vars:
                if not os.environ.get(var):
                    missing.append((var, f"module:{module_name}"))

    # Warn about optional vars
    for var in env_optional:
        if not os.environ.get(var):
            logger.warning("Optional env var %s is not set", var)

    # Raise aggregated error if any missing
    if missing:
        lines = [f"  - {var} (required by {source})" for var, source in missing]
        msg = "Missing required environment variables:\n" + "\n".join(lines)
        raise CredentialError(msg)


async def validate_module_credentials_async(
    module_credentials: dict[str, list[str]],
    credential_store: CredentialStore,
) -> dict[str, list[str]]:
    """Check module credentials via DB-first resolution and return per-module missing vars.

    Uses ``CredentialStore.resolve()`` for each declared credential key so that
    secrets stored in the database are visible at validation time.  Environment
    variables remain as a fallback (the ``CredentialStore`` default behaviour).

    Unlike ``validate_credentials``, this function does **not** raise.
    It returns a dict mapping module name to the list of missing credential
    keys.  An empty dict means all module credentials are resolvable.

    Parameters
    ----------
    module_credentials:
        Dict mapping module (or scoped-module) name to list of credential
        key names (typically env var names such as ``"TELEGRAM_BOT_TOKEN"``).
    credential_store:
        An initialised :class:`~butlers.credential_store.CredentialStore`
        instance backed by the butler's database pool.

    Returns
    -------
    dict[str, list[str]]
        Mapping of module name to missing credential key names.  Only modules
        with at least one unresolvable credential appear in the result.
    """
    failures: dict[str, list[str]] = {}
    for module_name, cred_vars in module_credentials.items():
        missing = []
        for var in cred_vars:
            value = await credential_store.resolve(var)
            if not value:
                missing.append(var)
        if missing:
            failures[module_name] = missing
    return failures


_RUNTIME_CORE_CREDENTIALS: dict[str, list[str]] = {
    "claude-code": ["ANTHROPIC_API_KEY"],
    "gemini": ["GOOGLE_API_KEY"],
    # "codex" has no core credential requirement (key is in env / adapter config)
}


async def validate_core_credentials_async(
    credential_store: CredentialStore,
    runtime_type: str = "claude-code",
) -> None:
    """Validate that core credentials for the configured runtime are resolvable.

    Uses ``CredentialStore.resolve()`` (DB-first, env fallback) so that
    secrets stored in the ``butler_secrets`` table are visible.

    Parameters
    ----------
    credential_store:
        An initialised :class:`~butlers.credential_store.CredentialStore`.
    runtime_type:
        The runtime type string (e.g. ``"claude-code"``, ``"codex"``,
        ``"gemini"``).  Only credentials required by the given runtime
        are checked.

    Raises
    ------
    CredentialError
        If any required core credential cannot be resolved from DB or env.
    """
    core_keys = _RUNTIME_CORE_CREDENTIALS.get(runtime_type, [])
    if not core_keys:
        return
    missing = []
    for key in core_keys:
        value = await credential_store.resolve(key)
        if not value:
            missing.append(key)
    if missing:
        lines = [f"  - {var} (required by core)" for var in missing]
        msg = (
            "Missing required core credentials (checked DB + env):\n"
            + "\n".join(lines)
            + "\n\nAdd via dashboard Secrets page or set as environment variable."
        )
        raise CredentialError(msg)


def detect_secrets(config_values: dict[str, Any]) -> list[str]:
    """Scan config string values for suspected inline secrets.

    Returns list of warning messages for suspected secrets.
    Advisory only — does not block startup.

    Parameters
    ----------
    config_values:
        Flat dict of config key-value pairs. Non-string values are skipped.

    Returns
    -------
    list[str]
        List of warning messages for suspected secrets.
    """
    warnings: list[str] = []

    # Patterns for common secret prefixes
    secret_prefixes = [
        "sk-",  # OpenAI
        "ghp_",  # GitHub Personal Access Token
        "xoxb-",  # Slack Bot
        "xoxp-",  # Slack User
        "xoxs-",  # Slack Workspace
        "xoxa-",  # Slack App
        "gho_",  # GitHub OAuth
        "github_pat_",  # GitHub Personal Access Token
    ]

    # Heuristic key names that suggest secret values
    secret_key_hints = {"password", "secret", "api_key", "token", "key"}

    # Base64-like pattern (40+ chars of alphanumeric + / + = )
    base64_pattern = re.compile(r"^[A-Za-z0-9/+=]{40,}$")

    for key, value in config_values.items():
        # Skip non-string values
        if not isinstance(value, str):
            continue

        # Skip short values to avoid false positives
        if len(value) < 8:
            continue

        # Skip common non-secret patterns (URLs, file paths, hostnames)
        if value.startswith(("http://", "https://", "/", ".", "file://")) or ":" in value:
            # Exception: allow `:` in base64 check, but skip URLs/file paths
            if "://" in value or value.startswith("/"):
                continue

        # Check for secret prefixes
        for prefix in secret_prefixes:
            if value.startswith(prefix):
                warnings.append(
                    f"Config key '{key}' may contain an inline secret "
                    f"(matches pattern: {prefix} prefix). "
                    f"Consider using an environment variable instead."
                )
                break  # Only report once per key
        else:
            # Only check other patterns if prefix check didn't match
            # Check for long base64-like strings
            if base64_pattern.match(value):
                warnings.append(
                    f"Config key '{key}' may contain an inline secret "
                    f"(matches pattern: long base64-like string). "
                    f"Consider using an environment variable instead."
                )
            # Check for secret key name heuristics with long values
            elif any(hint in key.lower() for hint in secret_key_hints) and len(value) >= 16:
                warnings.append(
                    f"Config key '{key}' may contain an inline secret "
                    f"(key name suggests secret and value is long). "
                    f"Consider using an environment variable instead."
                )

    return warnings
