"""Owner knowledge gaps: an unanswered owner question that answers itself (bu-q7vx1q.9).

A sourceless decline in the dashboard answer lane used to be terminal.  Now the
decline may name the ``(entity, predicate)`` it could not answer; that is recorded
here as an ``open`` gap in the answering butler's own memory schema.  A later write
of a matching active fact moves the gap to ``answerable`` in the *same transaction*
as the fact write (:func:`close_matching_gaps`), and a deterministic job
(:func:`deliver_knowledge_gaps`) then posts exactly one notice per origin thread.

Lifecycle: ``open -> answerable -> delivered``; ``dismissed`` and ``expired`` are
terminal and never reopen.

Capture, the born-answerable re-check and closure all serialize on a transaction
advisory lock keyed on ``(schema, entity, predicate)``.  Without it a fact written
between the capture's fact check and its commit would leave the gap ``open`` for a
value that already exists.

A gap never carries content authority.  Whether the eventual notice reads "now known"
or "reported by" is decided by the server-stamped authority of the closing fact.
"""

from __future__ import annotations

import importlib
import logging
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

from butlers.modules.memory.content_authority import OWNER_CLASS, SYSTEM, ContentAuthority

logger = logging.getLogger(__name__)

DEFAULT_TTL_DAYS = 90
MAX_DELIVERY_ATTEMPTS = 5
_BACKOFF_BASE = timedelta(minutes=5)
_BACKOFF_CAP = timedelta(hours=6)
_VALUE_EXCERPT_CHARS = 200
_SUMMARY_CHARS = 300
_DELIVERY_BATCH = 25

#: The only channel whose sender is the owner surface; other channels are a later slice.
CAPTURE_CHANNEL = "dashboard"

#: Closure failures swallowed since process start (the fact write always wins).
closure_failures = 0

STATUSES = ("open", "answerable", "delivered", "dismissed", "expired")


class KnowledgeGapError(ValueError):
    """A gap could not be recorded; the message is safe to show the calling model."""


@asynccontextmanager
async def _scoped(pool: Any, memory_schema: str | None) -> AsyncIterator[Any]:
    """A connection whose unqualified names resolve in *memory_schema* (when given).

    Dashboard pools belong to the butler's domain schema; a butler with a private memory
    schema (``chronicler_mem``) needs the search path pointed there for this transaction.
    """
    async with pool.acquire() as conn:
        async with conn.transaction():
            if memory_schema is not None:
                await conn.execute(
                    "SELECT set_config('search_path', $1, true)", f"{memory_schema}, public"
                )
            yield conn


def _lock_key(entity_id: uuid.UUID, predicate: str) -> str:
    return f"knowledge_gap:{entity_id}:{predicate}"


async def _lock_pair(conn: Any, entity_id: uuid.UUID, predicate: str) -> None:
    """Serialize capture, closure and the born-answerable re-check for one pair."""
    await conn.execute(
        "SELECT pg_advisory_xact_lock(hashtextextended(current_schema() || ':' || $1, 0))",
        _lock_key(entity_id, predicate),
    )


def _excerpt(value: str | None, limit: int) -> str | None:
    if value is None:
        return None
    value = " ".join(str(value).split())
    return value if len(value) <= limit else value[: limit - 1] + "…"


# ---------------------------------------------------------------------------
# Closure (called from inside a fact write's transaction)
# ---------------------------------------------------------------------------


