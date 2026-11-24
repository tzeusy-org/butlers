"""Role-authenticated database interface; caller owns connection/transaction.

No SET ROLE, label/grant derivation from request strings, producer registration,
model tool or live consumer wiring occurs here. A transaction rollback also rolls
back the SQL writer's slot, timeline and durable receipts.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from butlers.perception.model import EventRequest


class PerceptionStore:
    def __init__(self, connection) -> None:
        self.connection = connection

    async def write(self, request: EventRequest) -> dict:
        return await self.connection.fetchval(
            "SELECT public.perception_write($1::jsonb)", request.payload()
        )

    async def read(self, kind: str, subject: UUID | None, *, as_of: datetime) -> dict:
        return await self.connection.fetchval(
            "SELECT public.perception_read($1,$2,$3)", kind, subject, as_of
        )

    async def timeline(
        self,
        *,
        since: datetime,
        until: datetime,
        kind: str | None = None,
        subject: UUID | None = None,
        before: UUID | None = None,
        limit: int = 20,
        live: bool = False,
    ) -> list[dict]:
        return await self.connection.fetchval(
            "SELECT public.perception_timeline($1,$2,$3,$4,$5,$6,$7)",
            since,
            until,
            kind,
            subject,
            before,
            limit,
            live,
        )

    async def bind_condition(self, body: dict) -> dict:
        """Persist a dark immutable condition/suppression. Never evaluates/fires it."""
        return await self.connection.fetchval("SELECT public.perception_bind($1::jsonb)", body)

    async def registry(self) -> list[dict]:
        """Read the one versioned kind/schema/writer/freshness visibility contract."""
        return await self.connection.fetchval("SELECT public.perception_registry()")
