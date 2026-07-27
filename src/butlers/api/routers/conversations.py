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
    Substring search across conversation messages (case-insensitive ILIKE).

GET  /api/butlers/{name}/conversations/summary
    Aggregate statistics for all conversations of a butler.

GET  /api/butlers/{name}/conversations/{conversation_id}/messages
    List messages in a conversation with pagination.

POST /api/butlers/{name}/conversations/{conversation_id}/messages
    Send a follow-up message to an existing conversation.
    Response: SSE stream with ``token``, ``message_complete``, and
    ``done`` events.

POST /api/butlers/{name}/conversations/{conversation_id}/cancel
    Cancel the in-flight session behind the conversation's current turn
    (the chat "Stop" button) -- a real terminate of the routed butler's
    runtime subprocess, not a client-side stream detach. Always 200; see
    ``ConversationCancelResponse`` for the cancelled/already_finished/failed
    outcomes.

PATCH /api/butlers/{name}/conversations/{conversation_id}
    Update conversation title or status (archive/unarchive).

SSE event types
---------------
``conversation_created``
    First event on POST /conversations. Data: ``{conversation_id, title}``.
``token``
    Streamed assistant response token. Data: ``{content}``.
``message_complete``
    The routed butler's ``conversation_reply`` message, with attribution.
    Data: ``{message_id, model_name, input_tokens, output_tokens,
    duration_ms, tool_calls}``. ``model_name``/token/duration fields are
    ``null`` — the reply is persisted mid-session, before the spawned
    session's own accounting is known (see ``_stream_conversation_response``).
``error``
    Session failure. Data: ``{code, message}`` (``SESSION_TIMEOUT`` also
    carries ``session_id``, non-null when the routed session could be
    identified). ``code`` is one of ``SWITCHBOARD_UNAVAILABLE`` (MCP
    unreachable — message already persisted; a retry re-submits the same
    content and is deduplicated idempotently at the Switchboard ingest
    boundary), ``INGEST_REJECTED`` (deterministic envelope rejection, e.g.
    an invalid ``pinned_target`` — retrying the same envelope will not
    help), ``SWITCHBOARD_ERROR`` (unexpected submission failure), or
    ``SESSION_TIMEOUT`` (no ``conversation_reply`` arrived within the poll
    window — the routed session may still reply late; the thread stays
    open and a late reply is visible on next fetch/poll).
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
from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from fastmcp.exceptions import ToolError
from starlette.requests import Request
from starlette.responses import StreamingResponse

