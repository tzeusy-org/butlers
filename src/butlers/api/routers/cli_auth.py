"""CLI auth device-code flow endpoints.

Provides a REST API for starting and polling CLI tool authentication
flows (OpenCode, Codex) via device code authorization. The dashboard
uses these endpoints to present a one-click login experience.

After a successful auth flow, the token file is persisted to the shared
credential store so it survives container restarts (no PV needed).
"""

from __future__ import annotations

import logging
import secrets
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from butlers.api.models.cli_auth import (
    CLIAuthHealthState,
    CLIAuthProvider,
    CLIAuthSessionResponse,
    CLIAuthSessionState,
    CLIAuthStartResponse,
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
async def list_providers() -> list[CLIAuthProvider]:
    """List all registered CLI auth providers with current auth status.

    Runs live health probes against each provider's status command to
    determine whether tokens are actually valid (not just present on disk).
    """
    health_results = await probe_all()

    result = []
    for p in PROVIDERS.values():
        if not p.is_available():
            continue
        health = health_results.get(p.name)
        result.append(
            CLIAuthProvider(
                name=p.name,
                display_name=p.display_name,
                runtime=p.runtime,
                authenticated=p.is_authenticated(),
                health=CLIAuthHealthState(health.state) if health else None,
                health_detail=health.detail if health else None,
                token_path=str(p.token_path),
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
