"""CLI auth endpoints.

Provides a REST API for starting and polling CLI tool authentication
flows. Supports two modes:

- **device_code**: Interactive device-code authorization (OpenCode, Codex).
- **api_key**: Simple API key storage and validation (OpenCode Go).

After a successful auth flow, credentials are persisted to the shared
credential store so they survive container restarts (no PV needed).
"""

from __future__ import annotations

import asyncio
import logging
import secrets
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from butlers.api.models.cli_auth import (
    CLIAuthApiKeyRequest,
    CLIAuthApiKeyResponse,
    CLIAuthHealthState,
    CLIAuthProvider,
    CLIAuthSessionResponse,
    CLIAuthSessionState,
    CLIAuthStartResponse,
    CLIAuthTestResponse,
)
from butlers.cli_auth.health import probe_all
from butlers.cli_auth.persistence import persist_token
from butlers.cli_auth.registry import PROVIDERS, CLIAuthProviderDef
from butlers.cli_auth.session import (
    CLIAuthSession,
    get_session,
    store_session,
)
from butlers.credential_store import CredentialStore

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/cli-auth", tags=["cli-auth"])

# ---------------------------------------------------------------------------
# DB dependency (same pattern as oauth.py)
# ---------------------------------------------------------------------------


def _get_db_manager() -> Any:
    """Stub replaced at startup by wire_db_dependencies()."""
    return None


def _make_credential_store(db_manager: Any) -> CredentialStore | None:
    if db_manager is None:
        return None
    try:
        pool = db_manager.credential_shared_pool()
    except Exception:
        return None
    return CredentialStore(pool)


def _build_on_success(db_manager: Any):
    """Build an on_success callback that persists the token to DB."""
    store = _make_credential_store(db_manager)
    if store is None:
        return None

    async def _on_success(provider: CLIAuthProviderDef) -> None:
        await persist_token(provider, store)

    return _on_success


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get(
    "/providers",
    response_model=list[CLIAuthProvider],
    summary="List CLI auth providers and their status",
)
async def list_providers(
    db_manager: Any = Depends(_get_db_manager),
) -> list[CLIAuthProvider]:
    """List all registered CLI auth providers with current auth status.

    Runs live health probes against each provider's status command to
    determine whether tokens are actually valid (not just present on disk).
    """
    store = _make_credential_store(db_manager)
    health_results = await probe_all(credential_store=store)

    result = []
    for p in PROVIDERS.values():
        # Show api_key providers even if binary is not installed
        if not p.is_available() and p.auth_mode != "api_key":
            continue
        health = health_results.get(p.name)
        result.append(
            CLIAuthProvider(
                name=p.name,
                display_name=p.display_name,
                runtime=p.runtime,
                auth_mode=p.auth_mode,
                authenticated=(
                    p.is_authenticated()
                    if p.auth_mode == "device_code"
                    else (health is not None and health.state == "authenticated")
                ),
                health=CLIAuthHealthState(health.state) if health else None,
                health_detail=health.detail if health else None,
                token_path=str(p.token_path) if p.token_path else None,
                env_var=p.env_var or None,
            )
        )
    return result


@router.post(
    "/{provider}/start",
    response_model=CLIAuthStartResponse,
    summary="Start a CLI auth device-code flow",
)
async def start_auth(
    provider: str,
    db_manager: Any = Depends(_get_db_manager),
) -> CLIAuthStartResponse:
    """Spawn a CLI login subprocess and return the device code for authorization.

    The session runs in the background; poll GET /sessions/{session_id}
    for state updates. On success, the token is automatically persisted
    to the shared credential store.
    """
    provider_def = PROVIDERS.get(provider)
    if provider_def is None:
        raise HTTPException(status_code=404, detail=f"Unknown provider: {provider}")

    if not provider_def.is_available():
        raise HTTPException(
            status_code=503,
            detail=f"CLI binary '{provider_def.binary()}' not found on PATH.",
        )

    session_id = secrets.token_urlsafe(16)
    session = CLIAuthSession(
        id=session_id,
        provider=provider_def,
        on_success=_build_on_success(db_manager),
    )
    store_session(session)

    await session.start()

    # Wait briefly for the device code to appear in stdout
    await session.wait(timeout=10.0)

    return CLIAuthStartResponse(
        session_id=session.id,
        state=CLIAuthSessionState(session.state),
        auth_url=session.auth_url,
        device_code=session.device_code,
        message=session.message,
    )


@router.get(
    "/sessions/{session_id}",
    response_model=CLIAuthSessionResponse,
    summary="Poll CLI auth session status",
)
async def get_session_status(session_id: str) -> CLIAuthSessionResponse:
    """Check the current state of a CLI auth session."""
    session = get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found.")

    return CLIAuthSessionResponse(
        session_id=session.id,
        state=CLIAuthSessionState(session.state),
        auth_url=session.auth_url,
        device_code=session.device_code,
        message=session.message,
        provider=session.provider.name,
    )


@router.delete(
    "/sessions/{session_id}",
    summary="Cancel a CLI auth session",
)
async def cancel_session(session_id: str) -> dict[str, str]:
    """Terminate a running CLI auth session."""
    session = get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found.")

    await session.kill()
    return {"status": "cancelled", "session_id": session_id}


# ---------------------------------------------------------------------------
# API-key provider endpoints
# ---------------------------------------------------------------------------


