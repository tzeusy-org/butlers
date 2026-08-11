"""CLI auth health probes.

Most providers use a disposable sandboxed status command.  Codex is an
explicit exception: its status command can refresh credentials, so Dashboard
health validates the reconciled authority document and backend response in the
trusted parent without launching a Codex child.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import math
import os
import time
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

import httpx

from butlers.cli_auth.registry import PROVIDERS, CLIAuthProviderDef
from butlers.cli_auth.sandbox import (
    DashboardCLIAuthSandbox,
    SandboxUnavailableError,
    dashboard_cli_auth_sandbox,
    load_validated_readonly_authority,
)
from butlers.cli_auth.session import _strip_ansi
from butlers.credential_store import CredentialStore

logger = logging.getLogger(__name__)

_PROBE_TIMEOUT = 15  # seconds

# Dashboard must not use ``codex login status``: it can rotate the canonical
# authority document.  Hitting the models endpoint with the strictly parsed
# access token is the parent-only read-only check.  A 401 means the next real
# Codex invocation will also 401 — which is the state the dashboard needs to
# surface.
_CODEX_BACKEND_PROBE_URL = "https://chatgpt.com/backend-api/codex/models?client_version=0.118.0"
_CODEX_BACKEND_PROBE_TIMEOUT = 5.0  # seconds


def _parse_unexpired_codex_access_token(authority_document: str) -> tuple[str | None, str | None]:
    """Return an unexpired access token only from a strict authority document.

    The backend probe is read-only but still credential-sensitive.  Reject
    malformed JSON, missing tokens, malformed JWT payloads, and missing or
    expired expiry claims before making any network request.
    """
    try:
        document = json.loads(authority_document)
    except (TypeError, json.JSONDecodeError):
        return None, "Codex authority document is malformed."
    if not isinstance(document, dict):
        return None, "Codex authority document is malformed."
    tokens = document.get("tokens")
    if not isinstance(tokens, dict):
        return None, "Codex access token is missing."
    access_token = tokens.get("access_token")
    if not isinstance(access_token, str) or not access_token:
        return None, "Codex access token is missing."

    parts = access_token.split(".")
    if len(parts) != 3 or not parts[1]:
        return None, "Codex access token is malformed."
    try:
        payload_segment = parts[1].encode("ascii")
        payload_segment += b"=" * (-len(payload_segment) % 4)
        payload_bytes = base64.b64decode(payload_segment, altchars=b"-_", validate=True)
        payload = json.loads(payload_bytes.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError):
        return None, "Codex access token is malformed."
    if not isinstance(payload, dict):
        return None, "Codex access token is malformed."
    expiry = payload.get("exp")
    if isinstance(expiry, bool) or not isinstance(expiry, int | float) or not math.isfinite(expiry):
        return None, "Codex access token expiry is missing or malformed."
    if time.time() >= expiry:
        return None, "Access token expired — re-login required."
    return access_token, None


def _check_optional_jwt_expiry(token_path: Path) -> tuple[bool, str | None]:
    """Retain the permissive legacy expiry signal for non-Codex CLI providers.

    Those providers still use their own sandboxed status commands as the
    authority check, so an absent or non-JWT token is not itself a failure.
    Codex deliberately does not call this helper; its parent-only path above
    requires a complete, strict JWT parse before the backend request.
    """
    try:
        data = json.loads(token_path.read_text(encoding="utf-8"))
        access_token = (data.get("tokens") or {}).get("access_token", "")
        if not isinstance(access_token, str) or not access_token:
            return False, None
        parts = access_token.split(".")
        if len(parts) < 2:
            return False, None
        payload_b64 = parts[1] + "=" * (-len(parts[1]) % 4)
        payload = json.loads(base64.urlsafe_b64decode(payload_b64))
        expiry = payload.get("exp")
        if isinstance(expiry, bool) or not isinstance(expiry, int | float):
            return False, None
        if time.time() >= expiry:
            return True, "Access token expired — re-login required."
    except Exception:
        return False, None
    return False, None


async def _probe_codex_backend(access_token: str) -> tuple[bool, str | None]:
    """Validate a strictly parsed Codex access token against OpenAI's backend.

    Returns ``(revoked, detail)``. ``revoked=True`` means OpenAI rejected the
    token with 401 — the local file is stale and re-login is required.
    ``revoked=False`` covers both success and transient failures (network
    blips, non-401 HTTP errors) — we don't want a flaky probe to red-flag a
    provider that's actually fine.
    """
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
    """Reconcile and verify the global Codex authority before parent-only health."""
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
    prepared_codex_authority: str | None = None,
    codex_authority_prepared: bool = False,
    sandbox: DashboardCLIAuthSandbox | None = None,
) -> AuthHealthResult:
    """Run a provider's status command and determine auth health."""
    if provider.name == "codex":
        if codex_authority_prepared:
            expected_codex_authority = prepared_codex_authority
            token_path = provider.token_path
            if expected_codex_authority is None or token_path is None:
                return AuthHealthResult(
                    provider=provider.name,
                    state=AuthHealthState.probe_failed,
                    detail="System-global Codex authority unavailable; probe was not run.",
                )
            try:
                from butlers.core.runtimes._codex_auth_sync import codex_auth_file_matches_authority

                authority_matches = codex_auth_file_matches_authority(
                    token_path,
                    expected_codex_authority,
                )
            except Exception:
                authority_matches = False
            if not authority_matches:
                return AuthHealthResult(
                    provider=provider.name,
                    state=AuthHealthState.probe_failed,
                    detail="System-global Codex authority changed; probe was not run.",
                )
        else:
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

        access_token, invalid_detail = _parse_unexpired_codex_access_token(
            expected_codex_authority,
        )
        if access_token is None:
            return AuthHealthResult(
                provider=provider.name,
                state=AuthHealthState.not_authenticated,
                detail=invalid_detail or "Codex authority is invalid.",
            )
        revoked, revoked_detail = await _probe_codex_backend(access_token)
        if revoked:
            return AuthHealthResult(
                provider=provider.name,
                state=AuthHealthState.not_authenticated,
                detail=revoked_detail or "Backend rejected stored token.",
            )
        return AuthHealthResult(
            provider=provider.name,
            state=AuthHealthState.authenticated,
            detail="Codex authority validated.",
        )

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

    if provider.status_command is None or provider.status_ok_pattern is None:
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

    authority = load_validated_readonly_authority(
        provider,
        expected_content=None,
    )
    if authority is None:
        return AuthHealthResult(
            provider=provider.name,
            state=AuthHealthState.probe_failed,
            detail="CLI auth sandbox authority copy is unavailable; probe was not run.",
        )

    try:
        sandbox_result = await (sandbox or dashboard_cli_auth_sandbox()).run_readonly_command(
            provider,
            command=tuple(provider.status_command),
            authority=authority,
            timeout_s=_PROBE_TIMEOUT,
        )
        output = _strip_ansi(sandbox_result.output.decode(errors="replace"))

        if sandbox_result.returncode == 0 and provider.status_ok_pattern.search(output):
            # Status command says authenticated — verify the JWT hasn't expired.
            # The CLI's status check often only inspects the file, not the token.
            if provider.token_path is not None:
                expired, expiry_detail = _check_optional_jwt_expiry(provider.token_path)
                if expired:
                    return AuthHealthResult(
                        provider=provider.name,
                        state=AuthHealthState.not_authenticated,
                        detail=expiry_detail or "Token expired.",
                    )
            return AuthHealthResult(
                provider=provider.name,
                state=AuthHealthState.authenticated,
                detail="Provider CLI status check succeeded.",
            )

        return AuthHealthResult(
            provider=provider.name,
            state=AuthHealthState.not_authenticated,
            detail="Provider CLI status check failed.",
        )

    except SandboxUnavailableError:
        return AuthHealthResult(
            provider=provider.name,
            state=AuthHealthState.probe_failed,
            detail="CLI auth sandbox is unavailable; probe was not run.",
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