async def close_matching_gaps(
    conn: Any,
    *,
    entity_id: uuid.UUID | None,
    predicate: str,
    ref: str,
    value: str | None | Callable[[], Awaitable[str | None]],
    authority: ContentAuthority | None | Callable[[], Awaitable[ContentAuthority]],
) -> None:
    """Move an open gap for ``(entity_id, predicate)`` to ``answerable``.

    Runs on the fact write's connection so the transition commits or rolls back with
    the fact.  A schema without the gap table (a butler that never ran ``mem_014``) is
    skipped.  A closure failure never fails the fact write (logged and counted in
    ``closure_failures``).  Only ``open`` gaps move, so a replayed write cannot re-notify.

    *value* and *authority* may be coroutine functions; they run only when a gap is open.
    *authority* is the closing fact's server-stamped authority, or a coroutine function
    that derives it; the latter runs only when a gap is actually open.
    """
    if entity_id is None:
        return
    try:
        if not await conn.fetchval("SELECT to_regclass('knowledge_gaps') IS NOT NULL"):
            return
        # A savepoint, so a closure error rolls back only the closure: the fact write
        # that called us still commits.
        async with conn.transaction():
            await _lock_pair(conn, entity_id, predicate)
            if not await conn.fetchval(
                "SELECT 1 FROM knowledge_gaps"
                " WHERE entity_id = $1 AND predicate = $2 AND status = 'open'",
                entity_id,
                predicate,
            ):
                return
            if callable(value):
                value = await value()
            if callable(authority):
                authority = await authority()
            await conn.execute(
                """
                UPDATE knowledge_gaps
                SET status = 'answerable',
                    answered_at = now(),
                    answered_by_ref = $3,
                    answered_value = $4,
                    answered_authority = $5,
                    answered_authority_entity_id = $6,
                    next_attempt_at = now()
                WHERE entity_id = $1 AND predicate = $2 AND status = 'open'
                """,
                entity_id,
                predicate,
                ref,
                _excerpt(value, _VALUE_EXCERPT_CHARS),
                authority.authority if authority else None,
                authority.entity_id if authority else None,
            )
    except Exception:
        global closure_failures
        closure_failures += 1
        logger.warning(
            "knowledge gap closure failed for entity %s predicate %s (count=%d); "
            "the fact write is unaffected and the gap stays open",
            entity_id,
            predicate,
            closure_failures,
            exc_info=True,
        )


# ---------------------------------------------------------------------------
# Capture
# ---------------------------------------------------------------------------


async def _validate_entity(conn: Any, entity_id: uuid.UUID) -> None:
    row = await conn.fetchrow(
        "SELECT id, metadata->>'merged_into' AS merged_into FROM public.entities WHERE id = $1",
        entity_id,
    )
    if row is None:
        raise KnowledgeGapError(f"entity_id {entity_id} does not exist")
    if row["merged_into"]:
        raise KnowledgeGapError(f"entity_id {entity_id} was merged into another entity")


async def _validate_predicate(conn: Any, schema: str, predicate: str) -> None:
    if await conn.fetchval("SELECT 1 FROM predicate_registry WHERE name = $1", predicate):
        return
    # relationship.entity_predicate_registry is the local registry only for the
    # relationship butler; another role may not even see that schema.
    if schema == "relationship" and await conn.fetchval(
        "SELECT 1 FROM relationship.entity_predicate_registry WHERE name = $1", predicate
    ):
        return
    raise KnowledgeGapError(f"predicate {predicate!r} is not in this butler's predicate registry")


async def _existing_answer(
    conn: Any, schema: str, entity_id: uuid.UUID, predicate: str
) -> dict[str, Any] | None:
    """The active fact that already answers ``(entity_id, predicate)``, if any."""
    row = await conn.fetchrow(
        """
        SELECT id, content, content_authority, authority_entity_id
        FROM facts
        WHERE entity_id = $1 AND predicate = $2 AND validity = 'active'
        ORDER BY created_at DESC
        LIMIT 1
        """,
        entity_id,
        predicate,
    )
    if row is not None:
        return {
            "ref": f"fact:{row['id']}",
            "value": row["content"],
            "authority": row["content_authority"],
            "authority_entity_id": row["authority_entity_id"],
        }
    if schema != "relationship":
        return None
    row = await conn.fetchrow(
        """
        SELECT id, object FROM relationship.entity_facts
        WHERE subject = $1 AND predicate = $2 AND validity = 'active'
        ORDER BY observed_at DESC
        LIMIT 1
        """,
        entity_id,
        predicate,
    )
    if row is None:
        return None
    return {
        "ref": f"entity_fact:{row['id']}",
        "value": row["object"],
        "authority": None,
        "authority_entity_id": None,
    }


