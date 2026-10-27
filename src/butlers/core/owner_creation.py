"""Single-purpose owner creation admission; capabilities never enter tool arguments.

The raw MCP adapter only transports a claim. The owning database transaction
verifies it against current authentication, consumes it, creates the schedule,
and seals its immutable definition and closed retry receipt together.
"""

from __future__ import annotations

import contextvars
import functools
import hashlib
import json
import re
import uuid
from dataclasses import dataclass
from typing import Any

from fastmcp.server.middleware import Middleware
from opentelemetry.instrumentation.utils import suppress_instrumentation

META_KEY = "butlers/owner-creation-v1"
_TOKEN = re.compile(r"[A-Za-z0-9_-]{43}\Z")
_HASH = re.compile(r"[0-9a-f]{64}\Z")


@dataclass(frozen=True, repr=False)
class CreationClaim:
    nonce: str
    session_digest: str
    operation: str
    request_digest: str
    target: str


_pending: contextvars.ContextVar[CreationClaim | None] = contextvars.ContextVar(
    "private_owner_creation_claim", default=None
)


def argument_digest(arguments: dict[str, Any]) -> str:
    """RFC 8785 digest of the *raw* accepted wire object, including absence/null."""
    import rfc8785

    if type(arguments) is not dict:
        raise ValueError("Invalid owner creation request")

    def closed(value: Any) -> bool:
        if value is None or type(value) in (str, int, float, bool):
            return True
        if type(value) is list:
            return all(closed(item) for item in value)
        if type(value) is dict:
            return all(type(key) is str and closed(item) for key, item in value.items())
        return False

    if not closed(arguments):
        raise ValueError("Invalid owner creation request")
    encoded = rfc8785.dumps(arguments)
    if len(encoded) > 65536:
        raise ValueError("Invalid owner creation request")
    return hashlib.sha256(b"butlers:owner-creation:v1\0" + encoded).hexdigest()


class OwnerCreationMiddleware(Middleware):
    """Bind one raw request only; never modify a cached client or session defaults."""

    def __init__(self, target: str) -> None:
        self.target = target

    async def on_call_tool(self, context: Any, call_next: Any) -> Any:
        claim = None
        raw = context.fastmcp_context.request_context.meta if context.fastmcp_context else None
        meta = raw.model_dump(by_alias=True) if raw is not None else {}
        if META_KEY in meta:
            value = meta[META_KEY]
            if context.message.name != "schedule_create" or type(value) is not dict:
                raise ValueError("Invalid owner creation claim")
            if set(value) != {"nonce", "session_digest", "operation", "request_digest", "target"}:
                raise ValueError("Invalid owner creation claim")
            if any(type(v) is not str for v in value.values()):
                raise ValueError("Invalid owner creation claim")
            try:
                operation = str(uuid.UUID(value["operation"]))
                digest = argument_digest(context.message.arguments or {})
            except (ValueError, TypeError, UnicodeError):
                raise ValueError("Invalid owner creation claim") from None
            if (
                not _TOKEN.fullmatch(value["nonce"])
                or not _HASH.fullmatch(value["session_digest"])
                or not _HASH.fullmatch(value["request_digest"])
                or value["target"] != self.target
                or operation != value["operation"]
                or digest != value["request_digest"]
            ):
                raise ValueError("Invalid owner creation claim")
            claim = CreationClaim(**value)
        from butlers.core.attention_urgency import _model_urgency_requested

        hint_token = _model_urgency_requested.set(
            context.message.name == "notify"
            and (context.message.arguments or {}).get("priority") == "high"
        )
        token = _pending.set(claim)
        try:
            return await call_next(context)
        finally:
            _pending.reset(token)
            _model_urgency_requested.reset(hint_token)


def _object(value: Any) -> dict:
    value = json.loads(value) if isinstance(value, str) else value
    if type(value) is not dict:
        raise ValueError("Owner creation unavailable")
    return value


async def _consume(
    connection: Any, claim: CreationClaim, phase: str, task: uuid.UUID | None
) -> dict:
    # Instrumentation must not capture the unpersisted bearer or session hash.
    with suppress_instrumentation():
        return _object(
            await connection.fetchval(
                "SELECT dashboard_attention.consume($1,$2,$3,$4,$5,$6,$7)",
                phase,
                claim.nonce,
                claim.session_digest,
                claim.operation,
                claim.target,
                claim.request_digest,
                task,
            )
        )


def owner_creation_transaction(function: Any) -> Any:
    """Keep ordinary calls unchanged; enlist flagged calls in their domain transaction."""

    @functools.wraps(function)
    async def create(pool: Any, *args: Any, **kwargs: Any) -> Any:
        claim = _pending.get()
        if claim is None:
            return await function(pool, *args, **kwargs)
        # No deadline producer is silently exempted from its separate evaluator.
        if kwargs.get("task_type", "cron") != "cron":
            raise ValueError("Owner interrupt requires a cron schedule")
        token = _pending.set(None)
        try:
            async with pool.acquire() as connection, connection.transaction():
                before = await _consume(connection, claim, "begin", None)
                if before.get("state") == "completed":
                    return uuid.UUID(before["id"])
                if before.get("state") != "admitted":
                    raise ValueError("Owner creation refused")
                task = await function(connection, *args, **kwargs)
                after = await _consume(connection, claim, "finish", task)
                if after != {"state": "completed", "id": str(task)}:
                    raise ValueError("Owner creation refused")
                return task
        finally:
            _pending.reset(token)

    return create
