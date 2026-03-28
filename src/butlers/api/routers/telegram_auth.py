"""Telegram user-session bootstrap endpoints.

Implements a multi-step interactive flow for generating a Telethon
``StringSession`` from an API ID + API Hash, without requiring users to
run CLI tools or paste pre-generated session strings.

Flow:
  1. POST /api/telegram/session/send-code
     - Accepts ``api_id``, ``api_hash``, ``phone``.
     - Creates a temporary Telethon client, calls ``send_code_request()``.
     - Returns a ``session_token`` (opaque handle) and ``phone_code_hash``.

  2. POST /api/telegram/session/verify
     - Accepts ``session_token``, ``code``, and optional ``password`` (2FA).
     - Signs in with the OTP code (and 2FA if needed).
     - On success, exports the ``StringSession``, stores ``telegram_api_id``,
       ``telegram_api_hash``, and ``telegram_user_session`` on the owner
       entity via ``upsert_owner_entity_info()``, and disconnects the client.

  3. GET /api/telegram/session/status
     - Reports whether all three Telegram user credentials exist on the
       owner entity.

Security:
  - Pending auth sessions are held in-memory with a 10-minute TTL.
  - Session strings are never returned to the frontend.
  - The Telethon client is disconnected after use.
  - Phone numbers are not stored.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
import time
from dataclasses import dataclass, field
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from butlers.api.db import DatabaseManager
from butlers.credential_store import resolve_owner_entity_info, upsert_owner_entity_info

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/telegram", tags=["telegram"])

# ---------------------------------------------------------------------------
# Dependency stub (wired at startup via wire_db_dependencies)
# ---------------------------------------------------------------------------


def _get_db_manager() -> DatabaseManager:
    """Dependency stub — overridden at app startup or in tests."""
    raise RuntimeError("DatabaseManager not initialized")


# ---------------------------------------------------------------------------
# Pydantic request/response models
# ---------------------------------------------------------------------------


class SendCodeRequest(BaseModel):
    api_id: int
    api_hash: str
    phone: str


class SendCodeResponse(BaseModel):
    session_token: str
    phone_code_hash: str


class VerifyCodeRequest(BaseModel):
    session_token: str
    code: str
    password: str | None = None


class VerifyCodeResponse(BaseModel):
    success: bool
    user_name: str | None = None
    message: str


class SessionStatusResponse(BaseModel):
    has_api_id: bool
    has_api_hash: bool
    has_session: bool
    ready: bool


# ---------------------------------------------------------------------------
# In-memory pending-auth store (TTL = 10 minutes)
# ---------------------------------------------------------------------------

_SESSION_TTL = 1800  # 30 minutes — enough for OTP delivery + 2FA entry


@dataclass
class _PendingAuth:
    """Holds a live Telethon client mid-auth flow."""

    client: Any  # TelegramClient
    api_id: int
    api_hash: str
    phone: str
    phone_code_hash: str
    created_at: float = field(default_factory=time.monotonic)


# token → _PendingAuth
_pending: dict[str, _PendingAuth] = {}
_pending_lock = asyncio.Lock()


async def _cleanup_expired() -> None:
    """Remove expired pending auth sessions and disconnect their clients."""
    now = time.monotonic()
    expired_tokens = [tok for tok, pa in _pending.items() if now - pa.created_at > _SESSION_TTL]
    for tok in expired_tokens:
        pa = _pending.pop(tok, None)
        if pa and pa.client:
            try:
                await pa.client.disconnect()
            except Exception:
                pass


def _get_pending(token: str) -> _PendingAuth:
    """Look up a pending auth session, raising 404 if expired/missing."""
    pa = _pending.get(token)
    if pa is None or (time.monotonic() - pa.created_at > _SESSION_TTL):
        # Clean up if expired
        _pending.pop(token, None)
        raise HTTPException(
            status_code=404,
            detail=("Session token not found or expired. Please restart the Telegram login flow."),
        )
    return pa


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/session/send-code", response_model=SendCodeResponse)
async def send_code(req: SendCodeRequest) -> SendCodeResponse:
    """Start Telegram auth: send OTP code to the user's phone."""
    try:
        from telethon import TelegramClient
        from telethon.sessions import StringSession
    except ImportError:
        raise HTTPException(
            status_code=503,
            detail="Telethon is not installed on the server. Install with: uv pip install telethon",
        )

    # Clean up old sessions
    async with _pending_lock:
        await _cleanup_expired()

    client = TelegramClient(StringSession(), req.api_id, req.api_hash)
    try:
        await client.connect()
        result = await client.send_code_request(req.phone)
    except Exception as exc:
        await client.disconnect()
        logger.warning("Telegram send_code_request failed: %s", exc)
        raise HTTPException(
            status_code=400,
            detail=f"Failed to send code: {exc}",
        )

    token = secrets.token_urlsafe(32)
    async with _pending_lock:
        _pending[token] = _PendingAuth(
            client=client,
            api_id=req.api_id,
            api_hash=req.api_hash,
            phone=req.phone,
            phone_code_hash=result.phone_code_hash,
        )

    return SendCodeResponse(
        session_token=token,
        phone_code_hash=result.phone_code_hash,
    )


