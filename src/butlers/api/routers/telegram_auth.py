"""Telegram user-session bootstrap endpoints.

Implements a multi-step interactive flow for generating a Telethon
``StringSession`` from an API ID + API Hash, without requiring users to
run CLI tools or paste pre-generated session strings.

Flow:
  1. POST /api/telegram/session/send-code
     - Accepts ``api_id``, ``api_hash``, ``phone``, and an explicit
       account-wide ingestion acknowledgement.
     - Creates a temporary Telethon client, calls ``send_code_request()``.
     - Serializes the intermediate session state to the DB.
     - Returns a ``session_token`` (opaque handle) and ``phone_code_hash``.

  2. POST /api/telegram/session/verify
     - Accepts ``session_token``, ``code``, and optional ``password`` (2FA).
     - Reconstructs the Telethon client from the saved session state.
     - Signs in with the OTP code (and 2FA if needed).
     - On success, exports the ``StringSession``, stores ``telegram_api_id``,
       ``telegram_api_hash``, and ``telegram_user_session`` on the owner
       entity and the versioned non-secret consent grant in one shared-DB
       transaction, then disconnects the client.

  3. GET /api/telegram/session/status
     - Reports whether all three Telegram user credentials and the current
       account-wide consent grant exist.

Security:
  - Pending auth state is stored in butler_secrets with a TTL.
  - Session strings are never returned to the frontend.
  - The client must acknowledge the disclosed account-wide ingestion scope;
    a missing acknowledgement is rejected server-side.
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
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from butlers.api.db import DatabaseManager
from butlers.connectors.telegram_user_client_consent import (
    is_account_wide_ingestion_consent_granted,
    load_account_wide_ingestion_consent,
    save_account_wide_ingestion_consent,
)
from butlers.credential_store import (
    CredentialStore,
    resolve_owner_entity_info,
    upsert_owner_entity_info_on_connection,
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
    scope_consent: Literal[True]


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
    has_scope_consent: bool
    ready: bool


# ---------------------------------------------------------------------------
# Pending auth — DB-persisted state (multi-worker safe)
# ---------------------------------------------------------------------------

_PENDING_KEY_PREFIX = "_tg_auth_pending:"
_SESSION_TTL = 1800  # 30 minutes


class _CredentialPersistenceError(RuntimeError):
    """A verified session could not be durably written to owner credentials."""

    def __init__(self, info_type: str) -> None:
        self.info_type = info_type
        super().__init__(f"Credential upsert returned false for {info_type}")


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
    await store.store(
        _pending_key(token),
        json.dumps(data),
        category="_internal",
        description="Telegram auth pending state (auto-expires)",
    )


async def _load_pending(store: CredentialStore, token: str) -> dict:
    """Load and validate pending auth state from butler_secrets."""
    raw = await store.load(_pending_key(token))
    if raw is None:
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


async def _persist_verified_telegram_session(
    pool: Any,
    *,
    token: str,
    pending: dict[str, Any],
    session_string: str,
) -> None:
    """Atomically commit consent, owner credentials, and pending-state deletion.

    Final Telethon session material is staged in the pending record before this
    function runs. If any write here fails, the transaction rolls back and the
    staged record remains available for a safe retry without another OTP.
    """
    async with pool.acquire() as conn:
        async with conn.transaction():
            await save_account_wide_ingestion_consent(conn, datetime.now(UTC))
            for info_type, value, secured in (
                ("telegram_api_id", str(pending["api_id"]), False),
                ("telegram_api_hash", pending["api_hash"], True),
                ("telegram_user_session", session_string, True),
            ):
                persisted = await upsert_owner_entity_info_on_connection(
                    conn,
                    info_type,
                    value,
                    secured=secured,
                )
                if not persisted:
                    raise _CredentialPersistenceError(info_type)

            await conn.execute(
                "DELETE FROM butler_secrets WHERE secret_key = $1",
                _pending_key(token),
            )


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
            "scope_consent": req.scope_consent,
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
    if pending.get("scope_consent") is not True:
        raise HTTPException(
            status_code=400,
            detail=(
                "Explicit informed consent is required. Restart Telegram setup and acknowledge its "
                "scope."
            ),
        )

    # A final session is written to pending state before any credential writes.
    # If a later write fails, repeating verification can safely resume from this
    # verified session rather than consuming another one-time code.
    verified_session = pending.get("verified_session")
    auth_completed = isinstance(verified_session, str) and bool(verified_session)
    resuming_verified_session = auth_completed
    session = StringSession(verified_session if auth_completed else pending["session"])
    client = TelegramClient(session, pending["api_id"], pending["api_hash"])

    try:
        await client.connect()

        if not auth_completed:
            if req.password:
                # Second call: user is providing the 2FA password.
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

        # Authentication succeeded — export and durably stage the final session
        # before the atomic credential/control-state commit. This makes a failed
        # commit explicitly retryable and preserves the authenticated session.
        me = await client.get_me()
        session_string = (
            verified_session if resuming_verified_session else StringSession.save(client.session)
        )
        if not isinstance(session_string, str) or not session_string:
            raise RuntimeError("Telegram authentication produced an empty session")
        if not resuming_verified_session:
            pending["verified_session"] = session_string
            auth_completed = True
            await _save_pending(store, req.session_token, pending)

        await _persist_verified_telegram_session(
            pool,
            token=req.session_token,
            pending=pending,
            session_string=session_string,
        )

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
    except _CredentialPersistenceError as exc:
        logger.warning(
            "Telegram authentication verified but credential persistence failed for type=%s",
            exc.info_type,
        )
        raise HTTPException(
            status_code=503,
            detail=(
                "Telegram authentication succeeded, but credentials could not be saved. "
                "Retry verification."
            ),
        ) from exc
    except Exception as exc:
        if auth_completed:
            logger.warning("Telegram authentication verified but persistence failed: %s", exc)
            raise HTTPException(
                status_code=503,
                detail=(
                    "Telegram authentication succeeded, but setup data could not be persisted. "
                    "Retry verification."
                ),
            ) from exc
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
    try:
        settings = await load_account_wide_ingestion_consent(pool)
        has_scope_consent = is_account_wide_ingestion_consent_granted(settings)
    except Exception:
        logger.warning("Telegram session status could not verify account-wide ingestion consent")
        has_scope_consent = False

    return SessionStatusResponse(
        has_api_id=has_api_id,
        has_api_hash=has_api_hash,
        has_session=has_session,
        has_scope_consent=has_scope_consent,
        ready=has_api_id and has_api_hash and has_session,
    )
