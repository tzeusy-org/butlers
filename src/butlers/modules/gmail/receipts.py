"""Metadata-only send fence. A committed reservation precedes every provider handoff."""

from __future__ import annotations

from typing import Any
from uuid import UUID


class SendReceipts:
    def __init__(self, pool: Any) -> None:
        self.pool = pool

    async def get(self, effect_id: UUID) -> Any:
        return await self.pool.fetchrow(
            "SELECT * FROM gmail_owner_sends WHERE effect_id = $1", effect_id
        )

    async def reserve(
        self, effect_id: UUID, account: str, thread_id: str, message_id: str, call_digest: str
    ) -> Any:
        async with self.pool.acquire() as conn, conn.transaction():
            row = await conn.fetchrow(
                "INSERT INTO gmail_owner_sends "
                "(effect_id, account, thread_id, message_id, call_digest, state) "
                "VALUES ($1, $2, $3, $4, $5, 'dispatching') ON CONFLICT DO NOTHING RETURNING *",
                effect_id,
                account,
                thread_id,
                message_id,
                call_digest,
            )
        return row

    async def settle(self, effect_id: UUID, state: str, gmail_message_id: str | None = None) -> Any:
        return await self.pool.fetchrow(
            "UPDATE gmail_owner_sends SET state = $2, gmail_message_id = $3, updated_at = now() "
            "WHERE effect_id = $1 AND state IN ('dispatching', 'unknown') RETURNING *",
            effect_id,
            state,
            gmail_message_id,
        ) or await self.get(effect_id)

    async def cleanup(self, effect_id: UUID, outcome: str) -> Any:
        return await self.pool.fetchrow(
            "UPDATE gmail_owner_sends SET draft_cleanup = $2, updated_at = now() "
            "WHERE effect_id = $1 RETURNING *",
            effect_id,
            outcome,
        )


def receipt(row: Any) -> dict[str, Any]:
    return {
        "status": "unknown" if row["state"] == "dispatching" else row["state"],
        "effect_id": str(row["effect_id"]),
        "account": row["account"],
        "thread_id": row["thread_id"],
        "message_id": row["message_id"],
        "gmail_message_id": row["gmail_message_id"],
        "draft_cleanup": row["draft_cleanup"],
    }