@router.post("/session/verify", response_model=VerifyCodeResponse)
async def verify_code(
    req: VerifyCodeRequest,
    db: DatabaseManager = Depends(_get_db_manager),
) -> VerifyCodeResponse:
    """Complete Telegram auth: verify OTP code and persist session."""
    try:
        from telethon.errors import SessionPasswordNeededError
        from telethon.sessions import StringSession
    except ImportError:
        raise HTTPException(status_code=503, detail="Telethon is not installed.")

    async with _pending_lock:
        pa = _get_pending(req.session_token)

    client = pa.client
    needs_2fa = False

    try:
        # Attempt sign-in with OTP code (or 2FA password on second call)
        if req.password:
            # Second call: user is providing the 2FA password
            await client.sign_in(password=req.password)
        else:
            try:
                await client.sign_in(
                    phone=pa.phone,
                    code=req.code,
                    phone_code_hash=pa.phone_code_hash,
                )
            except SessionPasswordNeededError:
                # Signal the frontend to collect the 2FA password.
                # Keep the pending session alive so the next verify call
                # can complete sign-in with the password.
                needs_2fa = True
                return VerifyCodeResponse(
                    success=False,
                    message="Two-factor authentication is enabled. "
                    "Please provide your 2FA password.",
                )

        # Auth succeeded — export session string
        me = await client.get_me()
        session_string = StringSession.save(client.session)

        # Persist credentials to owner entity_info
        try:
            pool = db.credential_shared_pool()
        except KeyError:
            # Fall back to first available butler pool
            butler_names = list(db.butler_names)
            if not butler_names:
                raise HTTPException(
                    status_code=503,
                    detail="No database pool available to store credentials.",
                )
            pool = db.pool(butler_names[0])

        await upsert_owner_entity_info(pool, "telegram_api_id", str(pa.api_id), secured=False)
        await upsert_owner_entity_info(pool, "telegram_api_hash", pa.api_hash, secured=True)
        await upsert_owner_entity_info(pool, "telegram_user_session", session_string, secured=True)

        user_name = None
        if me:
            username = getattr(me, "username", None)
            first = getattr(me, "first_name", None) or ""
            last = getattr(me, "last_name", None) or ""
            user_name = f"@{username}" if username else f"{first} {last}".strip()

        return VerifyCodeResponse(
            success=True,
            user_name=user_name,
            message="Telegram session created and stored successfully.",
        )

    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("Telegram sign_in failed: %s", exc)
        raise HTTPException(
            status_code=400,
            detail=f"Sign-in failed: {exc}",
        )
    finally:
        # Only clean up when auth is complete (success or hard failure).
        # When 2FA is needed, keep the client alive for the next verify call.
        if not needs_2fa:
            try:
                await client.disconnect()
            except Exception:
                pass
            async with _pending_lock:
                _pending.pop(req.session_token, None)


@router.get("/session/status", response_model=SessionStatusResponse)
async def session_status(
    db: DatabaseManager = Depends(_get_db_manager),
) -> SessionStatusResponse:
    """Check whether Telegram user credentials are configured on the owner entity."""
    try:
        pool = db.credential_shared_pool()
    except KeyError:
        butler_names = list(db.butler_names)
        if not butler_names:
            return SessionStatusResponse(
                has_api_id=False, has_api_hash=False, has_session=False, ready=False
            )
        pool = db.pool(butler_names[0])

    has_api_id = await resolve_owner_entity_info(pool, "telegram_api_id") is not None
    has_api_hash = await resolve_owner_entity_info(pool, "telegram_api_hash") is not None
    has_session = await resolve_owner_entity_info(pool, "telegram_user_session") is not None

    return SessionStatusResponse(
        has_api_id=has_api_id,
        has_api_hash=has_api_hash,
        has_session=has_session,
        ready=has_api_id and has_api_hash and has_session,
    )
