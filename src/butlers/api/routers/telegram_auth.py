"""Telegram user-session bootstrap endpoints.

Implements a multi-step interactive flow for generating a Telethon
``StringSession`` from an API ID + API Hash, without requiring users to
run CLI tools or paste pre-generated session strings.

Flow:
  1. POST /api/telegram/session/send-code
     - Accepts ``api_id``, ``api_hash``, ``phone``.
     - Creates a temporary Telethon client, calls ``send_code_request()``.
     - Serializes the intermediate session state to the DB.
     - Returns a ``session_token`` (opaque handle) and ``phone_code_hash``.

  2. POST /api/telegram/session/verify
     - Accepts ``session_token``, ``code``, and optional ``password`` (2FA).
     - Reconstructs the Telethon client from the saved session state.
     - Signs in with the OTP code (and 2FA if needed).
     - On success, exports the ``StringSession``, stores ``telegram_api_id``,
       ``telegram_api_hash``, and ``telegram_user_session`` on the owner
       entity via ``upsert_owner_entity_info()``, and disconnects the client.

  3. GET /api/telegram/session/status
     - Reports whether all three Telegram user credentials exist on the
       owner entity.

Security:
  - Pending auth state is stored in butler_secrets with a TTL.
  - Session strings are never returned to the frontend.
  - The Telethon client is created fresh per request and disconnected after.
  - Phone numbers are stored only in the pending auth blob (deleted after use).

Multi-worker safety:
  - All state is persisted to the database, not held in-memory.
  - Any worker can handle any step of the flow.
"""

from __future__ import annotations

import json
import logging
import secrets
import time

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from butlers.api.db import DatabaseManager
from butlers.credential_store import (
    CredentialStore,
    resolve_owner_entity_info,
    upsert_owner_entity_info,
)

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
# Pending auth — DB-persisted state (multi-worker safe)
# ---------------------------------------------------------------------------

_PENDING_KEY_PREFIX = "_tg_auth_pending:"
_SESSION_TTL = 1800  # 30 minutes


def _pending_key(token: str) -> str:
    return f"{_PENDING_KEY_PREFIX}{token}"


def _get_pool(db: DatabaseManager):
    """Resolve the shared or first available pool."""
    try:
        return db.credential_shared_pool()
    except KeyError:
        butler_names = list(db.butler_names)
        if not butler_names:
            raise HTTPException(
                status_code=503,
                detail="No database pool available.",
            )
        return db.pool(butler_names[0])


async def _save_pending(
    store: CredentialStore,
    token: str,
    data: dict,
) -> None:
    """Persist pending auth state as a JSON blob in butler_secrets."""
    data["created_at"] = time.time()
    key = _pending_key(token)
    blob = json.dumps(data)
    logger.info("Saving pending Telegram auth: key=%s len=%d", key, len(blob))
    await store.store(
        key,
        blob,
        category="_internal",
        description="Telegram auth pending state (auto-expires)",
    )
    # Verify write succeeded by reading back
    verify = await store.load(key)
    if verify is None:
        logger.error("WRITE VERIFICATION FAILED: key=%s not found after store()", key)
    else:
        logger.info("Write verified OK: key=%s len=%d", key, len(verify))


async def _load_pending(store: CredentialStore, token: str) -> dict:
    """Load and validate pending auth state from butler_secrets."""
    key = _pending_key(token)
    logger.info("Loading pending Telegram auth: key=%s pool=%r", key, store.pool)
    raw = await store.load(key)
    if raw is None:
        logger.error("Pending Telegram auth NOT FOUND: key=%s", key)
        raise HTTPException(
            status_code=404,
            detail="Session token not found or expired. Please restart the Telegram login flow.",
        )
    data = json.loads(raw)
    if time.time() - data.get("created_at", 0) > _SESSION_TTL:
        await store.delete(_pending_key(token))
        raise HTTPException(
            status_code=404,
            detail="Session token expired. Please restart the Telegram login flow.",
        )
    return data


