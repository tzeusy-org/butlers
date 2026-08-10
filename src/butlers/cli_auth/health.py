"""CLI auth health probes.

Runs each provider's status command to check whether stored credentials
are still valid (not just present on disk).
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import time
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

import httpx

from butlers.cli_auth.registry import PROVIDERS, CLIAuthProviderDef
from butlers.cli_auth.session import _strip_ansi
from butlers.credential_store import CredentialStore

logger = logging.getLogger(__name__)

_PROBE_TIMEOUT = 15  # seconds

# `codex login status` only inspects the file on disk, so it reports
# "Logged in" even after OpenAI has revoked the refresh token server-side
# (e.g. refresh_token_reused). Hitting the models endpoint with the stored
# access token is the cheapest way to catch that: a 401 here means the next
# real Codex invocation will also 401 — which is the state the dashboard
# needs to surface.
_CODEX_BACKEND_PROBE_URL = "https://chatgpt.com/backend-api/codex/models?client_version=0.118.0"
_CODEX_BACKEND_PROBE_TIMEOUT = 5.0  # seconds


def _check_jwt_expiry(token_path: Path) -> tuple[bool, str | None]:
    """Check if the access token JWT in an auth file has expired.

    Returns (is_expired, detail_message).  Returns (False, None) if the
    token cannot be parsed (optimistic — let the status command decide).
    """
    try:
        data = json.loads(token_path.read_text(encoding="utf-8"))
        access_token = (data.get("tokens") or {}).get("access_token", "")
        if not access_token:
            return False, None

        # Decode JWT payload (second segment) without signature verification
        parts = access_token.split(".")
        if len(parts) < 2:
            return False, None

        # JWT base64url → standard base64 with padding
        payload_b64 = parts[1] + "=" * (-len(parts[1]) % 4)
        payload = json.loads(base64.urlsafe_b64decode(payload_b64))
        exp = payload.get("exp")
        if not isinstance(exp, int | float):
            return False, None

        if time.time() > exp:
            return True, "Access token expired — re-login required."
        return False, None
    except Exception:
        # Can't parse → don't block the probe
        return False, None


async def _probe_codex_backend(token_path: Path) -> tuple[bool, str | None]:
    """Validate the stored Codex access token against OpenAI's backend.

    Returns ``(revoked, detail)``. ``revoked=True`` means OpenAI rejected the
    token with 401 — the local file is stale and re-login is required.
    ``revoked=False`` covers both success and transient failures (network
    blips, non-401 HTTP errors) — we don't want a flaky probe to red-flag a
    provider that's actually fine.
    """
    try:
        data = json.loads(token_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False, None

    access_token = (data.get("tokens") or {}).get("access_token")
    if not access_token:
        return False, None

    try:
        async with httpx.AsyncClient(timeout=_CODEX_BACKEND_PROBE_TIMEOUT) as client:
            resp = await client.get(
                _CODEX_BACKEND_PROBE_URL,
                headers={"Authorization": f"Bearer {access_token}"},
            )
    except httpx.HTTPError:
        # Transport-library exceptions can include request metadata. The
        # probe is intentionally optimistic on transient failure, so retain
        # only a value-free diagnostic.
        logger.debug("Codex backend probe encountered a transient network failure")
        return False, None

    if resp.status_code == 401:
        return True, "OpenAI rejected the stored token (401) — re-login required."
    return False, None


async def _prepare_codex_probe_authority(
    provider: CLIAuthProviderDef,
    codex_authority: CredentialStore | None,
) -> str | None:
    """Reconcile and verify the global Codex authority before a status child.

    ``codex login status`` only reads the canonical file.  It must therefore
    never run merely because a schema-local process happens to have a usable
    file; an explicit global store snapshot and an exact file match are both
    required immediately before spawning the command.
    """
    token_path = provider.token_path
    if codex_authority is None or token_path is None:
        return None
    try:
        from butlers.core.runtimes._codex_auth_sync import (
            codex_auth_file_matches_authority,
            reconcile_codex_auth,
        )

        baseline = await reconcile_codex_auth(
            token_path,
            codex_authority,
            butler_name="cli-auth-probe",
        )
        if (
            not baseline.authority_known
            or baseline.expected_store_value is None
            or not codex_auth_file_matches_authority(
                token_path,
                baseline.expected_store_value,
            )
        ):
            return None
        return baseline.expected_store_value
    except Exception:
        # Do not log driver exception detail: it can carry credential binds.
        logger.warning("Codex probe: system-global authority preflight failed")
        return None


async def _finalize_codex_probe_if_rotated(
    provider: CLIAuthProviderDef,
    codex_authority: CredentialStore | None,
    expected_store_value: str | None,
) -> bool:
    """Fence a status-probe rotation and report whether its old result is safe."""
    token_path = provider.token_path
    if token_path is None or codex_authority is None or expected_store_value is None:
        return False
    try:
        from butlers.core.runtimes._codex_auth_sync import (
            codex_auth_file_matches_authority,
            finalize_codex_auth_rotation,
        )

        if codex_auth_file_matches_authority(token_path, expected_store_value):
            return True
        await finalize_codex_auth_rotation(
            token_path,
            codex_authority,
            expected_store_value=expected_store_value,
            authority_known=True,
            butler_name="cli-auth-probe",
        )
    except Exception:
        logger.warning("Codex probe: system-global authority finalization failed")
    return False


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
    *,
    codex_authority: CredentialStore | None = None,
) -> AuthHealthResult:
    """Run a provider's status command and determine auth health."""
    if not provider.is_available():
        return AuthHealthResult(
            provider=provider.name,
            state=AuthHealthState.unavailable,
            detail=f"Binary '{provider.binary()}' not found on PATH.",
        )

    # api_key providers: check if the key is available
    if provider.auth_mode == "api_key":
        # Claude provider: key is stored exclusively in the credential store.
        # Use the credential store when available; fall back to env for dev/testing.
        if provider.name == "claude":
            api_key: str | None = None
            if credential_store is not None:
                try:
                    api_key = await credential_store.load("cli-auth/claude")
                except Exception:
                    logger.debug(
                        "Failed to load API key for provider '%s' from credential store.",
                        provider.name,
                        exc_info=True,
                    )
            if api_key is None:
                api_key = os.environ.get("ANTHROPIC_API_KEY")

            if api_key and api_key.startswith("sk-ant-"):
                return AuthHealthResult(
                    provider=provider.name,
                    state=AuthHealthState.authenticated,
                    detail="Anthropic API key configured.",
                )
            if api_key:
                # Key exists but format is unexpected — still usable, warn only
                return AuthHealthResult(
                    provider=provider.name,
                    state=AuthHealthState.authenticated,
                    detail="API key configured (non-standard format).",
                )
            return AuthHealthResult(
                provider=provider.name,
                state=AuthHealthState.not_authenticated,
                detail="No Anthropic API key configured. Provide one via the dashboard.",
            )

        # Other api_key providers: check if the key exists in the auth file
        if provider.token_path is not None and provider.token_path.exists():
            try:
                import json

                auth_data = json.loads(provider.token_path.read_text(encoding="utf-8"))
                # OpenCode Go stores as {"opencode-go": {"type": "api", "key": "..."}}
                entry = auth_data.get("opencode-go", {})
                if entry.get("key"):
                    return AuthHealthResult(
                        provider=provider.name,
                        state=AuthHealthState.authenticated,
                        detail="API key configured.",
                    )
            except (json.JSONDecodeError, OSError):
                pass
        return AuthHealthResult(
            provider=provider.name,
            state=AuthHealthState.not_authenticated,
            detail="No API key configured.",
        )

    expected_codex_authority: str | None = None
    if provider.name == "codex":
        expected_codex_authority = await _prepare_codex_probe_authority(
            provider,
            codex_authority,
        )
        if expected_codex_authority is None:
            return AuthHealthResult(
                provider=provider.name,
                state=AuthHealthState.probe_failed,
                detail="System-global Codex authority unavailable; probe was not run.",
            )

    if provider.status_command is None or provider.status_ok_pattern is None:
        # Codex must never replace its explicit authority check with a local
        # auth-file presence signal, even if a future registry entry loses its
        # status command metadata.
        if provider.name == "codex":
            return AuthHealthResult(
                provider=provider.name,
                state=AuthHealthState.probe_failed,
                detail="Codex status probe is not configured; probe was not run.",
            )
        # No status command — retain file existence behavior for other CLI
        # providers, whose credentials are not this system-global identity.
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
        raw_output, _ = await asyncio.wait_for(proc.communicate(), timeout=_PROBE_TIMEOUT)
        output = _strip_ansi(raw_output.decode(errors="replace"))

        if provider.name == "codex" and not await _finalize_codex_probe_if_rotated(
            provider,
            codex_authority,
            expected_codex_authority,
        ):
            return AuthHealthResult(
                provider=provider.name,
                state=AuthHealthState.probe_failed,
                detail="System-global Codex authority changed during probe; outcome withheld.",
            )

        if proc.returncode == 0 and provider.status_ok_pattern.search(output):
            # Status command says authenticated — verify the JWT hasn't expired.
            # The CLI's status check often only inspects the file, not the token.
            if provider.token_path is not None:
                expired, expiry_detail = _check_jwt_expiry(provider.token_path)
                if expired:
                    return AuthHealthResult(
                        provider=provider.name,
                        state=AuthHealthState.not_authenticated,
                        detail=expiry_detail or "Token expired.",
                    )
            # For Codex, also validate the token against OpenAI's backend —
            # `codex login status` is file-only and misses server-side refresh
            # token revocation.
            if provider.name == "codex" and provider.token_path is not None:
                revoked, revoked_detail = await _probe_codex_backend(provider.token_path)
                if revoked:
                    return AuthHealthResult(
                        provider=provider.name,
                        state=AuthHealthState.not_authenticated,
                        detail=revoked_detail or "Backend rejected stored token.",
                    )
            return AuthHealthResult(
                provider=provider.name,
                state=AuthHealthState.authenticated,
                # The Codex CLI can include provider diagnostics in its
                # status output.  This result is persisted to the dashboard
                # probe history, so retain only a fixed value-free outcome.
                detail=(
                    "Codex CLI reports authenticated."
                    if provider.name == "codex"
                    else output.strip()[:200]
                ),
            )

        return AuthHealthResult(
            provider=provider.name,
            state=AuthHealthState.not_authenticated,
            detail=(
                f"Codex login status exited with code {proc.returncode}."
                if provider.name == "codex"
                else output.strip()[:200] or f"Exit code {proc.returncode}."
            ),
        )

    except TimeoutError:
        if provider.name == "codex":
            await _finalize_codex_probe_if_rotated(
                provider,
                codex_authority,
                expected_codex_authority,
            )
        logger.warning("CLI auth health probe timed out for %s", provider.name)
        return AuthHealthResult(
            provider=provider.name,
            state=AuthHealthState.probe_failed,
            detail=f"Status command timed out after {_PROBE_TIMEOUT}s.",
        )
    except Exception:
        if provider.name == "codex":
            await _finalize_codex_probe_if_rotated(
                provider,
                codex_authority,
                expected_codex_authority,
            )
            # Codex driver/process errors can carry auth details; retain only
            # the fixed safe diagnostic for this authority-sensitive path.
            logger.warning("Codex CLI auth health probe failed safely")
        else:
            logger.exception("CLI auth health probe failed for %s", provider.name)
        return AuthHealthResult(
            provider=provider.name,
            state=AuthHealthState.probe_failed,
            detail="Status command failed to execute.",
        )


async def probe_all(
    credential_store: CredentialStore | None = None,
    *,
    codex_authority: CredentialStore | None = None,
) -> dict[str, AuthHealthResult]:
    """Probe all registered providers concurrently."""
    tasks = {
        name: asyncio.create_task(
            probe_provider(
                provider,
                credential_store,
                codex_authority=codex_authority if provider.name == "codex" else None,
            )
        )
        for name, provider in PROVIDERS.items()
        if provider.is_available() or provider.auth_mode == "api_key"
    }
    results: dict[str, AuthHealthResult] = {}
    for name, task in tasks.items():
        results[name] = await task
    return results