async def record_gap(
    pool: Any,
    *,
    entity_id: uuid.UUID,
    predicate: str,
    question_summary: str,
    conversation_id: uuid.UUID,
    request_id: uuid.UUID | None,
    channel: str,
    ttl_days: int = DEFAULT_TTL_DAYS,
) -> dict[str, Any]:
    """Record (or merge into) the open gap for ``(entity_id, predicate)``.

    Returns ``{"gap_id", "gap_status", "merged"}``.  Raises :class:`KnowledgeGapError` for
    an unknown or merged-away entity, an unregistered predicate, or a channel other
    than the owner's dashboard.  When an active fact already answers the pair the gap
    is born ``answerable``.
    """
    if channel != CAPTURE_CHANNEL:
        raise KnowledgeGapError(f"gaps are captured from the {CAPTURE_CHANNEL} channel only")
    summary = _excerpt(question_summary, _SUMMARY_CHARS) or ""
    if not summary:
        raise KnowledgeGapError("a gap needs a non-empty question")

    async with pool.acquire() as conn:
        async with conn.transaction():
            schema = await conn.fetchval("SELECT current_schema()")
            await _validate_entity(conn, entity_id)
            await _validate_predicate(conn, schema, predicate)
            await _lock_pair(conn, entity_id, predicate)

            gap_id = await conn.fetchval(
                "SELECT id FROM knowledge_gaps"
                " WHERE entity_id = $1 AND predicate = $2 AND status = 'open'",
                entity_id,
                predicate,
            )
            merged = gap_id is not None
            status = "open"
            if not merged:
                answer = await _existing_answer(conn, schema, entity_id, predicate)
                status = "answerable" if answer is not None else "open"
                gap_id = await conn.fetchval(
                    """
                    INSERT INTO knowledge_gaps
                        (entity_id, predicate, question_summary, status, expires_at,
                         answered_at, answered_by_ref, answered_value, answered_authority,
                         answered_authority_entity_id, next_attempt_at)
                    VALUES ($1, $2, $3, $4, now() + $5 * interval '1 day',
                            CASE WHEN $4 = 'answerable' THEN now() END, $6, $7, $8, $9,
                            CASE WHEN $4 = 'answerable' THEN now() END)
                    RETURNING id
                    """,
                    entity_id,
                    predicate,
                    summary,
                    status,
                    ttl_days,
                    answer["ref"] if answer else None,
                    _excerpt(answer["value"], _VALUE_EXCERPT_CHARS) if answer else None,
                    answer["authority"] if answer else None,
                    answer["authority_entity_id"] if answer else None,
                )
            await conn.execute(
                """
                INSERT INTO knowledge_gap_origins (gap_id, conversation_id, request_id, channel)
                VALUES ($1, $2, $3, $4)
                ON CONFLICT (gap_id, conversation_id) DO NOTHING
                """,
                gap_id,
                conversation_id,
                request_id,
                channel,
            )
    return {"gap_id": str(gap_id), "gap_status": status, "merged": merged}


async def record_gap_result(pool: Any, **gap: Any) -> dict[str, Any]:
    """:func:`record_gap` as the session hook reports it: a status dict, never a raise
    for a rejected gap, so the decline reply that carried it is unaffected."""
    try:
        recorded = await record_gap(pool, **gap)
    except KnowledgeGapError as exc:
        return {"status": "refused", "error": str(exc)}
    return {"status": "recorded", **recorded}


# ---------------------------------------------------------------------------
# Owner actions and reads
# ---------------------------------------------------------------------------


async def dismiss_gap(
    pool: Any, gap_id: uuid.UUID, *, memory_schema: str | None = None
) -> str | None:
    """Owner dismissal.  Terminal; only a gap that is still open or answerable moves.

    Returns the gap's resulting status, or ``None`` when this pool holds no such gap.
    """
    async with _scoped(pool, memory_schema) as conn:
        return await conn.fetchval(
            """
            UPDATE knowledge_gaps
            SET status = CASE
                    WHEN status IN ('open', 'answerable') THEN 'dismissed' ELSE status END,
                dismissed_at = CASE
                    WHEN status IN ('open', 'answerable') THEN now() ELSE dismissed_at END
            WHERE id = $1
            RETURNING status
            """,
            gap_id,
        )


def _load_compose_state() -> Callable[..., str] | None:
    """The relationship butler's coverage composer, when its tools are importable."""
    try:
        return importlib.import_module("butlers.tools.relationship.fact_coverage").compose_state
    except ImportError:
        return None


async def _open_gap_coverage(conn: Any, schema: str, entity_id: uuid.UUID, predicate: str) -> str:
    """Coverage state of an open gap: ``unknown`` unless receipts say otherwise.

    An open gap has no active fact by construction, so only the receipts (recorded in
    ``relationship.fact_coverage``) and the entity's availability can move it off
    ``unknown``.  Any failure to compose reads ``unknown``, never ``absent_proven``.
    """
    compose = _load_compose_state()
    if compose is None or schema != "relationship":
        return "unknown"
    merged = await conn.fetchval(
        "SELECT metadata->>'merged_into' FROM public.entities WHERE id = $1", entity_id
    )
    exists = await conn.fetchval("SELECT 1 FROM public.entities WHERE id = $1", entity_id)
    outcomes = await conn.fetch(
        "SELECT outcome FROM relationship.fact_coverage WHERE subject = $1 AND predicate = $2",
        entity_id,
        predicate,
    )
    return compose(
        target_available=bool(exists) and not merged,
        active_value_count=0,
        receipt_outcomes=[r["outcome"] for r in outcomes],
    )


