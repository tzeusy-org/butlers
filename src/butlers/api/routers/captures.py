"""Owner capture admission/status and the explicit chat capture bridge (RFC0037).

REQ-general-capture-001/-006 ingress slice over the landed internal General
capture service. Every route here is owner-only and server-attributed:

- ``GET  /api/general/capture-intake-context`` issues a bounded, expiring owner
  action context for one door (``dashboard`` or ``chat``) bound to the verified
  session, the optional owner-verified conversation and the current host epoch.
- ``POST /api/general/captures`` and ``POST /api/general/capture-chat`` require
  that context in ``X-Capture-Intake`` and check it before any body octet is
  read. Admission commits a held receipt before responding; nothing classifies or
  routes here, so new intake truthfully stays held until processing is enabled.
- ``GET /api/general/captures`` and ``GET /api/general/captures/{capture_id}``
  return a content-free projection of the owner's own captures.

The chat bridge is a distinct server-known route. It never calls ordinary
conversation submission and never switches a conversation's privacy policy.

Content blindness: :class:`CaptureRoute` maps malformed JSON, validation, domain
errors, timeouts and unexpected failures to fixed codes before generic handlers
can log ``str(exc)`` or framework ``input``. It records only a fixed operation,
category and opaque capture id in ``request.state.capture_audit``; the dashboard
audit middleware selects its no-body policy for these exact routes server-side.
Cancellation propagates after that record and the transaction cleanup.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import os
import secrets
import uuid
from collections.abc import AsyncIterator, Callable, Coroutine
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import asyncpg
from fastapi import APIRouter, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import ValidationError
from starlette.responses import JSONResponse

from butlers.api.audit_emit import authenticated_principal
from butlers.api.db import DatabaseManager
from butlers.api.deps import OptionalDb
from butlers.api.models import ApiResponse, ErrorDetail, ErrorResponse
from butlers.api.models.capture import (
    CaptureDoor,
    CaptureIntakeContext,
    CaptureIntakeRequest,
    CaptureStatus,
    CaptureTarget,
)
from butlers.api.pagination import Page, decode_cursor, encode_cursor
from butlers.core.capture import (
    CaptureUnavailable,
    VerifiedAuthority,
    canonical_intake,
    load_epoch,
)
from butlers.roster_imports import roster_module

#: Request header carrying the server-issued intake context. Its value is a
#: bearer-style owner action grant: never logged, audited, traced or echoed.
INTAKE_HEADER = "x-capture-intake"
#: Host path of the nonsecret capture epoch manifest (outside repo and database).
EPOCH_PATH_ENV = "BUTLERS_CAPTURE_EPOCH_PATH"

GENERAL_ROLE = "butler_general_rw"
CONTEXT_TTL = timedelta(minutes=10)
REQUEST_TIMEOUT_S = 10.0
MAX_BODY_BYTES = 262_144
CURSOR_SCOPE = "general.captures"

#: Fixed POST doors. The audit middleware consumes this mapping unchanged.
POST_DOORS: dict[str, CaptureDoor] = {
    "/api/general/captures": "dashboard",
    "/api/general/capture-chat": "chat",
}
#: Fixed audit operation per door; no caller value selects or renames it.
AUDIT_OPERATIONS: dict[CaptureDoor, str] = {
    "dashboard": "general_capture_admit",
    "chat": "general_capture_chat",
}

_NAMESPACE = uuid.UUID("6f1c2a3e-8d4b-5c7a-9e0f-3a2b1c4d5e6f")
_SOURCE_DOORS = {"dashboard", "chat"}

_STATUS_COLUMNS = (
    "id, disposition, category, operation_id, receipt, mutation_key, created_at, updated_at"
)


class _Refusal(Exception):
    """A fixed, content-blind refusal; carries no input or underlying error."""

    def __init__(self, status_code: int, code: str, category: str) -> None:
        super().__init__(code)
        self.status_code = status_code
        self.code = code
        self.category = category


_MESSAGES = {
    "UNAUTHORIZED": "Dashboard owner authentication is required.",
    "CAPTURE_INTAKE_CONTEXT_REQUIRED": "A current capture intake context is required.",
    "CAPTURE_INVALID": "Capture request is invalid.",
    "CAPTURE_TOO_LARGE": "Capture request is too large.",
    "CAPTURE_CONFLICT": "This capture key was already used for different content.",
    "CAPTURE_NOT_FOUND": "Capture not found.",
    "CONVERSATION_NOT_FOUND": "Conversation not found.",
    "CAPTURE_UNAVAILABLE": "Capture service is unavailable.",
    "CAPTURE_TIMEOUT": "Capture request timed out.",
    "CAPTURE_FAILED": "Capture request failed.",
}

_UNAUTHORIZED = (401, "UNAUTHORIZED", "unauthorized")
_CONTEXT_REQUIRED = (403, "CAPTURE_INTAKE_CONTEXT_REQUIRED", "intake_context_refused")
_INVALID = (422, "CAPTURE_INVALID", "invalid_input")
_TOO_LARGE = (413, "CAPTURE_TOO_LARGE", "invalid_input")
_CONFLICT = (409, "CAPTURE_CONFLICT", "mutation_conflict")
_NOT_FOUND = (404, "CAPTURE_NOT_FOUND", "not_found")
_NO_CONVERSATION = (404, "CONVERSATION_NOT_FOUND", "not_found")
_UNAVAILABLE = (503, "CAPTURE_UNAVAILABLE", "unavailable")
_TIMEOUT = (504, "CAPTURE_TIMEOUT", "timeout")
_FAILED = (500, "CAPTURE_FAILED", "internal_error")

#: Service categories mapped to fixed refusals; anything else is unavailable.
_SERVICE_REFUSALS = {
    "invalid_input": _INVALID,
    "invalid_authority": _INVALID,
    "mutation_conflict": _CONFLICT,
}


def _error(refusal: tuple[int, str, str]) -> JSONResponse:
    status_code, code, _category = refusal
    body = ErrorResponse(error=ErrorDetail(code=code, message=_MESSAGES[code]))
    return JSONResponse(
        status_code=status_code,
        content=body.model_dump(mode="json"),
        headers={"Cache-Control": "no-store"},
    )


def _record(request: Request, category: str, capture_id: uuid.UUID | None = None) -> None:
    audit = request.state.capture_audit
    audit["category"] = category
    if capture_id is not None:
        audit["capture_id"] = str(capture_id)


class CaptureRoute(APIRoute):
    """Owner gate, pre-body intake check and fixed exception projection.

    Runs inside the outer owner middleware (authentication, Origin and CSRF
    already passed) and before FastAPI parses anything. Every exit sets a fixed
    audit category; nothing here formats an exception or retains request input.
    """

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        handler = super().get_route_handler()
        door = POST_DOORS.get(self.path) if "POST" in (self.methods or ()) else None

        async def guarded(request: Request) -> Response:
            request.state.capture_audit = {"category": "pending"}
            try:
                if getattr(request.state, "owner_authority", None) is None:
                    raise _Refusal(*_UNAUTHORIZED)
                if door is not None:
                    request.state.capture_intake = _verify_context(request, door)
                async with asyncio.timeout(REQUEST_TIMEOUT_S):
                    return await handler(request)
            except _Refusal as refusal:
                _record(request, refusal.category)
                return _error((refusal.status_code, refusal.code, refusal.category))
            except CaptureUnavailable as exc:
                refusal = _SERVICE_REFUSALS.get(exc.category, _UNAVAILABLE)
                _record(request, refusal[2])
                return _error(refusal)
            except TimeoutError:
                _record(request, _TIMEOUT[2])
                return _error(_TIMEOUT)
            except RequestValidationError:
                # Never format framework errors: they carry the raw ``input``.
                _record(request, _INVALID[2])
                return _error(_INVALID)
            except HTTPException:
                # Fixed-detail framework refusals (an invalid cursor is a 400).
                _record(request, "refused")
                raise
            except asyncio.CancelledError:
                # Never synthesize success. A held receipt committed before the
                # cancellation stays recoverable through owner status.
                _record(request, "cancelled")
                raise
            except (asyncpg.PostgresError, asyncpg.InterfaceError, OSError):
                _record(request, _UNAVAILABLE[2])
                return _error(_UNAVAILABLE)
            except Exception:
                _record(request, _FAILED[2])
                return _error(_FAILED)

        return guarded


router = APIRouter(prefix="/api/general", tags=["general-capture"], route_class=CaptureRoute)


# ---------------------------------------------------------------------------
# Intake context: server-issued owner action grant
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class IntakeContext:
    door: CaptureDoor
    epoch: uuid.UUID
    issued_at: datetime
    conversation_id: uuid.UUID | None
    digest: str


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _unb64(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def _context_key(request: Request) -> bytes:
    """Process-held HMAC key; a restart retires every outstanding context."""
    state = request.app.state
    key = getattr(state, "capture_intake_key", None)
    if key is None:
        key = secrets.token_bytes(32)
        state.capture_intake_key = key
    return key


def _session_binding(request: Request) -> str:
    authority = request.state.owner_authority
    session = getattr(authority, "session_digest", None)
    label = f"session:{session}" if session else "header"
    return hashlib.sha256(label.encode()).hexdigest()


def _epoch_path(request: Request) -> Path:
    configured = getattr(request.app.state, "capture_epoch_path", None) or os.environ.get(
        EPOCH_PATH_ENV
    )
    if not configured:
        raise _Refusal(*_UNAVAILABLE)
    return Path(configured)


def _current_epoch(request: Request):
    try:
        return load_epoch(_epoch_path(request))
    except CaptureUnavailable:
        raise _Refusal(*_UNAVAILABLE) from None


def _issue_context(
    request: Request, door: CaptureDoor, conversation_id: uuid.UUID | None
) -> CaptureIntakeContext:
    epoch = _current_epoch(request)
    issued_at = datetime.now(UTC)
    expires_at = issued_at + CONTEXT_TTL
    payload = {
        "v": 1,
        "door": door,
        "epoch": str(epoch.generation),
        "iat": issued_at.isoformat(),
        "exp": expires_at.isoformat(),
        "bind": _session_binding(request),
        "conv": str(conversation_id) if conversation_id else None,
        "n": secrets.token_urlsafe(16),
    }
    body = _b64(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode())
    mac = hmac.new(_context_key(request), body.encode(), hashlib.sha256).digest()
    return CaptureIntakeContext(
        intake_context=f"{body}.{_b64(mac)}", door=door, expires_at=expires_at
    )


def _verify_context(request: Request, door: CaptureDoor) -> IntakeContext:
    """Check the intake header before the body: door, session, expiry and epoch."""
    values = request.headers.getlist(INTAKE_HEADER)
    if len(values) != 1 or len(values[0]) > 1024:
        raise _Refusal(*_CONTEXT_REQUIRED)
    token = values[0]
    try:
        body, mac = token.split(".")
        expected = hmac.new(_context_key(request), body.encode(), hashlib.sha256).digest()
        if not hmac.compare_digest(_unb64(mac), expected):
            raise ValueError
        payload = json.loads(_unb64(body))
        issued_at = datetime.fromisoformat(payload["iat"])
        expires_at = datetime.fromisoformat(payload["exp"])
        context = IntakeContext(
            door=payload["door"],
            epoch=uuid.UUID(payload["epoch"]),
            issued_at=issued_at,
            conversation_id=uuid.UUID(payload["conv"]) if payload["conv"] else None,
            digest=hashlib.sha256(token.encode()).hexdigest(),
        )
        bound = payload["v"] == 1 and hmac.compare_digest(
            payload["bind"], _session_binding(request)
        )
    except (ValueError, TypeError, KeyError, AttributeError, UnicodeError):
        raise _Refusal(*_CONTEXT_REQUIRED) from None
    epoch = _current_epoch(request)
    if (
        not bound
        or context.door != door
        or expires_at <= datetime.now(UTC)
        or context.epoch != epoch.generation
        or context.issued_at < epoch.not_before
    ):
        raise _Refusal(*_CONTEXT_REQUIRED)
    return context


def owner_principal_id() -> uuid.UUID:
    """Server-derived ledger principal for the authenticated owner."""
    return uuid.uuid5(_NAMESPACE, f"principal|{authenticated_principal()}")


def source_binding(context: IntakeContext, mutation_id: str | None) -> tuple[uuid.UUID, str]:
    """Source occurrence and ledger mutation key for one explicit owner action.

    A supplied ``mutation_id`` reuses the occurrence bound to this context's
    digest, so a transport retry returns the same receipt. Without one, every
    request is a fresh occurrence. The raw context is never stored.
    """
    if mutation_id is None:
        return uuid.uuid4(), f"{context.door}:{uuid.uuid4().hex}"
    occurrence = uuid.uuid5(_NAMESPACE, f"source|{context.door}|{context.digest}")
    key = hashlib.sha256(mutation_id.encode("utf-8")).hexdigest()
    return occurrence, f"{context.door}:{key}"


# ---------------------------------------------------------------------------
# General-role transaction
# ---------------------------------------------------------------------------


class _BoundPool:
    """Hands the service the request's one role-scoped connection.

    The service's own ``transaction()`` becomes a savepoint inside the outer
    transaction, so its receipt is durable only when the outer one commits.
    """

    def __init__(self, conn: Any) -> None:
        self._conn = conn

    @asynccontextmanager
    async def acquire(self) -> AsyncIterator[Any]:
        yield self._conn

    async def fetchval(self, *args: Any) -> Any:
        return await self._conn.fetchval(*args)


def _general_pool(db: DatabaseManager | None) -> Any:
    if db is None:
        raise _Refusal(*_UNAVAILABLE)
    try:
        return db.pool("general")
    except Exception:
        raise _Refusal(*_UNAVAILABLE) from None


@asynccontextmanager
async def general_transaction(db: DatabaseManager | None) -> AsyncIterator[Any]:
    """One General-role transaction; SET LOCAL ends with it on every exit."""
    pool = _general_pool(db)
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute(f"SET LOCAL ROLE {GENERAL_ROLE}")
        await conn.execute("SET LOCAL search_path TO public")
        if await conn.fetchval("SELECT current_user") != GENERAL_ROLE:
            raise _Refusal(*_UNAVAILABLE)
        yield conn


def _capture_service():
    return roster_module("general", "tools/capture_service").CaptureService


def _status(row: Any) -> CaptureStatus:
    door = (row["mutation_key"] or "").partition(":")[0]
    receipt = row["receipt"]
    target = None
    if isinstance(receipt, dict):
        target = CaptureTarget(
            owner=receipt["owner"],
            operation_id=receipt["operation_id"],
            item_id=receipt["item_id"],
            version=receipt["version"],
        )
    return CaptureStatus.model_validate(
        {
            "capture_id": row["id"],
            "disposition": row["disposition"],
            "category": row["category"],
            "source_door": door if door in _SOURCE_DOORS else "internal",
            "target": target,
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }
    )


def _ok(payload: Any, status_code: int = 200) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content=payload.model_dump(mode="json"),
        headers={"Cache-Control": "no-store"},
    )


async def _read_intake(request: Request) -> CaptureIntakeRequest:
    """Read a bounded body and validate it without echoing input."""
    if request.url.query:
        raise _Refusal(*_INVALID)
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk[: MAX_BODY_BYTES + 1 - len(raw)])
        if len(raw) > MAX_BODY_BYTES:
            raise _Refusal(*_TOO_LARGE)
    try:
        return CaptureIntakeRequest.model_validate(json.loads(bytes(raw).decode("utf-8")))
    except (UnicodeError, ValueError, RecursionError, ValidationError):
        raise _Refusal(*_INVALID) from None


async def _admit(request: Request, db: DatabaseManager | None) -> JSONResponse:
    context: IntakeContext = request.state.capture_intake
    body = await _read_intake(request)
    references = [ref.model_dump(mode="json") for ref in body.references]
    _canonical, digest = canonical_intake(body.text, references)
    occurrence, mutation_key = source_binding(context, body.mutation_id)
    authority = VerifiedAuthority(
        principal_id=owner_principal_id(),
        source_occurrence=occurrence,
        source_occurred_at=context.issued_at,
        service_epoch=context.epoch,
        payload_digest=digest,
    )
    epoch_path = _epoch_path(request)
    async with general_transaction(db) as conn:
        service = _capture_service()(_BoundPool(conn), epoch_path)
        receipt = await service.admit(
            authority, body.text, references=references, mutation_key=mutation_key
        )
        row = await conn.fetchrow(
            f"SELECT {_STATUS_COLUMNS} FROM public.captures WHERE id=$1", receipt.capture_id
        )
    # Committed: the outer transaction has exited without error.
    status = _status(row)
    _record(request, status.category, status.capture_id)
    return _ok(ApiResponse[CaptureStatus](data=status), 202)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get(
    "/capture-intake-context",
    response_model=ApiResponse[CaptureIntakeContext],
    responses={403: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
)
async def get_capture_intake_context(
    request: Request,
    *,
    db: OptionalDb,
    door: str = Query("dashboard"),
    conversation_id: str | None = Query(None),
) -> Any:
    """Issue a bounded current-epoch owner action context for one intake door.

    It carries no source payload, model/session or target-operation authority.
    A chat context may bind one owner-verified conversation, kept opaque.
    """
    if door not in _SOURCE_DOORS:
        raise _Refusal(*_INVALID)
    conversation: uuid.UUID | None = None
    if conversation_id is not None:
        if door != "chat":
            raise _Refusal(*_INVALID)
        try:
            conversation = uuid.UUID(conversation_id)
        except ValueError:
            raise _Refusal(*_NO_CONVERSATION) from None
        if db is None:
            raise _Refusal(*_UNAVAILABLE)
        exists = await db.credential_shared_pool().fetchval(
            "SELECT 1 FROM public.dashboard_conversations WHERE id = $1", conversation
        )
        if not exists:
            raise _Refusal(*_NO_CONVERSATION)
    context = _issue_context(request, door, conversation)  # type: ignore[arg-type]
    return _ok(ApiResponse[CaptureIntakeContext](data=context))


@router.post(
    "/captures",
    status_code=202,
    response_model=ApiResponse[CaptureStatus],
    responses={
        403: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    },
)
async def admit_capture(request: Request, *, db: OptionalDb) -> Any:
    """Admit one explicit owner capture; returns held only after commit."""
    return await _admit(request, db)


@router.post(
    "/capture-chat",
    status_code=202,
    response_model=ApiResponse[CaptureStatus],
    responses={
        403: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    },
)
async def admit_chat_capture(request: Request, *, db: OptionalDb) -> Any:
    """Explicit chat capture bridge: its own source door, never conversation submit."""
    return await _admit(request, db)


@router.get(
    "/captures",
    response_model=Page[CaptureStatus],
    responses={400: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
)
async def list_captures(
    *,
    db: OptionalDb,
    limit: int = Query(20, ge=1, le=50),
    cursor: str | None = Query(None),
) -> Any:
    """Bounded keyset list of the owner's captures, newest first; never false empty."""
    position = decode_cursor(cursor, (datetime, uuid.UUID), scope=CURSOR_SCOPE) if cursor else None
    async with general_transaction(db) as conn:
        if position is None:
            rows = await conn.fetch(
                f"SELECT {_STATUS_COLUMNS} FROM public.captures WHERE principal_id=$1 "
                "ORDER BY created_at DESC, id DESC LIMIT $2",
                owner_principal_id(),
                limit + 1,
            )
        else:
            rows = await conn.fetch(
                f"SELECT {_STATUS_COLUMNS} FROM public.captures WHERE principal_id=$1 "
                "AND (created_at, id) < ($3, $4) ORDER BY created_at DESC, id DESC LIMIT $2",
                owner_principal_id(),
                limit + 1,
                *position,
            )
    page = rows[:limit]
    next_cursor = (
        encode_cursor((page[-1]["created_at"], page[-1]["id"]), scope=CURSOR_SCOPE)
        if len(rows) > limit
        else None
    )
    return _ok(
        Page[CaptureStatus](items=[_status(r) for r in page], next_cursor=next_cursor, limit=limit)
    )


@router.get(
    "/captures/{capture_id}",
    response_model=ApiResponse[CaptureStatus],
    responses={404: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
)
async def get_capture(capture_id: str, *, db: OptionalDb) -> Any:
    """One owner capture's content-free status; absent and foreign are one 404."""
    try:
        parsed = uuid.UUID(capture_id)
    except ValueError:
        raise _Refusal(*_NOT_FOUND) from None
    async with general_transaction(db) as conn:
        row = await conn.fetchrow(
            f"SELECT {_STATUS_COLUMNS} FROM public.captures WHERE id=$1 AND principal_id=$2",
            parsed,
            owner_principal_id(),
        )
    if row is None:
        raise _Refusal(*_NOT_FOUND)
    return _ok(ApiResponse[CaptureStatus](data=_status(row)))
