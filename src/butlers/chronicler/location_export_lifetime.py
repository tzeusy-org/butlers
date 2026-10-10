"""Private owning API response lifetime, never remote-recipient erasure.

Only native ASGI completion can settle these transient server copies. The
HTTP status, client ACK, principal string and caller headers cannot do so.
Durable stored/cache/session copies have their separate owning dispositions.
"""

from __future__ import annotations

import logging
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID, uuid4

logger = logging.getLogger(__name__)


@dataclass
class _LocationExportScope:
    request_id: UUID = field(default_factory=uuid4)
    copies: list[tuple[Any, str, UUID, bytes]] = field(default_factory=list)
    active: bool = True


_current_location_export: ContextVar[_LocationExportScope | None] = ContextVar(
    "owning_location_export", default=None
)


def native_export_request() -> UUID | None:
    scope = _current_location_export.get()
    return scope.request_id if scope is not None and scope.active else None


def register_native_export(pool: Any, kind: str, generation: UUID, digest: bytes) -> None:
    scope = _current_location_export.get()
    if scope is not None and not scope.active:
        from butlers.chronicler.location_retention import PolicyUnavailableError

        raise PolicyUnavailableError("Owning export lifetime has ended")
    if scope is not None:
        scope.copies.append((pool, kind, generation, digest))


async def _settle_server_exports(scope: _LocationExportScope) -> None:
    from butlers.chronicler.location_retention import (
        PolicyUnavailableError,
        _api_capture_configured,
    )
    from butlers.chronicler.storage import _lock_location_writes

    for pool, kind, generation, digest in scope.copies:
        if not _api_capture_configured(pool):
            raise PolicyUnavailableError("Owning export producer is unavailable")
        receipt = uuid4()
        async with pool.acquire() as conn:
            async with conn.transaction():
                if await conn.fetchval("SELECT current_schema()") != "chronicler":
                    raise PolicyUnavailableError("Owning export schema differs")
                await _lock_location_writes(conn)
                # Fixed native query profiles; no arbitrary relation/input selector.
                if kind == "native_read":
                    matching = await conn.fetchval(
                        "SELECT count(*) FROM location_native_copy_births "
                        "WHERE copy_generation=$1 AND receiving_server_request=$2 "
                        "AND input_digest=$3 AND producer_kind='api_export'",
                        generation,
                        scope.request_id,
                        digest,
                    )
                    total = await conn.fetchval(
                        "SELECT count(*) FROM location_native_copy_births WHERE copy_generation=$1",
                        generation,
                    )
                elif kind == "cache":
                    matching = await conn.fetchval(
                        "SELECT count(*) FROM location_native_cache_exports "
                        "WHERE copy_generation=$1 AND receiving_server_request=$2 "
                        "AND body_digest=$3",
                        generation,
                        scope.request_id,
                        digest,
                    )
                    total = 1
                else:
                    raise PolicyUnavailableError("Owning export profile differs")
                if not matching or matching != total:
                    raise PolicyUnavailableError("Owning server copy binding differs")
                prior = await conn.fetchrow(
                    "SELECT * FROM location_native_api_dispositions WHERE copy_generation=$1",
                    generation,
                )
                if prior is not None:
                    if (
                        prior["server_request"] != scope.request_id
                        or prior["body_digest"] != digest
                        or prior["producer_kind"] != kind
                    ):
                        raise PolicyUnavailableError("Committed server disposition differs")
                    receipt = prior["receipt_id"]
                else:
                    await conn.execute(
                        "INSERT INTO location_native_api_dispositions "
                        "(copy_generation,server_request,producer_kind,body_digest,receipt_id) "
                        "VALUES($1,$2,$3,$4,$5)",
                        generation,
                        scope.request_id,
                        kind,
                        digest,
                        receipt,
                    )
        async with pool.acquire() as committed:
            observed = await committed.fetchval(
                "SELECT receipt_id FROM location_native_api_dispositions "
                "WHERE copy_generation=$1 AND server_request=$2 AND body_digest=$3 "
                "AND producer_kind=$4",
                generation,
                scope.request_id,
                digest,
                kind,
            )
        if observed != receipt:
            raise PolicyUnavailableError("Committed server disposition is unknown")


class LocationExportLifetimeMiddleware:
    """Fixed API constructor installs one private scope around actual responses.

    A successful final-body send followed by delegate completion proves only
    the source-owned response lifetime ended. An interrupted stream or unknown
    receipt keeps the birth pending. No remote/browser erasure is attested.
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        path = scope.get("path", "")
        owning_route = path.startswith(
            (
                "/api/chronicler/",
                "/api/sessions",
                "/api/butlers/chronicler/sessions",
                "/api/memory/",
            )
        )
        if scope.get("type") != "http" or not owning_route:
            await self.app(scope, receive, send)
            return
        export = _LocationExportScope()
        token = _current_location_export.set(export)
        final_body = False
        invalid_terminal = False

        async def send_response(message):
            nonlocal final_body, invalid_terminal
            await send(message)
            if message.get("type") == "http.response.body":
                if final_body:
                    invalid_terminal = True
                if not message.get("more_body", False):
                    final_body = True

        try:
            await self.app(scope, receive, send_response)
            export.active = False
            if final_body and not invalid_terminal:
                try:
                    await _settle_server_exports(export)
                except Exception:
                    # Delivery already occurred; never rewrite it as failure or
                    # synthesize a receipt. Missing disposition remains unknown.
                    logger.warning("location_server_export_disposition_unknown")
        finally:
            export.active = False
            _current_location_export.reset(token)