async def list_gaps(
    pool: Any,
    *,
    statuses: tuple[str, ...] = ("open", "answerable"),
    limit: int = 50,
    after: tuple[datetime, uuid.UUID] | None = None,
    memory_schema: str | None = None,
) -> list[dict[str, Any]]:
    """Gaps newest-first (``asked_at DESC, id DESC``), keyset-paginated via ``after``.

    Each row carries its origins and a ``coverage_state``: ``present`` once answered,
    otherwise the composed receipt state (``unknown`` when nothing looked).  An
    answerable gap whose delivery is exhausted reports ``delivery_failed``.
    """
    bad = [s for s in statuses if s not in STATUSES]
    if bad:
        raise ValueError(f"unknown gap status {bad!r}")
    cursor_sql = "AND (g.asked_at, g.id) < ($3, $4)" if after is not None else ""
    args: list[Any] = [list(statuses), limit]
    if after is not None:
        args.extend(after)
    async with _scoped(pool, memory_schema) as conn:
        schema = await conn.fetchval("SELECT current_schema()")
        rows = await conn.fetch(
            f"""
            SELECT g.*, e.canonical_name AS entity_name,
                   COALESCE((
                       SELECT jsonb_agg(jsonb_build_object(
                                  'conversation_id', o.conversation_id,
                                  'request_id', o.request_id,
                                  'channel', o.channel,
                                  'asked_at', o.asked_at,
                                  'delivered_at', o.delivered_at)
                              ORDER BY o.asked_at)
                       FROM knowledge_gap_origins o WHERE o.gap_id = g.id
                   ), '[]'::jsonb) AS origins
            FROM knowledge_gaps g
            LEFT JOIN public.entities e ON e.id = g.entity_id
            WHERE g.status = ANY($1::text[]) {cursor_sql}
            ORDER BY g.asked_at DESC, g.id DESC
            LIMIT $2
            """,
            *args,
        )
        gaps = []
        for row in rows:
            gap = dict(row)
            if gap["status"] == "open":
                gap["coverage_state"] = await _open_gap_coverage(
                    conn, schema, gap["entity_id"], gap["predicate"]
                )
            else:
                gap["coverage_state"] = "present" if gap["answered_by_ref"] else "unknown"
            gap["delivery_failed"] = (
                gap["status"] == "answerable" and gap["delivery_attempts"] >= MAX_DELIVERY_ATTEMPTS
            )
            gaps.append(gap)
    return gaps


def public_view(gap: dict[str, Any]) -> dict[str, Any]:
    """The owner-facing shape of a listed gap (shared by the tool and the API)."""
    origins = gap["origins"]
    if isinstance(origins, str):
        import json

        origins = json.loads(origins)
    return {
        "gap_id": str(gap["id"]),
        "entity_id": str(gap["entity_id"]),
        "entity_name": gap["entity_name"],
        "predicate": gap["predicate"],
        "question": gap["question_summary"],
        "status": gap["status"],
        "coverage_state": gap["coverage_state"],
        "asked_at": gap["asked_at"].isoformat(),
        "expires_at": gap["expires_at"].isoformat(),
        "answered_by_ref": gap["answered_by_ref"],
        "delivery_failed": gap["delivery_failed"],
        "last_error": gap["last_error"],
        "origins": origins,
    }


# ---------------------------------------------------------------------------
# Deterministic job: expiry and delivery
# ---------------------------------------------------------------------------

PostNotice = Callable[[uuid.UUID, str], Awaitable[bool]]


def _backoff(attempts: int) -> timedelta:
    return min(_BACKOFF_BASE * (2 ** max(attempts - 1, 0)), _BACKOFF_CAP)


def _format_day(moment: datetime) -> str:
    return f"{moment.day} {moment:%b}"


async def _sender_name(conn: Any, entity_id: uuid.UUID | None) -> str:
    if entity_id is None:
        return "a third party"
    name = await conn.fetchval(
        "SELECT canonical_name FROM public.entities WHERE id = $1", entity_id
    )
    return name or "a third party"


