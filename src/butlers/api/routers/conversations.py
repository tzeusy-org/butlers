"""Dashboard conversation API endpoints with SSE streaming.

Provides:

- ``router`` — butler-scoped conversation endpoints under
  ``/api/butlers/{name}/conversations``

Endpoints
---------
GET  /api/butlers/{name}/conversations
    List conversations with status filter and pagination.

POST /api/butlers/{name}/conversations
    Create a new conversation with the first user message.
    Response: SSE stream with ``conversation_created``, ``token``,
    ``message_complete``, and ``done`` events.

GET  /api/butlers/{name}/conversations/search
    Full-text search across conversation messages.

GET  /api/butlers/{name}/conversations/summary
    Aggregate statistics for all conversations of a butler.

GET  /api/butlers/{name}/conversations/{conversation_id}/messages
    List messages in a conversation with pagination.

POST /api/butlers/{name}/conversations/{conversation_id}/messages
    Send a follow-up message to an existing conversation.
    Response: SSE stream with ``token``, ``message_complete``, and
    ``done`` events.

PATCH /api/butlers/{name}/conversations/{conversation_id}
    Update conversation title or status (archive/unarchive).

SSE event types
---------------
``conversation_created``
    First event on POST /conversations. Data: ``{conversation_id, title}``.
``token``
    Streamed assistant response token. Data: ``{content}``.
``message_complete``
    Final assistant message with attribution. Data:
    ``{message_id, model_name, input_tokens, output_tokens, duration_ms,
    tool_calls}``.
``error``
    Session failure. Data: ``{code, message}``.
``done``
    Stream terminator — always sent as the last event.
``keepalive`` (comment)
    Sent as ``: keepalive`` after 15 s of silence to prevent timeout.

Discretion bypass
-----------------
Dashboard messages are always operator-intentional and are never subject
to connector-level discretion evaluation.  The ``"dashboard"`` channel is
registered in ``DISCRETION_BYPASS_CHANNELS`` (see
``butlers.connectors.discretion``).
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncGenerator
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from starlette.requests import Request
from starlette.responses import StreamingResponse

from butlers.api.conversation_envelope import build_dashboard_envelope
from butlers.api.conversations import (
    conversation_create,
    conversation_get,
    conversation_list,
    conversation_message_count_increment,
    conversation_search,
    conversation_summary,
    conversation_unarchive_if_needed,
    conversation_update,
    conversation_update_aggregates,
    message_create,
    message_list,
)
from butlers.api.db import DatabaseManager
from butlers.api.models import PaginatedResponse, PaginationMeta
from butlers.api.models.conversation import (
    ConversationCreateRequest,
    ConversationMessage,
    ConversationSearchResult,
    ConversationStats,
    ConversationSummary,
    ConversationUpdateRequest,
    MessageCreateRequest,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/butlers", tags=["conversations"])

# SSE keepalive interval in seconds
_KEEPALIVE_INTERVAL_S: float = 15.0

# Polling interval for session completion (seconds)
_POLL_INTERVAL_S: float = 0.5

# Maximum wait time for session completion (seconds)
_SESSION_TIMEOUT_S: float = 300.0


def _get_db_manager() -> DatabaseManager:
    """Dependency stub — overridden at app startup or in tests."""
    raise RuntimeError("DatabaseManager not initialized")


# ---------------------------------------------------------------------------
# SSE helpers
# ---------------------------------------------------------------------------


def _sse_event(event_type: str, data: dict[str, Any]) -> str:
    """Format a named SSE event."""
    return f"event: {event_type}\ndata: {json.dumps(data)}\n\n"


def _sse_comment(text: str) -> str:
    """Format an SSE comment (keepalive)."""
    return f": {text}\n\n"


def _sse_error(code: str, message: str) -> str:
    """Format an SSE error event."""
    return _sse_event("error", {"code": code, "message": message})


def _sse_done() -> str:
    """Format the SSE done event (stream terminator)."""
    return _sse_event("done", {})


# ---------------------------------------------------------------------------
# Mock session runner
# ---------------------------------------------------------------------------
# NOTE: Real session dispatch will be wired when the Switchboard ingest
# call path is integrated (bu-72zr, bu-4m6i). For now, the router builds
# the envelope correctly and stubs the streaming response so the API shape
# is correct and testable without a live Switchboard.


async def _submit_to_switchboard(
    butler_name: str,
    envelope: dict[str, Any],
    *,
    db: DatabaseManager,
) -> dict[str, Any] | None:
    """Submit an ingest.v1 envelope to the Switchboard butler via MCP.

    Returns the accepted response dict, or None if the Switchboard is
    unavailable.  Failures are non-fatal; the caller streams an error event.
    """
    # TODO(bu-27mx): Wire to Switchboard MCP ingest tool once bu-4m6i lands.
    # For now, log the envelope and return a stub response so the API shape
    # is testable end-to-end without a running Switchboard.
    logger.info(
        "Dashboard envelope queued for %s: conv=%s msg=%s",
        butler_name,
        envelope["source"]["endpoint_identity"],
        envelope["event"]["external_event_id"],
    )
    return {
        "request_id": envelope["event"]["external_event_id"],
        "status": "accepted",
    }


# ---------------------------------------------------------------------------
# SSE generator
# ---------------------------------------------------------------------------


async def _stream_conversation_response(
    *,
    request: Request,
    butler_name: str,
    conversation_id: UUID,
    message_id: UUID,
    envelope: dict[str, Any],
    db: DatabaseManager,
    is_new_conversation: bool = False,
    conversation_title: str = "",
) -> AsyncGenerator[str, None]:
    """Generate SSE events for a conversation message submission.

    Lifecycle:
    1. Optionally emit ``conversation_created`` (for new conversations).
    2. Submit the ingest envelope to the Switchboard.
    3. Poll for session completion, streaming keepalives every 15 s.
    4. On completion, persist the assistant message and emit
       ``message_complete`` + ``done``.
    5. On error, emit ``error`` + ``done``.
    """
    # Step 1: conversation_created event (new conversations only)
    if is_new_conversation:
        yield _sse_event(
            "conversation_created",
            {"conversation_id": str(conversation_id), "title": conversation_title},
        )

    # Step 2: Submit to Switchboard
    try:
        accepted = await _submit_to_switchboard(butler_name, envelope, db=db)
    except Exception as exc:
        logger.exception(
            "Switchboard submission failed for conversation %s: %s",
            conversation_id,
            exc,
        )
        yield _sse_error("SWITCHBOARD_ERROR", str(exc))
        yield _sse_done()
        return

    if accepted is None:
        yield _sse_error("SWITCHBOARD_UNAVAILABLE", "Switchboard butler is not available")
        yield _sse_done()
        return

    request_id_str = accepted.get("request_id")

    # Step 3: Poll for session completion with keepalive
    # NOTE: Real streaming tokens would come from the Switchboard MCP stream.
    # For now, we emit a single placeholder and then message_complete once
    # the poll succeeds.  The SSE contract is correct; the streaming content
    # will be filled in when session streaming is wired.
    start_ts = time.monotonic()
    last_keepalive_ts = start_ts
    session_completed = False
    session_result: dict[str, Any] = {}

    while not session_completed:
        # Check client disconnect
        if await request.is_disconnected():
            logger.info("Client disconnected during conversation stream %s", conversation_id)
            return

        # Keepalive check
        now = time.monotonic()
        if now - last_keepalive_ts >= _KEEPALIVE_INTERVAL_S:
            yield _sse_comment("keepalive")
            last_keepalive_ts = now

        # Timeout guard
        if now - start_ts >= _SESSION_TIMEOUT_S:
            logger.warning("Session timeout waiting for conversation %s", conversation_id)
            yield _sse_error("SESSION_TIMEOUT", "Response timed out")
            yield _sse_done()
            return

        # Poll the DB for a completed session linked to this request_id.
        # When real SSE streaming is available, this loop is replaced by
        # direct token streaming from the adapter.
        session_result = await _poll_session_completion(
            db=db,
            butler_name=butler_name,
            request_id=request_id_str,
        )
        if session_result.get("completed"):
            session_completed = True
        else:
            await asyncio.sleep(_POLL_INTERVAL_S)

    # Step 4: Persist assistant message and emit events
    pool = db.credential_shared_pool()

    result_text: str = session_result.get("result", "")
    model_name: str | None = session_result.get("model")
    input_tokens: int | None = session_result.get("input_tokens")
    output_tokens: int | None = session_result.get("output_tokens")
    duration_ms: int | None = session_result.get("duration_ms")
    tool_calls: list[dict[str, Any]] | None = session_result.get("tool_calls")
    session_id: UUID | None = session_result.get("session_id")
    error_text: str | None = session_result.get("error")

    # Emit the result as a token event
    if result_text:
        yield _sse_event("token", {"content": result_text})

    # Persist assistant message
    try:
        assistant_msg = await message_create(
            pool,
            conversation_id=conversation_id,
            role="assistant",
            content=result_text or "",
            session_id=session_id,
            model_name=model_name,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            duration_ms=duration_ms,
            tool_calls=tool_calls,
            error=error_text,
            request_id=UUID(request_id_str) if request_id_str else None,
        )

        # Update conversation aggregates
        await conversation_update_aggregates(
            pool,
            conversation_id,
            input_tokens=input_tokens or 0,
            output_tokens=output_tokens or 0,
            duration_ms=duration_ms or 0,
        )

        yield _sse_event(
            "message_complete",
            {
                "message_id": str(assistant_msg["id"]),
                "model_name": model_name,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "duration_ms": duration_ms,
                "tool_calls": tool_calls or [],
            },
        )

        if error_text:
            yield _sse_error("SESSION_FAILED", error_text)

    except Exception as exc:
        logger.exception(
            "Failed to persist assistant message for conversation %s: %s",
            conversation_id,
            exc,
        )
        yield _sse_error("PERSISTENCE_ERROR", str(exc))

    yield _sse_done()


async def _poll_session_completion(
    *,
    db: DatabaseManager,
    butler_name: str,
    request_id: str | None,
) -> dict[str, Any]:
    """Poll the DB for a completed session linked to a request_id.

    Returns a dict with ``completed=True`` and session metadata when a
    matching session is found; returns ``{"completed": False}`` otherwise.

    NOTE: This stub always returns completed immediately with empty results.
    Real implementation queries the butler's sessions table filtered by
    request_id.
    """
    # Stub: return completed immediately.
    # Real implementation will query:
    #   SELECT id, result, model, input_tokens, output_tokens, duration_ms,
    #          tool_calls, success, error
    #   FROM <butler_schema>.sessions
    #   WHERE request_id = $1 AND completed_at IS NOT NULL
    #   LIMIT 1
    return {
        "completed": True,
        "result": "",
        "model": None,
        "input_tokens": None,
        "output_tokens": None,
        "duration_ms": None,
        "tool_calls": None,
        "session_id": None,
        "error": None,
    }


# ---------------------------------------------------------------------------
# GET /api/butlers/{name}/conversations
# ---------------------------------------------------------------------------


@router.get("/{name}/conversations", response_model=PaginatedResponse[ConversationSummary])
async def list_conversations(
    name: str,
    status: str = Query("active", description="Filter by status: 'active', 'archived', or 'all'"),
    limit: int = Query(20, ge=1, le=100, description="Max records to return"),
    offset: int = Query(0, ge=0, description="Number of records to skip"),
    db: DatabaseManager = Depends(_get_db_manager),
) -> PaginatedResponse[ConversationSummary]:
    """List conversations for a butler with optional status filter.

    Conversations are ordered by ``updated_at DESC``.  The ``status``
    parameter accepts ``active`` (default), ``archived``, or ``all``.
    """
    try:
        pool = db.credential_shared_pool()
    except (KeyError, RuntimeError) as exc:
        raise HTTPException(status_code=503, detail=f"Shared database unavailable: {exc}") from exc

    rows, total = await conversation_list(
        pool, butler_name=name, status=status, limit=limit, offset=offset
    )

    conversations = [ConversationSummary(**row) for row in rows]

    return PaginatedResponse[ConversationSummary](
        data=conversations,
        meta=PaginationMeta(total=total, offset=offset, limit=limit),
    )


# ---------------------------------------------------------------------------
# GET /api/butlers/{name}/conversations/search
# ---------------------------------------------------------------------------


@router.get("/{name}/conversations/search")
async def search_conversations(
    name: str,
    q: str | None = Query(None, description="Search query string"),
    limit: int = Query(20, ge=1, le=100, description="Max records to return"),
    offset: int = Query(0, ge=0, description="Number of records to skip"),
    db: DatabaseManager = Depends(_get_db_manager),
) -> PaginatedResponse[ConversationSearchResult]:
    """Search conversations by message content.

    Returns conversations whose messages contain the search term, ordered
    by most recent match first.  Each result includes a ``snippet`` with
    the matching message content (truncated to 200 characters).

    Returns 400 when ``q`` is empty or missing.
    """
    if not q or not q.strip():
        raise HTTPException(
            status_code=400,
            detail={"code": "VALIDATION_ERROR", "message": "Search query 'q' is required"},
        )

    try:
        pool = db.credential_shared_pool()
    except (KeyError, RuntimeError) as exc:
        raise HTTPException(status_code=503, detail=f"Shared database unavailable: {exc}") from exc

    rows, total = await conversation_search(
        pool, butler_name=name, query=q.strip(), limit=limit, offset=offset
    )

    results = [ConversationSearchResult(**row) for row in rows]

    return PaginatedResponse[ConversationSearchResult](
        data=results,
        meta=PaginationMeta(total=total, offset=offset, limit=limit),
    )


# ---------------------------------------------------------------------------
# GET /api/butlers/{name}/conversations/summary
# ---------------------------------------------------------------------------


@router.get("/{name}/conversations/summary")
async def get_conversation_summary(
    name: str,
    db: DatabaseManager = Depends(_get_db_manager),
) -> ConversationStats:
    """Return aggregate conversation statistics for a butler."""
    try:
        pool = db.credential_shared_pool()
    except (KeyError, RuntimeError) as exc:
        raise HTTPException(status_code=503, detail=f"Shared database unavailable: {exc}") from exc

    stats = await conversation_summary(pool, butler_name=name)
    return ConversationStats(**stats)


# ---------------------------------------------------------------------------
# POST /api/butlers/{name}/conversations
# ---------------------------------------------------------------------------


@router.post("/{name}/conversations")
async def create_conversation(
    name: str,
    body: ConversationCreateRequest,
    request: Request,
    db: DatabaseManager = Depends(_get_db_manager),
) -> StreamingResponse:
    """Create a new conversation with the first user message.

    Returns a Server-Sent Events stream.  The first event is
    ``conversation_created`` with the new ``conversation_id`` and ``title``.
    Subsequent events follow the standard SSE streaming pattern.

    Dashboard messages bypass connector discretion evaluation — they are
    always operator-intentional (see ``DISCRETION_BYPASS_CHANNELS``).
    """
    try:
        pool = db.credential_shared_pool()
    except (KeyError, RuntimeError) as exc:
        raise HTTPException(status_code=503, detail=f"Shared database unavailable: {exc}") from exc

    # Create conversation record
    conv = await conversation_create(pool, butler_name=name, first_message=body.message)
    conversation_id: UUID = conv["id"]

    # Persist user message
    user_msg = await message_create(
        pool,
        conversation_id=conversation_id,
        role="user",
        content=body.message,
    )

    # Increment conversation message count for the user message
    await conversation_message_count_increment(pool, conversation_id)

    # Build ingest envelope
    envelope = build_dashboard_envelope(
        conversation_id=conversation_id,
        message_id=user_msg["id"],
        message_text=body.message,
        conversation_context=None,
    )

    async def _generate() -> AsyncGenerator[str, None]:
        async for chunk in _stream_conversation_response(
            request=request,
            butler_name=name,
            conversation_id=conversation_id,
            message_id=user_msg["id"],
            envelope=envelope,
            db=db,
            is_new_conversation=True,
            conversation_title=conv["title"],
        ):
            yield chunk

    return StreamingResponse(
        _generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ---------------------------------------------------------------------------
# GET /api/butlers/{name}/conversations/{conversation_id}/messages
# ---------------------------------------------------------------------------


@router.get(
    "/{name}/conversations/{conversation_id}/messages",
    response_model=PaginatedResponse[ConversationMessage],
)
async def list_messages(
    name: str,
    conversation_id: UUID,
    limit: int = Query(50, ge=1, le=200, description="Max records to return"),
    offset: int = Query(0, ge=0, description="Number of records to skip"),
    db: DatabaseManager = Depends(_get_db_manager),
) -> PaginatedResponse[ConversationMessage]:
    """List messages in a conversation ordered by ``created_at ASC``.

    Returns 404 when the conversation does not exist or belongs to a
    different butler.
    """
    try:
        pool = db.credential_shared_pool()
    except (KeyError, RuntimeError) as exc:
        raise HTTPException(status_code=503, detail=f"Shared database unavailable: {exc}") from exc

    # Verify conversation belongs to this butler
    conv = await conversation_get(pool, conversation_id, butler_name=name)
    if conv is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "CONVERSATION_NOT_FOUND", "message": "Conversation not found"},
        )

    rows, total = await message_list(pool, conversation_id, limit=limit, offset=offset)
    messages = [ConversationMessage(**row) for row in rows]

    return PaginatedResponse[ConversationMessage](
        data=messages,
        meta=PaginationMeta(total=total, offset=offset, limit=limit),
    )


# ---------------------------------------------------------------------------
# POST /api/butlers/{name}/conversations/{conversation_id}/messages
# ---------------------------------------------------------------------------


@router.post("/{name}/conversations/{conversation_id}/messages")
async def send_message(
    name: str,
    conversation_id: UUID,
    body: MessageCreateRequest,
    request: Request,
    db: DatabaseManager = Depends(_get_db_manager),
) -> StreamingResponse:
    """Send a follow-up message in an existing conversation.

    Returns a Server-Sent Events stream using the same token/message_complete
    pattern as conversation creation, without the ``conversation_created`` event.

    If the conversation is archived, it is automatically reactivated.
    Returns 404 when the conversation does not exist or belongs to a
    different butler.
    """
    try:
        pool = db.credential_shared_pool()
    except (KeyError, RuntimeError) as exc:
        raise HTTPException(status_code=503, detail=f"Shared database unavailable: {exc}") from exc

    # Verify conversation belongs to this butler
    conv = await conversation_get(pool, conversation_id, butler_name=name)
    if conv is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "CONVERSATION_NOT_FOUND", "message": "Conversation not found"},
        )

    # Reactivate archived conversations
    await conversation_unarchive_if_needed(pool, conversation_id, butler_name=name)

    # Fetch conversation history for context (up to last 10 exchange pairs = 20 msgs)
    history_rows, _ = await message_list(pool, conversation_id, limit=20, offset=0)

    # Persist user message
    user_msg = await message_create(
        pool,
        conversation_id=conversation_id,
        role="user",
        content=body.message,
    )

    # Increment conversation message count for the user message
    await conversation_message_count_increment(pool, conversation_id)

    # Build ingest envelope with conversation context
    envelope = build_dashboard_envelope(
        conversation_id=conversation_id,
        message_id=user_msg["id"],
        message_text=body.message,
        conversation_context=history_rows,
    )

    async def _generate() -> AsyncGenerator[str, None]:
        async for chunk in _stream_conversation_response(
            request=request,
            butler_name=name,
            conversation_id=conversation_id,
            message_id=user_msg["id"],
            envelope=envelope,
            db=db,
            is_new_conversation=False,
        ):
            yield chunk

    return StreamingResponse(
        _generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ---------------------------------------------------------------------------
# PATCH /api/butlers/{name}/conversations/{conversation_id}
# ---------------------------------------------------------------------------


@router.patch(
    "/{name}/conversations/{conversation_id}",
    response_model=ConversationSummary,
)
async def update_conversation(
    name: str,
    conversation_id: UUID,
    body: ConversationUpdateRequest,
    db: DatabaseManager = Depends(_get_db_manager),
) -> ConversationSummary:
    """Update a conversation's title or status.

    Both ``title`` and ``status`` are optional; at least one must be provided.
    Returns 404 when the conversation does not exist or belongs to a
    different butler.
    """
    if body.title is None and body.status is None:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "VALIDATION_ERROR",
                "message": "At least one of 'title' or 'status' must be provided",
            },
        )

    try:
        pool = db.credential_shared_pool()
    except (KeyError, RuntimeError) as exc:
        raise HTTPException(status_code=503, detail=f"Shared database unavailable: {exc}") from exc

    updated = await conversation_update(
        pool,
        conversation_id,
        butler_name=name,
        title=body.title,
        status=body.status,
    )

    if updated is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "CONVERSATION_NOT_FOUND", "message": "Conversation not found"},
        )

    return ConversationSummary(**updated)