@router.put(
    "/{provider}/api-key",
    response_model=CLIAuthApiKeyResponse,
    summary="Store an API key for an api_key-mode provider",
)
async def save_api_key(
    provider: str,
    body: CLIAuthApiKeyRequest,
    db_manager: Any = Depends(_get_db_manager),
) -> CLIAuthApiKeyResponse:
    """Save an API key: write to the CLI's auth file and persist to DB."""
    import json

    provider_def = PROVIDERS.get(provider)
    if provider_def is None:
        raise HTTPException(status_code=404, detail=f"Unknown provider: {provider}")
    if provider_def.auth_mode != "api_key":
        raise HTTPException(
            status_code=400,
            detail=f"Provider '{provider}' uses {provider_def.auth_mode} mode, not api_key.",
        )

    # Write the key into the CLI's auth.json so the binary can use it
    if provider_def.token_path is not None:
        try:
            provider_def.token_path.parent.mkdir(parents=True, exist_ok=True)
            # Merge into existing auth.json (other providers may have entries)
            existing: dict = {}
            if provider_def.token_path.exists():
                try:
                    existing = json.loads(provider_def.token_path.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError):
                    pass
            # OpenCode Go stores API keys as {"type": "api", "key": "..."}
            existing["opencode-go"] = {"type": "api", "key": body.api_key}
            provider_def.token_path.write_text(json.dumps(existing, indent=2), encoding="utf-8")
            provider_def.token_path.chmod(0o600)
            logger.info("CLI auth: wrote API key to %s", provider_def.token_path)
        except OSError:
            logger.exception("CLI auth: failed to write auth file for %s", provider_def.name)
            raise HTTPException(status_code=500, detail="Failed to write auth file.")

    # Also persist to DB for K8s restarts
    store = _make_credential_store(db_manager)
    if store is not None:
        # Persist the entire auth.json (contains all providers' creds)
        if provider_def.token_path is not None and provider_def.token_path.exists():
            from butlers.cli_auth.persistence import persist_token

            await persist_token(provider_def, store)

    logger.info("CLI auth: stored API key for %s", provider_def.name)
    return CLIAuthApiKeyResponse(
        provider=provider_def.name,
        stored=True,
        message=f"API key saved for {provider_def.display_name}.",
    )


@router.delete(
    "/{provider}/api-key",
    summary="Delete a stored API key for an api_key-mode provider",
)
async def delete_api_key(
    provider: str,
    db_manager: Any = Depends(_get_db_manager),
) -> dict[str, str]:
    """Remove an API key from the auth file and credential store."""
    import json

    provider_def = PROVIDERS.get(provider)
    if provider_def is None:
        raise HTTPException(status_code=404, detail=f"Unknown provider: {provider}")
    if provider_def.auth_mode != "api_key":
        raise HTTPException(
            status_code=400,
            detail=f"Provider '{provider}' uses {provider_def.auth_mode} mode, not api_key.",
        )

    # Remove from the CLI's auth.json
    if provider_def.token_path is not None and provider_def.token_path.exists():
        try:
            existing = json.loads(provider_def.token_path.read_text(encoding="utf-8"))
            if "opencode-go" in existing:
                del existing["opencode-go"]
                provider_def.token_path.write_text(json.dumps(existing, indent=2), encoding="utf-8")
        except (json.JSONDecodeError, OSError):
            logger.exception("CLI auth: failed to update auth file for %s", provider_def.name)

    # Remove from DB
    store = _make_credential_store(db_manager)
    if store is not None:
        key = f"cli-auth/{provider_def.name}"
        await store.delete(key)

    logger.info("CLI auth: deleted API key for %s", provider_def.name)
    return {"status": "deleted", "provider": provider_def.name}


@router.post(
    "/{provider}/test",
    response_model=CLIAuthTestResponse,
    summary="Test an API key by running the provider's test command",
)
async def test_api_key(
    provider: str,
) -> CLIAuthTestResponse:
    """Run the provider's test command to validate the stored API key."""
    provider_def = PROVIDERS.get(provider)
    if provider_def is None:
        raise HTTPException(status_code=404, detail=f"Unknown provider: {provider}")
    if provider_def.auth_mode != "api_key":
        raise HTTPException(
            status_code=400,
            detail=f"Provider '{provider}' uses {provider_def.auth_mode} mode, not api_key.",
        )
    if not provider_def.test_command:
        raise HTTPException(status_code=400, detail=f"No test command configured for {provider}.")

    # The API key is in the CLI's auth.json — just run the test command
    from butlers.cli_auth.session import _strip_ansi

    try:
        proc = await asyncio.create_subprocess_exec(
            *provider_def.test_command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            stdin=asyncio.subprocess.DEVNULL,
        )
        raw_output, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
        output = _strip_ansi(raw_output.decode(errors="replace")).strip()[:200]

        if proc.returncode == 0:
            if provider_def.test_ok_pattern and provider_def.test_ok_pattern.search(output):
                return CLIAuthTestResponse(
                    provider=provider_def.name,
                    success=True,
                    detail=output or "Test passed.",
                )
            elif not provider_def.test_ok_pattern:
                return CLIAuthTestResponse(
                    provider=provider_def.name,
                    success=True,
                    detail=output or "Command succeeded (exit 0).",
                )
            else:
                return CLIAuthTestResponse(
                    provider=provider_def.name,
                    success=False,
                    detail=output or "Command succeeded but output didn't match expected pattern.",
                )

        return CLIAuthTestResponse(
            provider=provider_def.name,
            success=False,
            detail=output or f"Exit code {proc.returncode}.",
        )
    except TimeoutError:
        return CLIAuthTestResponse(
            provider=provider_def.name,
            success=False,
            detail="Test command timed out.",
        )
    except Exception:
        logger.exception("CLI auth test failed for %s", provider_def.name)
        return CLIAuthTestResponse(
            provider=provider_def.name,
            success=False,
            detail="Test command failed to execute.",
        )