async def _notice_text(conn: Any, gap: Any, asked_at: datetime) -> str:
    asked = f"You asked on {_format_day(asked_at)}: {gap['question_summary']}"
    value = gap["answered_value"] or "(no value recorded)"
    ref = gap["answered_by_ref"]
    authority = gap["answered_authority"]
    if authority in OWNER_CLASS or authority == SYSTEM:
        return f"{asked}. Now known: {value} (source: {ref})."
    sender = await _sender_name(conn, gap["answered_authority_entity_id"])
    return f"{asked}. Reported by {sender}, not verified: {value} (source: {ref})."


async def _deliver_one(pool: Any, gap_id: uuid.UUID, post: PostNotice, now: datetime) -> str:
    """Deliver one gap's pending origins under a row lock.  Returns the outcome."""
    async with pool.acquire() as conn:
        async with conn.transaction():
            gap = await conn.fetchrow(
                "SELECT * FROM knowledge_gaps WHERE id = $1 AND status = 'answerable' FOR UPDATE",
                gap_id,
            )
            if gap is None:
                return "skipped"
            origins = await conn.fetch(
                "SELECT * FROM knowledge_gap_origins"
                " WHERE gap_id = $1 AND delivered_at IS NULL ORDER BY asked_at",
                gap_id,
            )
            error: str | None = None
            for origin in origins:
                try:
                    text = await _notice_text(conn, gap, origin["asked_at"])
                    posted = await post(origin["conversation_id"], text)
                except Exception as exc:
                    logger.warning("knowledge gap %s delivery failed", gap_id, exc_info=True)
                    error = f"{type(exc).__name__}: {exc}"
                    break
                if not posted:
                    # The origin conversation no longer exists: nothing left to tell.
                    logger.warning(
                        "knowledge gap %s: origin conversation %s is gone; notice skipped",
                        gap_id,
                        origin["conversation_id"],
                    )
                await conn.execute(
                    "UPDATE knowledge_gap_origins SET delivered_at = now() WHERE id = $1",
                    origin["id"],
                )
            if error is None:
                await conn.execute(
                    "UPDATE knowledge_gaps SET status = 'delivered', delivered_at = now(),"
                    " last_error = NULL WHERE id = $1",
                    gap_id,
                )
                return "delivered"
            attempts = gap["delivery_attempts"] + 1
            await conn.execute(
                "UPDATE knowledge_gaps SET delivery_attempts = $2, next_attempt_at = $3,"
                " last_error = $4 WHERE id = $1",
                gap_id,
                attempts,
                now + _backoff(attempts),
                error[:500],
            )
            return "exhausted" if attempts >= MAX_DELIVERY_ATTEMPTS else "retry"


async def deliver_knowledge_gaps(
    pool: Any,
    *,
    post: PostNotice,
    origin_butler: str,
    now: datetime | None = None,
) -> dict[str, int]:
    """Expire stale open gaps, then deliver due answerable ones.

    ``post(conversation_id, text)`` returns False when the conversation is gone and
    raises on a delivery failure.  A failure keeps the gap ``answerable`` with its
    attempt count and a bounded backoff; at ``MAX_DELIVERY_ATTEMPTS`` it is recorded on
    the attention ledger and stays listed as ``delivery_failed``.
    """
    now = now or datetime.now(UTC)
    counts = {"expired": 0, "delivered": 0, "retry": 0, "exhausted": 0}
    result = await pool.execute(
        "UPDATE knowledge_gaps SET status = 'expired' WHERE status = 'open' AND expires_at <= $1",
        now,
    )
    counts["expired"] = int(str(result).rsplit(" ", 1)[-1])

    due = await pool.fetch(
        """
        SELECT id FROM knowledge_gaps
        WHERE status = 'answerable' AND delivery_attempts < $2
          AND (next_attempt_at IS NULL OR next_attempt_at <= $1)
        ORDER BY answered_at
        LIMIT $3
        """,
        now,
        MAX_DELIVERY_ATTEMPTS,
        _DELIVERY_BATCH,
    )
    for row in due:
        outcome = await _deliver_one(pool, row["id"], post, now)
        if outcome in counts:
            counts[outcome] += 1
        if outcome == "exhausted":
            from butlers.core.attention_ledger import record_attention_event

            await record_attention_event(
                pool,
                origin_butler=origin_butler,
                source="notify",
                outcome="failed",
                channel=CAPTURE_CHANNEL,
                intent="knowledge_gap_answer",
                dedup_key=f"knowledge_gap:{row['id']}",
                reason="delivery attempts exhausted",
            )
    return counts