from butlers.api.conversation_envelope import build_dashboard_envelope
from butlers.api.conversations import (
    conversation_create,
    conversation_get,
    conversation_list,
    conversation_message_count_increment,
    conversation_search,
    conversation_set_routed_butler,
    conversation_summary,
    conversation_unarchive_if_needed,
    conversation_update,
    message_create,
    message_create_idempotent,
    message_find_reply_since,
    message_get_by_id,
    message_list,
)
from butlers.api.db import DatabaseManager
from butlers.api.deps import ButlerUnreachableError, MCPClientManager, get_mcp_manager
from butlers.api.models import PaginatedResponse, PaginationMeta
from butlers.api.models.conversation import (
    ConversationCancelResponse,
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

# Timeout for the Switchboard MCP ingest call
_MCP_DISPATCH_TIMEOUT_S: float = 30.0

# Staffer butler that owns message classification for the dashboard chat
# widget — conversations addressed to it are never pinned so its own
# classify -> route pipeline can pick the target butler.
_SWITCHBOARD_BUTLER: str = "switchboard"

# conversation_id -> {"routed_butler": str, "request_id": str} for the turn
# currently streaming a reply. Process-local (the dashboard API runs as a
# single uvicorn worker, no `workers=` override in cli.py) -- POST
# .../cancel resolves through this to find the live session to kill without
# a DB round trip or plumbing request_id through the SSE payload. Populated
# in _stream_conversation_response, cleared at every exit from that turn.
_ACTIVE_TURNS: dict[UUID, dict[str, str]] = {}


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


def _sse_error(code: str, message: str, *, session_id: UUID | None = None) -> str:
    """Format an SSE error event.  ``session_id`` is included only when given."""
    data: dict[str, Any] = {"code": code, "message": message}
    if session_id is not None:
        data["session_id"] = str(session_id)
    return _sse_event("error", data)


def _sse_done() -> str:
    """Format the SSE done event (stream terminator)."""
    return _sse_event("done", {})


# ---------------------------------------------------------------------------
# Switchboard MCP dispatch
# ---------------------------------------------------------------------------


def _first_json_block(mcp_result: Any) -> Any:
    """Return the first JSON-decoded text block from an MCP tool result, or None.

    Falls back to ``{"value": <text>}`` for a non-JSON text block. Note the
    decoded value may be any JSON type, not necessarily a dict — callers that
    expect an object must ``isinstance``-guard the result.
    """
    content = getattr(mcp_result, "content", None)
    if not content:
        return None
    for block in content:
        if hasattr(block, "text"):
            try:
                return json.loads(block.text)
            except (json.JSONDecodeError, TypeError):
                return {"value": block.text}
    return None


async def _submit_to_switchboard(
    butler_name: str,
    envelope: dict[str, Any],
    *,
    mcp_mgr: MCPClientManager,
) -> dict[str, Any] | None:
    """Submit an ingest.v1 envelope to the Switchboard butler via MCP.

    Returns the accepted response dict (``request_id``, ``status``,
    ``duplicate``, ``triage_decision``, ``triage_target``), or ``None`` if the
    Switchboard MCP server is unreachable — a non-fatal, retryable condition
    the caller surfaces as an SSE error.

    Raises
    ------
    ValueError
        If the Switchboard ingest tool rejected the envelope (e.g. an invalid
        ``pinned_target``) or returned an unexpected response shape. This is
        a deterministic rejection, not a connectivity failure, so the caller
        surfaces it distinctly rather than inviting a retry.
    """
    try:
        client = await asyncio.wait_for(
            mcp_mgr.get_client(_SWITCHBOARD_BUTLER), timeout=_MCP_DISPATCH_TIMEOUT_S
        )
        mcp_result = await asyncio.wait_for(
            client.call_tool("ingest", envelope), timeout=_MCP_DISPATCH_TIMEOUT_S
        )
    except (ButlerUnreachableError, TimeoutError, OSError) as exc:
        logger.warning(
            "Switchboard unreachable while submitting dashboard envelope for %s: %s",
            butler_name,
            exc,
        )
        return None

    result = _first_json_block(mcp_result)
    if mcp_result.is_error or (isinstance(result, dict) and result.get("status") == "error"):
        error_msg = (
            result.get("error") if isinstance(result, dict) else None
        ) or "Switchboard ingest tool returned an error"
        raise ValueError(error_msg)

    if not isinstance(result, dict) or "request_id" not in result:
        raise ValueError("Switchboard ingest tool returned an unexpected response shape")

    logger.info(
        "Dashboard envelope submitted for %s: conv=%s msg=%s request_id=%s",
        butler_name,
        envelope["source"]["endpoint_identity"],
        envelope["event"]["external_event_id"],
        result.get("request_id"),
    )
    return result


# ---------------------------------------------------------------------------
# SSE generator
# ---------------------------------------------------------------------------


async def _stream_conversation_response(
    *,
    request: Request,
    butler_name: str,
    conversation_id: UUID,
    message_created_at: datetime,
    envelope: dict[str, Any],
    db: DatabaseManager,
    mcp_mgr: MCPClientManager,
    is_new_conversation: bool = False,
    conversation_title: str = "",
) -> AsyncGenerator[str, None]:
    """Generate SSE events for a conversation message submission.

    Lifecycle:
    1. Optionally emit ``conversation_created`` (for new conversations).
    2. Submit the ingest envelope to the Switchboard; stamp sticky
       ``routed_butler`` on first classification route (widget conversations
       only — pinned conversations are already deterministic), then emit a
       truthful ``dispatch_accepted`` routing receipt before polling.
    3. Poll for the routed butler's ``conversation_reply`` message, streaming
       keepalives every 15 s.
    4. On arrival, emit ``message_complete`` + ``done`` for the (already
       persisted) reply — no further DB write happens here.
    5. On error or timeout, emit ``error`` + ``done``.
    """
    # Step 1: conversation_created event (new conversations only)
    if is_new_conversation:
        yield _sse_event(
            "conversation_created",
            {"conversation_id": str(conversation_id), "title": conversation_title},
        )

    # Step 2: Submit to Switchboard
    try:
        accepted = await _submit_to_switchboard(butler_name, envelope, mcp_mgr=mcp_mgr)
    except ValueError as exc:
        # Deterministic envelope rejection (e.g. invalid pinned_target) — a
        # retry with the same envelope would fail the same way, so this is
        # surfaced distinctly from a transient connectivity failure.
        logger.warning(
            "Switchboard rejected dashboard envelope for conversation %s: %s",
            conversation_id,
            exc,
        )
        yield _sse_error("INGEST_REJECTED", str(exc))
        yield _sse_done()
        return
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
        # The user message is already persisted (step 0, before this
        # generator started) — a client retry re-submits its stable message
        # identity, so Switchboard deduplicates by external event ID even if
        # the retry crosses an hourly content-hash bucket or context changes.
        yield _sse_error("SWITCHBOARD_UNAVAILABLE", "Switchboard offline — retry")
        yield _sse_done()
        return

    request_id_str = accepted.get("request_id")
    triage_decision = accepted.get("triage_decision")
    triage_target = accepted.get("triage_target")
    routed_this_turn = triage_decision == "route_to" and bool(triage_target)
    shared_pool = db.credential_shared_pool()

    # Sticky routing (bu-p6ey8.1): a classification-routed (Switchboard
    # widget) conversation stamps its first successful route target so
    # follow-ups can bypass classification entirely (see send_message).
    # Pinned per-butler conversations are already deterministic and never
    # reach this branch (butler_name != _SWITCHBOARD_BUTLER there).
    if butler_name == _SWITCHBOARD_BUTLER and routed_this_turn:
        try:
            await conversation_set_routed_butler(
                shared_pool, conversation_id, routed_butler=triage_target
            )
        except Exception:
            # Non-fatal — sticky routing is a follow-up convenience, not
            # required for this turn's reply to work.
            logger.warning(
                "Failed to stamp routed_butler=%s on conversation %s",
                triage_target,
                conversation_id,
                exc_info=True,
            )

    # The butler whose `sessions` row we'd consult for a timeout's
    # "inspect session" link — the classification target when routed this
    # turn, otherwise the pinned/addressed butler itself.
    routed_butler = triage_target if routed_this_turn else butler_name

    # Register this turn as cancellable (POST .../cancel resolves conversation_id
    # -> routed_butler + request_id -> live session, see _resolve_session_id).
    # Cleared in the finally below so a stale entry never outlives the turn it
    # describes -- an unregistered conversation_id means "nothing to cancel"
    # (benign no-op), never a dangling handle to an unrelated later turn. The
    # try/finally (rather than a pop() at each exit point) also covers exit
    # paths callers didn't anticipate, e.g. an unhandled exception from
    # message_find_reply_since during polling, or the generator being closed
    # early by the ASGI server.
    if request_id_str:
        _ACTIVE_TURNS[conversation_id] = {
            "routed_butler": routed_butler,
            "request_id": request_id_str,
        }

    try:
        # A Switchboard acceptance only confirms that the envelope was
        # accepted. Surface a domain butler only when Switchboard explicitly
        # routed this turn; every other accepted decision is intentionally
        # represented as null rather than mislabelled as a domain route.
        # Keep this inside the active-turn try/finally: a client that closes
        # immediately after the receipt must not leave a dangling cancel handle.
        yield _sse_event(
            "dispatch_accepted",
            {"routed_butler": triage_target if routed_this_turn else None},
        )

        # Step 3: Poll for the conversation_reply message, with keepalive.
        start_ts = time.monotonic()
        last_keepalive_ts = start_ts
        reply_row: dict[str, Any] | None = None

        while reply_row is None:
            # Check client disconnect
            if await request.is_disconnected():
                logger.info("Client disconnected during conversation stream %s", conversation_id)
                return

            # Keepalive check
            now = time.monotonic()
            if now - last_keepalive_ts >= _KEEPALIVE_INTERVAL_S:
                yield _sse_comment("keepalive")
                last_keepalive_ts = now

            # Timeout guard — graceful: the thread stays open and a late reply
            # remains visible on the next history fetch/poll.
            if now - start_ts >= _SESSION_TIMEOUT_S:
                timeout_session_id = await _resolve_session_id(
                    db=db, routed_butler=routed_butler, request_id=request_id_str
                )
                logger.warning(
                    "No conversation_reply for conversation %s within %.0fs (routed_butler=%s)",
                    conversation_id,
                    _SESSION_TIMEOUT_S,
                    routed_butler,
                )
                yield _sse_error(
                    "SESSION_TIMEOUT",
                    "No reply yet — inspect the session for details.",
                    session_id=timeout_session_id,
                )
                yield _sse_done()
                return

            reply_row = await message_find_reply_since(
                shared_pool, conversation_id, since=message_created_at
            )
            if reply_row is None:
                await asyncio.sleep(_POLL_INTERVAL_S)

        # Step 4: Emit the already-persisted conversation_reply — no DB write
        # happens here; conversation_reply_create() did it inside the routed
        # butler's own session.
        yield _sse_event("token", {"content": reply_row["content"]})
        yield _sse_event(
            "message_complete",
            {
                "message_id": str(reply_row["id"]),
                "model_name": reply_row.get("model_name"),
                "input_tokens": reply_row.get("input_tokens"),
                "output_tokens": reply_row.get("output_tokens"),
                "duration_ms": reply_row.get("duration_ms"),
                "tool_calls": reply_row.get("tool_calls") or [],
            },
        )
        yield _sse_done()
    finally:
        _ACTIVE_TURNS.pop(conversation_id, None)


async def _persist_dashboard_user_message(
    pool: Any,
    *,
    conversation_id: UUID,
    message: str,
    message_id: UUID | None,
) -> tuple[dict[str, Any], bool]:
    """Persist a dashboard user message, reusing a retry's stable identity."""
    if message_id is None:
        return (
            await message_create(
                pool,
                conversation_id=conversation_id,
                role="user",
                content=message,
            ),
            True,
        )

    try:
        return await message_create_idempotent(
            pool,
            message_id=message_id,
            conversation_id=conversation_id,
            role="user",
            content=message,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": "MESSAGE_ID_CONFLICT", "message": str(exc)},
        ) from exc


async def _resolve_session_id(
    *,
    db: DatabaseManager,
    routed_butler: str,
    request_id: str | None,
) -> UUID | None:
    """Best-effort ``request_id`` -> ``sessions.id`` lookup on ``routed_butler``.

    ``request_id`` is the canonical Switchboard ingest request reference,
    which the routing pipeline stamps onto the resulting session row
    (``Spawner.trigger`` -> ``session_create(request_id=...)``). Returns
    ``None`` (never raises) when the butler's pool is unavailable or no
    session row is found yet.

    Shared by the ``SESSION_TIMEOUT`` event's "inspect session" link and
    ``POST .../cancel`` (bu-ep4ks.2) -- both need the same request_id ->
    session_id resolution, just for different follow-up actions.
    """
    if not request_id:
        return None

    try:
        pool = db.pool(routed_butler)
    except KeyError:
        logger.warning(
            "No DB pool registered for butler '%s'; cannot resolve session id",
            routed_butler,
        )
        return None

    try:
        return await pool.fetchval(
            "SELECT id FROM sessions WHERE request_id = $1 ORDER BY started_at DESC LIMIT 1",
            request_id,
        )
    except Exception:
        logger.warning(
            "Failed to resolve session id (butler=%s, request_id=%s)",
            routed_butler,
            request_id,
            exc_info=True,
        )
        return None


# ---------------------------------------------------------------------------
# GET /api/butlers/{name}/conversations
# ---------------------------------------------------------------------------


@router.get("/{name}/conversations", response_model=PaginatedResponse[ConversationSummary])
async def list_conversations(
    name: str,
    status: Annotated[
        Literal["active", "archived", "all"],
        Query(description="Filter by status: 'active', 'archived', or 'all'"),
    ] = "active",
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


@router.get(
    "/{name}/conversations/search",
    response_model=PaginatedResponse[ConversationSearchResult],
)
async def search_conversations(
    name: str,
    q: str | None = Query(None, description="Search query string"),
    limit: int = Query(20, ge=1, le=100, description="Max records to return"),
    offset: int = Query(0, ge=0, description="Number of records to skip"),
    db: DatabaseManager = Depends(_get_db_manager),
) -> PaginatedResponse[ConversationSearchResult]:
    """Search conversations by message content (case-insensitive substring match).

    Returns conversations whose messages contain the search term as a
    substring (``ILIKE '%query%'``), ordered by most recent matching message
    first.  Each result includes a ``snippet`` with the matching message
    content (truncated to 200 characters).

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
    mcp_mgr: MCPClientManager = Depends(get_mcp_manager),
) -> StreamingResponse:
    """Create a new conversation with the first user message.

    Returns a Server-Sent Events stream.  The first event is
    ``conversation_created`` with the new ``conversation_id`` and ``title``.
    Subsequent events follow the standard SSE streaming pattern.

    Dashboard messages bypass connector discretion evaluation — they are
    always operator-intentional (see ``DISCRETION_BYPASS_CHANNELS``).

    Envelopes addressed to a specific butler (any ``name`` other than the
    Switchboard staffer itself) carry ``control.pinned_target={name}`` so the
    message routes there deterministically instead of going through
    classification.
    """
    try:
        pool = db.credential_shared_pool()
    except (KeyError, RuntimeError) as exc:
        raise HTTPException(status_code=503, detail=f"Shared database unavailable: {exc}") from exc

    existing_user_msg = (
        await message_get_by_id(pool, body.message_id) if body.message_id is not None else None
    )
    if existing_user_msg is not None:
        conversation_id: UUID = existing_user_msg["conversation_id"]
        conv = await conversation_get(pool, conversation_id, butler_name=name)
        if (
            conv is None
            or existing_user_msg["role"] != "user"
            or existing_user_msg["content"] != body.message
        ):
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "MESSAGE_ID_CONFLICT",
                    "message": (
                        "message_id is already associated with a different dashboard message"
                    ),
                },
            )
        user_msg = existing_user_msg
        user_message_is_new = False
    else:
        # Create conversation record only when this is not a retry of an
        # initial message whose client-generated identity already exists.
        conv = await conversation_create(pool, butler_name=name, first_message=body.message)
        conversation_id = conv["id"]

        # Persist user message. A retry reuses its client-generated message ID,
        # avoiding a duplicate user row and preserving the ingest event identity.
        user_msg, user_message_is_new = await _persist_dashboard_user_message(
            pool,
            conversation_id=conversation_id,
            message=body.message,
            message_id=body.message_id,
        )

    if user_message_is_new:
        await conversation_message_count_increment(pool, conversation_id, butler_name=name)

    # Build ingest envelope
    envelope = build_dashboard_envelope(
        conversation_id=conversation_id,
        message_id=user_msg["id"],
        message_text=body.message,
        conversation_context=None,
        page_context=body.page_context.model_dump() if body.page_context else None,
        pinned_target=None if name == _SWITCHBOARD_BUTLER else name,
    )

    async def _generate() -> AsyncGenerator[str, None]:
        async for chunk in _stream_conversation_response(
            request=request,
            butler_name=name,
            conversation_id=conversation_id,
            message_created_at=user_msg["created_at"],
            envelope=envelope,
            db=db,
            mcp_mgr=mcp_mgr,
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
    mcp_mgr: MCPClientManager = Depends(get_mcp_manager),
) -> StreamingResponse:
    """Send a follow-up message in an existing conversation.

    Returns a Server-Sent Events stream using the same token/message_complete
    pattern as conversation creation, without the ``conversation_created`` event.

    If the conversation is archived, it is automatically reactivated.
    Returns 404 when the conversation does not exist or belongs to a
    different butler.

    Sticky routing: once a Switchboard-addressed (classification-routed)
    conversation has recorded a ``routed_butler`` from its first successful
    route, follow-ups pin to that butler directly instead of re-running
    classification.
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

    # Reusing ``message_id`` makes retry persistence and the downstream
    # Switchboard external_event_id idempotent.
    user_msg, user_message_is_new = await _persist_dashboard_user_message(
        pool,
        conversation_id=conversation_id,
        message=body.message,
        message_id=body.message_id,
    )

    if user_message_is_new:
        await conversation_message_count_increment(pool, conversation_id, butler_name=name)

    # Sticky routing: a Switchboard-addressed conversation that has already
    # routed once pins follow-ups directly to that butler; otherwise (not yet
    # routed, or a bug-lane conversation with no domain-butler target) it
    # continues through classification as before.
    if name == _SWITCHBOARD_BUTLER:
        pinned_target = conv.get("routed_butler")
    else:
        pinned_target = name

    # Build ingest envelope with conversation context
    envelope = build_dashboard_envelope(
        conversation_id=conversation_id,
        message_id=user_msg["id"],
        message_text=body.message,
        conversation_context=history_rows,
        page_context=body.page_context.model_dump() if body.page_context else None,
        pinned_target=pinned_target,
    )

    async def _generate() -> AsyncGenerator[str, None]:
        async for chunk in _stream_conversation_response(
            request=request,
            butler_name=name,
            conversation_id=conversation_id,
            message_created_at=user_msg["created_at"],
            envelope=envelope,
            db=db,
            mcp_mgr=mcp_mgr,
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
# POST /api/butlers/{name}/conversations/{conversation_id}/cancel
# ---------------------------------------------------------------------------


@router.post(
    "/{name}/conversations/{conversation_id}/cancel",
    response_model=ConversationCancelResponse,
)
async def cancel_conversation_turn(
    name: str,
    conversation_id: UUID,
    mcp_mgr: MCPClientManager = Depends(get_mcp_manager),
    db: DatabaseManager = Depends(_get_db_manager),
) -> ConversationCancelResponse:
    """Cancel the in-flight session behind this conversation's current turn.

    Implements the chat "Stop" button (bu-ep4ks.2) as a real terminate, not a
    client-side stream detach: resolves ``conversation_id`` -> the active
    turn's ``(routed_butler, request_id)`` registered by
    ``_stream_conversation_response`` -> ``request_id`` -> ``session_id`` ->
    the routed butler's ``cancel_session`` MCP tool, which kills the actual
    runtime subprocess.

    Always returns HTTP 200 -- see ``ConversationCancelResponse`` for the
    three distinct outcomes it distinguishes. Never claims ``cancelled=True``
    unless the routed butler itself confirmed the kill -- or, for a Stop
    click that lands before the runtime invocation has started (the session
    row exists but ``Spawner._run`` hasn't reached ``asyncio.create_task
    (runtime.invoke(...))`` yet), confirmed the invocation will be skipped
    entirely rather than falsely reporting ``already_finished``.
    """
    turn = _ACTIVE_TURNS.get(conversation_id)
    if turn is None:
        # Nothing registered for this conversation right now -- the turn
        # already finished (or was never dispatched). Benign no-op.
        return ConversationCancelResponse(cancelled=False, already_finished=True)

    routed_butler = turn["routed_butler"]
    request_id = turn["request_id"]

    session_id = await _resolve_session_id(
        db=db, routed_butler=routed_butler, request_id=request_id
    )
    if session_id is None:
        # The session row hasn't landed yet (very early race) or the pool
        # lookup failed -- nothing concrete to cancel against.
        return ConversationCancelResponse(cancelled=False, already_finished=True)

    try:
        client = await asyncio.wait_for(
            mcp_mgr.get_client(routed_butler), timeout=_MCP_DISPATCH_TIMEOUT_S
        )
        mcp_result = await asyncio.wait_for(
            client.call_tool("cancel_session", {"session_id": str(session_id)}),
            timeout=_MCP_DISPATCH_TIMEOUT_S,
        )
    except (ButlerUnreachableError, ToolError, TimeoutError, OSError) as exc:
        logger.warning(
            "cancel_session MCP call failed for conversation %s (butler=%s, session=%s): %s",
            conversation_id,
            routed_butler,
            session_id,
            exc,
        )
        return ConversationCancelResponse(
            cancelled=False,
            already_finished=False,
            session_id=session_id,
            message=f"Could not reach {routed_butler} to confirm cancellation.",
        )

    result = _first_json_block(mcp_result)
    if mcp_result.is_error or not isinstance(result, dict):
        logger.warning(
            "cancel_session tool returned an unexpected result for conversation %s "
            "(butler=%s, session=%s): %r",
            conversation_id,
            routed_butler,
            session_id,
            result,
        )
        return ConversationCancelResponse(
            cancelled=False,
            already_finished=False,
            session_id=session_id,
            message="Cancellation request failed.",
        )

    cancelled = bool(result.get("cancelled"))
    return ConversationCancelResponse(
        cancelled=cancelled,
        already_finished=not cancelled,
        session_id=session_id,
        message=None if cancelled else "Session already finished.",
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