async def _delete_pending(store: CredentialStore, token: str) -> None:
    """Remove pending auth state from butler_secrets."""
    try:
        await store.delete(_pending_key(token))
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/session/send-code", response_model=SendCodeResponse)
async def send_code(
    req: SendCodeRequest,
    db: DatabaseManager = Depends(_get_db_manager),
) -> SendCodeResponse:
    """Start Telegram auth: send OTP code to the user's phone."""
    try:
        from telethon import TelegramClient
        from telethon.sessions import StringSession
    except ImportError:
        raise HTTPException(
            status_code=503,
            detail=(
                "Telethon is not installed on the server. Install with: uv pip install telethon"
            ),
        )

    pool = _get_pool(db)
    store = CredentialStore(pool)

    client = TelegramClient(StringSession(), req.api_id, req.api_hash)
    try:
        await client.connect()
        result = await client.send_code_request(req.phone)

        # Serialize the intermediate session (contains the auth_key needed
        # to complete sign-in on a subsequent request/worker).
        intermediate_session = StringSession.save(client.session)
    except Exception as exc:
        logger.warning("Telegram send_code_request failed: %s", exc)
        raise HTTPException(
            status_code=400,
            detail=f"Failed to send code: {exc}",
        )
    finally:
        await client.disconnect()

    token = secrets.token_urlsafe(32)
    await _save_pending(
        store,
        token,
        {
            "api_id": req.api_id,
            "api_hash": req.api_hash,
            "phone": req.phone,
            "phone_code_hash": result.phone_code_hash,
            "session": intermediate_session,
        },
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
        from telethon import TelegramClient
        from telethon.errors import SessionPasswordNeededError
        from telethon.sessions import StringSession
    except ImportError:
        raise HTTPException(status_code=503, detail="Telethon is not installed.")

    pool = _get_pool(db)
    store = CredentialStore(pool)
    pending = await _load_pending(store, req.session_token)

    # Reconstruct the client from the saved intermediate session.
    session = StringSession(pending["session"])
    client = TelegramClient(session, pending["api_id"], pending["api_hash"])

    try:
        await client.connect()

        if req.password:
            # Second call: user is providing the 2FA password
            await client.sign_in(password=req.password)
        else:
            try:
                await client.sign_in(
                    phone=pending["phone"],
                    code=req.code,
                    phone_code_hash=pending["phone_code_hash"],
                )
            except SessionPasswordNeededError:
                # Save updated session state (auth progressed past OTP)
                # so the 2FA call can continue from this point.
                updated_session = StringSession.save(client.session)
                pending["session"] = updated_session
                await _save_pending(store, req.session_token, pending)
                return VerifyCodeResponse(
                    success=False,
                    message="Two-factor authentication is enabled. "
                    "Please provide your 2FA password.",
                )

        # Auth succeeded — export final session string
        me = await client.get_me()
        session_string = StringSession.save(client.session)

        await upsert_owner_entity_info(
            pool, "telegram_api_id", str(pending["api_id"]), secured=False
        )
        await upsert_owner_entity_info(pool, "telegram_api_hash", pending["api_hash"], secured=True)
        await upsert_owner_entity_info(pool, "telegram_user_session", session_string, secured=True)

        # Clean up pending state
        await _delete_pending(store, req.session_token)

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
        # Clean up on hard failure
        await _delete_pending(store, req.session_token)
        raise HTTPException(
            status_code=400,
            detail=f"Sign-in failed: {exc}",
        )
    finally:
        try:
            await client.disconnect()
        except Exception:
            pass


@router.get("/session/status", response_model=SessionStatusResponse)
async def session_status(
    db: DatabaseManager = Depends(_get_db_manager),
) -> SessionStatusResponse:
    """Check whether Telegram user credentials are configured on the owner entity."""
    pool = _get_pool(db)

    has_api_id = await resolve_owner_entity_info(pool, "telegram_api_id") is not None
    has_api_hash = await resolve_owner_entity_info(pool, "telegram_api_hash") is not None
    has_session = await resolve_owner_entity_info(pool, "telegram_user_session") is not None

    return SessionStatusResponse(
        has_api_id=has_api_id,
        has_api_hash=has_api_hash,
        has_session=has_session,
        ready=has_api_id and has_api_hash and has_session,
    )
