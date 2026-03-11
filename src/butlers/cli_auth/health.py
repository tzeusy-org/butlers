"""CLI auth health probes.

Runs each provider's status command to check whether stored credentials
are still valid (not just present on disk).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from enum import StrEnum

from butlers.cli_auth.registry import PROVIDERS, CLIAuthProviderDef
from butlers.cli_auth.session import _strip_ansi
from butlers.credential_store import CredentialStore

logger = logging.getLogger(__name__)

_PROBE_TIMEOUT = 15  # seconds


class AuthHealthState(StrEnum):
    """Health state of a CLI auth provider."""

    authenticated = "authenticated"
    """Credentials are valid and usable."""

    not_authenticated = "not_authenticated"
    """No credentials found or credentials are invalid/expired."""

    unavailable = "unavailable"
    """CLI binary not installed or status command not configured."""

    probe_failed = "probe_failed"
    """Status command failed to execute or timed out."""


@dataclass
class AuthHealthResult:
    provider: str
    state: AuthHealthState
    detail: str | None = None


async def probe_provider(
    provider: CLIAuthProviderDef,
    credential_store: CredentialStore | None = None,
) -> AuthHealthResult:
    """Run a provider's status command and determine auth health."""
    if not provider.is_available():
        # api_key providers may still have a stored key even if the binary
        # is not installed — report based on key presence.
        if provider.auth_mode == "api_key" and credential_store is not None:
            key = f"cli-auth/{provider.name}"
            try:
                value = await credential_store.load(key)
            except Exception:
                value = None
            if value:
                return AuthHealthResult(
                    provider=provider.name,
                    state=AuthHealthState.authenticated,
                    detail="API key stored (binary not on PATH).",
                )
        return AuthHealthResult(
            provider=provider.name,
            state=AuthHealthState.unavailable,
            detail=f"Binary '{provider.binary()}' not found on PATH.",
        )

    # api_key providers: check if a key is stored in the credential store
    if provider.auth_mode == "api_key":
        if credential_store is None:
            return AuthHealthResult(
                provider=provider.name,
                state=AuthHealthState.not_authenticated,
                detail="No credential store available.",
            )
        key = f"cli-auth/{provider.name}"
        try:
            value = await credential_store.load(key)
        except Exception:
            logger.exception("CLI auth health: failed to load key for %s", provider.name)
            return AuthHealthResult(
                provider=provider.name,
                state=AuthHealthState.probe_failed,
                detail="Failed to check credential store.",
            )
        if value:
            return AuthHealthResult(
                provider=provider.name,
                state=AuthHealthState.authenticated,
                detail="API key configured.",
            )
        return AuthHealthResult(
            provider=provider.name,
            state=AuthHealthState.not_authenticated,
            detail="No API key stored.",
        )

    if provider.status_command is None or provider.status_ok_pattern is None:
        # No status command — fall back to file existence check
        if provider.is_authenticated():
            return AuthHealthResult(
                provider=provider.name,
                state=AuthHealthState.authenticated,
                detail="Token file exists (no status probe available).",
            )
        return AuthHealthResult(
            provider=provider.name,
            state=AuthHealthState.not_authenticated,
            detail="Token file not found.",
        )

    try:
        proc = await asyncio.create_subprocess_exec(
            *provider.status_command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            stdin=asyncio.subprocess.DEVNULL,
        )
        raw_output, _ = await asyncio.wait_for(
            proc.communicate(), timeout=_PROBE_TIMEOUT
        )
        output = _strip_ansi(raw_output.decode(errors="replace"))

        if proc.returncode == 0 and provider.status_ok_pattern.search(output):
            return AuthHealthResult(
                provider=provider.name,
                state=AuthHealthState.authenticated,
                detail=output.strip()[:200],
            )

        return AuthHealthResult(
            provider=provider.name,
            state=AuthHealthState.not_authenticated,
            detail=output.strip()[:200] or f"Exit code {proc.returncode}.",
        )

    except TimeoutError:
        logger.warning("CLI auth health probe timed out for %s", provider.name)
        return AuthHealthResult(
            provider=provider.name,
            state=AuthHealthState.probe_failed,
            detail=f"Status command timed out after {_PROBE_TIMEOUT}s.",
        )
    except Exception:
        logger.exception("CLI auth health probe failed for %s", provider.name)
        return AuthHealthResult(
            provider=provider.name,
            state=AuthHealthState.probe_failed,
            detail="Status command failed to execute.",
        )


async def probe_all(
    credential_store: CredentialStore | None = None,
) -> dict[str, AuthHealthResult]:
    """Probe all registered providers concurrently."""
    tasks = {
        name: asyncio.create_task(probe_provider(provider, credential_store))
        for name, provider in PROVIDERS.items()
        if provider.is_available() or provider.auth_mode == "api_key"
    }
    results: dict[str, AuthHealthResult] = {}
    for name, task in tasks.items():
        results[name] = await task
    return results
